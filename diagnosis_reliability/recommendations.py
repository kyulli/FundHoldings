"""
Generate Office-facing recommended actions.
"""

from typing import List

from .models import ExceptionIssue
from .config import ACTIONS


def recommend_issue(
    issue: ExceptionIssue
) -> ExceptionIssue:
    """
    Assign recommended review action.
    """

    if issue.exception_type == "Missing Field":

        issue.recommended_action = (
            ACTIONS["verify_source"]
        )

    elif issue.exception_type == "Deal Status Inconsistency":

        issue.recommended_action = (
            ACTIONS["verify_status"]
        )

    elif issue.exception_type == "PDF / Vendor Value Difference":

        issue.recommended_action = (
            ACTIONS["review_value"]
        )

    elif issue.exception_type == "PDF Comparison Blocked":

        issue.recommended_action = (
            ACTIONS["resolve_extraction"]
        )

    elif issue.exception_type == "Entity Mapping Review":

        issue.recommended_action = (
            ACTIONS["confirm_mapping"]
        )

    else:

        issue.recommended_action = (
            "Review Exception — Further investigation required"
        )

    return issue


def recommend_all(
    issues: List[ExceptionIssue]
) -> List[ExceptionIssue]:

    return [
        recommend_issue(issue)
        for issue in issues
    ]