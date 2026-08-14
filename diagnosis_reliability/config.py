"""
Configuration for the Data Quality Diagnosis & Reliability Framework.

This module contains shared paths, issue taxonomies, and configurable
scoring parameters. It intentionally contains no diagnosis logic.
"""

from pathlib import Path


# Project paths

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = REPO_ROOT / "reports"
CLEANING_PIPELINE_DIR = REPO_ROOT / "cleaning_pipeline"
ENTITY_RESOLUTION_DIR = REPO_ROOT / "entity_resolution"
ENTITY_RESOLUTION_OUTPUT_DIR = ENTITY_RESOLUTION_DIR / "output"

ENTITY_ALIAS_FILE = (
    ENTITY_RESOLUTION_OUTPUT_DIR / "proposed_aliases.json"
)

ENTITY_REVIEW_FILE = (
    ENTITY_RESOLUTION_OUTPUT_DIR / "review_records.jsonl"
)


# Existing report inputs produced by the team's pipeline

REPORT_FILES = {
    "missing_fields": REPORTS_DIR / "flagged_missing_fields_conditional.csv",
    "deal_status": REPORTS_DIR / "deal_status_conformance_exceptions.csv",
    "consistency_rules": REPORTS_DIR / "consistency_rule_results.csv",
    "fund_quality": REPORTS_DIR / "data_state_by_fund_conditional.csv",
    "manager_quality": REPORTS_DIR / "data_state_by_manager_conditional.csv",
}


# Standardized issue types

ISSUE_TYPES = {
    "missing_field": "MISSING_FIELD",
    "deal_status_mismatch": "DEAL_STATUS_MISMATCH",
    "consistency_violation": "CONSISTENCY_RULE_VIOLATION",
    "pdf_vendor_mismatch": "PDF_VENDOR_MISMATCH",
}


# Root-cause taxonomy
#
# Important:
# These labels describe the most likely explanation supported by the
# available evidence. They should not automatically be interpreted as
# confirmed vendor errors.

ROOT_CAUSES = {
    "source_reporting_gap": "SOURCE_REPORTING_GAP",
    "status_logic": "STATUS_LOGIC_INCONSISTENCY",
    "temporal": "TEMPORAL_CONSISTENCY_VIOLATION",
    "accounting": "ACCOUNTING_IDENTITY_VIOLATION",
    "invalid_value": "INVALID_FIELD_VALUE",
    "extraction_uncertainty": "EXTRACTION_UNCERTAINTY",
    "entity_mapping": "ENTITY_MAPPING_UNCERTAINTY",
    "unknown": "UNKNOWN_REQUIRES_REVIEW",
}


# Severity configuration
#
# These are INITIAL heuristic weights. They are deliberately centralized
# here so they can later be calibrated with Investment Office feedback.

FIELD_CRITICALITY = {
    "Deal Status": 3,
    "Current Cost": 3,
    "Unrealized Value": 3,
    "Realized Proceeds": 3,
    "Capital Invested": 3,
    "Ownership": 2,
    "Sector": 1,
}

DEFAULT_FIELD_CRITICALITY = 1


ISSUE_TYPE_WEIGHT = {
    "MISSING_FIELD": 1,
    "DEAL_STATUS_MISMATCH": 2,
    "CONSISTENCY_RULE_VIOLATION": 2,
    "PDF_VENDOR_MISMATCH": 3,
}


SEVERITY_THRESHOLDS = {
    "HIGH": 5,
    "MEDIUM": 3,
}


# Confidence levels
#
# Confidence describes confidence in the DIAGNOSIS, not confidence that
# the underlying vendor value is wrong.

CONFIDENCE_LEVELS = {
    "high": "HIGH",
    "medium": "MEDIUM",
    "low": "LOW",
}

DECISION_STATES = {
    "verify_source": "VERIFY_SOURCE",
    "verify_status": "VERIFY_STATUS_AGAINST_SOURCE",
    "review_correction": "REVIEW_PROPOSED_CORRECTION",
    "confirm_mapping": "CONFIRM_ENTITY_MAPPING",
    "resolve_extraction": "RESOLVE_EXTRACTION_UNCERTAINTY",
    "document_source_gap": "DOCUMENT_SOURCE_GAP",
    "escalate_vendor": "ESCALATE_VENDOR_MISMATCH",
    "manual_review": "MANUAL_REVIEW",
}

# Output

OUTPUT_DIR = REPO_ROOT / "diagnosis_reliability" / "outputs"
DEFAULT_DIAGNOSIS_OUTPUT = (
    OUTPUT_DIR / "diagnosis_reliability_report.xlsx"
)
