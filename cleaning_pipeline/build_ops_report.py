"""Build the single Excel workbook the operations team opens each quarter.

Sheets, in the order ops should read them:
  1. Summary                    - one page, plain language, top numbers
  2. New Issues This Quarter    - flagged rows not present in the last run
  3. Escalation List            - funds below threshold on both scores
  4. Deal Status Exceptions     - CSV-reported vs numbers-implied status
  5. Consistency Rule Results   - all 16 rules, with rate change vs last run
  6. PDF Mismatches             - only present if a PDF was staged this run
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill(start_color="1F3864", end_color="1F3864", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)
FLAG_FILL = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")


def _style_header(ws) -> None:
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"


def _autofit(ws, df: pd.DataFrame, max_width: int = 42) -> None:
    for i, col in enumerate(df.columns, start=1):
        sample = df[col].astype(str).head(200)
        width = max([len(str(col))] + [len(v) for v in sample]) + 2
        ws.column_dimensions[get_column_letter(i)].width = min(width, max_width)


def _write_sheet(writer, df: pd.DataFrame, sheet_name: str) -> None:
    df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
    ws = writer.sheets[sheet_name[:31]]
    _style_header(ws)
    _autofit(ws, df)


def _distinct_ids(df: pd.DataFrame | None, col: str = "Fund Allocator ID", limit: int = 15) -> str:
    """Comma-joined distinct IDs from a column, truncated with a '+N more' tail."""
    if df is None or df.empty or col not in df.columns:
        return "none"
    ids = sorted(df[col].dropna().astype(str).unique())
    if not ids:
        return "none"
    if len(ids) <= limit:
        return ", ".join(ids)
    return ", ".join(ids[:limit]) + f"  (+{len(ids) - limit} more — see the detail tab)"


def build_summary_df(
    diffs: dict[str, Any],
    pdf_results: dict[str, Any],
    fill_threshold: float,
    baseline_manifest: dict[str, Any],
) -> pd.DataFrame:
    flagged = diffs.get("flagged_diff")
    deal_status = diffs.get("deal_status_diff")
    fund_diff = diffs.get("fund_diff")
    manager_diff = diffs.get("manager_diff")

    rows: list[tuple[str, Any]] = []
    rows.append(("Baseline compared against", baseline_manifest.get("taken_at_utc", "no prior baseline (first run)")))
    rows.append(("Baseline data as of", baseline_manifest.get("max_as_at_date_in_csv", "n/a")))
    rows.append(("", ""))
    rows.append(("New completeness issues this run", flagged.n_new if flagged else 0))
    rows.append(("  -> funds affected", _distinct_ids(flagged.new_rows if flagged else None)))
    rows.append(("Resolved completeness issues this run", flagged.n_resolved if flagged else 0))
    rows.append(("  -> funds affected", _distinct_ids(flagged.resolved_rows if flagged else None)))
    rows.append(("Still-open completeness issues", flagged.n_persisting if flagged else 0))
    rows.append(("", ""))
    rows.append(("New Deal Status exceptions this run", deal_status.n_new if deal_status else 0))
    rows.append(("  -> funds affected", _distinct_ids(deal_status.new_rows if deal_status else None)))
    rows.append(("Resolved Deal Status exceptions this run", deal_status.n_resolved if deal_status else 0))
    rows.append(("  -> funds affected", _distinct_ids(deal_status.resolved_rows if deal_status else None)))
    rows.append(("", ""))
    if fund_diff is not None:
        newly_bad = fund_diff[fund_diff["newly_below_threshold"]]
        rows.append((f"Funds newly below {fill_threshold:.0%} completeness", len(newly_bad)))
        rows.append(("  -> which funds", _distinct_ids(newly_bad)))
    if manager_diff is not None:
        newly_bad_mgr = manager_diff[manager_diff["newly_below_threshold"]]
        rows.append((f"Managers newly below {fill_threshold:.0%} completeness", len(newly_bad_mgr)))
        rows.append(("  -> which managers", _distinct_ids(newly_bad_mgr, col="Investment Manager Allocator ID")))
    rows.append(("", ""))
    rows.append(("PDF mismatches found this run", pdf_results.get("total_mismatches", 0)))
    mismatch_funds = sorted({m.get("fund_id") for m in (pdf_results.get("mismatches") or []) if m.get("fund_id")})
    rows.append(("  -> funds with a PDF mismatch", ", ".join(mismatch_funds) or "none"))
    checked = [f["fund_id"] for f in pdf_results.get("per_fund", []) if f.get("status") == "checked"]
    skipped_no_pdf = [f["fund_id"] for f in pdf_results.get("per_fund", []) if f.get("status") == "no_new_pdf"]
    rows.append(("Funds checked against a new PDF", ", ".join(checked) or "none"))
    rows.append(("Funds with an approved template but no new PDF staged", ", ".join(skipped_no_pdf) or "none"))

    return pd.DataFrame(rows, columns=["Metric", "Value"])


def build_escalation_df(fund_diff: pd.DataFrame | None, fill_threshold: float) -> pd.DataFrame:
    if fund_diff is None:
        return pd.DataFrame(columns=["Fund Allocator ID", "cond_completeness"])
    cols = [c for c in ["Fund Allocator ID", "uncond_fill", "cond_completeness", "score_delta",
                         "newly_below_threshold"] if c in fund_diff.columns]
    out = fund_diff[fund_diff["cond_completeness"] < fill_threshold][cols].copy()
    return out.sort_values("cond_completeness")


def build_report(
    out_path: Path,
    diffs: dict[str, Any],
    pdf_results: dict[str, Any],
    fill_threshold: float,
) -> Path:
    baseline_manifest = diffs.get("baseline_manifest") or {}

    summary_df = build_summary_df(diffs, pdf_results, fill_threshold, baseline_manifest)

    flagged = diffs.get("flagged_diff")
    new_issues_df = flagged.new_rows if flagged is not None else pd.DataFrame()
    if not new_issues_df.empty and "Fund Allocator ID" in new_issues_df.columns:
        new_issues_df = new_issues_df.sort_values("Fund Allocator ID")

    fund_diff = diffs.get("fund_diff")
    escalation_df = build_escalation_df(fund_diff, fill_threshold)

    deal_status = diffs.get("deal_status_diff")
    deal_status_df = pd.DataFrame()
    if deal_status is not None:
        deal_status_df = deal_status.new_rows.copy()
        if not deal_status.new_rows.empty:
            deal_status_df["status_this_run"] = "NEW"
        persisting = deal_status.persisting_rows.copy()
        if not persisting.empty:
            persisting["status_this_run"] = "persisting"
        deal_status_df = pd.concat([deal_status_df, persisting], ignore_index=True) if not persisting.empty else deal_status_df
        if not deal_status_df.empty and "Fund Allocator ID" in deal_status_df.columns:
            deal_status_df = deal_status_df.sort_values(["status_this_run", "Fund Allocator ID"], ascending=[False, True])

    rules_df = diffs.get("rules_diff")
    if rules_df is not None:
        rules_df = rules_df.sort_values("rate", ascending=False)

    pdf_mismatch_rows = pdf_results.get("mismatches") or []
    pdf_df = pd.DataFrame(pdf_mismatch_rows) if pdf_mismatch_rows else None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        _write_sheet(writer, summary_df, "Summary")
        _write_sheet(
            writer,
            new_issues_df if not new_issues_df.empty else pd.DataFrame({"note": ["No new issues this run."]}),
            "New Issues This Quarter",
        )
        _write_sheet(
            writer,
            escalation_df if not escalation_df.empty else pd.DataFrame({"note": ["No funds below threshold."]}),
            "Escalation List",
        )
        _write_sheet(
            writer,
            deal_status_df if not deal_status_df.empty else pd.DataFrame({"note": ["No Deal Status exceptions."]}),
            "Deal Status Exceptions",
        )
        if rules_df is not None:
            _write_sheet(writer, rules_df, "Consistency Rule Results")
        if pdf_df is not None and not pdf_df.empty:
            _write_sheet(writer, pdf_df, "PDF Mismatches")

    return out_path
