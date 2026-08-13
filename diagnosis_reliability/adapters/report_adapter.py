"""
Adapter from existing Phase 1 / cleaning report outputs to StandardIssue.
"""

from __future__ import annotations

from diagnosis_reliability.issue_builder import build_standardized_issues
from diagnosis_reliability.loaders import load_all_reports
from diagnosis_reliability.models import StandardIssue


def load_report_issues() -> list[StandardIssue]:
    """
    Load current report outputs and convert them into canonical issues.
    """
    
    reports = load_all_reports()

    return build_standardized_issues(
        missing_df=reports["missing_fields"],
        deal_status_df=reports["deal_status"],
    )