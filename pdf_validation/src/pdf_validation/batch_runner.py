"""Unified batch runner for sample_data using document router + auto template."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from pdf_validation.document_router import classify_sample_tree, load_registry, route_document
from pdf_validation.entity_mapping import build_entity_mappings
from pdf_validation.pipeline import run_extract
from pdf_validation.vendor_comparison import _parse_vendor_date, compare_with_vendor


ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "pdf_validation"
SAMPLE_ROOT = ROOT / "sample_data"
CSV_PATH = ROOT / "holdings_anonymized.csv"
OUT_ROOT = PKG / "outputs" / "generalized_batch"
CONFIG_DIR = PKG / "configs" / "generated"

VENDOR_DATE_MAP = {
    "2025-03-31": "31-Mar-25",
    "2025-06-30": "30-Jun-25",
    "2025-09-30": "30-Sep-25",
    "2025-12-31": "31-Dec-25",
    "2024-12-31": "31-Dec-24",
    "2026-03-31": "31-Mar-26",
}


def _base_mapping_for_fund(fund_id: str) -> dict[str, Any]:
    base_path = PKG / "configs" / "syn_ventures_fund_ii_q3_2025_vendor_mapping.json"
    mapping = json.loads(base_path.read_text(encoding="utf-8"))
    mapping["comparability"]["fund_identity"]["vendor_fund_id"] = fund_id
    mapping["entity_mappings"] = []
    return mapping


def _run_one(pdf: Path, fund_id: str) -> dict[str, Any]:
    route = route_document(pdf)
    as_of = route.get("as_of_date") or "unknown"
    stem = f"{fund_id}_{as_of}_{pdf.stem[:40].replace(' ', '_')}"
    out_dir = OUT_ROOT / stem
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = run_extract(
        pdf_path=pdf,
        config_path=None,
        output_dir=out_dir / "extract",
        repo_root=ROOT,
        cli_args={"command": "batch", "pdf": str(pdf), "auto_template": True},
        auto_template=True,
    )
    mode = (payload.get("route") or route).get("extraction_mode")
    result: dict[str, Any] = {
        "fund_id": fund_id,
        "pdf": str(pdf),
        "as_of": as_of,
        "document_class": route.get("document_class"),
        "template_family": route.get("template_family"),
        "extraction_mode": mode,
        "selected_parser": payload.get("selected_parser"),
        "position_count": len(payload.get("company_summary") or []),
        "status": "ran",
    }

    if mode in {"blocked_narrative", "manual_review", "position_level_inferred"}:
        result["comparability_status"] = "not_comparable" if mode != "position_level_inferred" else "needs_review"
        result["match"] = 0
        result["mismatch"] = 0
        result["blocked_reason"] = mode
        result["onboarding_status"] = (payload.get("onboarding_summary") or {}).get("onboarding_status")
        result["compare_allowed"] = False
        if mode == "position_level_inferred":
            result["inferred_schema"] = (payload.get("route") or {}).get("inferred_schema")
            result["status"] = "discover_only"
        return result

    mapping = _base_mapping_for_fund(fund_id)
    mapping["mapping_id"] = f"{stem}_vendor"
    mapping["extraction_mode"] = mode
    if as_of and as_of != "unknown":
        mapping["comparability"]["as_of_date"]["pdf_normalized_expected"] = as_of
        mapping["comparability"]["as_of_date"]["vendor_raw_expected"] = VENDOR_DATE_MAP.get(as_of)
    mapping["comparability"]["currency"]["waiver"]["enabled"] = True
    mapping["comparability"]["unit"]["waiver"]["enabled"] = True
    mapping["has_realized_schedule"] = "realized" in (route.get("schedules_detected") or [])
    if not mapping["has_realized_schedule"]:
        # Audited/simple schedules without realized pages cannot support realized metrics.
        skip_realized = {
            "realized_cost",
            "realized_proceeds",
            "realized_gain_loss",
            "capital_invested",
            "total_value",
            "deal_status",
        }
        mapping["amount_fields"] = [
            f for f in mapping.get("amount_fields", []) if f.get("logical_field") not in skip_realized
        ]
        for row in mapping.get("field_dictionary", []):
            if row.get("logical_field") in skip_realized:
                row["mapping_type"] = "not_applicable"
                row["notes"] = (row.get("notes") or "") + " Skipped: PDF has no realized schedule."

    if mode == "position_level":
        df = pd.read_csv(CSV_PATH, usecols=["Fund Allocator ID", "As At Date", "Source Asset"], low_memory=False)
        slice_df = df[df["Fund Allocator ID"].astype(str) == fund_id].copy()
        slice_df["_iso"] = slice_df["As At Date"].map(
            lambda v: _parse_vendor_date(v, ["%d-%b-%y", "%d-%b-%Y", "%Y-%m-%d"])
        )
        vendor_names = slice_df.loc[slice_df["_iso"] == as_of, "Source Asset"].dropna().astype(str).tolist()
        # Only map company/security grain rows into vendor compare.
        companies = [
            c["company_name"]
            for c in payload.get("company_summary") or []
            if c.get("company_name") and c.get("entity_grain", "company") in {"company", "security"}
        ]
        realized = sorted(
            {
                r["company_name"]
                for r in payload.get("realized_lots") or []
                if r.get("company_name")
            }
        )
        mapping["entity_mappings"] = build_entity_mappings(
            companies, realized, vendor_names, fund_id=fund_id
        )
        mapping["comparability"]["grain"]["pdf_comparison_grain"] = route.get("comparison_grain") or "company"

    map_path = CONFIG_DIR / f"{stem}_vendor_mapping.json"
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    map_path.write_text(json.dumps(mapping, indent=2), encoding="utf-8")

    report = compare_with_vendor(
        extraction_dir=out_dir / "extract",
        vendor_csv=CSV_PATH,
        mapping_config=map_path,
        output_dir=out_dir / "compare",
        repo_root=ROOT,
    )
    amounts = report.get("amount_comparisons") or []
    result.update(
        {
            "comparability_status": report.get("comparability_status"),
            "match": sum(1 for a in amounts if a.get("status") == "match"),
            "mismatch": sum(1 for a in amounts if a.get("status") == "mismatch"),
            "entities_mapped": len(mapping.get("entity_mappings") or []),
            "fund_aggregate": payload.get("fund_aggregate"),
            "mismatches": [
                {
                    "company": a.get("vendor_source_asset") or a.get("pdf_company_name"),
                    "field": a.get("logical_field"),
                    "pdf": a.get("pdf_value"),
                    "csv": a.get("csv_value"),
                    "diff": a.get("difference"),
                }
                for a in amounts
                if a.get("status") == "mismatch"
            ][:30],
        }
    )
    return result


def run_batch(sample_root: Path | None = None, out_root: Path | None = None) -> dict[str, Any]:
    sample_root = Path(sample_root or SAMPLE_ROOT)
    global OUT_ROOT
    if out_root:
        OUT_ROOT = Path(out_root)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    classification = classify_sample_tree(sample_root, load_registry())
    (OUT_ROOT / "classification_manifest.json").write_text(
        json.dumps(classification, indent=2), encoding="utf-8"
    )

    results: list[dict[str, Any]] = []
    for item in classification:
        pdf = Path(item["pdf"])
        fund_id = item["fund_id_dir"]
        print(f"\n=== {fund_id} :: {pdf.name} :: {item.get('extraction_mode')} ===")
        try:
            summary = _run_one(pdf, fund_id)
            print(
                json.dumps(
                    {k: summary[k] for k in summary if k not in {"mismatches", "fund_aggregate"}},
                    indent=2,
                )
            )
            if summary.get("mismatches"):
                print("MISMATCHES:", json.dumps(summary["mismatches"], indent=2))
            results.append(summary)
        except Exception as exc:  # noqa: BLE001
            err = {
                "fund_id": fund_id,
                "pdf": str(pdf),
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "document_class": item.get("document_class"),
                "extraction_mode": item.get("extraction_mode"),
            }
            print("FAILED", err)
            results.append(err)

    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    path = OUT_ROOT / "batch_summary.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\nWrote", path)
    return report


if __name__ == "__main__":
    run_batch()
