"""Shared schema constants and record builders."""

from __future__ import annotations

from typing import Any

PARSE_STATUSES = (
    "ok",
    "blank",
    "dash",
    "zero",
    "not_disclosed",
    "not_applicable",
    "parse_error",
)

ROW_TYPES = (
    "repeated_header",
    "investment_lot",
    "company_subtotal",
    "grand_total",
    "page_continuation",
    "blank_or_noise",
    "sector_subtotal",
    "country_subtotal",
    "asset_class_header",
    "other_bucket",
    "fund_aggregate_line",
)

ENTITY_GRAINS = (
    "lot",
    "security",
    "company",
    "sector_rollup",
    "country_rollup",
    "other_bucket",
    "fund",
)

EXTRACTION_MODES = (
    "position_level",
    "fund_aggregate_only",
    "blocked_narrative",
    "manual_review",
)

DEAL_STATUS_FIELDS = (
    "deal_status_reported",
    "deal_status_inferred",
    "inference_rule",
    "inference_evidence",
    "inference_confidence",
)

NUMERIC_FIELD_SUFFIXES = (
    "_raw",
    "_normalized",
    "_parse_status",
    "_source_page",
    "_source_bbox",
)

EXCEL_SHEETS = (
    "README",
    "run_manifest",
    "metadata",
    "parser_decisions",
    "raw_cells",
    "investment_lots",
    "company_summary",
    "realized_lots",
    "reconciliation",
    "validation_issues",
)


def empty_deal_status() -> dict[str, Any]:
    return {
        "deal_status_reported": None,
        "deal_status_inferred": None,
        "inference_rule": None,
        "inference_evidence": None,
        "inference_confidence": None,
    }


def numeric_field_bundle(
    *,
    field: str,
    raw: Any,
    normalized: Any,
    parse_status: str,
    source_page: int | None,
    source_bbox: list[float] | None,
) -> dict[str, Any]:
    if parse_status not in PARSE_STATUSES:
        raise ValueError(f"Invalid parse_status: {parse_status}")
    return {
        f"{field}_raw": raw,
        f"{field}_normalized": normalized,
        f"{field}_parse_status": parse_status,
        f"{field}_source_page": source_page,
        f"{field}_source_bbox": source_bbox,
    }
