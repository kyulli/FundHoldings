"""pdfplumber fallback raw table extraction."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pdfplumber


def _clamp_crop_to_page(page: pdfplumber.page.Page, crop_bbox: list[float]) -> tuple[float, float, float, float]:
    """Map page-local crop coords into page.bbox space (handles negative mediabox origins)."""
    eps = 0.5
    local_x0 = max(eps, min(float(crop_bbox[0]), page.width - eps))
    local_top = max(eps, min(float(crop_bbox[1]), page.height - eps))
    local_x1 = max(local_x0 + eps, min(float(crop_bbox[2]), page.width - eps))
    local_bottom = max(local_top + eps, min(float(crop_bbox[3]), page.height - eps))
    return (
        page.bbox[0] + local_x0,
        page.bbox[1] + local_top,
        page.bbox[0] + local_x1,
        page.bbox[1] + local_bottom,
    )


def _extract_schedule(
    pdf: pdfplumber.PDF,
    *,
    schedule: str,
    pages: list[int],
    crop_bbox: list[float],
    table_settings: dict[str, Any],
    column_names: list[str],
    extracted_at: str,
) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for page_number in pages:
        page = pdf.pages[page_number - 1]
        crop = _clamp_crop_to_page(page, crop_bbox)
        cropped = page.crop(crop)
        table = cropped.extract_table(table_settings) or []
        cells: list[dict[str, Any]] = []
        for row_idx, row in enumerate(table):
            for col_idx, text in enumerate(row):
                col_name = column_names[col_idx] if col_idx < len(column_names) else f"col_{col_idx}"
                cells.append(
                    {
                        "parser": "pdfplumber",
                        "schedule": schedule,
                        "page": page_number,
                        "table_order": 1,
                        "row_index": row_idx,
                        "col_index": col_idx,
                        "column_name": col_name,
                        "text": "" if text is None else str(text),
                        "bbox": None,
                        "extracted_at_utc": extracted_at,
                        "note": "Cell-level bbox not assigned for pdfplumber grid cells; page crop bbox retained at table level.",
                    }
                )
        outputs.append(
            {
                "parser": "pdfplumber",
                "schedule": schedule,
                "page": page_number,
                "table_order": 1,
                "shape": [len(table), len(table[0]) if table else 0],
                "parsing_report": {
                    "crop_bbox": crop_bbox,
                    "table_settings": table_settings,
                },
                "column_names": column_names,
                "dataframe_records": table,
                "cells": cells,
                "table_bbox": crop_bbox,
            }
        )
    return outputs


def extract_pdfplumber_tables(pdf_path: str, config: dict[str, Any]) -> dict[str, Any]:
    extracted_at = datetime.now(timezone.utc).isoformat()
    plumber_cfg = config["pdfplumber"]
    camelot_names = config["camelot"]
    results: dict[str, Any] = {"investments": [], "realized": []}

    with pdfplumber.open(pdf_path) as pdf:
        inv = plumber_cfg["investments"]
        if inv.get("pages"):
            results["investments"] = _extract_schedule(
                pdf,
                schedule="schedule_of_investments",
                pages=inv["pages"],
                crop_bbox=inv["crop_bbox"],
                table_settings=inv["table_settings"],
                column_names=camelot_names["investments"]["column_names"],
                extracted_at=extracted_at,
            )
        realized = plumber_cfg["realized"]
        if realized.get("pages"):
            results["realized"] = _extract_schedule(
                pdf,
                schedule="schedule_of_realized",
                pages=realized["pages"],
                crop_bbox=realized["crop_bbox"],
                table_settings=realized["table_settings"],
                column_names=camelot_names["realized"]["column_names"],
                extracted_at=extracted_at,
            )
    return results
