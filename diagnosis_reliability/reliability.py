"""
Reliability assessment for data-quality diagnoses.

Reliability describes how strongly the available evidence supports the
assigned diagnosis. It is intentionally separate from issue severity.
"""

from __future__ import annotations

from diagnosis_reliability.models import StandardIssue


def assess_report_only_reliability(
    issue: StandardIssue,
) -> StandardIssue:
    """
    Assess reliability when only structured report evidence is available.
    """

    if issue.issue_type == "MISSING_FIELD":
        issue.confidence = "LOW"
        issue.evidence_strength = "LOW"
        issue.reliability_reason = (
            "The structured dataset confirms that an expected field is missing, "
            "but no source-PDF evidence is attached. The framework therefore "
            "cannot distinguish a source-reporting gap from an extraction or "
            "mapping problem."
        )
        return issue

    if issue.issue_type == "DEAL_STATUS_MISMATCH":
        has_numeric_evidence = any(
            value is not None
            for value in [
                issue.current_cost,
                issue.unrealized_value,
                issue.realized_proceeds,
            ]
        )

        if (
            issue.reported_value is not None
            and issue.derived_value is not None
            and has_numeric_evidence
        ):
            issue.confidence = "MEDIUM"
            issue.evidence_strength = "MEDIUM"
            issue.reliability_reason = (
                "The disagreement is supported by structured financial values "
                "used to derive Deal Status, but the original source PDF has "
                "not yet confirmed which status is authoritative."
            )
        else:
            issue.confidence = "LOW"
            issue.evidence_strength = "LOW"
            issue.reliability_reason = (
                "The Deal Status disagreement exists, but supporting structured "
                "evidence is incomplete."
            )

        return issue

    issue.confidence = "LOW"
    issue.evidence_strength = "LOW"
    issue.reliability_reason = (
        "No source-specific reliability rule is available for this issue."
    )

    return issue


def assess_pdf_reliability(
    issue: StandardIssue,
) -> StandardIssue:
    """
    Assess reliability using evidence supplied by PDF validation.

    This logic is intentionally based on stable canonical fields rather than
    the upstream PDF-validation file format.
    """

    # Strongest case:
    # extraction succeeded, comparison was allowed, and mapping was confirmed.
    extraction_pass = issue.extraction_quality == "PASS"

    comparison_pass = issue.comparability_status in {
        "PASS",
        "COMPARABLE",
        "comparable",
    }

    mapping_confirmed = issue.mapping_status in {
        "confirmed",
        "CONFIRMED",
        "exact",
        "approved",
    }

    if extraction_pass and comparison_pass and mapping_confirmed:
        issue.confidence = "HIGH"
        issue.evidence_strength = "HIGH"
        issue.reliability_reason = (
            "PDF extraction quality passed, the record was considered "
            "comparable to the vendor data, and the entity mapping was "
            "confirmed."
        )
        return issue

    # Extraction itself is questionable: do not trust the mismatch strongly.
    if issue.extraction_quality in {
        "FAIL",
        "REVIEW_REQUIRED",
        "BLOCKED",
    }:
        issue.confidence = "LOW"
        issue.evidence_strength = "LOW"
        issue.reliability_reason = (
            "PDF extraction quality did not pass. Any apparent mismatch may "
            "result from extraction uncertainty and requires manual review."
        )
        return issue

    # Mapping uncertainty also weakens the evidence substantially.
    if issue.mapping_status in {
        "ambiguous",
        "candidate_only",
        "unconfirmed",
    }:
        issue.confidence = "LOW"
        issue.evidence_strength = "LOW"
        issue.reliability_reason = (
            "The PDF and vendor records are not linked by a confirmed entity "
            "mapping, so the observed difference cannot yet support a strong "
            "root-cause conclusion."
        )
        return issue

    issue.confidence = "MEDIUM"
    issue.evidence_strength = "MEDIUM"
    issue.reliability_reason = (
        "Some PDF validation evidence is available, but not all reliability "
        "conditions are strong enough for a high-confidence diagnosis."
    )

    return issue


def assess_issue_reliability(
    issue: StandardIssue,
) -> StandardIssue:
    """
    Select the appropriate reliability assessment for one issue.
    """

    if issue.source_system == "pdf_validation":
        return assess_pdf_reliability(issue)

    return assess_report_only_reliability(issue)


def assess_reliability(
    issues: list[StandardIssue],
) -> list[StandardIssue]:
    """
    Assess diagnosis reliability for a collection of issues.
    """
    
    return [
        assess_issue_reliability(issue)
        for issue in issues
    ]