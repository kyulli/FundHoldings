"""Write human-reviewed extraction numbers to a new Excel workbook.

Never modifies the third-party vendor CSV/Excel.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from pdf_validation.discrepancy_review import build_discrepancy_items, load_discrepancy_decisions


COLUMNS = [
    "document_id",
    "fund_id",
    "as_of_date",
    "pdf_company_name",
    "vendor_source_asset",
    "logical_field",
    "pdf_field",
    "excel_field",
    "pdf_value",
    "excel_value",
    "final_value",
    "decision",
    "difference",
    "difference_pct",
    "tolerance",
    "pages",
    "evidence_status",
    "reviewer",
    "decided_at_utc",
    "notes",
]


def _load_route(extraction_dir: Path) -> dict[str, Any]:
    path = Path(extraction_dir) / "route.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def write_reviewed_extraction_excel(
    *,
    extraction_dir: Path,
    fund_id: str,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Materialize ``reviewed_extraction.xlsx`` from discrepancy items + decisions."""
    extraction_dir = Path(extraction_dir)
    route = _load_route(extraction_dir)
    items = build_discrepancy_items(extraction_dir=extraction_dir, locate_evidence=True)
    decisions = load_discrepancy_decisions(extraction_dir)
    out = Path(output_path) if output_path else (extraction_dir / "reviewed_extraction.xlsx")

    wb = Workbook()
    ws = wb.active
    ws.title = "reviewed_extraction"
    ws.append(COLUMNS)

    rows_written = 0
    for item in items:
        decision = item.get("user_decision") or decisions.get(item["item_id"]) or {}
        action = decision.get("action")
        if item.get("status") == "match" and not action:
            action = "auto_match"
        final_value = item.get("final_value")
        if action == "adopt_pdf":
            final_value = item.get("pdf_value")
        elif action == "adopt_excel":
            final_value = item.get("excel_value")
        elif action == "keep_real_discrepancy":
            final_value = decision.get("final_value")
        elif action == "pdf_evidence_unreliable":
            final_value = None
        elif action == "auto_match":
            final_value = item.get("pdf_value") if item.get("pdf_value") is not None else item.get("excel_value")

        evidence = item.get("evidence") or {}
        ws.append(
            [
                route.get("document_id"),
                fund_id,
                item.get("as_of_date") or route.get("as_of_date"),
                item.get("pdf_company_name"),
                item.get("vendor_source_asset"),
                item.get("logical_field"),
                item.get("pdf_field"),
                item.get("excel_field"),
                item.get("pdf_value"),
                item.get("excel_value"),
                final_value,
                action,
                item.get("difference"),
                item.get("difference_pct"),
                item.get("tolerance"),
                ",".join(str(p) for p in (item.get("pages") or [])),
                evidence.get("status"),
                decision.get("reviewer"),
                decision.get("decided_at_utc"),
                decision.get("notes"),
            ]
        )
        rows_written += 1

    wb.save(out)
    meta = {
        "status": "ok",
        "path": str(out),
        "rows": rows_written,
        "written_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_vendor_csv_untouched": True,
    }
    (extraction_dir / "mapping_review" / "reviewed_extraction_meta.json").parent.mkdir(parents=True, exist_ok=True)
    (extraction_dir / "mapping_review" / "reviewed_extraction_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return meta
