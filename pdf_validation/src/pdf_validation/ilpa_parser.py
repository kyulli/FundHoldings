"""Parse ILPA Fee Reporting templates into fund-level aggregates.

Produces layout-agnostic raw_rows first, then a deterministic mapping layer
selects only CSV-comparable fund metrics. Values/column selection stay rule-based.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pdfplumber

from pdf_validation.watermark import extract_clean_page_text

ILPA_TITLE_MARKERS = (
    "ilpa fee reporting template",
    "ilpa fee reporting",
)

_AMOUNT_TOKEN = r"(?:\(\$?-?[\d,]+\)|\$?-?[\d,]+)"
_NINE_AMOUNTS_RE = re.compile(
    rf"^(?P<label>.+?)\s+(?P<amounts>(?:{_AMOUNT_TOKEN}\s+){{8}}{_AMOUNT_TOKEN})\s*$"
)
_AMOUNT_FIND_RE = re.compile(_AMOUNT_TOKEN)
_GROUP_HEADER_RE = re.compile(
    r"LP'?s Allocation of Total Fund.*?Total Fund \(incl\. GP Allocation\).*?GP'?s Allocation of Total Fund",
    flags=re.I,
)
_PERIOD_END_RE = re.compile(
    r"\(([A-Za-z]{3})-(\d{2})\s*-\s*\n?\s*([A-Za-z]{3})-(\d{2})\)",
    flags=re.I,
)
# Cleaned text often splits period across lines as "(Oct-24 -" / "Dec-24)"
_PERIOD_SPLIT_RE = re.compile(
    r"\(([A-Za-z]{3})-(\d{2})\s*-\s+([A-Za-z]{3})-(\d{2})\)",
    flags=re.I,
)
_FUND_NAME_RE = re.compile(r"^(?P<name>.+?)\s*\(USD\)", flags=re.I)
_LP_RE = re.compile(r"Capital Account Statement for\s+(.+?)\s*\(\"LP\"\)", flags=re.I)

_MONTH = {
    "jan": "01",
    "feb": "02",
    "mar": "03",
    "apr": "04",
    "may": "05",
    "jun": "06",
    "jul": "07",
    "aug": "08",
    "sep": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
}


def is_ilpa_fee_reporting(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in ILPA_TITLE_MARKERS)


def _parse_amount(token: str) -> str | None:
    raw = (token or "").strip()
    if not raw:
        return None
    neg = raw.startswith("(") and raw.endswith(")")
    digits = re.sub(r"[^\d-]", "", raw.replace("(", "").replace(")", "").replace("$", ""))
    if digits in {"", "-"}:
        return None
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    if neg and value > 0:
        value = -value
    return format(value, "f")


def _split_nine_amounts(line: str) -> tuple[str, list[str]] | None:
    match = _NINE_AMOUNTS_RE.match(line.strip())
    if not match:
        return None
    label = match.group("label").strip(" :")
    tokens = _AMOUNT_FIND_RE.findall(match.group("amounts"))
    if len(tokens) != 9:
        return None
    return label, tokens


def _period_to_iso(mon: str, yy: str) -> str | None:
    mm = _MONTH.get(mon.lower()[:3])
    if not mm:
        return None
    year = 2000 + int(yy)
    # Month-end day for ILPA period labels.
    day = {
        "01": 31,
        "02": 28,
        "03": 31,
        "04": 30,
        "05": 31,
        "06": 30,
        "07": 31,
        "08": 31,
        "09": 30,
        "10": 31,
        "11": 30,
        "12": 31,
    }[mm]
    if mm == "02" and (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        day = 29
    return f"{year}-{mm}-{day:02d}"


def _detect_as_of(text: str) -> str | None:
    # Prefer QTD window end (first period pair on the cleaned cover).
    compact = re.sub(r"[ \t]+", " ", text)
    compact = re.sub(r"\n+", " ", compact)
    for pattern in (_PERIOD_SPLIT_RE, _PERIOD_END_RE):
        match = pattern.search(compact)
        if match:
            return _period_to_iso(match.group(3), match.group(4))
    return None


def _detect_fund_name(text: str) -> str | None:
    for line in text.splitlines():
        m = _FUND_NAME_RE.match(line.strip())
        if m:
            return m.group("name").strip()
    return None


def _detect_lp_name(text: str) -> str | None:
    m = _LP_RE.search(text)
    if m:
        return m.group(1).strip()
    return None


def _has_group_headers(text: str) -> bool:
    return bool(_GROUP_HEADER_RE.search(text.replace("\n", " ")))


def extract_ilpa_raw_rows(text: str, *, page: int) -> list[dict[str, Any]]:
    """Layout parse only: label + 9 amount tokens + group indices."""
    rows: list[dict[str, Any]] = []
    if not _has_group_headers(text):
        return rows
    for line in text.splitlines():
        split = _split_nine_amounts(line)
        if not split:
            continue
        label, tokens = split
        normalized = [_parse_amount(t) for t in tokens]
        if any(v is None for v in normalized):
            continue
        rows.append(
            {
                "label_raw": label,
                "label_normalized": re.sub(r"\s+", " ", label).strip(),
                "amount_tokens": tokens,
                "amounts_normalized": normalized,
                "groups": {
                    "lp": {
                        "qtd": normalized[0],
                        "ytd": normalized[1],
                        "since_inception": normalized[2],
                    },
                    "total_fund": {
                        "qtd": normalized[3],
                        "ytd": normalized[4],
                        "since_inception": normalized[5],
                    },
                    "gp": {
                        "qtd": normalized[6],
                        "ytd": normalized[7],
                        "since_inception": normalized[8],
                    },
                },
                "source_page": page,
                "raw_line": line,
                "column_groups_valid": True,
            }
        )
    return rows


def map_ilpa_comparable_metrics(raw_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic semantic mapping for CSV-comparable fund metrics only."""
    by_label = {row["label_normalized"].lower(): row for row in raw_rows}
    ending = by_label.get("ending nav - net of incentive allocation")
    ugl = by_label.get("change in unrealized gain / (loss)")
    mapped: dict[str, Any] = {}
    evidence: dict[str, Any] = {}

    if ending and ending.get("column_groups_valid"):
        value = ending["groups"]["total_fund"]["since_inception"]
        # Ending NAV is constant across QTD/YTD/SI for this template; prefer SI, fall back to QTD.
        if value is None:
            value = ending["groups"]["total_fund"]["qtd"]
        mapped["fund_total_fair_value"] = value
        evidence["fund_total_fair_value"] = {
            "label": ending["label_normalized"],
            "group": "total_fund",
            "period": "since_inception",
            "raw_line": ending["raw_line"],
            "source_page": ending["source_page"],
            "amount_token": ending["amount_tokens"][5],
        }

    if ugl and ugl.get("column_groups_valid"):
        value = ugl["groups"]["total_fund"]["since_inception"]
        mapped["fund_unrealized"] = value
        evidence["fund_unrealized"] = {
            "label": ugl["label_normalized"],
            "group": "total_fund",
            "period": "since_inception",
            "raw_line": ugl["raw_line"],
            "source_page": ugl["source_page"],
            "amount_token": ugl["amount_tokens"][5],
        }

    return {"mapped_metrics": mapped, "evidence": evidence}


def parse_ilpa_fee_reporting(pdf_path: Path | str, *, max_pages: int = 4) -> dict[str, Any]:
    """Parse ILPA Fee Reporting into fund_aggregate-compatible payload."""
    pdf_path = Path(pdf_path)
    page_texts: list[tuple[int, str]] = []
    with pdfplumber.open(str(pdf_path)) as doc:
        for page in doc.pages[:max_pages]:
            text, _, _ = extract_clean_page_text(page)
            page_texts.append((int(page.page_number), text or ""))

    full_text = "\n".join(text for _, text in page_texts)
    if not is_ilpa_fee_reporting(full_text):
        return {
            "fund_name": None,
            "as_of_date": None,
            "fund_total_cost": None,
            "fund_total_fair_value": None,
            "fund_unrealized": None,
            "source_page": None,
            "raw_line": None,
            "parse_status": "parse_error",
            "document_subtype": "ilpa_fee_reporting",
            "reason": "ilpa_title_not_found",
            "raw_rows": [],
            "mapped_metrics": {},
            "evidence": {},
        }

    if not _has_group_headers(full_text):
        return {
            "fund_name": _detect_fund_name(full_text),
            "lp_name": _detect_lp_name(full_text),
            "as_of_date": _detect_as_of(full_text),
            "fund_total_cost": None,
            "fund_total_fair_value": None,
            "fund_unrealized": None,
            "source_page": page_texts[0][0] if page_texts else None,
            "raw_line": None,
            "parse_status": "parse_error",
            "document_subtype": "ilpa_fee_reporting",
            "reason": "missing_lp_total_fund_gp_headers",
            "raw_rows": [],
            "mapped_metrics": {},
            "evidence": {},
        }

    raw_rows: list[dict[str, Any]] = []
    for page_num, text in page_texts:
        raw_rows.extend(extract_ilpa_raw_rows(text, page=page_num))

    mapped_payload = map_ilpa_comparable_metrics(raw_rows)
    mapped = mapped_payload["mapped_metrics"]
    evidence = mapped_payload["evidence"]
    as_of = _detect_as_of(full_text)
    fund_name = _detect_fund_name(full_text)
    lp_name = _detect_lp_name(full_text)

    fair_value = mapped.get("fund_total_fair_value")
    unrealized = mapped.get("fund_unrealized")
    ok = bool(as_of and fair_value and unrealized)
    return {
        "fund_name": fund_name,
        "lp_name": lp_name,
        "as_of_date": as_of,
        "fund_total_cost": None,
        "fund_total_fair_value": fair_value,
        "fund_unrealized": unrealized,
        "source_page": (evidence.get("fund_total_fair_value") or {}).get("source_page")
        or (page_texts[0][0] if page_texts else None),
        "raw_line": (evidence.get("fund_total_fair_value") or {}).get("raw_line"),
        "parse_status": "ok" if ok else "parse_error",
        "document_subtype": "ilpa_fee_reporting",
        "reason": None if ok else "missing_comparable_metrics_or_as_of",
        "raw_rows": raw_rows,
        "mapped_metrics": mapped,
        "evidence": evidence,
        "column_groups": ["lp", "total_fund", "gp"],
        "period_columns": ["qtd", "ytd", "since_inception"],
    }
