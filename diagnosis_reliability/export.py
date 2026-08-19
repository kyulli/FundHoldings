"""
Export Office-facing diagnosis reports.
"""

from pathlib import Path
import pandas as pd
from .config import DIAGNOSIS_OUTPUT_DIR

from .summary import (
    build_exception_summary,
    build_action_summary,
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

    DIAGNOSIS_OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    output_file = (
        DIAGNOSIS_OUTPUT_DIR
        / "diagnosis_report.xlsx"
    )

    exception_df = build_exception_summary(
        issues
    )

    action_df = build_action_summary(
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

        exception_df.to_excel(
            writer,
            sheet_name="Exception Summary",
            index=False,
        )

        action_df.to_excel(
            writer,
            sheet_name="Action Summary",
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