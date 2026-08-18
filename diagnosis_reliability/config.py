"""
Configuration for Office-facing Exception Review Queue.
"""

from pathlib import Path


# Paths
REPO_ROOT = Path(__file__).resolve().parents[1]

REPORTS_DIR = REPO_ROOT / "reports"

OUTPUT_DIR = (
    REPO_ROOT
    / "diagnosis_reliability"
    / "outputs"
)


# Existing validation outputs
MISSING_FIELD_REPORT = (
    REPORTS_DIR
    / "flagged_missing_fields_conditional.csv"
)

DEAL_STATUS_REPORT = (
    REPORTS_DIR
    / "deal_status_conformance_exceptions.csv"
)


# PDF validation outputs
PDF_VALIDATION_SEARCH_ROOTS = [
    REPO_ROOT / "test_output",
    REPO_ROOT / "pdf_validation" / "outputs",
]

PDF_SURVEY_REPORT = (
    REPO_ROOT
    / "pdf_validation"
    / "outputs"
    / "original_pdf_survey"
    / "reports"
    / "survey_by_pdf.csv"
)


# Exception Types
EXCEPTION_TYPES = {
    "missing_field":
        "Missing Field",

    "deal_status":
        "Deal Status Inconsistency",

    "pdf_vendor":
        "PDF / Vendor Value Difference",

    "pdf_blocked":
        "PDF Comparison Blocked",

    "mapping":
        "Entity Mapping Review",
}


# Business Rules
DEAL_STATUS_RULES = {
    "written_off":
    {
        "description":
            "Written Off is valid only when Unrealized Value >= 0.",

        "condition":
            {
                "field": "Unrealized Value",
                "operator": ">=",
                "value": 0,
            }
    }
}


# Recommended Actions
ACTIONS = {
    "verify_source":
        "Verify Source — Review PDF/source report",

    "verify_status":
        "Verify Status — Review PDF and confirm status classification",

    "review_value":
        "Review Value — Compare PDF and vendor value",

    "resolve_extraction":
        "Resolve Extraction — Review PDF extraction before comparison",

    "confirm_mapping":
        "Confirm Mapping — Validate entity identity",

}

BATCH_SUMMARY_REPORT = (
    REPO_ROOT
    / "test_output"
    / "batch_summary.json"
)