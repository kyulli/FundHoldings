"""
Operational recommendations for diagnosed data-quality issues.
"""

from __future__ import annotations

from diagnosis_reliability.models import StandardIssue
from diagnosis_reliability.config import DECISION_STATES


def recommend_for_missing_field(
    issue: StandardIssue,
) -> StandardIssue:
    """
    Generate an action for a missing expected field.
    """

    if issue.confidence == "LOW":
        issue.decision_state = DECISION_STATES["verify_source"]

        issue.recommended_action = (
            "Check the source report to determine whether the field was "
            "not reported or was missed during extraction. Do not correct "
            "the structured dataset until the source is verified."
        )
        issue.review_required = True
        return issue

    issue.decision_state = DECISION_STATES["manual_review"]
    issue.recommended_action = (
        "Review the supporting source evidence and determine whether the "
        "structured value should be populated."
    )
    issue.review_required = True

    return issue


def recommend_for_status_mismatch(
    issue: StandardIssue,
) -> StandardIssue:
    """
    Generate an action for a Deal Status inconsistency.
    """

    if issue.confidence in {"LOW", "MEDIUM"}:
        issue.decision_state = DECISION_STATES["verify_status"]

        issue.recommended_action = (
            "Compare the reported Deal Status with the source PDF and the "
            "underlying financial values. If the PDF supports the derived "
            "status, flag the structured Deal Status for correction; "
            "otherwise retain the reported status and document the exception."
        )
        issue.review_required = True
        return issue

    issue.decision_state = DECISION_STATES["review_correction"]
    issue.recommended_action = (
        "The evidence strongly supports a Deal Status inconsistency. "
        "Review and approve the proposed correction before updating the "
        "structured dataset."
    )
    issue.review_required = True

    return issue


def recommend_for_pdf_mismatch(
    issue: StandardIssue,
) -> StandardIssue:
    """
    Generate an action for PDF-vs-vendor value disagreements.
    """

    if issue.confidence == "HIGH":
        issue.decision_state = DECISION_STATES["escalate_vendor"]
        issue.recommended_action = (
            "The PDF comparison is supported by strong extraction, mapping, "
            "and comparability evidence. Review the mismatch for vendor-data "
            "correction or escalation."
        )
        issue.review_required = True
        return issue

    if issue.extraction_quality in {
        "FAIL",
        "REVIEW_REQUIRED",
        "BLOCKED",
    }:
        issue.decision_state = DECISION_STATES["resolve_extraction"]
        issue.recommended_action = (
            "Resolve the PDF extraction uncertainty before treating this "
            "difference as a vendor-data issue."
        )
        issue.review_required = True
        return issue

    if issue.mapping_status in {
        "ambiguous",
        "candidate_only",
        "unconfirmed",
    }:
        issue.decision_state = DECISION_STATES["confirm_mapping"]
        issue.recommended_action = (
            "Confirm the entity mapping before evaluating the apparent "
            "PDF-vs-vendor mismatch."
        )
        issue.review_required = True
        return issue

    issue.decision_state = DECISION_STATES["manual_review"]
    issue.recommended_action = (
        "Review the PDF evidence and comparison gates before assigning a "
        "confirmed root cause."
    )
    issue.review_required = True

    return issue


def generate_recommendation(
    issue: StandardIssue,
) -> StandardIssue:
    """
    Generate the operational recommendation for one issue.
    """

    if issue.issue_type == "MISSING_FIELD":
        return recommend_for_missing_field(issue)

    if issue.issue_type == "DEAL_STATUS_MISMATCH":
        return recommend_for_status_mismatch(issue)

    if issue.issue_type == "PDF_VENDOR_MISMATCH":
        return recommend_for_pdf_mismatch(issue)

    issue.recommended_action = (
        "Review the issue manually because no specific remediation policy "
        "has been defined for this issue type."
    )
    issue.review_required = True

    return issue


def generate_recommendations(
    issues: list[StandardIssue],
) -> list[StandardIssue]:
    """
    Generate recommendations for a collection of issues.
    """

    return [
        generate_recommendation(issue)
        for issue in issues
    ]