"""Schema-driven holdings schedule extraction for unseen templates.

No fund-specific or named secondary-statement parsers. Company/lot recovery
uses only inferred columns + generic line geometry on schedule pages.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pdfplumber

from pdf_validation.text_fallback import _company_row


_AMOUNT = re.compile(
    r"(?<!\S)\$?\(?\d{1,3}(?:,\d{3})+\)?(?!\S)|(?<!\S)\$?\(?\d{4,}\)?(?!\S)|(?<!\S)\(\d[\d,]*\)(?!\S)"
)
_PER_SHARE = re.compile(r"(?<!\S)\d+\.\d{1,4}(?!\S)")
_BARE_NAME = re.compile(r"^[A-Z0-9][A-Za-z0-9 .,&'()/+-]*$")

_SECURITY_TOKENS = (
    "shares",
    "preferred",
    "preference",
    "common",
    "note",
    "warrant",
    "convertible",
    "safe",
    "stock",
    "equity",
    "token",
    "tpa",
    "saft",
    "interest",
    "series",
    "seed",
    "class",
    "pre-",
    "cccps",
    "ccps",
    "rcps",
    "cps",
    "ordinary",
)

_SKIP_PREFIXES = (
    "schedule of",
    "confidential",
    "as of ",
    "shares ",
    "cost/",
    "fv/",
    "fair value",
    "private investments",
    "crypto assets",
    "subtotal ",
    "total ",
    "page ",
)


def parse_inferred_schedule_companies(
    pdf_path: Path | str,
    inv_pages: list[int],
    inferred_schema: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Extract company Cost/FV from schedule pages using inferred schema cues."""
    inferred_schema = inferred_schema or {}
    logical = set(inferred_schema.get("logical_columns") or [])
    company_row_headers = bool(inferred_schema.get("company_row_headers"))
    has_shares = "shares" in logical
    has_cost = "cost" in logical or not logical
    has_fv = "fair_value" in logical or not logical

    companies: list[dict[str, Any]] = []
    active: str | None = None
    active_page: int | None = None
    lot_amounts: list[int] = []

    def _cost_fv_from_money(money_vals: list[int]) -> tuple[str, str]:
        if not money_vals:
            return "0", "0"
        if len(money_vals) == 1:
            amt = str(money_vals[0])
            return amt, amt
        # Share-count schedules often print: shares_total, carrying_amount.
        if company_row_headers and has_shares:
            amt = str(money_vals[-1])
            return amt, amt
        if has_cost and has_fv:
            return str(money_vals[-2]), str(money_vals[-1])
        amt = str(money_vals[-1])
        return amt, amt

    def flush(*, cost: str | None = None, fv: str | None = None, page: int | None = None) -> None:
        nonlocal active, active_page, lot_amounts
        if not active:
            lot_amounts = []
            return
        if cost is None or fv is None:
            total = sum(lot_amounts)
            if total <= 0:
                active = None
                active_page = None
                lot_amounts = []
                return
            cost = cost or str(total)
            fv = fv or str(total)
        page_num = page or active_page or (inv_pages[0] if inv_pages else 1)
        row = _company_row(active, page_num, cost, fv, None, None, cost, fv)
        row["subtotal_event"] = "inferred_schedule_text"
        row["entity_grain"] = "company"
        if company_row_headers:
            row["schema_source"] = "company_row_headers"
        companies.append(row)
        active = None
        active_page = None
        lot_amounts = []

    with pdfplumber.open(pdf_path) as doc:
        for page_num in inv_pages:
            if page_num < 1 or page_num > len(doc.pages):
                continue
            for line in (doc.pages[page_num - 1].extract_text() or "").splitlines():
                text = line.strip()
                if not text:
                    continue
                low = text.lower()
                if any(low.startswith(p) for p in _SKIP_PREFIXES):
                    if low.startswith("total ") or low.startswith("subtotal "):
                        if active and lot_amounts:
                            flush(page=page_num)
                    continue
                if re.fullmatch(r"\d+", text):
                    continue

                amounts = _AMOUNT.findall(text)
                per_shares = _PER_SHARE.findall(text)
                money_vals = [_to_int(a) for a in amounts]
                money_vals = [m for m in money_vals if m is not None]

                name_part = text
                for token in amounts + per_shares:
                    idx = name_part.rfind(token)
                    if idx >= 0:
                        name_part = name_part[:idx].rstrip()
                name_part = name_part.strip(" :-$")
                if not name_part and money_vals:
                    if not active:
                        continue
                    cost_v, fv_v = _cost_fv_from_money(money_vals)
                    flush(cost=cost_v, fv=fv_v, page=page_num)
                    continue

                if (
                    not amounts
                    and not per_shares
                    and _BARE_NAME.match(text)
                    and not any(tok in low for tok in _SECURITY_TOKENS)
                    and "total" not in low
                ):
                    if active and lot_amounts:
                        flush(page=page_num)
                    elif active:
                        active = None
                        lot_amounts = []
                    active = text
                    active_page = page_num
                    continue

                if active and money_vals:
                    lot_amounts.append(money_vals[-1])
                    continue

                if amounts and name_part and not company_row_headers:
                    if any(tok in name_part.lower() for tok in _SECURITY_TOKENS):
                        continue
                    cost_v, fv_v = _cost_fv_from_money(money_vals)
                    row = _company_row(name_part, page_num, cost_v, fv_v, None, None, cost_v, fv_v)
                    row["subtotal_event"] = "inferred_schedule_text"
                    companies.append(row)

    if active and lot_amounts:
        flush()
    return companies


def _to_int(token: str) -> int | None:
    digits = re.sub(r"[^\d]", "", token)
    if not digits:
        return None
    return int(digits)
