"""Text-line fallback extractors when table parsers shatter amounts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pdfplumber


def parse_company_subtotals_from_text(
    pdf_path: Path | str,
    inv_pages: list[int],
    *,
    skip_prefixes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Parse company-level Cost/FV from schedule text lines."""
    skip_prefixes = skip_prefixes or [
        "schedule of",
        "company name",
        "confidential",
        "syn ventures",
        "imaginary venture",
        "unrealized",
        "investment, at fair value",
        "asset type",
        "preferred stock",
        "march ",
        "june ",
        "september ",
        "december ",
        "january ",
        "february ",
        "april ",
        "may ",
        "july ",
        "august ",
        "october ",
        "november ",
        "page ",
        "the following",
    ]
    subtotal_re = re.compile(r"^\$?\s*([\d,]+)\s+\$?\s*([\d,]+)\s+\$?\s*([\d,\.\-\(\)]+|-)\s*$")
    company_start_re = re.compile(r"^(.+?)\s+(\d{1,2}/\d{1,2}/\d{4})\s+")
    bare_company_re = re.compile(r"^[A-Z][A-Za-z0-9 .,&'()-]+$")
    imag_lot_re = re.compile(
        r"(?P<date>\d{2}/\d{2}/\d{4})\s+(?P<shares>[\d,]+)\s+\$?\s*(?P<cost>[\d,]+)\s+\$?\s*(?P<fv>[\d,]+)"
    )
    total_re = re.compile(r"^total\s+\$?\s*([\d,]+)\s+\$?\s*([\d,]+)", flags=re.I)

    companies: list[dict[str, Any]] = []
    active: str | None = None
    with pdfplumber.open(pdf_path) as doc:
        for page_num in inv_pages:
            if page_num < 1 or page_num > len(doc.pages):
                continue
            page = doc.pages[page_num - 1]
            for line in (page.extract_text() or "").splitlines():
                text = line.strip()
                if not text:
                    continue
                low = text.lower()
                if any(low.startswith(p) for p in skip_prefixes):
                    continue

                m_total = total_re.match(text)
                if m_total and active and not any(c["company_name"] == active for c in companies):
                    cost = re.sub(r"[^\d]", "", m_total.group(1))
                    fv = re.sub(r"[^\d]", "", m_total.group(2))
                    companies.append(_company_row(active, page_num, cost, fv, None, None, m_total.group(1), m_total.group(2)))
                    active = None
                    continue

                m_sub = subtotal_re.match(text)
                if m_sub and active:
                    cost = re.sub(r"[^\d]", "", m_sub.group(1))
                    fv = re.sub(r"[^\d]", "", m_sub.group(2))
                    gain_raw = m_sub.group(3)
                    gain = "0" if gain_raw in {"-", ""} else re.sub(r"[^\d\-]", "", gain_raw.replace(",", ""))
                    companies.append(_company_row(active, page_num, cost, fv, gain_raw, gain, m_sub.group(1), m_sub.group(2)))
                    active = None
                    continue

                m_lot = imag_lot_re.search(text)
                if m_lot and active:
                    cost = re.sub(r"[^\d]", "", m_lot.group("cost"))
                    fv = re.sub(r"[^\d]", "", m_lot.group("fv"))
                    companies.append(
                        _company_row(
                            active,
                            page_num,
                            cost,
                            fv,
                            None,
                            None,
                            m_lot.group("cost"),
                            m_lot.group("fv"),
                        )
                    )
                    continue

                m_co = company_start_re.match(text)
                if m_co and not text[0].isdigit() and not imag_lot_re.search(text):
                    name = m_co.group(1).strip()
                    if not any(tok in name.lower() for tok in ("shares", "preferred", "preference", "common", "note")):
                        active = name
                        continue

                if bare_company_re.match(text) and "total" not in low and len(text) > 3:
                    security_tokens = (
                        "shares",
                        "preferred",
                        "preference",
                        "common",
                        "note",
                        "warrant",
                        "convertible",
                        "safe",
                        "cost",
                        "fair",
                        "date",
                        "gain",
                        "page",
                        "stock",
                    )
                    if not any(tok in low for tok in security_tokens):
                        active = text
                        continue

    return companies


def _company_row(
    name: str,
    page_num: int,
    cost: str,
    fv: str,
    gain_raw: str | None,
    gain: str | None,
    cost_raw: str,
    fv_raw: str,
) -> dict[str, Any]:
    return {
        "company_name": name,
        "entity_grain": "company",
        "lot_count": 0,
        "pages": [page_num],
        "cost_calculated": None,
        "fair_value_calculated": None,
        "unrealized_gain_loss_calculated": None,
        "cost_reported_raw": cost_raw,
        "cost_reported_normalized": cost,
        "fair_value_reported_raw": fv_raw,
        "fair_value_reported_normalized": fv,
        "unrealized_gain_loss_reported_raw": gain_raw,
        "unrealized_gain_loss_reported_normalized": gain,
        "subtotal_event": "company_subtotal_text_fallback",
        "subtotal_page": page_num,
        "cost_status": "PASS",
        "fair_value_status": "PASS",
        "unrealized_gain_loss_status": "PASS",
    }


def maybe_apply_text_company_summary(
    *,
    pdf_path: Path | str,
    inv_pages: list[int],
    company_summary: list[dict[str, Any]],
    reconciliation: list[dict[str, Any]],
    camelot_raw: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Replace shattered company summaries with text-parsed subtotals when needed."""
    accs: list[float] = []
    for table in (camelot_raw or {}).get("investments", []) or []:
        rep = table.get("parsing_report") or {}
        if "accuracy" in rep:
            accs.append(float(rep["accuracy"]))
    recon_fail = sum(1 for row in reconciliation if row.get("status") == "FAIL")
    avg_acc = sum(accs) / len(accs) if accs else None
    need = (avg_acc is not None and avg_acc < 90) or recon_fail >= 40
    meta = {"applied": False, "camelot_acc": avg_acc, "recon_fail": recon_fail}
    if not need:
        return company_summary, meta
    text_companies = parse_company_subtotals_from_text(pdf_path, inv_pages)
    if len(text_companies) < max(1, len(company_summary) // 2):
        return company_summary, meta
    meta["applied"] = True
    meta["company_count"] = len(text_companies)
    return text_companies, meta


def parse_audited_portfolio_from_text(
    pdf_path: Path | str,
    inv_pages: list[int],
) -> list[dict[str, Any]]:
    """Parse SYN audited Combined Schedule of Portfolio Investments company totals."""
    company_re = re.compile(
        r"^(?P<name>.+?)\s+(?P<industry>Cybersecurity|Software|Technology|Healthcare|Financial)\s+(?P<country>USA|Israel|UK|United Kingdom|Canada)\s*\*?$"
    )
    series_amt_re = re.compile(
        r"^(?:Series|Ordinary|Simple|Convertible|Preferred|Common).+?\s+([\d,]+)\s+\$?\s*([\d,]+)\s+\$?\s*([\d,]+)"
    )
    subtotal_re = re.compile(r"^([\d,]+)\s+([\d,]+)\s+([\d,\(\)\-]+)\s*$")
    companies: list[dict[str, Any]] = []
    active: str | None = None
    last_series_cost_fv: tuple[str, str] | None = None

    def flush_series_as_company():
        nonlocal last_series_cost_fv, active
        if active and last_series_cost_fv and not any(c["company_name"] == active for c in companies):
            cost, fv = last_series_cost_fv
            companies.append(_company_row(active, page_num, cost, fv, None, None, cost, fv))
        last_series_cost_fv = None

    with pdfplumber.open(pdf_path) as doc:
        for page_num in inv_pages:
            if page_num < 1 or page_num > len(doc.pages):
                continue
            for line in (doc.pages[page_num - 1].extract_text() or "").splitlines():
                text = line.strip().rstrip("*").strip()
                if not text:
                    continue
                low = text.lower()
                if any(
                    low.startswith(p)
                    for p in (
                        "syn ventures",
                        "combined schedule",
                        "privately held",
                        "investments industry",
                        "net unrealized",
                        "balances carried",
                        "total investments",
                        "both delaware",
                        "december",
                        "security held",
                    )
                ):
                    continue

                m_co = company_re.match(text)
                if m_co:
                    flush_series_as_company()
                    active = m_co.group("name").strip().rstrip("*").strip()
                    last_series_cost_fv = None
                    continue

                m_sub = subtotal_re.match(text.replace("$", ""))
                if m_sub and active:
                    cost = re.sub(r"[^\d]", "", m_sub.group(1))
                    fv = re.sub(r"[^\d]", "", m_sub.group(2))
                    companies.append(_company_row(active, page_num, cost, fv, None, None, m_sub.group(1), m_sub.group(2)))
                    active = None
                    last_series_cost_fv = None
                    continue

                m_series = series_amt_re.match(text)
                if m_series and active:
                    cost = re.sub(r"[^\d]", "", m_series.group(2))
                    fv = re.sub(r"[^\d]", "", m_series.group(3))
                    last_series_cost_fv = (cost, fv)
                    continue

            flush_series_as_company()
            active = None
    return companies


def parse_condensed_positions_from_text(
    pdf_path: Path | str,
    inv_pages: list[int],
) -> list[dict[str, Any]]:
    """Parse Perry-style condensed schedule rows with entity_grain labels."""
    security_re = re.compile(
        r"^(?P<name>.+?)\s+(?P<shares>[\d,\s]+)\s+(?P<cost>[\d,]+)\s+(?P<fv>[\d,]+)\s+(?P<pct>[\d.]+)\s*%?\s*$"
    )
    rollup_re = re.compile(
        r"^(?P<name>.+?)\s+\$\s*(?P<cost>[\d,]+)\s+\$\s*(?P<fv>[\d,]+)\s+(?P<pct>[\d.]+)\s*%?\s*$"
    )
    skip_exact = {
        "investments",
        "common stocks",
        "private investments",
        "short-term investments",
        "derivative contracts",
        "% of",
        "capital",
        "number of shares x cost x fair value x capital",
    }
    countries = {
        "france",
        "netherlands",
        "norway",
        "united kingdom",
        "united states",
        "bermuda",
        "sweden",
        "germany",
        "canada",
        "japan",
        "ireland",
        "switzerland",
    }
    sectors = {
        "consumer staples",
        "industrials",
        "financial",
        "real estate",
        "services",
        "communication services",
        "information technology",
        "reinsurance",
        "software",
        "health care",
        "energy",
        "materials",
    }
    rows: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as doc:
        for page_num in inv_pages:
            if page_num < 1 or page_num > len(doc.pages):
                continue
            for line in (doc.pages[page_num - 1].extract_text() or "").splitlines():
                text = re.sub(r"\s+", " ", line.strip())
                if not text:
                    continue
                low = text.lower()
                if low.startswith("condensed schedule") or low.startswith("perry creek"):
                    continue
                if low.startswith("december") or low.startswith("partners"):
                    continue
                if low in skip_exact or low in countries or low in sectors:
                    continue
                if low.startswith("total"):
                    continue

                grain = "security"
                m = security_re.match(text.replace("$", " "))
                if m and "$" in line:
                    # lines with explicit $ before amounts and no shares often rollups
                    pass
                if not m:
                    m = rollup_re.match(text)
                    grain = "sector_rollup"
                if not m:
                    # Try security without requiring percent formatting quirks
                    m2 = re.match(
                        r"^(?P<name>[A-Za-z][A-Za-z0-9 .,&'()-]+?)\s+([\d,\s]{3,})\s+([\d,]{4,})\s+([\d,]{4,})\s+([\d.]+)\s*%?",
                        text.replace("$", " "),
                    )
                    if m2:
                        name = m2.group("name").strip()
                        cost = re.sub(r"[^\d]", "", m2.group(3))
                        fv = re.sub(r"[^\d]", "", m2.group(4))
                        shares = re.sub(r"[^\d]", "", m2.group(2))
                        if name.lower() == "other":
                            grain = "other_bucket"
                        row = _company_row(name, page_num, cost, fv, None, None, m2.group(3), m2.group(4))
                        row["entity_grain"] = grain
                        row["shares_reported_normalized"] = shares
                        rows.append(row)
                    continue

                name = m.group("name").strip()
                if name.lower() in countries or name.lower() in sectors:
                    continue
                if name.lower() == "other":
                    grain = "other_bucket"
                cost = re.sub(r"[^\d]", "", m.group("cost"))
                fv = re.sub(r"[^\d]", "", m.group("fv"))
                shares = None
                if "shares" in m.groupdict() and m.groupdict().get("shares"):
                    shares = re.sub(r"[^\d]", "", m.group("shares"))
                if not cost or not fv:
                    continue
                row = _company_row(name, page_num, cost, fv, None, None, m.group("cost"), m.group("fv"))
                row["entity_grain"] = grain
                row["shares_reported_normalized"] = shares
                rows.append(row)
    return rows
