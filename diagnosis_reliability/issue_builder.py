"""
Build Office-facing exception issues
from validation outputs.
"""

from typing import List
from .models import ExceptionIssue
from .config import EXCEPTION_TYPES, ACTIONS


# Phase 1 Missing Field Issues

def build_missing_field_issues(df) -> List[ExceptionIssue]:

    """
    Convert Phase 1 missing field findings
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

                    exception_type=(
                        EXCEPTION_TYPES["missing_field"]
                    ),

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
                        "Structured holdings data available; "
                        "source report verification required"
                    ),

                    diagnosis=(
                        "Missing information requires "
                        "confirmation from the source report."
                    ),

                    recommended_action=(
                        ACTIONS["verify_source"]
                    ),
                )
            )

    return issues


# Phase 1 Deal Status Issues

def build_deal_status_issues(df) -> List[ExceptionIssue]:

    """
    Convert Phase 1 deal status mismatch findings
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

        issues.append(

            ExceptionIssue(

                issue_id=f"STATUS_{idx}",

                exception_type=(
                    EXCEPTION_TYPES["deal_status"]
                ),

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

                issue_detail=(
                    f"Reported status: {reported}; "
                    f"Derived status: {derived}"
                ),

                evidence_available=(
                    "Reported status and financial fields "
                    "available; source report confirmation required"
                ),

                reported_value=reported,

                derived_value=derived,

                current_cost=row.get(
                    "Current Cost"
                ),

                unrealized_value=row.get(
                    "Unrealized Value"
                ),

                realized_proceeds=row.get(
                    "Realized Proceeds"
                ),

                diagnosis=(
                    "Potential status classification "
                    "inconsistency identified."
                ),

                recommended_action=(
                    ACTIONS["verify_status"]
                ),
            )
        )

    return issues


# Build unified exception queue

def build_all_issues(validation_outputs):

    """
    Combine all validation sources into
    one Office review queue.
    """

    issues = []

    # Phase 1 missing fields

    missing_fields = validation_outputs.get(
        "phase1_missing_fields"
    )

    if (
        missing_fields is not None
        and not missing_fields.empty
    ):

        issues.extend(
            build_missing_field_issues(
                missing_fields
            )
        )


    # Phase 1 deal status

    deal_status = validation_outputs.get(
        "phase1_deal_status"
    )

    if (
        deal_status is not None
        and not deal_status.empty
    ):

        issues.extend(
            build_deal_status_issues(
                deal_status
            )
        )


    # PDF validation

    issues.extend(
        validation_outputs.get(
            "pdf_validation",
            []
        )
    )


    # Entity resolution

    entity_resolution = validation_outputs.get(
        "entity_resolution",
        []
    )

    if isinstance(entity_resolution, list):

        issues.extend(
            entity_resolution
        )
        

    return issues