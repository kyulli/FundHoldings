"""
Generate business-facing diagnosis explanations.
"""

from typing import List

from .models import ExceptionIssue


def diagnose_issue(issue: ExceptionIssue) -> ExceptionIssue:
    """
    Add diagnosis explanation based on exception type.
    """

    if issue.exception_type == "Missing Field":

        issue.diagnosis = (
            "Cause cannot be determined without source evidence. "
            "The missing value may reflect either non-disclosure "
            "or an extraction gap."
        )

    elif issue.exception_type == "Deal Status Inconsistency":

        # Special Written Off case
        if (
            issue.issue_detail
            and "Realized Proceeds is negative"
            in issue.issue_detail
        ):

            issue.diagnosis = (
                "Current classification rule treats non-zero "
                "Realized Proceeds as Fully Exited, while "
                "vendor classification treats the investment "
                "as Written Off. Business definition confirmation "
                "is required."
            )

        else:

            issue.diagnosis = (
                "Reported Deal Status differs from "
                "financial-value-based classification. "
                "Source confirmation is required."
            )

    elif issue.exception_type == "Entity Mapping Review":

        issue.diagnosis = (
            "Entity mapping between PDF extracted company "
            "name and structured holdings data is not confirmed."
        )

    elif issue.exception_type == "PDF / Vendor Value Difference":

        issue.diagnosis = (
            "PDF extracted value differs from structured "
            "holdings data. Source verification is required."
        )

    elif issue.exception_type == "PDF Comparison Blocked":

        issue.diagnosis = (
            "PDF validation checks prevented automated "
            "comparison. Extraction or document review is required."
        )

    else:

        issue.diagnosis = (
            "Exception requires further review."
        )


    return issue


def diagnose_all(
    issues: List[ExceptionIssue]
) -> List[ExceptionIssue]:

    return [
        diagnose_issue(issue)
        for issue in issues
    ]