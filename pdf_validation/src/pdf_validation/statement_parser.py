"""Parse fund-level investment aggregates from statement pages."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pdfplumber


def _clean_num(value: str | None) -> str | None:
    if not value:
        return None
    neg = "(" in value or value.strip().startswith("-")
    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        return None
    return f"-{digits}" if neg else digits


def parse_fund_aggregate(pdf_path: Path | str, max_pages: int = 6) -> dict[str, Any]:
    """Extract Investments at fair value (cost ...) style fund aggregates."""
    pdf_path = Path(pdf_path)
    cost = None
    fair_value = None
    source_page = None
    raw_line = None
    as_of = None
    fund_name = None

    with pdfplumber.open(pdf_path) as doc:
        if doc.pages:
            cover = doc.pages[0].extract_text() or ""
            lines = [ln.strip() for ln in cover.splitlines() if ln.strip()]
            if lines:
                fund_name = lines[0]
            m_date = re.search(
                r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s*(\d{4})",
                cover,
                flags=re.I,
            )
            if not m_date:
                m_date = re.search(
                    r"ENDED\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s*(\d{4})",
                    cover,
                    flags=re.I,
                )
            if m_date:
                month = {
                    "january": "01",
                    "february": "02",
                    "march": "03",
                    "april": "04",
                    "may": "05",
                    "june": "06",
                    "july": "07",
                    "august": "08",
                    "september": "09",
                    "october": "10",
                    "november": "11",
                    "december": "12",
                }[m_date.group(1).lower()]
                as_of = f"{m_date.group(3)}-{month}-{int(m_date.group(2)):02d}"
            # Prefer period-end if multiple dates present on cover.
            all_dates = list(
                re.finditer(
                    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s*(\d{4})",
                    cover,
                    flags=re.I,
                )
            )
            if all_dates:
                m_date = all_dates[-1]
                month = {
                    "january": "01",
                    "february": "02",
                    "march": "03",
                    "april": "04",
                    "may": "05",
                    "june": "06",
                    "july": "07",
                    "august": "08",
                    "september": "09",
                    "october": "10",
                    "november": "11",
                    "december": "12",
                }[m_date.group(1).lower()]
                as_of = f"{m_date.group(3)}-{month}-{int(m_date.group(2)):02d}"

        for idx, page in enumerate(doc.pages[:max_pages]):
            text = page.extract_text() or ""
            # Pattern 1: single-line format
            #   Investments, at fair value (Cost $X) $Y
            patterns_single = [
                r"Investments?, at (?:estimated\s+)?fair value\s*\(cost[:\s=]*\$?\s*([0-9,\s]+)\)\s*\$?\s*([0-9,\s]+)",
                r"Investment, at (?:estimated\s+)?fair value\s*\(cost\s*\$?\s*([0-9,\s]+)\)\s*\$?\s*([0-9,\s]+)",
            ]
            for pattern in patterns_single:
                match = re.search(pattern, text, flags=re.I)
                if match:
                    cost = _clean_num(match.group(1))
                    fair_value = _clean_num(match.group(2))
                    source_page = idx + 1
                    raw_line = match.group(0)
                    break

            # Pattern 2: two-line format
            #   Investments, at estimated fair value  $ 207,461,318
            #   (Cost equal to $177,475,405 at ...)
            if not (cost and fair_value):
                m_fv = re.search(
                    r"Investments?, at (?:estimated\s+)?fair value\s*\$?\s*([0-9,\s]+)",
                    text,
                    flags=re.I,
                )
                m_cost = re.search(
                    r"\(Cost equal to\s*\$?\s*([0-9,]+)",
                    text,
                    flags=re.I,
                )
                if m_fv and m_cost:
                    fair_value = _clean_num(m_fv.group(1))
                    cost = _clean_num(m_cost.group(1))
                    source_page = idx + 1
                    raw_line = m_fv.group(0) + " / " + m_cost.group(0)

            if cost and fair_value:
                break

    unrealized = None
    if cost and fair_value:
        try:
            unrealized = str(int(fair_value) - int(cost))
        except ValueError:
            unrealized = None

    return {
        "fund_name": fund_name,
        "as_of_date": as_of,
        "fund_total_cost": cost,
        "fund_total_fair_value": fair_value,
        "fund_unrealized": unrealized,
        "source_page": source_page,
        "raw_line": raw_line,
        "parse_status": "ok" if cost and fair_value else "parse_error",
    }
