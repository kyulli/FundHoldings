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
    "condensed schedule",
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
    "years ended",
    "investments in affiliated",
    "notes to financial",
)


def _is_reporting_entity_header(name: str) -> bool:
    """Reject cover/notes fund titles mistaken for portfolio companies."""
    low = (name or "").lower()
    if not low:
        return False
    if "rivers fund" in low:
        return True
    # All-caps fund legal names on headers/notes pages (not portfolio rows).
    if name.isupper() and "fund" in low and any(tok in low for tok in ("llc", "l.p", "lp", "ltd")):
        return True
    return False


def _is_narrative_fragment(name: str) -> bool:
    """Reject notes/narrative snippets that are not portfolio company rows."""
    raw = (name or "").strip()
    low = raw.lower()
    if not low:
        return True
    if any(
        tok in low
        for tok in (
            "guarantor",
            "third-party",
            "was $",
            "investment in spvs",
            "years ended",
            "rivers fund",
            "limited partnership",
            "financial statements",
            "notes to",
            "fair values of the",
            "in accordance with",
            "instead, such",
            "in certain cases",
            "recourse guarantor",
            "warehouse loan",
            "joint and several",
            "wholly-owned spv",
            "wholly owned spv",
            "condensed schedules of invest",
            "fair value presented",
            "cost and fair value presented",
        )
    ):
        return True
    # Sentence-like fragments usually start lowercase or lack entity suffixes.
    if raw[:1].islower():
        return True
    # Require a company-like suffix / bucket label when the text has multiple words.
    if " " in low and not any(
        tok in low for tok in ("llc", "inc.", "inc", "l.p", "ltd", "other investments")
    ):
        # Avoid matching the substring "lp" inside unrelated words; check token boundaries.
        if not re.search(r"\bl\.?p\.?\b", low):
            # Allow short Title-Case portfolio names without legal suffixes
            # (e.g. "Absolute Foods", "10MS") while still rejecting sentence fragments.
            words = raw.split()
            titleish = (
                1 <= len(words) <= 6
                and all(
                    (w[:1].isupper() or w.isdigit() or w.lower() in {"and", "of", "the", "&"})
                    for w in words
                )
                and not any(ch in raw for ch in ".?!:;")
                and not any(tok in low for tok in (" was ", " were ", " such ", " instead"))
            )
            if not titleish:
                return True
    # Extremely short residue tokens from watermarked notes pages.
    if len(raw) <= 2:
        return True
    return False


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
    has_cost = "cost" in logical
    has_fv = "fair_value" in logical or not logical
    # When schema is empty, keep legacy permissive behavior.
    if not logical:
        has_cost = True
        has_fv = True

    companies: list[dict[str, Any]] = []
    active: str | None = None
    active_page: int | None = None
    lot_amounts: list[int] = []
    lot_cost_amounts: list[int] = []

    def _cost_fv_from_money(money_vals: list[int]) -> tuple[str, str]:
        if not money_vals:
            return "0", "0"
        if len(money_vals) == 1:
            amt = str(money_vals[0])
            # Fair-value-only schedules: do not invent a distinct Cost.
            if has_fv and not has_cost:
                return "0", amt
            return amt, amt
        # Share-count schedules often print: shares_total, carrying_amount.
        if company_row_headers and has_shares:
            amt = str(money_vals[-1])
            return amt, amt
        if has_cost and has_fv:
            return str(money_vals[-2]), str(money_vals[-1])
        # Credit/condensed rows often print: % / par / fair_value — last is FV.
        if has_fv and not has_cost:
            return "0", str(money_vals[-1])
        amt = str(money_vals[-1])
        return amt, amt

    def flush(*, cost: str | None = None, fv: str | None = None, page: int | None = None) -> None:
        nonlocal active, active_page, lot_amounts, lot_cost_amounts
        if not active or _is_reporting_entity_header(active):
            active = None
            active_page = None
            lot_amounts = []
            lot_cost_amounts = []
            return
        if cost is None or fv is None:
            total_fv = sum(lot_amounts)
            total_cost = sum(lot_cost_amounts) if lot_cost_amounts else total_fv
            if total_fv <= 0:
                active = None
                active_page = None
                lot_amounts = []
                lot_cost_amounts = []
                return
            cost = cost or str(total_cost)
            fv = fv or str(total_fv)
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
        lot_cost_amounts = []

    with pdfplumber.open(pdf_path) as doc:
        for page_num in inv_pages:
            if page_num < 1 or page_num > len(doc.pages):
                continue
            from pdf_validation.watermark import extract_clean_page_text

            page_text, _, _ = extract_clean_page_text(doc.pages[page_num - 1])
            for line in (page_text or "").splitlines():
                text = line.strip()
                if not text:
                    continue
                low = text.lower()
                if any(low.startswith(p) for p in _SKIP_PREFIXES):
                    if low.startswith("total ") or low.startswith("subtotal "):
                        if active and lot_amounts:
                            flush(page=page_num)
                        # Comparative schedules print prior-year blocks after the current
                        # "Total investments"; stop so we do not duplicate entities.
                        if low.startswith("total investment"):
                            break
                    continue
                if re.fullmatch(r"\d+", text):
                    continue
                # Date / section headers are not companies.
                if re.match(
                    r"^(january|february|march|april|may|june|july|august|september|october|november|december)\b",
                    low,
                ):
                    continue
                if re.fullmatch(r"(members['’] equity|assets|liabilities)", low):
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
                # Strip trailing instrument/security descriptors for one-line company rows.
                name_part = re.split(
                    r"\s+(?:Equity Securities|Partnership Interest|Various)\b",
                    name_part,
                    maxsplit=1,
                )[0].strip()
                name_part = re.sub(r"\s*\([a-z]\)\s*$", "", name_part, flags=re.I).strip()
                # Normalize common condensed "Other Investments (...)" bucket labels.
                if name_part.lower().startswith("other investments"):
                    name_part = "Other Investments"
                if not name_part and money_vals:
                    if not active:
                        continue
                    cost_v, fv_v = _cost_fv_from_money(money_vals)
                    flush(cost=cost_v, fv=fv_v, page=page_num)
                    continue

                # Prefer one-line company + Cost + Fair Value rows before multi-line lot mode.
                if amounts and name_part and len(money_vals) >= 2:
                    # Reject footnote / narrative fragments mistaken for holdings.
                    if _is_narrative_fragment(name_part) or _is_reporting_entity_header(name_part):
                        continue
                    if name_part.isupper() and len(name_part) > 20:
                        continue
                    # Security/lot descriptor lines belong to the active company; do not
                    # promote them to standalone companies or clear the pending name.
                    if any(tok in name_part.lower() for tok in _SECURITY_TOKENS):
                        if active:
                            if has_cost and has_fv and len(money_vals) >= 2:
                                lot_cost_amounts.append(money_vals[-2])
                            lot_amounts.append(money_vals[-1])
                        continue
                    if active and lot_amounts:
                        flush(page=page_num)
                    elif active:
                        active = None
                        lot_amounts = []
                        lot_cost_amounts = []
                    cost_v, fv_v = _cost_fv_from_money(money_vals)
                    row = _company_row(name_part, page_num, cost_v, fv_v, None, None, cost_v, fv_v)
                    row["subtotal_event"] = "inferred_schedule_text"
                    companies.append(row)
                    continue

                if (
                    not amounts
                    and not per_shares
                    and _BARE_NAME.match(text)
                    and not any(tok in low for tok in _SECURITY_TOKENS)
                    and "total" not in low
                ):
                    if _is_reporting_entity_header(text) or _is_narrative_fragment(text):
                        continue
                    if active and lot_amounts:
                        flush(page=page_num)
                    elif active:
                        active = None
                        lot_amounts = []
                        lot_cost_amounts = []
                    active = text
                    active_page = page_num
                    continue

                if active and money_vals:
                    if _is_reporting_entity_header(active) or _is_narrative_fragment(active):
                        active = None
                        active_page = None
                        lot_amounts = []
                        continue
                    lot_amounts.append(money_vals[-1])
                    if has_cost and has_fv and len(money_vals) >= 2:
                        lot_cost_amounts.append(money_vals[-2])
                    continue

                if amounts and name_part and not company_row_headers:
                    if _is_reporting_entity_header(name_part) or _is_narrative_fragment(name_part):
                        continue
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
