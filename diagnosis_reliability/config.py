"""
Configuration for Office-facing exception diagnosis and reporting.

This module defines:
- upstream output locations
- Office-facing exception labels
- recommended review actions

Diagnosis does not recalculate Phase 1 metrics or redefine
upstream validation rules.
"""

from pathlib import Path

# Repository paths

REPO_ROOT = Path(__file__).resolve().parents[1]

REPORTS_DIR = REPO_ROOT / "reports"

DIAGNOSIS_OUTPUT_DIR = (
    REPO_ROOT
    / "diagnosis_reliability"
    / "outputs"
)


# Phase 1 / data-state analysis outputs
# Phase 1 is the source of truth for structured-data quality findings.

PHASE1_MISSING_FIELDS_REPORT = (
    REPORTS_DIR
    / "flagged_missing_fields_conditional.csv"
)

PHASE1_DEAL_STATUS_REPORT = (
    REPORTS_DIR
    / "deal_status_conformance_exceptions.csv"
)

PHASE1_CONSISTENCY_REPORT = (
    REPORTS_DIR
    / "consistency_rule_results.csv"
)

PHASE1_FUND_QUALITY_REPORT = (
    REPORTS_DIR
    / "data_state_by_fund_conditional.csv"
)

PHASE1_MANAGER_QUALITY_REPORT = (
    REPORTS_DIR
    / "data_state_by_manager_conditional.csv"
)

PHASE1_FIELD_QUALITY_REPORT = (
    REPORTS_DIR
    / "data_state_by_field.csv"
)

PHASE1_REPORTING_GAPS_REPORT = (
    REPORTS_DIR
    / "reporting_gaps_by_series.csv"
)


# PDF validation outputs

PDF_VALIDATION_OUTPUT_DIR = (
    REPO_ROOT
    / "pdf_validation"
    / "outputs"
)

PDF_SURVEY_REPORT = (
    PDF_VALIDATION_OUTPUT_DIR
    / "original_pdf_survey"
    / "reports"
    / "survey_by_pdf.csv"
)

# Temporary/test outputs can still be read as a fallback while
# the PDF validation workflow is being finalized.
PDF_VALIDATION_SEARCH_ROOTS = [
    PDF_VALIDATION_OUTPUT_DIR,
    REPO_ROOT / "test_output",
]

PDF_BATCH_SUMMARY_REPORT = (
    REPO_ROOT
    / "test_output"
    / "batch_summary.json"
)


# Entity resolution outputs

ENTITY_RESOLUTION_DIR = (
    REPO_ROOT
    / "entity_resolution"
)

ENTITY_ALIAS_FILE = (
    ENTITY_RESOLUTION_DIR
    / "entity_aliases.json"
)

ENTITY_REVIEW_FILE = (
    ENTITY_RESOLUTION_DIR
    / "entity_review.jsonl"
)


# Office-facing exception types

EXCEPTION_TYPES = {
    "missing_field": "Missing Field",
    "deal_status": "Deal Status Mismatch",
    "pdf_value": "PDF / Vendor Value Mismatch",
    "pdf_blocked": "PDF Comparison Blocked",
    "mapping": "Entity Mapping Review",
}


# Office-facing recommended actions

ACTIONS = {
    "verify_source":
        "Verify Source — Review PDF/source report",

    "verify_status":
        "Verify Status — Review PDF and financial values",

    "review_value":
        "Review Value — Compare PDF and structured data",

    "resolve_extraction":
        "Resolve Extraction — Review PDF extraction before comparison",

    "confirm_mapping":
        "Confirm Mapping — Validate entity identity",
}