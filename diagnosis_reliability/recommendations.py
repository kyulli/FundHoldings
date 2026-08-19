"""
Generate Office-facing recommended actions.
"""

from typing import List
from .models import ExceptionIssue
from .config import ACTIONS


RECOMMENDATION_GUIDANCE = {

    "Missing Field":
        (
            "Review the source report to confirm whether "
            "the missing field was disclosed. "
            "If available, update the structured record; "
            "otherwise document the missing disclosure."
        ),

    "Deal Status Mismatch":
        (
            "Compare the reported Deal Status with the "
            "source document and confirm the appropriate "
            "investment classification."
        ),

    "PDF / Vendor Value Mismatch":
        (
            "Compare the PDF-reported value with the "
            "structured holdings value and determine "
            "which source should be treated as correct."
        ),

    "PDF Comparison Blocked":
        (
            "Review PDF extraction results and determine "
            "whether manual validation is required."
        ),

    "Entity Mapping Review":
        (
            "Confirm that the source company name and "
            "structured asset represent the same entity "
            "before updating exposure analysis."
        ),
}


def recommend_issue(
    issue: ExceptionIssue
) -> ExceptionIssue:

    """
    Assign Office-facing recommended action
    and review guidance.
    """

    if issue.exception_type == "Missing Field":

        issue.recommended_action = (
            ACTIONS["verify_source"]
        )

    elif issue.exception_type == "Deal Status Mismatch":

        issue.recommended_action = (
            ACTIONS["verify_status"]
        )

    elif issue.exception_type == "PDF / Vendor Value Mismatch":

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
            "Review Exception — "
            "Further investigation required"
        )

    issue.recommended_guidance = (
        RECOMMENDATION_GUIDANCE.get(
            issue.exception_type,
            "Review exception details and determine "
            "appropriate follow-up action."
        )
    )

    return issue


def recommend_all(
    issues: List[ExceptionIssue]
) -> List[ExceptionIssue]:

    return [
        recommend_issue(issue)
        for issue in issues
    ]