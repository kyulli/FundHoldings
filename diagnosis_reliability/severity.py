"""
Severity scoring for standardized data-quality issues.
"""

from __future__ import annotations

from diagnosis_reliability.config import (
    DEFAULT_FIELD_CRITICALITY,
    FIELD_CRITICALITY,
    ISSUE_TYPE_WEIGHT,
    SEVERITY_THRESHOLDS,
)
from diagnosis_reliability.models import StandardIssue


def _field_criticality(issue: StandardIssue) -> int:
    """
    Return configured criticality weight for the affected field.
    """

    if issue.field is None:
        return DEFAULT_FIELD_CRITICALITY

    return FIELD_CRITICALITY.get(
        issue.field,
        DEFAULT_FIELD_CRITICALITY,
    )


def _issue_type_weight(issue: StandardIssue) -> int:
    """
    Return configured weight for the issue type.
    """

    return ISSUE_TYPE_WEIGHT.get(
        issue.issue_type,
        1,
    )


def calculate_severity_score(
    issue: StandardIssue,
) -> float:
    """
    Calculate the first-version heuristic severity score.
    """

    score = (
        _field_criticality(issue)
        + _issue_type_weight(issue)
    )

    return float(score)


def severity_label(score: float) -> str:
    """
    Map a numeric severity score into HIGH / MEDIUM / LOW.
    """

    if score >= SEVERITY_THRESHOLDS["HIGH"]:
        return "HIGH"

    if score >= SEVERITY_THRESHOLDS["MEDIUM"]:
        return "MEDIUM"

    return "LOW"


def score_issue_severity(
    issue: StandardIssue,
) -> StandardIssue:
    """
    Populate severity score and severity label for one issue.
    """

    score = calculate_severity_score(issue)

    issue.severity_score = score
    issue.severity = severity_label(score)

    return issue


def score_issue_severities(
    issues: list[StandardIssue],
) -> list[StandardIssue]:
    """
    Score severity for a collection of issues.
    """
    
    return [
        score_issue_severity(issue)
        for issue in issues
    ]