"""JSONL and Excel export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from pdf_validation.schemas import EXCEL_SHEETS


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _records_to_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    normalized = []
    for row in rows:
        item = {}
        for key, value in row.items():
            if isinstance(value, (dict, list)):
                text = json.dumps(value, ensure_ascii=False, default=str)
            else:
                text = value
            if isinstance(text, str):
                # openpyxl rejects some control chars from PDF text extracts
                text = "".join(ch for ch in text if ord(ch) >= 32 or ch in "\n\t")
            item[key] = text
        normalized.append(item)
    return pd.DataFrame(normalized)


def export_results(output_dir: Path, payload: dict[str, Any]) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    jsonl_map = {
        "run_manifest": [payload["run_manifest"]],
        "metadata": payload["metadata"]["fields"],
        "parser_decisions": payload["parser_decisions"],
        "raw_cells": payload["raw_cells"],
        "investment_lots": payload["investment_lots"],
        "company_summary": payload["company_summary"],
        "realized_lots": payload["realized_lots"],
        "reconciliation": payload["reconciliation"],
        "validation_issues": payload["validation_issues"],
        "classified_rows": payload["classified_rows"],
    }
    for name, rows in jsonl_map.items():
        path = output_dir / f"{name}.jsonl"
        _write_jsonl(path, rows)
        paths[name] = path

    excel_path = output_dir / "extraction_review.xlsx"
    readme_rows = [
        {"section": "purpose", "text": "Human-review workbook for PDF extraction evidence and reconciliation."},
        {"section": "machine_source", "text": "Authoritative machine outputs are the sibling JSONL files."},
        {"section": "layers", "text": "raw evidence / normalized reported / derived-inferred remain separate."},
        {"section": "references", "text": "See pdf_validation/REFERENCES.md and IMPLEMENTATION_PLAN.md."},
    ]

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        sheet_data = {
            "README": readme_rows,
            "run_manifest": [payload["run_manifest"]],
            "metadata": payload["metadata"]["fields"],
            "parser_decisions": payload["parser_decisions"],
            "raw_cells": payload["raw_cells"],
            "investment_lots": payload["investment_lots"],
            "company_summary": payload["company_summary"],
            "realized_lots": payload["realized_lots"],
            "reconciliation": payload["reconciliation"],
            "validation_issues": payload["validation_issues"],
        }
        for sheet in EXCEL_SHEETS:
            _records_to_df(sheet_data.get(sheet, [])).to_excel(writer, sheet_name=sheet, index=False)

    paths["excel"] = excel_path
    return paths
