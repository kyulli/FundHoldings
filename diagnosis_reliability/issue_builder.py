"""
Build standardized issue records from existing pipeline outputs.
"""

from __future__ import annotations

import hashlib

import pandas as pd

from diagnosis_reliability.config import ISSUE_TYPES

from diagnosis_reliability.models import StandardIssue


def _clean_text(value: object) -> str | None:
    """
    Convert a value to clean text while preserving missing values as None.
    """

    if pd.isna(value):
        return None

    text = str(value).strip()
    return text if text else None


def _make_issue_id(*parts: object) -> str:
    """
    Create a stable short identifier from the issue's identifying fields.
    """

    raw = "|".join("" if pd.isna(part) else str(part) for part in parts)

    digest = hashlib.sha1(
        raw.encode("utf-8")
    ).hexdigest()[:12]

    return f"DQ-{digest.upper()}"


def _split_missing_fields(value: object) -> list[str]:
    """
    Split the pipeline's missing_expected_fields value into individual fields.
    """

    text = _clean_text(value)

    if text is None:
        return []

    # The current report may use commas or semicolons to separate fields.
    text = text.replace(";", ",")

    return [
        field.strip()
        for field in text.split(",")
        if field.strip()
    ]


def build_missing_field_issues(
    missing_df: pd.DataFrame,
) -> list[StandardIssue]:
    """
    Convert missing-field rows into one standardized issue per missing field.
    """
    
    issues: list[StandardIssue] = []

    for _, row in missing_df.iterrows():
        fields = _split_missing_fields(
            row["missing_expected_fields"]
        )

        for field in fields:
            fund_id = _clean_text(row.get("Fund Allocator ID"))
            manager_id = _clean_text(
                row.get("Investment Manager Allocator ID")
            )
            source_asset = _clean_text(row.get("Source Asset"))
            as_at_date = _clean_text(row.get("As At Date"))

            issue_id = _make_issue_id(
                fund_id,
                source_asset,
                as_at_date,
                field,
                ISSUE_TYPES["missing_field"],
            )

            issues.append(
                StandardIssue(
                    issue_id=issue_id,
                    source_system="phase1_report",
                    issue_type=ISSUE_TYPES["missing_field"],
                    fund_id=fund_id,
                    manager_id=manager_id,
                    source_asset=source_asset,
                    as_at_date=as_at_date,
                    field=field,
                    issue_detail=f"Expected field is missing: {field}",
                    source_report="flagged_missing_fields_conditional.csv",
                )
            )

    return issues


def build_deal_status_issues(
    deal_status_df: pd.DataFrame,
) -> list[StandardIssue]:
    """
    Convert Deal Status conformance exceptions into standardized issues.
    """

    issues: list[StandardIssue] = []

    for _, row in deal_status_df.iterrows():
        fund_id = _clean_text(row.get("Fund Allocator ID"))
        manager_id = _clean_text(
            row.get("Investment Manager Allocator ID")
        )
        source_asset = _clean_text(row.get("Source Asset"))
        as_at_date = _clean_text(row.get("As At Date"))

        reported_status = _clean_text(row.get("Deal Status"))
        derived_status = _clean_text(
            row.get("Deal Status (derived)")
        )

        current_cost = row.get("Current Cost")
        unrealized_value = row.get("Unrealized Value")
        realized_proceeds = row.get("Realized Proceeds")

        issue_id = _make_issue_id(
            fund_id,
            source_asset,
            as_at_date,
            "Deal Status",
            ISSUE_TYPES["deal_status_mismatch"],
        )

        detail = (
            f"Reported Deal Status is {reported_status!r}; "
            f"derived status is {derived_status!r}."
        )

        issues.append(
            StandardIssue(
                issue_id=issue_id,
                source_system="phase1_report",
                issue_type=ISSUE_TYPES["deal_status_mismatch"],
                fund_id=fund_id,
                manager_id=manager_id,
                source_asset=source_asset,
                as_at_date=as_at_date,
                field="Deal Status",
                issue_detail=detail,
                source_report="deal_status_conformance_exceptions.csv",
                reported_value=reported_status,
                derived_value=derived_status,
                current_cost=current_cost,
                unrealized_value=unrealized_value,
                realized_proceeds=realized_proceeds,
            )
        )

    return issues


def build_standardized_issues(
    missing_df: pd.DataFrame,
    deal_status_df: pd.DataFrame,
) -> list[StandardIssue]:
    """
    Combine supported upstream issue sources into one canonical issue list.
    """

    issues = (
        build_missing_field_issues(missing_df)
        + build_deal_status_issues(deal_status_df)
    )

    seen: set[str] = set()
    unique_issues: list[StandardIssue] = []

    for issue in issues:
        if issue.issue_id in seen:
            continue

        seen.add(issue.issue_id)
        unique_issues.append(issue)

    return unique_issues


def issues_to_dataframe(
    issues: list[StandardIssue],
) -> pd.DataFrame:
    """
    Convert canonical issues into a DataFrame for inspection/export.
    """

    return pd.DataFrame(
        [issue.to_dict() for issue in issues]
    )