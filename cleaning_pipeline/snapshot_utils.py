"""Snapshotting and diffing of Phase 1 report outputs across pipeline runs.

A "snapshot" is a copy of the tracked reports/*.csv files at a point in time,
plus a manifest recording when it was taken and what the data looked like.
Each run diffs the freshly computed reports/ against the most recent snapshot
to answer: what's newly wrong, what's still wrong, what got fixed.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from config import DATA_CSV, REPORTS_DIR, SNAPSHOTS_DIR, TRACKED_REPORTS


def _max_as_at_date(csv_path: Path) -> str | None:
    try:
        df = pd.read_csv(csv_path, usecols=["As At Date"], low_memory=False)
    except Exception:
        return None
    dates = pd.to_datetime(df["As At Date"], format="%d-%b-%y", errors="coerce")
    dates2 = pd.to_datetime(df["As At Date"], errors="coerce")
    dates = dates.fillna(dates2)
    if dates.notna().any():
        return dates.max().date().isoformat()
    return None


def list_snapshots() -> list[Path]:
    if not SNAPSHOTS_DIR.exists():
        return []
    return sorted((p for p in SNAPSHOTS_DIR.iterdir() if p.is_dir()), key=lambda p: p.name)


def latest_snapshot() -> Path | None:
    snaps = list_snapshots()
    return snaps[-1] if snaps else None


def take_snapshot(label: str | None = None, source_reports_dir: Path = REPORTS_DIR) -> Path:
    """Copy the current tracked reports/ CSVs into a new timestamped snapshot dir."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{ts}_{label}" if label else ts
    dest = SNAPSHOTS_DIR / name
    dest.mkdir(parents=True, exist_ok=True)

    copied = {}
    for key, filename in TRACKED_REPORTS.items():
        src = source_reports_dir / filename
        if src.exists():
            shutil.copy2(src, dest / filename)
            copied[key] = filename

    manifest = {
        "taken_at_utc": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "max_as_at_date_in_csv": _max_as_at_date(DATA_CSV),
        "vendor_csv_row_count": _csv_row_count(DATA_CSV),
        "reports_copied": copied,
        "reports_missing": [f for f in TRACKED_REPORTS.values() if f not in copied.values()],
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return dest


def _csv_row_count(csv_path: Path) -> int | None:
    if not csv_path.exists():
        return None
    try:
        with open(csv_path, "r", encoding="utf-8", errors="ignore") as fh:
            return sum(1 for _ in fh) - 1
    except Exception:
        return None


def load_manifest(snapshot_dir: Path) -> dict[str, Any]:
    path = snapshot_dir / "manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_tracked(snapshot_dir: Path, key: str) -> pd.DataFrame | None:
    filename = TRACKED_REPORTS.get(key)
    if not filename:
        return None
    path = snapshot_dir / filename
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return None


@dataclass
class DiffResult:
    new_rows: pd.DataFrame = field(default_factory=pd.DataFrame)
    resolved_rows: pd.DataFrame = field(default_factory=pd.DataFrame)
    persisting_rows: pd.DataFrame = field(default_factory=pd.DataFrame)
    n_new: int = 0
    n_resolved: int = 0
    n_persisting: int = 0


def _row_key(df: pd.DataFrame, key_cols: list[str]) -> pd.Series:
    present = [c for c in key_cols if c in df.columns]
    return df[present].astype(str).agg("||".join, axis=1)


def diff_flagged_rows(current: pd.DataFrame, baseline: pd.DataFrame | None) -> DiffResult:
    """Row-level diff for flagged_missing_fields_conditional.csv-shaped data."""
    key_cols = ["Fund Allocator ID", "Investment Manager Allocator ID", "Source Asset",
                "As At Date", "missing_expected_fields"]
    if baseline is None or baseline.empty:
        return DiffResult(new_rows=current, n_new=len(current))

    cur_key = _row_key(current, key_cols)
    base_key = _row_key(baseline, key_cols)

    new_mask = ~cur_key.isin(set(base_key))
    persisting_mask = ~new_mask
    resolved_mask = ~base_key.isin(set(cur_key))

    return DiffResult(
        new_rows=current[new_mask],
        resolved_rows=baseline[resolved_mask],
        persisting_rows=current[persisting_mask],
        n_new=int(new_mask.sum()),
        n_resolved=int(resolved_mask.sum()),
        n_persisting=int(persisting_mask.sum()),
    )


def diff_deal_status_exceptions(current: pd.DataFrame, baseline: pd.DataFrame | None) -> DiffResult:
    key_cols = ["Fund Allocator ID", "Source Asset", "As At Date"]
    if baseline is None or baseline.empty:
        return DiffResult(new_rows=current, n_new=len(current))

    cur_key = _row_key(current, key_cols)
    base_key = _row_key(baseline, key_cols)

    new_mask = ~cur_key.isin(set(base_key))
    resolved_mask = ~base_key.isin(set(cur_key))

    return DiffResult(
        new_rows=current[new_mask],
        resolved_rows=baseline[resolved_mask],
        persisting_rows=current[~new_mask],
        n_new=int(new_mask.sum()),
        n_resolved=int(resolved_mask.sum()),
        n_persisting=int((~new_mask).sum()),
    )


def diff_consistency_rules(current: pd.DataFrame, baseline: pd.DataFrame | None) -> pd.DataFrame:
    """Per-rule delta in violation rate vs. baseline. Positive delta = got worse."""
    if baseline is None or baseline.empty:
        out = current.copy()
        out["baseline_rate"] = None
        out["rate_delta"] = None
        return out

    merged = current.merge(
        baseline[["rule", "rate", "violations"]].rename(
            columns={"rate": "baseline_rate", "violations": "baseline_violations"}
        ),
        on="rule",
        how="left",
    )
    merged["rate_delta"] = merged["rate"] - merged["baseline_rate"]
    return merged


def diff_fund_or_manager_completeness(
    current: pd.DataFrame, baseline: pd.DataFrame | None, id_col: str, score_col: str, fill_threshold: float
) -> pd.DataFrame:
    """Per-entity completeness delta vs. baseline, plus a newly-below-threshold flag."""
    if baseline is None or baseline.empty:
        out = current.copy()
        out["baseline_score"] = None
        out["score_delta"] = None
        out["newly_below_threshold"] = out[score_col] < fill_threshold
        return out

    merged = current.merge(
        baseline[[id_col, score_col]].rename(columns={score_col: "baseline_score"}),
        on=id_col,
        how="left",
    )
    merged["score_delta"] = merged[score_col] - merged["baseline_score"]
    merged["newly_below_threshold"] = (merged[score_col] < fill_threshold) & (
        merged["baseline_score"].isna() | (merged["baseline_score"] >= fill_threshold)
    )
    return merged


def run_all_diffs(current_reports_dir: Path, baseline_dir: Path | None, fill_threshold: float) -> dict[str, Any]:
    """Compute every tracked diff for one pipeline run."""

    def _read_current(key: str) -> pd.DataFrame | None:
        filename = TRACKED_REPORTS.get(key)
        path = current_reports_dir / filename
        return pd.read_csv(path, low_memory=False) if path.exists() else None

    cur_flagged = _read_current("flagged_conditional")
    cur_deal_status = _read_current("deal_status_exceptions")
    cur_rules = _read_current("consistency_rules")
    cur_by_fund = _read_current("by_fund_conditional")
    cur_by_manager = _read_current("by_manager_conditional")

    base_flagged = _read_tracked(baseline_dir, "flagged_conditional") if baseline_dir else None
    base_deal_status = _read_tracked(baseline_dir, "deal_status_exceptions") if baseline_dir else None
    base_rules = _read_tracked(baseline_dir, "consistency_rules") if baseline_dir else None
    base_by_fund = _read_tracked(baseline_dir, "by_fund_conditional") if baseline_dir else None
    base_by_manager = _read_tracked(baseline_dir, "by_manager_conditional") if baseline_dir else None

    return {
        "flagged_diff": diff_flagged_rows(cur_flagged, base_flagged) if cur_flagged is not None else None,
        "deal_status_diff": diff_deal_status_exceptions(cur_deal_status, base_deal_status)
        if cur_deal_status is not None else None,
        "rules_diff": diff_consistency_rules(cur_rules, base_rules) if cur_rules is not None else None,
        "fund_diff": diff_fund_or_manager_completeness(
            cur_by_fund, base_by_fund, "Fund Allocator ID", "cond_completeness", fill_threshold
        ) if cur_by_fund is not None else None,
        "manager_diff": diff_fund_or_manager_completeness(
            cur_by_manager, base_by_manager, "Investment Manager Allocator ID", "cond_completeness", fill_threshold
        ) if cur_by_manager is not None else None,
        "baseline_manifest": load_manifest(baseline_dir) if baseline_dir else {},
    }
