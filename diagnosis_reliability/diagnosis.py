"""
Root-cause diagnosis logic for standardized data-quality issues.
"""

from __future__ import annotations

from diagnosis_reliability.config import CONFIDENCE_LEVELS, ROOT_CAUSES
from diagnosis_reliability.models import StandardIssue


def diagnose_missing_field(issue: StandardIssue) -> StandardIssue:
    """
    Diagnose a missing-field issue conservatively.

    A missing value in the vendor dataset does not by itself prove whether
    the source PDF omitted the field or whether extraction failed.
    """

    issue.root_cause = ROOT_CAUSES["unknown"]
    issue.confidence = CONFIDENCE_LEVELS["low"]
    issue.evidence_strength = "LOW"

    issue.diagnosis_reason = (
        "The expected field is missing from the structured dataset, but "
        "no source-PDF evidence is currently attached to determine whether "
        "the field was not reported or was missed during extraction."
    )

    issue.recommended_action = (
        "Verify the field against the source report before assigning a "
        "confirmed root cause or correcting the structured data."
    )

    issue.review_required = True

    return issue


def diagnose_deal_status_mismatch(issue: StandardIssue) -> StandardIssue:
    """
    Diagnose disagreement between reported and numerically derived Deal Status.
    """

    reported = issue.reported_value
    derived = issue.derived_value

    numeric_evidence_available = any(
        value is not None
        for value in [
            issue.current_cost,
            issue.unrealized_value,
            issue.realized_proceeds,
        ]
    )

    issue.root_cause = ROOT_CAUSES["status_logic"]

    if reported is not None and derived is not None and numeric_evidence_available:
        issue.confidence = CONFIDENCE_LEVELS["medium"]
        issue.evidence_strength = "MEDIUM"

        issue.diagnosis_reason = (
            f"The reported Deal Status ({reported!r}) conflicts with the "
            f"status implied by available financial values ({derived!r}). "
            "The discrepancy is supported by structured numeric evidence, "
            "but the source PDF has not yet been used to confirm which value "
            "should be treated as authoritative."
        )
    else:
        issue.confidence = CONFIDENCE_LEVELS["low"]
        issue.evidence_strength = "LOW"

        issue.diagnosis_reason = (
            "Reported and derived Deal Status differ, but the supporting "
            "numeric evidence is incomplete."
        )

    issue.recommended_action = (
        "Verify Deal Status against the source PDF before correcting the "
        "vendor dataset."
    )

    issue.review_required = True

    return issue


def diagnose_issue(issue: StandardIssue) -> StandardIssue:
    """
    Apply the appropriate diagnosis rule to one standardized issue.
    """

    if issue.issue_type == "MISSING_FIELD":
        return diagnose_missing_field(issue)

    if issue.issue_type == "DEAL_STATUS_MISMATCH":
        return diagnose_deal_status_mismatch(issue)

    issue.root_cause = ROOT_CAUSES["unknown"]
    issue.confidence = CONFIDENCE_LEVELS["low"]
    issue.evidence_strength = "LOW"
    issue.diagnosis_reason = (
        f"No diagnosis rule has been implemented for issue type "
        f"{issue.issue_type!r}."
    )
    issue.recommended_action = "Manual review required."
    issue.review_required = True

    return issue


def diagnose_issues(
    issues: list[StandardIssue],
) -> list[StandardIssue]:
    """
    Diagnose a collection of standardized issues.
    """
    
    return [
        diagnose_issue(issue)
        for issue in issues
    ]