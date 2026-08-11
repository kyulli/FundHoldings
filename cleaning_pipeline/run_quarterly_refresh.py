#!/usr/bin/env python3
"""Quarterly cleaning/validation pipeline. One command, one Excel output.

What it does, in order:
  1. Re-runs the Phase 1 notebook end to end on whatever CSV currently sits at
     FundHoldings/data/holdings_anonymized.csv, regenerating every file in
     reports/. This is the same analysis that has been reviewed and used all
     along -- this step does not reimplement it, it just re-executes it
     non-interactively.
  2. Compares the fresh reports/ against the last saved snapshot to find
     what's newly wrong, what's still wrong, and what got fixed.
  3. For any fund with an approved PDF template (see
     pdf_validation/configs/vendor_mapping_registry.json) that has a new PDF
     staged under cleaning_pipeline/incoming_pdfs/<fund_id>/, extracts it and
     compares it against the CSV, folding any mismatches into the same report.
  4. Writes one Excel workbook to cleaning_pipeline/runs/<timestamp>/ops_report.xlsx.
  5. Saves this run's reports/ as the new baseline snapshot, so the next run
     diffs against today instead of against today's baseline forever.

Usage (from FundHoldings/cleaning_pipeline/):
    python run_quarterly_refresh.py
    python run_quarterly_refresh.py --skip-pdf-check
    python run_quarterly_refresh.py --skip-snapshot   # dry run, don't move the baseline forward
"""

from __future__ import annotations

import argparse
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from config import DATA_CSV, NOTEBOOK_PATH, REPORTS_DIR, RUNS_DIR, read_notebook_thresholds  # noqa: E402
from build_ops_report import build_report  # noqa: E402
from pdf_check import run_pdf_checks  # noqa: E402
from snapshot_utils import latest_snapshot, load_manifest, run_all_diffs, take_snapshot  # noqa: E402


class _CsvSwap:
    """Temporarily point data/holdings_anonymized.csv at a different file.

    Used by --csv for testing against a partitioned/historical extract without
    touching the real file by hand. The real file is always restored, even if
    the run fails partway through.
    """

    def __init__(self, replacement: Path | None):
        self.replacement = replacement
        self._backup: Path | None = None

    def __enter__(self) -> None:
        if self.replacement is None:
            return
        if not self.replacement.exists():
            raise SystemExit(f"--csv file not found: {self.replacement}")
        if DATA_CSV.exists():
            self._backup = DATA_CSV.with_suffix(".csv.pipeline_swap_backup")
            shutil.copy2(DATA_CSV, self._backup)
        shutil.copy2(self.replacement, DATA_CSV)
        print(f"      using --csv override: {self.replacement.name} "
              f"({'real file backed up, will restore after run' if self._backup else 'no prior file to restore'})")

    def __exit__(self, *exc_info) -> None:
        if self.replacement is None:
            return
        if self._backup is not None:
            shutil.copy2(self._backup, DATA_CSV)
            try:
                self._backup.unlink()
            except OSError:
                pass  # harmless leftover on filesystems that restrict unlink
            print(f"      restored real {DATA_CSV.name} after --csv test run")


def execute_notebook() -> None:
    import nbformat
    from nbclient import NotebookClient

    print(f"[1/5] Re-running notebook: {NOTEBOOK_PATH.name}")
    if not NOTEBOOK_PATH.exists():
        raise SystemExit(f"Notebook not found: {NOTEBOOK_PATH}")
    if not DATA_CSV.exists():
        raise SystemExit(
            f"Vendor CSV not found at {DATA_CSV}\n"
            "Replace it with the new quarterly extract before running this pipeline."
        )

    nb = nbformat.read(NOTEBOOK_PATH, as_version=4)
    client = NotebookClient(nb, timeout=1200, kernel_name="python3")
    client.execute(cwd=str(NOTEBOOK_PATH.parent))
    nbformat.write(nb, NOTEBOOK_PATH)
    print("      done — reports/ regenerated.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-pdf-check", action="store_true", help="Skip the PDF spot-check step")
    ap.add_argument("--skip-notebook", action="store_true",
                     help="Skip re-running the notebook; diff/report against reports/ as-is (for testing)")
    ap.add_argument("--skip-snapshot", action="store_true",
                     help="Do not save this run as the new baseline (dry run)")
    ap.add_argument("--csv", type=Path, default=None,
                     help="Run against this CSV instead of data/holdings_anonymized.csv "
                          "(e.g. a partitioned test file). The real file is swapped back in "
                          "automatically once the run finishes.")
    args = ap.parse_args()

    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Quarterly cleaning pipeline — run {run_id}")
    print(f"Vendor CSV: {DATA_CSV}")

    # Everything stays inside the swap context, including the snapshot step:
    # take_snapshot() reads DATA_CSV's own stats (row count, max As At Date) into
    # the manifest, so it must run before the real file is restored, or a
    # --csv baseline would end up mislabeled with the real file's stats.
    with _CsvSwap(args.csv):
        if not args.skip_notebook:
            try:
                execute_notebook()
            except Exception as exc:  # noqa: BLE001
                print(f"\nNOTEBOOK EXECUTION FAILED: {type(exc).__name__}: {exc}")
                traceback.print_exc(limit=6)
                print("\nStopping here — reports/ may be in a partial state. Fix the notebook error and re-run.")
                return 1
        else:
            print("[1/5] Skipped notebook re-run (--skip-notebook)")

        print("[2/5] Loading baseline snapshot for comparison")
        baseline_dir = latest_snapshot()
        if baseline_dir:
            manifest = load_manifest(baseline_dir)
            print(f"      baseline: {baseline_dir.name}  (taken {manifest.get('taken_at_utc', '?')})")
        else:
            print("      no baseline found — this run establishes the first one; nothing to diff against yet.")

        thresholds = read_notebook_thresholds()
        fill_threshold = thresholds["fill_threshold"]

        print("[3/5] Diffing current results against baseline")
        diffs = run_all_diffs(REPORTS_DIR, baseline_dir, fill_threshold)
        if diffs.get("flagged_diff"):
            d = diffs["flagged_diff"]
            print(f"      completeness issues: {d.n_new} new, {d.n_resolved} resolved, {d.n_persisting} still open")
        if diffs.get("deal_status_diff"):
            d = diffs["deal_status_diff"]
            print(f"      Deal Status exceptions: {d.n_new} new, {d.n_resolved} resolved")

        if args.skip_pdf_check:
            print("[4/5] Skipped PDF spot-check (--skip-pdf-check)")
            pdf_results = {"approved_funds": [], "per_fund": [], "total_mismatches": 0, "mismatches": []}
        else:
            print("[4/5] Checking PDFs staged under incoming_pdfs/ for funds with an approved template")
            pdf_results = run_pdf_checks(out_root=run_dir / "pdf_checks")
            checked = [f for f in pdf_results["per_fund"] if f["status"] == "checked"]
            print(f"      {len(checked)} fund(s) checked, {pdf_results['total_mismatches']} mismatch(es) found")
            for f in pdf_results["per_fund"]:
                if f["status"] == "no_new_pdf":
                    print(f"      {f['fund_id']}: no new PDF staged, skipped")
                elif f["status"] == "no_approved_mapping":
                    print(f"      {f['fund_id']}: PDF staged but no approved template yet, skipped")

        print("[5/5] Building ops workbook")
        out_path = build_report(run_dir / "ops_report.xlsx", diffs, pdf_results, fill_threshold)
        print(f"      wrote {out_path}")

        if not args.skip_snapshot:
            label = f"csv_{args.csv.stem}" if args.csv else None
            new_snapshot = take_snapshot(label=label)
            print(f"\nSaved this run as the new baseline: {new_snapshot.name}")
        else:
            print("\n--skip-snapshot set: baseline NOT moved forward (this run repeats next time too).")

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"\nDone in {elapsed/60:.1f} min.")
    print(f"Open this file: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
