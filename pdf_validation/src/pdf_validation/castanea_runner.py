"""Scanned FS OCR compatibility entrypoint — thin wrapper over unified pipeline.

Fund/template paths come from CLI args or vendor_mapping_registry.json.
No fund names or PDF-specific semantics live here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pdf_validation.pipeline import load_config, run_extract
from pdf_validation.vendor_comparison import compare_with_vendor


VENDOR_DATE_MAP = {
    "2024-12-31": "31-Dec-24",
    "2025-03-31": "31-Mar-25",
    "2025-06-30": "30-Jun-25",
    "2025-09-30": "30-Sep-25",
    "2025-12-31": "31-Dec-25",
    "2026-03-31": "31-Mar-26",
}


def run_scanned_ocr_case(
    *,
    pdf_path: Path,
    config_path: Path,
    mapping_path: Path,
    vendor_csv: Path,
    output_dir: Path,
    role: str,
    as_of_expected: str | None = None,
) -> dict[str, Any]:
    """Unified extract + shared vendor compare for any scanned-FS template/mapping pair."""
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = Path(config_path)
    mapping_path = Path(mapping_path)
    vendor_csv = Path(vendor_csv)

    config = load_config(config_path)
    config["requires_ocr"] = True
    if as_of_expected:
        config.setdefault("document", {})["as_of_date_expected"] = as_of_expected
    runtime_config = output_dir / "runtime_config.json"
    runtime_config.write_text(json.dumps(config, indent=2), encoding="utf-8")

    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    if as_of_expected:
        mapping["comparability"]["as_of_date"]["pdf_normalized_expected"] = as_of_expected
        mapping["comparability"]["as_of_date"]["vendor_raw_expected"] = VENDOR_DATE_MAP.get(as_of_expected)
    mapping["extraction_mode"] = mapping.get("extraction_mode") or "scanned_financial_statements"
    runtime_mapping = output_dir / "runtime_mapping.json"
    runtime_mapping.write_text(json.dumps(mapping, indent=2), encoding="utf-8")

    extract_dir = output_dir / "extract"
    payload = run_extract(
        pdf_path=pdf_path,
        config_path=runtime_config,
        output_dir=extract_dir,
        repo_root=Path(__file__).resolve().parents[3],
        cli_args={
            "command": "scanned-ocr",
            "role": role,
            "as_of_expected": as_of_expected,
            "pdf": str(pdf_path),
        },
        auto_template=False,
    )

    compare_dir = output_dir / "vendor_comparison"
    report = compare_with_vendor(
        extraction_dir=extract_dir,
        vendor_csv=vendor_csv,
        mapping_config=runtime_mapping,
        output_dir=compare_dir,
        repo_root=Path(__file__).resolve().parents[3],
    )

    overall = report.get("overall_status")
    if overall is None:
        overall = (
            "PASS"
            if report.get("comparability_status") == "comparable"
            and report["summary"].get("amount_mismatch_count", 0) == 0
            else "REVIEW_REQUIRED"
        )

    (output_dir / "comparison_report.json").write_text(
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )
    (output_dir / "alarm_summary.json").write_text(
        json.dumps({"overall_status": overall, "alarms": report.get("alarms") or []}, indent=2, default=str),
        encoding="utf-8",
    )
    case = {
        "role": role,
        "overall_status": overall,
        "summary": report.get("summary") or {},
        "alarms": report.get("alarms") or [],
        "comparisons": report.get("amount_comparisons") or [],
        "extraction_quality": payload.get("extraction_quality"),
        "scan_detection": payload.get("scan_detection"),
        "pdf_as_of": (report.get("summary") or {}).get("pdf_as_of_date"),
        "schedule_pages": (payload.get("ocr_artifact") or {}).get("schedule_pages"),
        "export_paths": {
            "output_dir": str(output_dir),
            "extract_dir": str(extract_dir),
            "comparison_report": str(output_dir / "comparison_report.json"),
            "alarm_summary": str(output_dir / "alarm_summary.json"),
            "vendor_comparison": str(compare_dir),
            **(payload.get("export_paths") or {}),
            **(report.get("export_paths") or {}),
        },
        "route": payload.get("route"),
        "selected_parser": payload.get("selected_parser"),
        "adapter_kind": "ocr",
        "template_family": (payload.get("route") or {}).get("template_family") or config.get("template_family"),
    }
    (output_dir / "case_summary.json").write_text(json.dumps(case, indent=2, default=str), encoding="utf-8")
    return case


# Backwards-compatible alias used by castanea-ocr CLI.
def run_castanea_case(**kwargs: Any) -> dict[str, Any]:
    return run_scanned_ocr_case(**kwargs)


compare_castanea_to_vendor = None  # deprecated; use compare_with_vendor
