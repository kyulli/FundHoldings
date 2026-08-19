"""
Generate Office-facing exception summaries.
"""

import pandas as pd
from .models import ExceptionIssue


def build_exception_summary(
    issues: list[ExceptionIssue]
) -> pd.DataFrame:

    """
    Build executive exception summary table.
    """

    summary_rows = []
    grouped = {}

    for issue in issues:

        grouped.setdefault(
            issue.exception_type,
            []
        ).append(issue)


    for exception_type, group in grouped.items():

        first = group[0]

        summary_rows.append(

            {
                "Exception Type":
                    exception_type,

                "Description":
                    first.description,

                "Count":
                    len(group),

                "Evidence Available":
                    first.evidence_available,

                "Diagnosis":
                    first.diagnosis or "",

                "Recommended Action":
                    first.recommended_action,

                "Review Guidance":
                    first.recommended_guidance or "",
            }
        )

    summary_df = pd.DataFrame(summary_rows)

    impact_df = build_impact_summary(
        issues
    )

    summary_df = summary_df.merge(
        impact_df[
            [
                "Exception Type",
                "Affected Funds",
                "Affected Managers",
            ]
        ],
        on="Exception Type",
        how="left",
    )

    return summary_df


def build_action_summary(
    issues: list[ExceptionIssue]
) -> pd.DataFrame:

    """
    Summarize issues by recommended action.
    """

    rows = []
    grouped = {}

    for issue in issues:

        grouped.setdefault(
            issue.recommended_action,
            []
        ).append(issue)


    for action, group in grouped.items():

        rows.append(

            {
                "Recommended Action":
                    action,

                "Count":
                    len(group),
            }
        )

    return pd.DataFrame(rows)


def build_impact_summary(
    issues: list[ExceptionIssue]
) -> pd.DataFrame:

    """
    Summarize issue impact by exception type.
    """

    rows = []
    grouped = {}

    for issue in issues:

        grouped.setdefault(
            issue.exception_type,
            []
        ).append(issue)


    for exception_type, group in grouped.items():

        rows.append(

            {
                "Exception Type":
                    exception_type,

                "Count":
                    len(group),

                "Affected Funds":
                    len(
                        {
                            i.fund_id
                            for i in group
                            if i.fund_id
                        }
                    ),

                "Affected Managers":
                    len(
                        {
                            i.manager_id
                            for i in group
                            if i.manager_id
                        }
                    ),
            }
        )

    return pd.DataFrame(rows)