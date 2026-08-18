"""
Build Office-facing exception issues
from validation outputs.
"""

from typing import List

from .models import ExceptionIssue
from .config import EXCEPTION_TYPES


def build_missing_field_issues(df) -> List[ExceptionIssue]:
    """
    Convert missing field validation results
    into ExceptionIssue objects.
    """

    issues = []

    for idx, row in df.iterrows():

        fields = str(
            row.get(
                "missing_expected_fields",
                "Unknown field"
            )
        ).split(",")

        for field in fields:
            field = field.strip()

            issues.append(
                ExceptionIssue(
                    issue_id=f"MISSING_{idx}_{field}",

                    exception_type=EXCEPTION_TYPES["missing_field"],

                    fund_id=row.get(
                        "Fund Allocator ID"
                    ),

                    manager_id=row.get(
                        "Investment Manager Allocator ID"
                    ),

                    source_asset=row.get(
                        "Source Asset"
                    ),

                    as_at_date=row.get(
                        "As At Date"
                    ),

                    description=(
                        "Required investment fields are missing "
                        "from structured holdings data"
                    ),

                    issue_detail=(
                        f"Missing field: {field}"
                    ),

                    evidence_available=(
                        "Structured dataset indicates missing values; "
                        "source PDF evidence not yet verified"
                    )
                )
            )

    return issues


def build_deal_status_issues(df) -> List[ExceptionIssue]:
    """
    Convert deal status validation results
    into ExceptionIssue objects.
    """

    issues = []

    for idx, row in df.iterrows():

        reported = row.get(
            "Deal Status"
        )

        derived = row.get(
            "Deal Status (derived)"
        )

        unrealized = row.get(
            "Unrealized Value"
        )

        # Default mismatch description
        detail = (
            f"Reported status: {reported}; "
            f"Derived status: {derived}"
        )


        # Business rule:
        # Written Off should not have negative Unrealized Value
        realized = row.get("Realized Proceeds")
        current_cost = row.get("Current Cost")

        if (
            str(reported).lower() == "written off"
        and unrealized == 0
        and current_cost == 0
        and realized is not None
        and realized < 0
        ):
            detail = (
                "Written Off reported while "
                "Realized Proceeds is negative"
            )


        issues.append(
            ExceptionIssue(

                issue_id=f"STATUS_{idx}",

                exception_type=EXCEPTION_TYPES["deal_status"],

                fund_id=row.get(
                    "Fund Allocator ID"
                ),

                manager_id=row.get(
                    "Investment Manager Allocator ID"
                ),

                source_asset=row.get(
                    "Source Asset"
                ),

                as_at_date=row.get(
                    "As At Date"
                ),

                description=(
                    "Reported Deal Status differs "
                    "from financial-value-based classification"
                ),

                issue_detail=detail,

                evidence_available=(
                    "Reported status and financial fields "
                    "available; source PDF confirmation required"
                ),

                reported_value=reported,

                derived_value=derived,

                current_cost=row.get(
                    "Current Cost"
                ),

                unrealized_value=unrealized,

                realized_proceeds=row.get(
                    "Realized Proceeds"
                )
            )
        )

    return issues


def build_all_issues(validation_outputs):
    """
    Build all exception issues.
    """

    issues = []

    if not validation_outputs[
        "missing_fields"
    ].empty:
        issues.extend(
            build_missing_field_issues(
                validation_outputs["missing_fields"]
            )
        )

    if not validation_outputs[
        "deal_status"
    ].empty:
        issues.extend(
            build_deal_status_issues(
                validation_outputs["deal_status"]
            )
        )

    if validation_outputs.get(
        "pdf_validation"
    ):
        issues.extend(
            validation_outputs["pdf_validation"]
        )

    return issues