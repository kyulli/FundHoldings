"""
Generate business-facing diagnosis explanations.
"""

from typing import List
from .models import ExceptionIssue


def diagnose_issue(
    issue: ExceptionIssue
) -> ExceptionIssue:

    """
    Add Office-facing diagnosis explanation
    based on exception type.
    """

    if issue.exception_type == "Missing Field":

        issue.diagnosis = (
            "Missing information requires source report "
            "verification. The issue may be caused by "
            "unavailable disclosure or incomplete extraction."
        )

    elif issue.exception_type == "Deal Status Mismatch":

        issue.diagnosis = (
            "Reported Deal Status does not match "
            "financial-value-based classification. "
            "Source confirmation is required."
        )

    elif issue.exception_type == "Entity Mapping Review":

        issue.diagnosis = (
            "Entity identity between source documents "
            "and structured holdings data is not confirmed."
        )

    elif issue.exception_type == "PDF / Vendor Value Mismatch":

        issue.diagnosis = (
            "PDF reported value differs from structured "
            "holdings data. Source verification is required."
        )

    elif issue.exception_type == "PDF Comparison Blocked":

        issue.diagnosis = (
            "Automated PDF comparison could not be completed. "
            "Document review or extraction resolution is required."
        )

    else:

        issue.diagnosis = (
            "Exception requires further review."
        )

    return issue


def diagnose_all(
    issues: List[ExceptionIssue]
) -> List[ExceptionIssue]:

    """
    Add diagnosis explanations to all issues.
    """

    return [
        diagnose_issue(issue)
        for issue in issues
    ]