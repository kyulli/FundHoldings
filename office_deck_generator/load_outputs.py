"""
Locate existing project outputs for the Office reporting deck.

This module only discovers outputs produced by upstream workflows.
It does not recompute, modify, or fabricate analytical results.
"""

from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from office_deck_generator.config import (
    CLEANING_RUNS_DIR,
    CURRENT_OPS_REPORT,
    DIAGNOSIS_OUTPUT,
    DATA_STATE_IMAGES,
    DIAGNOSIS_EXCEPTION_SUMMARY_SHEET,
    DIAGNOSIS_ACTION_SUMMARY_SHEET,
    DIAGNOSIS_IMPACT_SUMMARY_SHEET,
    DIAGNOSIS_DETAIL_SHEET,
    REPO_ROOT,
    REPORTS_DIR,
)

from .phase1_summary import (
    get_phase1_summary,
)


def get_data_state_images() -> list[Path]:
    """
    Return existing Data-State Analysis image exports.
    """

    return [
        path
        for path in DATA_STATE_IMAGES
        if path.exists()
    ]


def get_current_ops_report() -> Path | None:
    """
    Return the explicitly configured reporting-period Ops workbook.
    """

    if CURRENT_OPS_REPORT.exists():
        return CURRENT_OPS_REPORT

    return None


def get_diagnosis_report() -> Path | None:
    """
    Return the diagnosis workbook if it exists.
    """

    if DIAGNOSIS_OUTPUT.exists():
        return DIAGNOSIS_OUTPUT

    return None


# PDF Validation

def get_pdf_validation_summary():
    """
    Build Office-facing PDF validation summary
    from existing comparison_report.json outputs.

    This function only adapts upstream PDF validation results.
    """

    comparison_files = list(
        (REPO_ROOT / "test_output").rglob(
            "comparison_report.json"
        )
    )

    if not comparison_files:
        return pd.DataFrame()

    rows = []

    for file in comparison_files:

        try:
            with file.open(
                encoding="utf-8"
            ) as f:
                report = json.load(f)

        except Exception:
            continue

        summary = report.get(
            "summary",
            {}
        )

        rows.append(
            {
                "Fund ID":
                    summary.get(
                        "vendor_fund_id"
                    ),

                "PDF Date":
                    summary.get(
                        "pdf_as_of_date"
                    ),

                "Comparability":
                    report.get(
                        "comparability_status"
                    ),

                "Overall Status":
                    report.get(
                        "overall_status"
                    ),

                "Matched Values":
                    summary.get(
                        "amount_match_count",
                        0
                    ),

                "Mismatched Values":
                    summary.get(
                        "amount_mismatch_count",
                        0
                    ),
            }
        )

    return pd.DataFrame(rows)


def get_pdf_validation_metrics():
    """
    Generate executive PDF validation metrics.
    """

    df = get_pdf_validation_summary()

    if df.empty:
        return {}

    return {
        "PDFs Reviewed":
            len(df),

        "Comparable Documents":
            int(
                (
                    df["Comparability"]
                    == "comparable"
                ).sum()
            ),

        "Passed Validation":
            int(
                (
                    df["Overall Status"]
                    == "PASS"
                ).sum()
            ),

        "Requires Review":
            int(
                (
                    df["Overall Status"]
                    == "REVIEW_REQUIRED"
                ).sum()
            ),

        "Value Mismatches":
            int(
                df["Mismatched Values"]
                .sum()
            ),
    }


# Entity Resolution

def get_entity_resolution_summary():
    """
    Build Office-facing entity resolution summary
    from existing PDF validation outputs.

    The matching logic remains owned by the upstream
    entity resolution pipeline. This function only
    summarizes existing results.
    """

    comparison_files = list(
        (REPO_ROOT / "test_output").rglob(
            "comparison_report.json"
        )
    )

    if not comparison_files:
        return pd.DataFrame()

    rows = []

    for file in comparison_files:

        try:
            with file.open(
                encoding="utf-8"
            ) as f:
                report = json.load(f)

        except Exception:
            continue

        summary = report.get(
            "summary",
            {}
        )

        rows.append(
            {
                "Fund ID":
                    summary.get(
                        "vendor_fund_id"
                    ),

                "PDF Date":
                    summary.get(
                        "pdf_as_of_date"
                    ),

                "Confirmed Entity Mappings":
                    summary.get(
                        "confirmed_entity_mappings",
                        0
                    ),
            }
        )

    return pd.DataFrame(rows)


def get_entity_resolution_metrics():
    """
    Generate executive entity resolution metrics
    from existing upstream outputs.
    """

    df = get_entity_resolution_summary()

    if df.empty:
        return {}

    return {
        "Confirmed Entity Mappings":
            int(
                df[
                    "Confirmed Entity Mappings"
                ].sum()
            ),

        "Documents With Confirmed Mappings":
            int(
                (
                    df[
                        "Confirmed Entity Mappings"
                    ]
                    > 0
                ).sum()
            ),
    }


# Diagnosis

def load_diagnosis_tables():
    """
    Load exported diagnosis workbook tables.
    """

    if not DIAGNOSIS_OUTPUT.exists():
        return {}

    return {
        "exception_summary":
            pd.read_excel(
                DIAGNOSIS_OUTPUT,
                sheet_name=DIAGNOSIS_EXCEPTION_SUMMARY_SHEET,
            ),

        "action_summary":
            pd.read_excel(
                DIAGNOSIS_OUTPUT,
                sheet_name=DIAGNOSIS_ACTION_SUMMARY_SHEET,
            ),

        "impact_summary":
            pd.read_excel(
                DIAGNOSIS_OUTPUT,
                sheet_name=DIAGNOSIS_IMPACT_SUMMARY_SHEET,
            ),

        "exception_detail":
            pd.read_excel(
                DIAGNOSIS_OUTPUT,
                sheet_name=DIAGNOSIS_DETAIL_SHEET,
            ),
    }


# Unified reporting inputs

def discover_outputs() -> dict:
    """
    Return currently available reporting inputs.
    """

    return {
        "phase1_summary":
            get_phase1_summary(
                REPORTS_DIR
            ),

        "data_state_images":
            get_data_state_images(),

        "ops_report":
            get_current_ops_report(),

        "pdf_validation":
            get_pdf_validation_summary(),

        "pdf_validation_metrics":
            get_pdf_validation_metrics(),

        "entity_resolution":
            get_entity_resolution_summary(),

        "entity_resolution_metrics":
            get_entity_resolution_metrics(),

        "diagnosis_report":
            get_diagnosis_report(),

        "diagnosis_tables":
            load_diagnosis_tables(),
    }