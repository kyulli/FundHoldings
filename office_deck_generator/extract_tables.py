"""
Read existing analytical tables for the Office reporting deck.

This module only reads values already produced by upstream workflows.
It does not recompute or modify analytical results.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def read_sheet(
    workbook: Path,
    sheet_name: str,
) -> pd.DataFrame:
    """
    Read one existing Excel sheet as displayed values.
    """

    if not workbook.exists():
        raise FileNotFoundError(
            f"Workbook not found: {workbook}"
        )

    return pd.read_excel(
        workbook,
        sheet_name=sheet_name,
    )

def read_sheet_if_exists(
        workbook: Path,
        sheet_name: str,
) -> pd.DataFrame | None:
    """
    Read a worksheet only if it exists.
    """

    sheets = list_sheets(workbook)

    if sheet_name not in sheets:
        return None

    return read_sheet(
        workbook,
        sheet_name,
    )


def list_sheets(
    workbook: Path,
) -> list[str]:
    """
    Return available worksheet names.
    """

    if not workbook.exists():
        return []

    excel_file = pd.ExcelFile(workbook)

    return excel_file.sheet_names


def preview_workbook(
    workbook: Path,
    rows: int = 5,
) -> None:
    """
    Print worksheet names and a short preview.
    """

    print(f"\nWorkbook: {workbook}")

    for sheet in list_sheets(workbook):
        print(f"\n=== {sheet} ===")

        df = read_sheet(
            workbook,
            sheet,
        )

        print(
            f"shape: {df.shape}"
        )

        print(
            df.head(rows).to_string(
                index=False
            )
        )