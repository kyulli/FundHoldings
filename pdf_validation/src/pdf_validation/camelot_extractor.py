"""Camelot Stream raw table extraction."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import camelot


def _cell_bbox(cell: Any) -> list[float]:
    return [float(cell.x1), float(cell.y1), float(cell.x2), float(cell.y2)]


def _table_to_raw(
    table: Any,
    *,
    schedule: str,
    column_names: list[str],
    extracted_at: str,
) -> dict[str, Any]:
    df = table.df
    n_rows, n_cols = df.shape
    cells: list[dict[str, Any]] = []

    geometry_rows = getattr(table, "cells", None)
    for row_idx in range(n_rows):
        for col_idx in range(n_cols):
            text = df.iat[row_idx, col_idx]
            bbox = None
            if geometry_rows is not None and row_idx < len(geometry_rows) and col_idx < len(geometry_rows[row_idx]):
                cell = geometry_rows[row_idx][col_idx]
                bbox = _cell_bbox(cell)
            col_name = column_names[col_idx] if col_idx < len(column_names) else f"col_{col_idx}"
            cells.append(
                {
                    "parser": "camelot",
                    "schedule": schedule,
                    "page": int(table.page),
                    "table_order": int(table.order),
                    "row_index": row_idx,
                    "col_index": col_idx,
                    "column_name": col_name,
                    "text": "" if text is None else str(text),
                    "bbox": bbox,
                    "extracted_at_utc": extracted_at,
                }
            )

    return {
        "parser": "camelot",
        "schedule": schedule,
        "page": int(table.page),
        "table_order": int(table.order),
        "shape": [n_rows, n_cols],
        "parsing_report": dict(table.parsing_report),
        "column_names": column_names,
        "dataframe_records": df.astype(str).values.tolist(),
        "cells": cells,
    }


def extract_camelot_tables(pdf_path: str, config: dict[str, Any]) -> dict[str, Any]:
    extracted_at = datetime.now(timezone.utc).isoformat()
    camelot_cfg = config["camelot"]
    results: dict[str, Any] = {"investments": [], "realized": [], "statement_of_assets": []}

    inv = camelot_cfg["investments"]
    if inv.get("pages"):
        inv_tables = camelot.read_pdf(
            pdf_path,
            pages=inv["pages"],
            flavor=camelot_cfg["flavor"],
            table_areas=inv.get("table_areas"),
            columns=inv.get("columns"),
            split_text=camelot_cfg.get("split_text", True),
            strip_text=camelot_cfg.get("strip_text", "\n"),
            parallel=camelot_cfg.get("parallel", False),
        )
        for table in inv_tables:
            results["investments"].append(
                _table_to_raw(
                    table,
                    schedule="schedule_of_investments",
                    column_names=inv["column_names"],
                    extracted_at=extracted_at,
                )
            )

    realized = camelot_cfg["realized"]
    if realized.get("pages"):
        realized_tables = camelot.read_pdf(
            pdf_path,
            pages=realized["pages"],
            flavor=camelot_cfg["flavor"],
            table_areas=realized.get("table_areas"),
            columns=realized.get("columns"),
            split_text=camelot_cfg.get("split_text", True),
            strip_text=camelot_cfg.get("strip_text", "\n"),
            parallel=camelot_cfg.get("parallel", False),
        )
        for table in realized_tables:
            results["realized"].append(
                _table_to_raw(
                    table,
                    schedule="schedule_of_realized",
                    column_names=realized["column_names"],
                    extracted_at=extracted_at,
                )
            )

    assets_cfg = camelot_cfg.get("statement_of_assets")
    if assets_cfg:
        kwargs: dict[str, Any] = {
            "pages": assets_cfg["pages"],
            "flavor": camelot_cfg["flavor"],
            "split_text": camelot_cfg.get("split_text", True),
            "strip_text": camelot_cfg.get("strip_text", "\n"),
            "parallel": camelot_cfg.get("parallel", False),
        }
        if assets_cfg.get("table_areas"):
            kwargs["table_areas"] = assets_cfg["table_areas"]
        if assets_cfg.get("columns"):
            kwargs["columns"] = assets_cfg["columns"]
        asset_tables = camelot.read_pdf(pdf_path, **kwargs)
        for table in asset_tables:
            width = table.df.shape[1]
            col_names = [f"col_{i}" for i in range(width)]
            results["statement_of_assets"].append(
                _table_to_raw(
                    table,
                    schedule="statement_of_assets",
                    column_names=col_names,
                    extracted_at=extracted_at,
                )
            )

    return results
