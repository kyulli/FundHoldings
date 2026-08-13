"""
Run the complete diagnosis and reliability workflow.
"""

from __future__ import annotations
from collections import Counter

from diagnosis_reliability.adapters.report_adapter import load_report_issues
from diagnosis_reliability.diagnosis import diagnose_issues
from diagnosis_reliability.recommendations import generate_recommendations
from diagnosis_reliability.reliability import assess_reliability
from diagnosis_reliability.severity import score_issue_severities
from diagnosis_reliability.export import export_diagnosis_report


def run_diagnosis():
    """
    Run the current report-based diagnosis workflow.
    """

    issues = load_report_issues()

    issues = diagnose_issues(issues)
    issues = score_issue_severities(issues)
    issues = assess_reliability(issues)
    issues = generate_recommendations(issues)

    return issues


if __name__ == "__main__":
    results = run_diagnosis()

    print(f"Evaluated issues: {len(results)}")

    root_cause_counts = Counter(
        issue.root_cause
        for issue in results
    )

    confidence_counts = Counter(
        issue.confidence
        for issue in results
    )

    decision_counts = Counter(
        issue.decision_state
        for issue in results
    )

    print(
        "Root-cause status:",
        dict(root_cause_counts),
    )

    print(
        "Diagnosis confidence:",
        dict(confidence_counts),
    )

    print(
        "Office decision queue:",
        dict(decision_counts),
    )

    output_path = export_diagnosis_report(results)

    print(
        f"Report written to: {output_path}"
    )
    