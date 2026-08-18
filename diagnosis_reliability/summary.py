"""
Generate Office-facing exception summary.
"""

import pandas as pd

from .models import ExceptionIssue


def build_exception_summary(
    issues: list[ExceptionIssue]
) -> pd.DataFrame:
    """
    Build PPT summary table.
    """

    summary_rows = []

    grouped = {}

    for issue in issues:

        key = issue.exception_type

        if key not in grouped:
            grouped[key] = []

        grouped[key].append(issue)

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
                    first.diagnosis,

                "Recommended Action":
                    first.recommended_action,
            }

        )

    return pd.DataFrame(summary_rows)


def build_impact_summary(
    issues:list[ExceptionIssue]
)->pd.DataFrame:

    rows=[]

    grouped={}

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