"""Pull PDF-vs-CSV mismatches for funds that already have an approved template.

This is a thin wrapper around the existing pdf_validation package (extract +
compare). Nothing under pdf_validation/src/ is modified. Only funds with an
approved entry in configs/vendor_mapping_registry.json are attempted — a fund
with no template is reported as "skipped, needs a template" rather than
guessed at.

How ops uses this: drop the new quarter's PDF for a fund into
    cleaning_pipeline/incoming_pdfs/<fund_id>/
and the next pipeline run will extract it, compare it against the current
vendor CSV, and list any mismatches in the ops workbook. An empty or missing
incoming_pdfs/<fund_id>/ is not an error — it just means nothing new to check
for that fund this quarter.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from config import (
    DATA_CSV,
    INCOMING_PDFS_DIR,
    PDF_VALIDATION_ROOT,
    PDF_VALIDATION_SRC,
    VENDOR_MAPPING_REGISTRY,
)

if str(PDF_VALIDATION_SRC) not in sys.path:
    sys.path.insert(0, str(PDF_VALIDATION_SRC))


def approved_fund_ids() -> list[str]:
    if not VENDOR_MAPPING_REGISTRY.exists():
        return []
    registry = json.loads(VENDOR_MAPPING_REGISTRY.read_text(encoding="utf-8"))
    return sorted((registry.get("by_fund_id") or {}).keys())


def _template_config_for_family(family: str) -> Path | None:
    from pdf_validation.document_router import load_registry

    registry = load_registry()
    fam = (registry.get("template_families") or {}).get(family) or {}
    base = fam.get("base_config")
    if not base:
        return None
    path = Path(base)
    if not path.is_absolute():
        path = PDF_VALIDATION_ROOT / base
    return path if path.exists() else None


def _archive_pdf(pdf: Path, fund_dir: Path) -> None:
    processed_dir = fund_dir / "_processed"
    processed_dir.mkdir(exist_ok=True)
    dest = processed_dir / pdf.name
    if dest.exists():
        dest = processed_dir / f"{pdf.stem}_{pdf.stat().st_mtime_ns}{pdf.suffix}"
    try:
        pdf.rename(dest)
    except OSError:
        pass  # non-fatal: some mounted filesystems restrict this; leave the file in place


def check_fund(fund_id: str, out_root: Path) -> dict[str, Any]:
    """Run extract+compare for every PDF staged under incoming_pdfs/<fund_id>/.

    Successfully-checked PDFs are moved into incoming_pdfs/<fund_id>/_processed/
    afterward (not deleted) so the same file is not re-checked on the next run,
    while still leaving an audit trail of what was already looked at.
    """
    from pdf_validation.castanea_runner import run_scanned_ocr_case
    from pdf_validation.mapping_registry import load_vendor_mapping_registry, resolve_mapping_path_for_fund
    from pdf_validation.pipeline import run_extract
    from pdf_validation.vendor_comparison import compare_with_vendor

    fund_dir = INCOMING_PDFS_DIR / fund_id
    pdfs = sorted(p for p in fund_dir.glob("*.pdf") if p.is_file()) if fund_dir.is_dir() else []
    if not pdfs:
        return {"fund_id": fund_id, "status": "no_new_pdf", "pdfs": [], "mismatches": []}

    registry = load_vendor_mapping_registry()
    entry = (registry.get("by_fund_id") or {}).get(fund_id) or {}
    mapping_path = resolve_mapping_path_for_fund(fund_id, registry=registry, pkg_root=PDF_VALIDATION_ROOT)
    if mapping_path is None:
        return {
            "fund_id": fund_id,
            "status": "no_approved_mapping",
            "pdfs": [p.name for p in pdfs],
            "mismatches": [],
        }

    extraction_mode = entry.get("extraction_mode", "position_level")
    template_family = entry.get("template_family")
    results: list[dict[str, Any]] = []
    all_mismatches: list[dict[str, Any]] = []

    for pdf in pdfs:
        pdf_out = out_root / fund_id / pdf.stem
        pdf_out.mkdir(parents=True, exist_ok=True)

        try:
            if extraction_mode == "scanned_financial_statements":
                config_path = _template_config_for_family(template_family) if template_family else None
                res = run_scanned_ocr_case(
                    pdf_path=pdf.resolve(),
                    config_path=(config_path or Path()).resolve(),
                    mapping_path=mapping_path.resolve(),
                    vendor_csv=DATA_CSV.resolve(),
                    output_dir=pdf_out,
                    role="validate",
                    as_of_expected=None,
                )
                status = res.get("overall_status")
                mismatches = [a for a in (res.get("amount_comparisons") or []) if a.get("status") == "mismatch"]
            else:
                payload = run_extract(
                    pdf_path=pdf.resolve(),
                    config_path=None,
                    output_dir=pdf_out / "extract",
                    repo_root=PDF_VALIDATION_ROOT.parent,
                    cli_args={"command": "quarterly_refresh", "pdf": str(pdf)},
                    auto_template=True,
                )
                report = compare_with_vendor(
                    extraction_dir=pdf_out / "extract",
                    vendor_csv=DATA_CSV,
                    mapping_config=mapping_path,
                    output_dir=pdf_out / "compare",
                    repo_root=PDF_VALIDATION_ROOT.parent,
                )
                status = report.get("comparability_status")
                mismatches = [
                    a for a in (report.get("amount_comparisons") or []) if a.get("status") == "mismatch"
                ]

            for m in mismatches:
                m2 = dict(m)
                m2["fund_id"] = fund_id
                m2["pdf_name"] = pdf.name
                all_mismatches.append(m2)

            results.append({"pdf": pdf.name, "status": status, "mismatch_count": len(mismatches)})
            _archive_pdf(pdf, fund_dir)
        except Exception as exc:  # noqa: BLE001
            # Left in place (not archived) so a failed check can be retried next run.
            results.append({"pdf": pdf.name, "status": "error", "error": f"{type(exc).__name__}: {exc}"})

    return {"fund_id": fund_id, "status": "checked", "pdfs": results, "mismatches": all_mismatches}


def run_pdf_checks(out_root: Path) -> dict[str, Any]:
    """Check every fund with an approved mapping; funds with nothing staged are cheap no-ops."""
    fund_ids = approved_fund_ids()
    per_fund = [check_fund(fid, out_root) for fid in fund_ids]
    all_mismatches: list[dict[str, Any]] = []
    for f in per_fund:
        all_mismatches.extend(f.get("mismatches") or [])
    return {
        "approved_funds": fund_ids,
        "per_fund": per_fund,
        "total_mismatches": len(all_mismatches),
        "mismatches": all_mismatches,
    }
