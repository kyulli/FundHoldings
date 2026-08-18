"""
Export Office-facing diagnosis reports.
"""

from pathlib import Path

import pandas as pd

from .config import OUTPUT_DIR
from .summary import (
    build_exception_summary,
    build_impact_summary,
)


def issues_to_dataframe(issues):
    """
    Convert ExceptionIssue objects
    into detail dataframe.
    """

    return pd.DataFrame(
        [
            issue.to_dict()
            for issue in issues
        ]
    )


def export_diagnosis_report(issues):
    """
    Export Excel workbook for Office review.
    """

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    output_file = (
        OUTPUT_DIR
        / "diagnosis_report.xlsx"
    )

    summary_df = build_exception_summary(
        issues
    )

    impact_df = build_impact_summary(
        issues
    )

    detail_df = issues_to_dataframe(
        issues
    )

    with pd.ExcelWriter(
        output_file,
        engine="openpyxl"
    ) as writer:

        summary_df.to_excel(
            writer,
            sheet_name="Exception Summary",
            index=False,
        )

        impact_df.to_excel(
            writer,
            sheet_name="Impact Summary",
            index=False,
        )

        detail_df.to_excel(
            writer,
            sheet_name="Exception Detail",
            index=False,
        )

    return output_file