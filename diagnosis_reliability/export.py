"""
Excel export for diagnosis and reliability results.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from diagnosis_reliability.config import DEFAULT_DIAGNOSIS_OUTPUT
from diagnosis_reliability.issue_builder import issues_to_dataframe
from diagnosis_reliability.models import StandardIssue


def build_diagnosis_overview(
    issues_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build a diagnosis-oriented overview for Investment Office review.
    """

    total_issues = len(issues_df)

    resolved_root_cause = (
        issues_df["root_cause"].notna()
        & (issues_df["root_cause"] != "UNKNOWN_REQUIRES_REVIEW")
    ).sum()

    unresolved_root_cause = (
        issues_df["root_cause"] == "UNKNOWN_REQUIRES_REVIEW"
    ).sum()

    source_verification_required = (
        issues_df["decision_state"] == "VERIFY_SOURCE"
    ).sum()

    status_verification_required = (
        issues_df["decision_state"]
        == "VERIFY_STATUS_AGAINST_SOURCE"
    ).sum()

    rows = [
        {
            "category": "Diagnosis Coverage",
            "metric": "Issues Evaluated",
            "value": int(total_issues),
            "office_guidance": (
                "Upstream data-quality issues evaluated by the diagnosis "
                "and reliability framework."            ),
        },
        {
            "category": "Diagnosis Coverage",
            "metric": "Specific Root Cause Assigned",
            "value": int(resolved_root_cause),
            "office_guidance": (
                "Issues with a specific preliminary root-cause classification "
                "based on currently available evidence."
            ),
        },
        {
            "category": "Diagnosis Coverage",
            "metric": "Root Cause Unresolved",
            "value": int(unresolved_root_cause),
            "office_guidance": (
                "Source evidence is still required before a root cause can be assigned."
            ),
        },
        {
            "category": "Evidence Reliability",
            "metric": "High-Confidence Diagnoses",
            "value": int((issues_df["confidence"] == "HIGH").sum()),
            "office_guidance": (
                "Evidence is strong enough to support a high-confidence diagnosis."
            ),
        },
        {
            "category": "Evidence Reliability",
            "metric": "Medium-Confidence Diagnoses",
            "value": int((issues_df["confidence"] == "MEDIUM").sum()),
            "office_guidance": (
                "Structured evidence supports the diagnosis, but source confirmation remains appropriate."
            ),
        },
        {
            "category": "Evidence Reliability",
            "metric": "Low-Confidence Diagnoses",
            "value": int((issues_df["confidence"] == "LOW").sum()),
            "office_guidance": (
                "The issue is detected, but available evidence is insufficient to determine the cause."
            ),
        },
        {
            "category": "Office Action",
            "metric": "Verify Against Source Report",
            "value": int(source_verification_required),
            "office_guidance": (
                "Check the original source report before correcting or escalating the structured data."
            ),
        },
        {
            "category": "Office Action",
            "metric": "Verify Deal Status Against Source",
            "value": int(status_verification_required),
            "office_guidance": (
                "Compare the reported status, derived financial logic, and source PDF."
            ),
        },
    ]

    return pd.DataFrame(rows)


def build_decision_queue(
    issues_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build an office-facing decision queue grouped by required action.
    """

    grouped = (
        issues_df.groupby(
            [
                "decision_state",
                "severity",
                "confidence",
            ],
            dropna=False,
        )
        .agg(
            issue_count=("issue_id", "count"),
            affected_funds=("fund_id", "nunique"),
            affected_managers=("manager_id", "nunique"),
        )
        .reset_index()
    )

    guidance_map = {
        "VERIFY_SOURCE": (
            "Review the original source report to determine whether the "
            "missing value reflects a reporting gap or an extraction issue."
        ),
        "VERIFY_STATUS_AGAINST_SOURCE": (
            "Compare the reported Deal Status with the source PDF and "
            "underlying financial values."
        ),
        "REVIEW_PROPOSED_CORRECTION": (
            "Review and approve the proposed structured-data correction."
        ),
        "CONFIRM_ENTITY_MAPPING": (
            "Confirm the entity mapping before relying on the comparison."
        ),
        "RESOLVE_EXTRACTION_UNCERTAINTY": (
            "Resolve PDF extraction uncertainty before assigning a root cause."
        ),
        "DOCUMENT_SOURCE_GAP": (
            "Document that the source report itself does not disclose the field."
        ),
        "ESCALATE_VENDOR_MISMATCH": (
            "Review the confirmed source-vendor mismatch for correction or escalation."
        ),
        "MANUAL_REVIEW": (
            "Review manually because no specific decision path is currently available."
        ),
    }

    grouped["office_guidance"] = (
        grouped["decision_state"]
        .map(guidance_map)
        .fillna("Review the issue and determine the appropriate next action.")
    )

    severity_order = {
        "HIGH": 0,
        "MEDIUM": 1,
        "LOW": 2,
    }

    confidence_order = {
        "HIGH": 0,
        "MEDIUM": 1,
        "LOW": 2,
    }

    grouped["_severity_order"] = (
        grouped["severity"]
        .map(severity_order)
        .fillna(99)
    )

    grouped["_confidence_order"] = (
        grouped["confidence"]
        .map(confidence_order)
        .fillna(99)
    )

    grouped = grouped.sort_values(
        [
            "_severity_order",
            "_confidence_order",
            "issue_count",
        ],
        ascending=[True, True, False],
    )

    return grouped.drop(
        columns=[
            "_severity_order",
            "_confidence_order",
        ]
    ).reset_index(drop=True)


def build_diagnosis_ledger(
    issues_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build the office-facing diagnosis ledger.
    """

    base_columns = [
        "issue_id",
        "fund_id",
        "manager_id",
        "source_asset",
        "canonical_entity",
        "as_at_date",
        "issue_type",
        "field",
        "root_cause",
        "severity",
        "confidence",
        "evidence_strength",
        "decision_state",
        "diagnosis_reason",
        "reliability_reason",
        "recommended_action",
        "review_required",
        "source_system",
    ]

    evidence_columns = [
        "reported_value",
        "derived_value",
        "pdf_value",
        "vendor_value",
        "difference",
        "difference_pct",
        "extraction_quality",

        "entity_resolution_status",
        "entity_resolution_confidence",
        "entity_resolution_reason",
        "entity_resolution_followup",

        "mapping_status",
        "comparability_status",
    ]

    # Only expose evidence columns when at least one row actually contains
    # meaningful evidence. This prevents empty PDF-placeholder columns from
    # cluttering the office-facing workbook.
    visible_evidence_columns = [
        column
        for column in evidence_columns
        if column in issues_df.columns
        and issues_df[column].notna().any()
    ]

    columns = base_columns + visible_evidence_columns

    ledger = issues_df[columns].copy()

    return ledger


def build_reliability_analysis(
    issues_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build an evidence-coverage and diagnosis-reliability analysis.
    """

    total = len(issues_df)

    def count(condition) -> int:
        return int(condition.sum())

    rows = [
        {
            "category": "Diagnosis Confidence",
            "metric": "High Confidence",
            "count": count(issues_df["confidence"] == "HIGH"),
            "percent_of_issues": (
                count(issues_df["confidence"] == "HIGH") / total
                if total
                else 0
            ),
            "interpretation": (
                "Available evidence strongly supports the assigned diagnosis."
            ),
        },
        {
            "category": "Diagnosis Confidence",
            "metric": "Medium Confidence",
            "count": count(issues_df["confidence"] == "MEDIUM"),
            "percent_of_issues": (
                count(issues_df["confidence"] == "MEDIUM") / total
                if total
                else 0
            ),
            "interpretation": (
                "Structured evidence supports the diagnosis, but source "
                "confirmation is still appropriate."
            ),
        },
        {
            "category": "Diagnosis Confidence",
            "metric": "Low Confidence",
            "count": count(issues_df["confidence"] == "LOW"),
            "percent_of_issues": (
                count(issues_df["confidence"] == "LOW") / total
                if total
                else 0
            ),
            "interpretation": (
                "The issue is detected, but current evidence is insufficient "
                "to confidently assign the underlying cause."
            ),
        },
        {
            "category": "Root-Cause Resolution",
            "metric": "Specific Root Cause Assigned",
            "count": count(
                issues_df["root_cause"].notna()
                & (
                    issues_df["root_cause"]
                    != "UNKNOWN_REQUIRES_REVIEW"
                )
            ),
            "percent_of_issues": (
                count(
                    issues_df["root_cause"].notna()
                    & (
                        issues_df["root_cause"]
                        != "UNKNOWN_REQUIRES_REVIEW"
                    )
                )
                / total
                if total
                else 0
            ),
            "interpretation": (
                "A preliminary specific root-cause category can be assigned "
                "from the currently available evidence."
            ),
        },
        {
            "category": "Root-Cause Resolution",
            "metric": "Root Cause Unresolved",
            "count": count(
                issues_df["root_cause"]
                == "UNKNOWN_REQUIRES_REVIEW"
            ),
            "percent_of_issues": (
                count(
                    issues_df["root_cause"]
                    == "UNKNOWN_REQUIRES_REVIEW"
                )
                / total
                if total
                else 0
            ),
            "interpretation": (
                "Additional source evidence is required before the framework "
                "can distinguish reporting, extraction, or mapping causes."
            ),
        },
        {
            "category": "Evidence Coverage",
            "metric": "Source Verification Required",
            "count": count(
                issues_df["decision_state"].isin(
                    [
                        "VERIFY_SOURCE",
                        "VERIFY_STATUS_AGAINST_SOURCE",
                    ]
                )
            ),
            "percent_of_issues": (
                count(
                    issues_df["decision_state"].isin(
                        [
                            "VERIFY_SOURCE",
                            "VERIFY_STATUS_AGAINST_SOURCE",
                        ]
                    )
                )
                / total
                if total
                else 0
            ),
            "interpretation": (
                "The source report must still be checked before correction "
                "or escalation."
            ),
        },
        {
            "category": "Evidence Coverage",
            "metric": "PDF Evidence Attached",
            "count": count(
                issues_df["source_system"]
                == "pdf_validation"
            ),
            "percent_of_issues": (
                count(
                    issues_df["source_system"]
                    == "pdf_validation"
                )
                / total
                if total
                else 0
            ),
            "interpretation": (
                "Issues currently supported by evidence from the PDF "
                "validation workflow."
            ),
        },
        {
            "category": "Evidence Coverage",
            "metric": "Extraction Quality Known",
            "count": count(
                issues_df["extraction_quality"].notna()
            ),
            "percent_of_issues": (
                count(
                    issues_df["extraction_quality"].notna()
                )
                / total
                if total
                else 0
            ),
            "interpretation": (
                "PDF extraction quality has been evaluated for these issues."
            ),
        },
        {
            "category": "Evidence Coverage",
            "metric": "Entity Mapping Status Known",
            "count": count(
                issues_df["mapping_status"].notna()
            ),
            "percent_of_issues": (
                count(
                    issues_df["mapping_status"].notna()
                )
                / total
                if total
                else 0
            ),
            "interpretation": (
                "Entity linkage evidence is available for these issues."
            ),
        },
        {
            "category": "Evidence Coverage",
            "metric": "Comparability Status Known",
            "count": count(
                issues_df["comparability_status"].notna()
            ),
            "percent_of_issues": (
                count(
                    issues_df["comparability_status"].notna()
                )
                / total
                if total
                else 0
            ),
            "interpretation": (
                "The framework knows whether the source and vendor values "
                "are valid to compare."
            ),
        },
    ]

    return pd.DataFrame(rows)


def export_diagnosis_report(
    issues: list[StandardIssue],
    output_path: Path | None = None,
) -> Path:
    """
    Export diagnosis results to an Excel workbook.
    """

    output_path = output_path or DEFAULT_DIAGNOSIS_OUTPUT
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    issues_df = issues_to_dataframe(issues)

    diagnosis_overview = build_diagnosis_overview(
        issues_df
    )

    diagnosis_ledger = build_diagnosis_ledger(
        issues_df
    )
    
    decision_queue = build_decision_queue(
        issues_df
    )

    reliability_analysis = build_reliability_analysis(
        issues_df
    )

    with pd.ExcelWriter(
        output_path,
        engine="openpyxl",
    ) as writer:

        diagnosis_overview.to_excel(
            writer,
            sheet_name="Diagnosis Overview",
            index=False,
        )

        decision_queue.to_excel(
            writer,
            sheet_name="Decision Queue",
            index=False,
        )

        reliability_analysis.to_excel(
            writer,
            sheet_name="Reliability Analysis",
            index=False,
        )

        diagnosis_ledger.to_excel(
            writer,
            sheet_name="Diagnosis Ledger",
            index=False,
        )

    return output_path