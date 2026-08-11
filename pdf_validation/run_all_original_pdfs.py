#!/usr/bin/env python3
"""Survey runner for every PDF under original_pdf_files/.

This is a WRAPPER around the existing pipeline. It does not modify any module
under src/pdf_validation/. Two known integration gaps are handled here at
runtime so the shared code stays untouched and merge-safe:

  1. batch_runner.CSV_PATH points at FundHoldings/holdings_anonymized.csv,
     but the vendor extract actually lives at FundHoldings/data/holdings_anonymized.csv.
     Patched in-process (same bug as tests/test_syn_vendor_comparison.py line 11).

  2. batch_runner derives the Fund Allocator ID from the directory name
     (fund_id_dir = pdf.parent.name). Four directories carry annotations that
     are not part of the ID:

         A103ce5(OCR)            -> A103ce5
         A2234f1_(mgr_A12c595)   -> A2234f1
         A6fcde0_(mgr_A6f625f)   -> A6fcde0
         A9ffbf3_(mgr_Ad7e703)   -> A9ffbf3

     A staging tree of symlinks with cleaned directory names is built so the ID
     resolves against vendor_mapping_registry.json and the vendor CSV. The
     original folders are never renamed or written to.

A103ce5 is the only fund with an approved scanned/OCR mapping in the registry,
so it is additionally run through the dedicated `scanned-ocr` entry point, which
emits the full vendor amount-field matrix and alarms that the batch path skips.

Usage
-----
    python run_all_original_pdfs.py                    # everything
    python run_all_original_pdfs.py --dry-run          # plan only, no extraction
    python run_all_original_pdfs.py --funds A0d2a71 A103ce5
    python run_all_original_pdfs.py --limit 2          # first 2 PDFs per fund
    python run_all_original_pdfs.py --skip-ocr         # native funds only
    python run_all_original_pdfs.py --ocr-only         # A103ce5 scanned path only
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

REPO_ROOT = HERE.parent
PDF_ROOT = HERE / "original_pdf_files"
VENDOR_CSV = REPO_ROOT / "data" / "holdings_anonymized.csv"

RUN_ROOT = HERE / "outputs" / "original_pdf_survey"
BATCH_OUT = RUN_ROOT / "batch"
OCR_OUT = RUN_ROOT / "ocr"
REPORT_DIR = RUN_ROOT / "reports"

# The staging tree is scratch: symlinks only, rebuilt each run. It lives in the
# system temp dir rather than under outputs/ because some mounted/synced volumes
# refuse unlink() on symlinks, which would break re-runs.
STAGING: Path = Path(tempfile.gettempdir()) / "pdf_survey_staging"

# Directory-name annotations that are not part of the Fund Allocator ID.
FUND_ID_RE = re.compile(r"^(A[0-9A-Za-z]+?)(?:\s*\(|_\(|$)")


def clean_fund_id(dirname: str) -> str:
    """A103ce5(OCR) -> A103ce5 ; A2234f1_(mgr_A12c595) -> A2234f1."""
    match = FUND_ID_RE.match(dirname)
    return match.group(1) if match else dirname


def build_staging_tree(funds: list[str] | None, limit: int | None) -> list[dict[str, Any]]:
    """Symlink every PDF into a tree whose directory names are clean fund IDs."""
    global STAGING
    shutil.rmtree(STAGING, ignore_errors=True)
    if STAGING.exists():
        # Stale tree that refused deletion; use a fresh unique directory instead.
        STAGING = Path(tempfile.mkdtemp(prefix="pdf_survey_staging_"))
    STAGING.mkdir(parents=True, exist_ok=True)

    plan: list[dict[str, Any]] = []
    for fund_dir in sorted(PDF_ROOT.iterdir()):
        if not fund_dir.is_dir():
            continue
        fund_id = clean_fund_id(fund_dir.name)
        if funds and fund_id not in funds:
            continue

        pdfs = sorted(p for p in fund_dir.glob("*.pdf") if not p.name.startswith("."))
        if limit:
            pdfs = pdfs[:limit]
        if not pdfs:
            continue

        target_dir = STAGING / fund_id
        target_dir.mkdir(parents=True, exist_ok=True)

        for pdf in pdfs:
            # Spaces and commas in filenames are fine for the pipeline, but the
            # staged name is normalised so output directory stems stay readable.
            safe_name = re.sub(r"[^\w.\-]+", "_", pdf.name)
            link = target_dir / safe_name
            if link.exists() or link.is_symlink():
                link.unlink()
            os.symlink(pdf.resolve(), link)
            plan.append(
                {
                    "fund_id": fund_id,
                    "source_dir": fund_dir.name,
                    "pdf_name": pdf.name,
                    "staged_path": str(link),
                    "size_kb": round(pdf.stat().st_size / 1024, 1),
                }
            )
    return plan


def patch_batch_runner() -> dict[str, str]:
    """Point the shared batch runner at the real vendor CSV without editing it."""
    from pdf_validation import batch_runner

    before = str(batch_runner.CSV_PATH)
    if not VENDOR_CSV.exists():
        raise SystemExit(f"Vendor CSV not found: {VENDOR_CSV}")
    batch_runner.CSV_PATH = VENDOR_CSV
    return {"csv_path_before": before, "csv_path_after": str(VENDOR_CSV)}


def run_survey() -> dict[str, Any]:
    from pdf_validation.batch_runner import run_batch

    BATCH_OUT.mkdir(parents=True, exist_ok=True)
    return run_batch(sample_root=STAGING, out_root=BATCH_OUT)


def run_ocr_case(pdf: Path, role: str, as_of: str | None) -> dict[str, Any]:
    """Dedicated scanned-OCR entry point for the one fund with an approved mapping."""
    from pdf_validation.castanea_runner import run_scanned_ocr_case
    from pdf_validation.mapping_registry import (
        load_vendor_mapping_registry,
        resolve_mapping_path_for_fund,
    )

    registry = load_vendor_mapping_registry()
    entry = (registry.get("by_fund_id") or {}).get("A103ce5") or {}
    family = entry.get("template_family")
    fam_cfg = (
        (json.loads((HERE / "configs" / "template_registry.json").read_text()).get("template_families") or {})
        .get(family, {})
        .get("base_config")
    )
    config = HERE / fam_cfg if fam_cfg else HERE / "configs" / "castanea_fund_iii_balance_sheet.json"
    mapping = resolve_mapping_path_for_fund("A103ce5", registry=registry, pkg_root=HERE)
    if mapping is None:
        raise SystemExit("A103ce5 has no approved mapping in vendor_mapping_registry.json")

    out_dir = OCR_OUT / re.sub(r"[^\w.\-]+", "_", pdf.stem)[:60]
    out_dir.mkdir(parents=True, exist_ok=True)

    return run_scanned_ocr_case(
        pdf_path=pdf.resolve(),
        config_path=Path(config).resolve(),
        mapping_path=Path(mapping).resolve(),
        vendor_csv=VENDOR_CSV.resolve(),
        output_dir=out_dir,
        role=role,
        as_of_expected=as_of,
    )


def write_summary(report: dict[str, Any], plan: list[dict[str, Any]]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results = report.get("results") or []

    csv_path = REPORT_DIR / "survey_by_pdf.csv"
    cols = [
        "fund_id",
        "pdf",
        "as_of",
        "document_class",
        "template_family",
        "extraction_mode",
        "selected_parser",
        "position_count",
        "comparability_status",
        "match",
        "mismatch",
        "blocked_reason",
        "status",
        "error",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for row in results:
            row = dict(row)
            row["pdf"] = Path(row.get("pdf", "")).name
            writer.writerow(row)

    by_fund: dict[str, dict[str, Any]] = {}
    for row in results:
        fund = row.get("fund_id", "?")
        agg = by_fund.setdefault(
            fund,
            {"pdfs": 0, "ran": 0, "failed": 0, "comparable": 0, "match": 0, "mismatch": 0, "modes": set()},
        )
        agg["pdfs"] += 1
        if row.get("status") == "failed":
            agg["failed"] += 1
        else:
            agg["ran"] += 1
        if row.get("comparability_status") in {"comparable", "aggregate_only_comparable"}:
            agg["comparable"] += 1
        agg["match"] += int(row.get("match") or 0)
        agg["mismatch"] += int(row.get("mismatch") or 0)
        if row.get("extraction_mode"):
            agg["modes"].add(row["extraction_mode"])

    fund_csv = REPORT_DIR / "survey_by_fund.csv"
    with fund_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["fund_id", "pdfs", "ran", "failed", "comparable", "amount_match", "amount_mismatch", "extraction_modes"]
        )
        for fund in sorted(by_fund):
            a = by_fund[fund]
            writer.writerow(
                [fund, a["pdfs"], a["ran"], a["failed"], a["comparable"], a["match"], a["mismatch"],
                 "|".join(sorted(a["modes"]))]
            )

    print("\n" + "=" * 78)
    print("SURVEY SUMMARY")
    print("=" * 78)
    print(f"{'fund':12} {'pdfs':>5} {'ran':>5} {'fail':>5} {'cmp':>5} {'match':>7} {'mism':>6}  modes")
    print("-" * 78)
    for fund in sorted(by_fund):
        a = by_fund[fund]
        print(
            f"{fund:12} {a['pdfs']:>5} {a['ran']:>5} {a['failed']:>5} {a['comparable']:>5} "
            f"{a['match']:>7} {a['mismatch']:>6}  {'|'.join(sorted(a['modes']))[:34]}"
        )
    total_pdfs = sum(a["pdfs"] for a in by_fund.values())
    total_fail = sum(a["failed"] for a in by_fund.values())
    total_mis = sum(a["mismatch"] for a in by_fund.values())
    print("-" * 78)
    print(f"{'TOTAL':12} {total_pdfs:>5} {total_pdfs - total_fail:>5} {total_fail:>5}"
          f"{'':>6} {sum(a['match'] for a in by_fund.values()):>7} {total_mis:>6}")
    print(f"\nPer-PDF detail : {csv_path}")
    print(f"Per-fund rollup: {fund_csv}")
    return csv_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--funds", nargs="*", help="Restrict to these cleaned fund IDs")
    ap.add_argument("--limit", type=int, help="Max PDFs per fund (smoke test)")
    ap.add_argument("--dry-run", action="store_true", help="Show the plan, do not extract")
    ap.add_argument("--skip-ocr", action="store_true", help="Skip the dedicated A103ce5 scanned-OCR pass")
    ap.add_argument("--ocr-only", action="store_true", help="Only run the A103ce5 scanned-OCR pass")
    ap.add_argument("--ocr-role", default="validate", choices=["train", "test", "validate"])
    args = ap.parse_args()

    if not PDF_ROOT.is_dir():
        raise SystemExit(f"Not found: {PDF_ROOT}")

    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)

    print(f"PDF root   : {PDF_ROOT}")
    print(f"Vendor CSV : {VENDOR_CSV}  (exists={VENDOR_CSV.exists()})")
    print(f"Output     : {RUN_ROOT}")

    plan = build_staging_tree(args.funds, args.limit)
    funds_in_plan = sorted({p["fund_id"] for p in plan})
    print(f"\nStaged {len(plan)} PDFs across {len(funds_in_plan)} funds.")
    print(f"Staging tree: {STAGING}")

    renamed = [p for p in plan if p["fund_id"] != p["source_dir"]]
    if renamed:
        pairs = sorted({(p["source_dir"], p["fund_id"]) for p in renamed})
        print("\nFund IDs cleaned for registry/vendor lookup:")
        for src, dst in pairs:
            print(f"  {src:26} -> {dst}")

    (REPORT_DIR).mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "staging_plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")

    if args.dry_run:
        print("\n--dry-run: stopping before extraction.")
        print(f"Plan written to {REPORT_DIR / 'staging_plan.json'}")
        return 0

    patch_info = patch_batch_runner()
    print(f"\nPatched batch_runner.CSV_PATH\n  from {patch_info['csv_path_before']}\n  to   {patch_info['csv_path_after']}")

    report: dict[str, Any] = {"results": []}
    if not args.ocr_only:
        print("\n" + "=" * 78)
        print("BATCH SURVEY (route -> extract -> compare)")
        print("=" * 78)
        report = run_survey()
        write_summary(report, plan)

    ocr_results: list[dict[str, Any]] = []
    run_ocr = ("A103ce5" in funds_in_plan or args.ocr_only) and not args.skip_ocr
    if run_ocr:
        ocr_dir = PDF_ROOT / "A103ce5(OCR)"
        pdfs = sorted(ocr_dir.glob("*.pdf")) if ocr_dir.is_dir() else []
        if args.limit:
            pdfs = pdfs[: args.limit]
        if pdfs:
            print("\n" + "=" * 78)
            print(f"DEDICATED SCANNED-OCR PASS  (A103ce5, role={args.ocr_role})")
            print("=" * 78)
            for pdf in pdfs:
                print(f"\n--- {pdf.name} ---")
                try:
                    res = run_ocr_case(pdf, args.ocr_role, as_of=None)
                    summary = {
                        "pdf": pdf.name,
                        "overall_status": res.get("overall_status"),
                        "comparability_status": res.get("comparability_status"),
                        "alarms": len(res.get("alarms") or []),
                    }
                    print(json.dumps(summary, indent=2, default=str))
                    for alarm in (res.get("alarms") or [])[:10]:
                        print("  ALARM:", json.dumps(alarm, ensure_ascii=False, default=str)[:300])
                    ocr_results.append({**summary, "ok": True})
                except Exception as exc:  # noqa: BLE001
                    print(f"  FAILED: {type(exc).__name__}: {exc}")
                    traceback.print_exc(limit=3)
                    ocr_results.append({"pdf": pdf.name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})

            (REPORT_DIR / "ocr_results.json").write_text(
                json.dumps(ocr_results, indent=2, default=str), encoding="utf-8"
            )
            print(f"\nOCR detail written to {REPORT_DIR / 'ocr_results.json'}")

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    manifest = {
        "started_utc": started.isoformat(),
        "elapsed_sec": round(elapsed, 1),
        "pdfs_staged": len(plan),
        "funds": funds_in_plan,
        "vendor_csv": str(VENDOR_CSV),
        "batch_results": len(report.get("results") or []),
        "ocr_results": ocr_results,
        "patch": patch_info,
        "note": "No files under src/pdf_validation/ were modified; CSV path and fund-id "
                "normalisation are applied at runtime only.",
    }
    (REPORT_DIR / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\nDone in {elapsed/60:.1f} min. Manifest: {REPORT_DIR / 'run_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
