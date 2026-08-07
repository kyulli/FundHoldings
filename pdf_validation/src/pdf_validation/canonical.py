"""Canonical extraction helpers shared by native and OCR adapters."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pdf_validation.schemas import empty_deal_status


def empty_canonical_lists() -> dict[str, Any]:
    return {
        "parser_decisions": [],
        "raw_cells": [],
        "investment_lots": [],
        "company_summary": [],
        "statement_entities": [],
        "realized_lots": [],
        "reconciliation": [],
        "validation_issues": [],
        "classified_rows": [],
        "camelot_raw": {},
        "pdfplumber_raw": {},
        "company_events": [],
        "realized_totals": [],
        "fund_aggregate": None,
    }


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def statement_entity_from_balance_line(line: dict[str, Any]) -> dict[str, Any] | None:
    """Map an OCR/template balance-sheet line to a statement_entities row."""
    vendor_name = line.get("vendor_source_asset")
    field_map = line.get("vendor_field_map") or {}
    if not vendor_name or not field_map:
        return None
    metrics: dict[str, Any] = {}
    for vendor_field, pdf_key in field_map.items():
        metrics[vendor_field] = line.get(pdf_key)
    return {
        "entity_grain": "statement_line",
        "line_id": line.get("line_id"),
        "label_normalized": line.get("label_normalized") or line.get("label_raw"),
        "label_raw": line.get("label_raw"),
        "section": line.get("section"),
        "vendor_source_asset": vendor_name,
        "vendor_field_map": field_map,
        "metrics": metrics,
        "amount_raw": line.get("amount_raw"),
        "amount_normalized": line.get("amount_normalized"),
        "source_page": line.get("source_page"),
        "source_bbox": line.get("source_bbox"),
        "ocr_confidence": line.get("ocr_confidence"),
        "engine": line.get("engine"),
        "derived": bool(line.get("derived")),
        "sign_rule": "force_negative" if line.get("amount_normalized", "").startswith("-") and "liabilit" in str(line.get("section") or "").lower() else None,
        "notes": line.get("notes"),
        "cost_from_label_normalized": line.get("cost_from_label_normalized"),
    }


def company_summary_from_ocr_portfolio(company: dict[str, Any]) -> dict[str, Any]:
    """Normalize OCR portfolio company into native-compatible company_summary shape."""
    cost = company.get("cost_normalized")
    fv = company.get("fair_value_normalized")
    if fv is None:
        fv = company.get("unrealized_value_normalized")
    ugl = company.get("unrealized_gain_loss_normalized")
    if ugl is None and cost is not None and fv is not None:
        c = _to_decimal(cost)
        f = _to_decimal(fv)
        if c is not None and f is not None:
            ugl = format(f - c, "f")

    row = {
        "company_name": company.get("company_name"),
        "lot_count": 0,
        "pages": [company.get("source_page")] if company.get("source_page") is not None else [],
        "cost_reported_raw": company.get("label_raw") or company.get("instrument_raw"),
        "cost_reported_normalized": cost,
        "cost_calculated": cost,  # reported-only; do not invent lots
        "cost_difference": "0" if cost is not None else None,
        "cost_status": "PASS" if cost is not None else "not_applicable",
        "fair_value_reported_raw": company.get("label_raw") or company.get("instrument_raw"),
        "fair_value_reported_normalized": fv,
        "fair_value_calculated": fv,
        "fair_value_difference": "0" if fv is not None else None,
        "fair_value_status": "PASS" if fv is not None else "not_applicable",
        "unrealized_gain_loss_reported_raw": None,
        "unrealized_gain_loss_reported_normalized": ugl,
        "unrealized_gain_loss_calculated": ugl,
        "unrealized_gain_loss_difference": "0" if ugl is not None else None,
        "unrealized_gain_loss_status": "PASS" if ugl is not None else "not_applicable",
        "entity_grain": "company",
        "subtotal_event": "portfolio_investments_ocr",
        "subtotal_page": company.get("source_page"),
        "instrument_raw": company.get("instrument_raw"),
        "ocr_confidence": company.get("ocr_confidence"),
        "engine": company.get("engine"),
        "source_page": company.get("source_page"),
        **empty_deal_status(),
    }
    row["deal_status_inferred"] = None
    row["inference_rule"] = "not_applicable"
    row["inference_evidence"] = "OCR portfolio schedule without realized lots; Deal Status not inferred."
    row["inference_confidence"] = None
    return row


def reconciliation_from_ocr_checks(checks: list[dict[str, Any]], *, schedule: str = "balance_sheet") -> list[dict[str, Any]]:
    """Map OCR internal_checks into native-like reconciliation rows."""
    rows: list[dict[str, Any]] = []
    for check in checks or []:
        rows.append(
            {
                "check_id": check.get("check_id"),
                "schedule": schedule,
                "entity": "fund",
                "reported": check.get("right"),
                "calculated": check.get("left"),
                "difference": check.get("difference"),
                "tolerance": check.get("tolerance") or "0",
                "tolerance_reason": "ocr_internal_identity",
                "status": check.get("status"),
                "reason": check.get("reason"),
                "severity": "high" if check.get("check_id", "").startswith(("cash_", "total_", "fees_", "gp_")) else "info",
            }
        )
    return rows


def ocr_extraction_to_canonical(
    *,
    extraction: dict[str, Any],
    route: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert OCR extractor output into the shared canonical payload shape."""
    config = config or {}
    route = route or {
        "document_class": "financial_statements_with_schedule",
        "template_family": config.get("template_family"),
        "extraction_mode": "scanned_financial_statements",
        "comparison_grain": config.get("comparison_grain") or "statement_and_portfolio",
        "recommended_adapter": "ocr",
        "text_source": (extraction.get("scan_detection") or {}).get("source"),
        "requires_ocr": True,
        "compare_allowed": True,
    }

    statement_entities: list[dict[str, Any]] = []
    for line in (extraction.get("balance_sheet") or {}).get("lines") or []:
        entity = statement_entity_from_balance_line(line)
        if entity:
            statement_entities.append(entity)

    company_summary = [
        company_summary_from_ocr_portfolio(c) for c in (extraction.get("portfolio") or {}).get("companies") or []
    ]

    hard_ids = {
        "cash_equals_total_assets",
        "cash_plus_portfolio_equals_total_assets",
        "total_assets_equals_total_l_and_c",
        "fees_plus_capital_equals_tlc",
        "gp_plus_lp_equals_total_capital",
    }
    all_checks = extraction.get("internal_checks") or []
    reconciliation = reconciliation_from_ocr_checks(all_checks)
    hard_fails = [c for c in reconciliation if c.get("check_id") in hard_ids and c.get("status") != "PASS"]

    validation_issues: list[dict[str, Any]] = []
    for fail in hard_fails:
        validation_issues.append(
            {
                "issue_type": "reconciliation_fail",
                "check_id": fail.get("check_id"),
                "schedule": fail.get("schedule"),
                "entity": fail.get("entity"),
                "detail": fail,
            }
        )

    eq = dict(extraction.get("extraction_quality") or {})
    if hard_fails and eq.get("status") == "PASS":
        eq["status"] = "REVIEW_REQUIRED"
        eq["reason"] = (eq.get("reason") or "") + "; hard_internal_check_failed"

    parser_decisions = [
        {
            "schedule": "balance_sheet",
            "selected_parser": extraction.get("selected_parser") or "tesseract",
            "status": "PASS" if not hard_fails else "FAIL",
            "reason": eq.get("reason") or "OCR scanned financial statements",
            "extraction_quality": eq,
        }
    ]

    base = empty_canonical_lists()
    base.update(
        {
            "metadata": extraction.get("metadata") or {"fields": []},
            "parser_decisions": parser_decisions,
            "company_summary": company_summary,
            "statement_entities": statement_entities,
            "reconciliation": reconciliation,
            "validation_issues": validation_issues,
            "route": route,
            "extraction_quality": eq,
            "selected_parser": extraction.get("selected_parser") or "tesseract",
            "ocr_artifact": {
                "balance_sheet": extraction.get("balance_sheet"),
                "operations": extraction.get("operations"),
                "cash_flows": extraction.get("cash_flows"),
                "portfolio": extraction.get("portfolio"),
                "schedule_pages": extraction.get("schedule_pages"),
                "pages_ocr": extraction.get("pages_ocr"),
                "scan_detection": extraction.get("scan_detection"),
                "internal_checks": all_checks,
            },
        }
    )
    return base
