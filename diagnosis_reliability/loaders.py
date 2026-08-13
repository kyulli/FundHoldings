"""
Load report outputs produced by the existing cleaning pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from diagnosis_reliability.config import REPORT_FILES


REQUIRED_COLUMNS = {
    "missing_fields": {
        "Fund Allocator ID",
        "Investment Manager Allocator ID",
        "Source Asset",
        "As At Date",
        "missing_expected_fields",
    },
    "deal_status": {
        "Fund Allocator ID",
        "Source Asset",
        "As At Date",
        "Deal Status",
    },
    "consistency_rules": {
        "rule",
        "violations",
        "rate",
    },
    "fund_quality": {
        "Fund Allocator ID",
    },
    "manager_quality": {
        "Investment Manager Allocator ID",
    },
}


class ReportSchemaError(ValueError):
    """
    Raised when an existing pipeline output does not match the expected schema.
    """


def _validate_columns(
    df: pd.DataFrame,
    report_name: str,
    required_columns: set[str],
) -> None:
    """
    Check that a report contains the columns required by our framework.
    """

    missing = required_columns - set(df.columns)

    if missing:
        raise ReportSchemaError(
            f"{report_name} is missing required columns: "
            f"{sorted(missing)}"
        )


def load_report(
    report_name: str,
    path: Path | None = None,
) -> pd.DataFrame:
    """
    Load and validate one report CSV.
    """

    if report_name not in REPORT_FILES:
        raise KeyError(
            f"Unknown report name: {report_name}. "
            f"Expected one of {sorted(REPORT_FILES)}"
        )

    report_path = path or REPORT_FILES[report_name]

    if not report_path.exists():
        raise FileNotFoundError(
            f"Report file not found: {report_path}"
        )

    df = pd.read_csv(report_path, low_memory=False)

    required = REQUIRED_COLUMNS.get(report_name, set())
    _validate_columns(df, report_name, required)

    return df


def load_all_reports() -> dict[str, pd.DataFrame]:
    """
    Load all report inputs used by the diagnosis framework.
    """
    
    return {
        name: load_report(name)
        for name in REPORT_FILES
    }