"""
Load upstream validation outputs for exception diagnosis.

This module only collects existing outputs.
It does not perform validation logic.
"""

import json
from pathlib import Path
from typing import Any
import pandas as pd

from .config import (
    PHASE1_MISSING_FIELDS_REPORT,
    PHASE1_DEAL_STATUS_REPORT,
    PDF_SURVEY_REPORT,
    PDF_VALIDATION_SEARCH_ROOTS,
    PDF_BATCH_SUMMARY_REPORT,
)


from .adapters.pdf_validation_adapter import (
    load_pdf_validation_issues,
    build_batch_blocked_issues,
)


from .adapters.entity_resolution_adapter import (
    load_entity_aliases,
    load_entity_review_records,
)


# Generic loaders

def load_csv(path: Path) -> pd.DataFrame:
    """
    Load CSV file.

    Return empty dataframe if file does not exist.
    """

    if not path.exists():
        return pd.DataFrame()

    return pd.read_csv(path)


# Phase 1 outputs

def load_phase1_missing_fields() -> pd.DataFrame:
    """
    Load Phase 1 conditional missing field results.
    """

    return load_csv(
        PHASE1_MISSING_FIELDS_REPORT
    )


def load_phase1_deal_status() -> pd.DataFrame:
    """
    Load Phase 1 deal status mismatch results.
    """

    return load_csv(
        PHASE1_DEAL_STATUS_REPORT
    )


# PDF validation outputs

def load_pdf_survey() -> pd.DataFrame:
    """
    Load PDF survey information.

    Includes:
    - document availability
    - extraction status
    - comparability status
    """

    return load_csv(
        PDF_SURVEY_REPORT
    )


def load_pdf_batch_summary() -> list:
    """
    Load PDF batch validation summary.
    """

    if not PDF_BATCH_SUMMARY_REPORT.exists():
        return []

    with PDF_BATCH_SUMMARY_REPORT.open(
        encoding="utf-8"
    ) as f:

        summary = json.load(f)

    return summary.get(
        "results",
        []
    )


def find_pdf_validation_outputs():
    """
    Find PDF validation comparison folders.
    """

    vendor_dirs = []

    for root in PDF_VALIDATION_SEARCH_ROOTS:

        if not root.exists():
            continue

        for name in [
            "vendor_comparison",
            "compare",
        ]:

            vendor_dirs.extend(
                [
                    path
                    for path in root.rglob(name)
                    if path.is_dir()
                    and (
                        path /
                        "comparison_report.json"
                    ).exists()
                ]
            )

    return vendor_dirs


def load_pdf_metadata(
    vendor_dir: Path
):
    """
    Extract fund metadata from PDF comparison output.
    """

    report_path = (
        vendor_dir /
        "comparison_report.json"
    )

    if not report_path.exists():
        return None, None

    with report_path.open(
        encoding="utf-8"
    ) as f:

        report = json.load(f)

    summary = report.get(
        "summary",
        {}
    )

    return (
        summary.get("vendor_fund_id"),
        summary.get("pdf_as_of_date"),
    )


def load_pdf_validation_outputs():

    issues = []

    issues.extend(
        build_batch_blocked_issues(
            load_pdf_batch_summary()
        )
    )

    for vendor_dir in find_pdf_validation_outputs():

        fund_id, as_at_date = (
            load_pdf_metadata(
                vendor_dir
            )
        )

        issues.extend(
            load_pdf_validation_issues(
                vendor_dir,
                fund_id=fund_id,
                as_at_date=as_at_date,
            )
        )

    return issues


# Entity resolution outputs

def load_entity_resolution_outputs():

    return {
        "aliases":
            load_entity_aliases(),

        "review_records":
            load_entity_review_records(),
    }


# Main loader

def load_all_validation_outputs() -> dict[str, Any]:
    """
    Load all upstream outputs.

    Returns:
        Phase 1 findings
        PDF validation findings
        Entity resolution evidence
    """

    return {

        "phase1_missing_fields":
            load_phase1_missing_fields(),

        "phase1_deal_status":
            load_phase1_deal_status(),

        "pdf_survey":
            load_pdf_survey(),

        "pdf_validation":
            load_pdf_validation_outputs(),

        "entity_resolution":
            load_entity_resolution_outputs(),
            
    }