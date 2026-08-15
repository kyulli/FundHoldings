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

    # ILPA Fee Reporting: dedicated fund-level parser (no position schedule).
    try:
        from pdf_validation.ilpa_parser import is_ilpa_fee_reporting, parse_ilpa_fee_reporting
        from pdf_validation.watermark import extract_clean_page_text

        with pdfplumber.open(str(pdf_path)) as doc:
            probe = ""
            for page in doc.pages[: min(2, len(doc.pages))]:
                text, _, _ = extract_clean_page_text(page)
                probe += "\n" + (text or "")
        if is_ilpa_fee_reporting(probe):
            return parse_ilpa_fee_reporting(pdf_path, max_pages=max(max_pages, 4))
    except Exception:  # noqa: BLE001
        # Fall through to statement aggregate parser.
        pass

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


_TOKEN = r"(?:\(\s*-?[\d,]+\s*\)|-?[\d,]+|[-–—])"
_TRAILING_AMOUNT = re.compile(rf"^(?P<label>.*)\s+(?P<a>{_TOKEN})\s*$")
_YEAR_HEADER = re.compile(r"^(?P<y1>20\d{2})\s+(?P<y2>20\d{2})\s*$")
_MONTH_LINE = re.compile(
    r"^(january|february|march|april|may|june|july|august|september|october|november|december)\b",
    flags=re.I,
)

STATEMENT_CLASSIFICATIONS = (
    "cash",
    "investment",
    "other_asset",
    "subscription_line",
    "other_liability",
    "equity",
    "ignore",
    "unknown",
    "total_assets",
    "total_liabilities",
)

DEFAULT_VENDOR_CATEGORY_MAP = {
    "cash": {"vendor_source_asset": "Non-Investment Assets - Cash", "sign": 1},
    "other_asset": {"vendor_source_asset": "Non-Investment Assets - Other Assets", "sign": 1},
    "subscription_line": {
        "vendor_source_asset": "Non-Investment Liabilities - Subscription Line of Credit",
        "sign": -1,
    },
    "other_liability": {
        "vendor_source_asset": "Non-Investment Liabilities - Other Liabilities",
        "sign": -1,
    },
}


def _parse_amount_token(token: str | None) -> tuple[str | None, str]:
    """Return (normalized_amount, parse_status). Dash means explicit zero."""
    if token is None:
        return None, "blank"
    raw = str(token).strip()
    if not raw:
        return None, "blank"
    if raw in {"-", "–", "—"}:
        return "0", "dash"
    cleaned = _clean_num(raw)
    if cleaned is None:
        return None, "parse_error"
    return cleaned, "ok"


def _split_statement_amounts(raw_line: str) -> tuple[str, list[str]] | None:
    """Peel one or two trailing amount tokens; return (label, [left, right?]).

    Currency signs like ``$ 858,926`` are normalized before splitting so the first
    column is not swallowed into the label.
    """
    norm = re.sub(r"\$", " ", str(raw_line or ""))
    norm = re.sub(r"\s+", " ", norm).strip()
    if not norm:
        return None
    tokens: list[str] = []
    rest = norm
    for _ in range(2):
        m = _TRAILING_AMOUNT.match(rest)
        if not m:
            break
        tokens.insert(0, m.group("a"))
        rest = m.group("label").strip()
    if not tokens or not rest:
        return None
    return rest, tokens


def _normalize_statement_label(label: str) -> str:
    """Conservative label cleanup: collapse spaces and trailing dash/noise glyphs."""
    from pdf_validation.watermark import normalize_statement_label

    return normalize_statement_label(label)


def classify_statement_line(
    *,
    label_normalized: str,
    section: str | None,
    aliases: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Rule-first statement-line classifier with LLM-ready closed category set."""
    low = (label_normalized or "").lower().strip(" :-")
    aliases = aliases or {}

    def _hit(keys: list[str]) -> bool:
        return any(k in low for k in keys)

    # Strong structural totals / controls.
    if low.startswith("total assets"):
        return {"classification": "total_assets", "confidence": 1.0, "classification_source": "rule", "reason": "total_assets"}
    if low.startswith("total liabilities and") or ("total liabilities" in low and "equity" in low):
        return {"classification": "ignore", "confidence": 1.0, "classification_source": "rule", "reason": "combined_total"}
    if low.startswith("total liabilities"):
        return {
            "classification": "total_liabilities",
            "confidence": 1.0,
            "classification_source": "rule",
            "reason": "total_liabilities",
        }
    if "investments at fair value" in low or low.startswith("investments, at fair value"):
        return {"classification": "investment", "confidence": 1.0, "classification_source": "rule", "reason": "investments"}

    # Configurable aliases first after structural rules.
    for cls, pats in aliases.items():
        if cls not in STATEMENT_CLASSIFICATIONS:
            continue
        for pat in pats or []:
            p = str(pat).lower()
            if p and (low == p or low.startswith(p) or p in low):
                return {
                    "classification": cls,
                    "confidence": 0.95,
                    "classification_source": "alias",
                    "reason": f"alias:{pat}",
                }

    if "cash and cash equivalents" in low or low == "cash":
        return {"classification": "cash", "confidence": 1.0, "classification_source": "rule", "reason": "cash"}
    if _hit(["note payable", "line of credit", "subscription line", "credit facility"]):
        return {
            "classification": "subscription_line",
            "confidence": 0.95,
            "classification_source": "rule",
            "reason": "subscription_line",
        }
    if section == "equity" or _hit(["managing member", "members' equity", "members’ equity", "partners' capital"]):
        return {"classification": "equity", "confidence": 0.9, "classification_source": "rule", "reason": "equity"}
    if section == "assets":
        return {"classification": "other_asset", "confidence": 0.85, "classification_source": "rule", "reason": "assets_residual"}
    if section == "liabilities":
        return {
            "classification": "other_liability",
            "confidence": 0.85,
            "classification_source": "rule",
            "reason": "liabilities_residual",
        }
    return {
        "classification": "unknown",
        "confidence": 0.2,
        "classification_source": "rule",
        "reason": "unclassified",
    }


def parse_statement_of_assets_lines(pdf_path: Path | str, max_pages: int = 8) -> list[dict[str, Any]]:
    """Generic Statement of Assets/Liabilities extraction with current/prior columns."""
    from pdf_validation.watermark import extract_clean_page_text

    pdf_path = Path(pdf_path)
    rows: list[dict[str, Any]] = []
    with pdfplumber.open(str(pdf_path)) as doc:
        for page in doc.pages[:max_pages]:
            text, _, _ = extract_clean_page_text(page)
            low_page = (text or "").lower()
            if "cash and cash equivalents" not in low_page and "total assets" not in low_page:
                continue

            section: str | None = None
            year_current: str | None = None
            year_prior: str | None = None
            left_is_current = True
            page_rows: list[dict[str, Any]] = []

            for line in (text or "").splitlines():
                raw = line.strip()
                if not raw:
                    continue
                low = raw.lower()

                # Year headers establish which amount column is current vs prior.
                yh = _YEAR_HEADER.match(raw)
                if yh:
                    y1, y2 = yh.group("y1"), yh.group("y2")
                    left_is_current = int(y1) >= int(y2)
                    year_current = y1 if left_is_current else y2
                    year_prior = y2 if left_is_current else y1
                    continue

                if low in {"assets:", "assets"} or low.startswith("assets:"):
                    section = "assets"
                    continue
                if low.startswith("liabilities and") or low in {"liabilities:", "liabilities"}:
                    section = "liabilities"
                    continue
                if low.startswith("members'") or low.startswith("members’") or low.startswith("partners'"):
                    section = "equity"
                    continue

                if _MONTH_LINE.match(low):
                    continue
                if low.startswith(("unaudited", "audited", "see notes", "page ")):
                    continue
                if re.fullmatch(r"[a-z0-9]", low):
                    continue
                if re.fullmatch(r"20\d{2}", low):
                    continue

                m = _split_statement_amounts(raw)
                if not m:
                    continue
                label_raw, amount_tokens = m
                label_normalized = _normalize_statement_label(label_raw)
                if not label_normalized or len(label_normalized) <= 1:
                    continue

                if len(amount_tokens) == 1:
                    amount_left, status_left = _parse_amount_token(amount_tokens[0])
                    amount_right, status_right = (None, "blank")
                else:
                    amount_left, status_left = _parse_amount_token(amount_tokens[0])
                    amount_right, status_right = _parse_amount_token(amount_tokens[1])

                if left_is_current or amount_right is None and status_right == "blank":
                    current_amount, current_status = amount_left, status_left
                    prior_amount, prior_status = amount_right, status_right
                else:
                    current_amount, current_status = amount_right, status_right
                    prior_amount, prior_status = amount_left, status_left

                # Guard: label that is only a dash/year noise.
                if label_normalized.lower() in {"-", "–", "—"}:
                    continue

                # "Dividends receivable - 7,400,000" => current=0, prior=7400000
                amount_normalized = current_amount
                parse_status = current_status
                if current_status == "parse_error":
                    parse_status = "parse_error"
                    amount_normalized = None

                classified = classify_statement_line(
                    label_normalized=label_normalized,
                    section=section,
                )
                page_rows.append(
                    {
                        "label_raw": label_raw,
                        "label_normalized": label_normalized,
                        "section": section,
                        "current_amount": current_amount,
                        "prior_amount": prior_amount,
                        "amount_normalized": amount_normalized,  # backward compatible = current
                        "current_parse_status": current_status,
                        "prior_parse_status": prior_status,
                        "amount_parse_status": parse_status,
                        "classification": classified["classification"],
                        "classification_source": classified["classification_source"],
                        "confidence": classified["confidence"],
                        "reason": classified["reason"],
                        "year_current": year_current,
                        "year_prior": year_prior,
                        "left_is_current": left_is_current,
                        "source_page": int(page.page_number),
                        "raw_line": raw,
                    }
                )

            if page_rows:
                rows = page_rows
                break
    return rows


def reconcile_statement_categories(soa_lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Reconcile classified SOA amounts against printed totals."""
    buckets: dict[str, int] = {
        "cash": 0,
        "investment": 0,
        "other_asset": 0,
        "subscription_line": 0,
        "other_liability": 0,
    }
    reported = {
        "total_assets": None,
        "total_liabilities": None,
    }
    unknowns: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []

    for row in soa_lines or []:
        cls = row.get("classification")
        status = row.get("amount_parse_status")
        if status == "parse_error":
            parse_errors.append(row)
            continue
        amt = row.get("current_amount")
        if amt is None:
            continue
        try:
            value = int(amt)
        except (TypeError, ValueError):
            parse_errors.append(row)
            continue
        if cls in buckets:
            buckets[cls] += value
        elif cls == "total_assets":
            reported["total_assets"] = value
        elif cls == "total_liabilities":
            reported["total_liabilities"] = value
        elif cls == "unknown":
            unknowns.append(row)

    assets_calc = buckets["cash"] + buckets["investment"] + buckets["other_asset"]
    liab_calc = buckets["subscription_line"] + buckets["other_liability"]
    checks = []

    def _check(check_id: str, left: int | None, right: int | None) -> dict[str, Any]:
        if left is None or right is None:
            return {
                "check_id": check_id,
                "status": "REVIEW_REQUIRED",
                "left": None if left is None else str(left),
                "right": None if right is None else str(right),
                "difference": None,
                "reason": "missing_printed_total_or_components",
            }
        diff = left - right
        return {
            "check_id": check_id,
            "status": "PASS" if diff == 0 else "FAIL",
            "left": str(left),
            "right": str(right),
            "difference": str(diff),
            "reason": "ok" if diff == 0 else "identity_mismatch",
        }

    checks.append(_check("cash_plus_investments_plus_other_assets_equals_total_assets", assets_calc, reported["total_assets"]))
    checks.append(
        _check(
            "subscription_plus_other_liabilities_equals_total_liabilities",
            liab_calc,
            reported["total_liabilities"],
        )
    )
    # FAIL or missing printed totals / unknowns / parse errors all block auto full-match.
    hard_fail = (
        any(c["status"] in {"FAIL", "REVIEW_REQUIRED"} for c in checks)
        or bool(unknowns)
        or bool(parse_errors)
    )
    return {
        "status": "PASS" if not hard_fail else ("REVIEW_REQUIRED" if not any(c["status"] == "FAIL" for c in checks) and not unknowns and not parse_errors else "FAIL"),
        "buckets": {k: str(v) for k, v in buckets.items()},
        "reported_totals": {
            "total_assets": None if reported["total_assets"] is None else str(reported["total_assets"]),
            "total_liabilities": None if reported["total_liabilities"] is None else str(reported["total_liabilities"]),
        },
        "checks": checks,
        "unknown_lines": [
            {
                "label_normalized": u.get("label_normalized"),
                "section": u.get("section"),
                "current_amount": u.get("current_amount"),
                "raw_line": u.get("raw_line"),
                "source_page": u.get("source_page"),
            }
            for u in unknowns
        ],
        "parse_errors": [
            {
                "label_normalized": e.get("label_normalized"),
                "raw_line": e.get("raw_line"),
                "source_page": e.get("source_page"),
            }
            for e in parse_errors
        ],
        "blocks_amount_comparison": hard_fail,
    }


def apply_non_investment_rollups(
    soa_lines: list[dict[str, Any]],
    rollups: list[dict[str, Any]] | None = None,
    *,
    category_map: dict[str, dict[str, Any]] | None = None,
    aliases: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Map classified SOA lines into vendor Non-Investment holdings.

    Prefer category-based rollups. Legacy match_labels configs remain supported but
    are treated as optional aliases rather than required line lists.
    """
    from pdf_validation.canonical import company_summary_from_ocr_portfolio

    # Re-classify with optional aliases so mapping can refine residual categories.
    lines: list[dict[str, Any]] = []
    for row in soa_lines or []:
        item = dict(row)
        classified = classify_statement_line(
            label_normalized=str(item.get("label_normalized") or ""),
            section=item.get("section"),
            aliases=aliases,
        )
        # Preserve strong structural classes already set by parser.
        if item.get("classification") in {"total_assets", "total_liabilities", "investment", "cash"}:
            pass
        else:
            item.update(
                {
                    "classification": classified["classification"],
                    "classification_source": classified["classification_source"],
                    "confidence": classified["confidence"],
                    "reason": classified["reason"],
                }
            )
        # Backward compatible amount field.
        if item.get("current_amount") is not None and item.get("amount_normalized") is None:
            item["amount_normalized"] = item["current_amount"]
        lines.append(item)

    # Build alias map from legacy match_labels if provided.
    legacy_aliases: dict[str, list[str]] = dict(aliases or {})
    for rule in rollups or []:
        vendor = rule.get("vendor_source_asset")
        labels = [str(x).lower() for x in (rule.get("match_labels") or [])]
        if not vendor or not labels:
            continue
        # Infer category from vendor naming.
        vlow = str(vendor).lower()
        if "cash" in vlow:
            legacy_aliases.setdefault("cash", []).extend(labels)
        elif "subscription" in vlow or "credit" in vlow:
            legacy_aliases.setdefault("subscription_line", []).extend(labels)
        elif "other assets" in vlow:
            legacy_aliases.setdefault("other_asset", []).extend(labels)
        elif "other liabilities" in vlow:
            legacy_aliases.setdefault("other_liability", []).extend(labels)

    if legacy_aliases:
        refined: list[dict[str, Any]] = []
        for item in lines:
            classified = classify_statement_line(
                label_normalized=str(item.get("label_normalized") or ""),
                section=item.get("section"),
                aliases=legacy_aliases,
            )
            if item.get("classification") in {"total_assets", "total_liabilities", "investment"}:
                refined.append(item)
                continue
            # Don't override cash/subscription if already high-confidence rule.
            if item.get("classification") in {"cash", "subscription_line"} and item.get("classification_source") == "rule":
                refined.append(item)
                continue
            upd = dict(item)
            upd.update(
                {
                    "classification": classified["classification"],
                    "classification_source": classified["classification_source"],
                    "confidence": classified["confidence"],
                    "reason": classified["reason"],
                }
            )
            refined.append(upd)
        lines = refined

    cat_map = dict(DEFAULT_VENDOR_CATEGORY_MAP)
    if category_map:
        cat_map.update(category_map)

    # Optional override from rollups with explicit category field.
    for rule in rollups or []:
        cat = rule.get("category")
        vendor = rule.get("vendor_source_asset")
        if cat and vendor:
            cat_map[cat] = {
                "vendor_source_asset": vendor,
                "sign": int(rule.get("sign") or cat_map.get(cat, {}).get("sign") or 1),
            }

    buckets: dict[str, dict[str, Any]] = {
        cat: {"total": 0, "matched": [], "missing": False} for cat in cat_map
    }
    unknowns: list[dict[str, Any]] = []

    for row in lines:
        cls = row.get("classification")
        if cls == "unknown":
            unknowns.append(row)
            continue
        if cls not in buckets:
            continue
        if row.get("amount_parse_status") == "parse_error" or row.get("current_amount") is None:
            buckets[cls]["missing"] = True
            continue
        buckets[cls]["total"] += int(row["current_amount"])
        buckets[cls]["matched"].append(row.get("label_normalized"))

    reconciliation = reconcile_statement_categories(lines)
    companies: list[dict[str, Any]] = []
    missing_entities: list[dict[str, Any]] = []
    soa_parsed = bool(lines)

    for cat, cfg in cat_map.items():
        vendor_name = cfg["vendor_source_asset"]
        sign = int(cfg.get("sign") or 1)
        bucket = buckets[cat]
        # Fail-closed: parse errors on category lines => pdf_missing (never invent 0).
        if bucket["missing"]:
            missing_entities.append(
                {
                    "vendor_source_asset": vendor_name,
                    "category": cat,
                    "status": "pdf_missing",
                    "matched_labels": bucket["matched"],
                    "reason": "category_line_parse_error",
                }
            )
            continue
        # Empty category after a successful SOA parse is a real zero rollup.
        # Completely missing SOA page/lines is pdf_missing.
        if not bucket["matched"] and not soa_parsed:
            missing_entities.append(
                {
                    "vendor_source_asset": vendor_name,
                    "category": cat,
                    "status": "pdf_missing",
                    "matched_labels": [],
                    "reason": "soa_not_parsed",
                }
            )
            continue
        value = str(sign * int(bucket["total"]))
        company = company_summary_from_ocr_portfolio(
            {
                "company_name": vendor_name,
                "cost_normalized": None,
                "fair_value_normalized": value,
                "source_page": (lines[0].get("source_page") if lines else None),
                "label_raw": "; ".join(bucket["matched"]) if bucket["matched"] else f"{cat}:empty",
                "engine": "soa_category_rollup",
            }
        )
        company["entity_grain"] = "statement_line"
        company["subtotal_event"] = "non_investment_soa_rollup"
        company["vendor_source_asset"] = vendor_name
        company["classification"] = cat
        companies.append(company)

    if unknowns:
        reconciliation["status"] = "FAIL"
        reconciliation["blocks_amount_comparison"] = True

    return {
        "companies": companies,
        "missing_entities": missing_entities,
        "unknown_lines": [
            {
                "label_normalized": u.get("label_normalized"),
                "section": u.get("section"),
                "current_amount": u.get("current_amount"),
                "raw_line": u.get("raw_line"),
                "source_page": u.get("source_page"),
            }
            for u in unknowns
        ],
        "reconciliation": reconciliation,
        "lines": lines,
    }


def classify_statement_line_for_llm(
    *,
    label_normalized: str,
    section: str | None,
    allowed_classes: tuple[str, ...] = (
        "cash",
        "investment",
        "other_asset",
        "subscription_line",
        "other_liability",
        "equity",
        "ignore",
        "unknown",
    ),
) -> dict[str, Any]:
    """LLM-ready classification boundary. Not connected in this change.

    Future LLM may only choose from allowed_classes for low-confidence/unknown
    labels. It must not receive CSV target amounts and must not alter amounts,
    signs, period columns, or reconciliation outcomes. Strong rules always win.
    """
    rule = classify_statement_line(label_normalized=label_normalized, section=section)
    return {
        "input": {
            "label_normalized": label_normalized,
            "section": section,
            "allowed_classes": list(allowed_classes),
        },
        "classification": rule["classification"],
        "confidence": rule["confidence"],
        "reason": rule["reason"],
        "classification_source": rule["classification_source"],
        "model_version": None,
        "llm_eligible": rule["classification"] == "unknown" or float(rule["confidence"]) < 0.7,
    }
