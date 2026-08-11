"""Shared paths and thresholds for the quarterly cleaning pipeline.

Nothing in this file duplicates scoring logic. FILL_THRESHOLD and
MIN_ROWS_PER_GROUP are read straight out of the Phase 1 notebook's own
parameter cell at runtime (see read_notebook_thresholds below) so the two
can never quietly drift apart. If someone changes the threshold in the
notebook, this pipeline picks it up automatically on the next run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# --- directory layout ------------------------------------------------------
PKG_ROOT = Path(__file__).resolve().parent          # FundHoldings/cleaning_pipeline
REPO_ROOT = PKG_ROOT.parent                          # FundHoldings/

NOTEBOOK_PATH = REPO_ROOT / "notebooks" / "phase1_holdings_data_state_analysis.ipynb"
DATA_CSV = REPO_ROOT / "data" / "holdings_anonymized.csv"
REPORTS_DIR = REPO_ROOT / "reports"

SNAPSHOTS_DIR = PKG_ROOT / "snapshots"
RUNS_DIR = PKG_ROOT / "runs"
INCOMING_PDFS_DIR = PKG_ROOT / "incoming_pdfs"

PDF_VALIDATION_ROOT = REPO_ROOT / "pdf_validation"
PDF_VALIDATION_SRC = PDF_VALIDATION_ROOT / "src"
VENDOR_MAPPING_REGISTRY = PDF_VALIDATION_ROOT / "configs" / "vendor_mapping_registry.json"

# --- which report files the diff/export stages track -----------------------
# key -> filename under reports/. Add a line here if a new report should be
# tracked by the ops workbook; nothing else needs to change.
TRACKED_REPORTS: dict[str, str] = {
    "by_field": "data_state_by_field.csv",
    "by_fund_conditional": "data_state_by_fund_conditional.csv",
    "by_manager_conditional": "data_state_by_manager_conditional.csv",
    "flagged_conditional": "flagged_missing_fields_conditional.csv",
    "deal_status_exceptions": "deal_status_conformance_exceptions.csv",
    "consistency_rules": "consistency_rule_results.csv",
    "reporting_gaps": "reporting_gaps_by_series.csv",
}


def read_notebook_thresholds() -> dict[str, Any]:
    """Pull FILL_THRESHOLD / MIN_ROWS_PER_GROUP out of the notebook's params cell."""
    nb = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    for cell in nb["cells"]:
        if cell.get("id") == "code-imports-params":
            src = "".join(cell["source"])
            fill = re.search(r"FILL_THRESHOLD\s*=\s*([0-9.]+)", src)
            min_rows = re.search(r"MIN_ROWS_PER_GROUP\s*=\s*(\d+)", src)
            return {
                "fill_threshold": float(fill.group(1)) if fill else 0.85,
                "min_rows_per_group": int(min_rows.group(1)) if min_rows else 10,
            }
    return {"fill_threshold": 0.85, "min_rows_per_group": 10}
