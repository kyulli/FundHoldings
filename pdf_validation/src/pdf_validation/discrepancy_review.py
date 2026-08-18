"""PDF ↔ Excel amount discrepancy proposals for human review.

Never writes the third-party vendor Excel/CSV. Decisions land in review_state
and feed ``reviewed_extraction.xlsx``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pdf_validation.review_preview import locate_amount_evidence, resolve_pdf_path


PRIMARY_LOGICAL_FIELDS = {"current_cost", "unrealized_value"}


def _review_dir(extraction_dir: Path) -> Path:
    path = Path(extraction_dir) / "mapping_review"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _item_id(row: dict[str, Any]) -> str:
    company = row.get("pdf_company_name") or ""
    vendor = row.get("vendor_source_asset") or ""
    field = row.get("logical_field") or row.get("vendor_field") or ""
    return f"{company}::{vendor}::{field}"


def load_amount_rows(extraction_dir: Path) -> list[dict[str, Any]]:
    """Prefer draft-compare amount rows; fall back to vendor_comparison output."""
    extraction_dir = Path(extraction_dir)
    candidates = [
        extraction_dir / "mapping_review" / "compare" / "amount_comparisons.jsonl",
        extraction_dir / "vendor_comparison" / "amount_comparisons.jsonl",
    ]
    for path in candidates:
        rows = _load_jsonl(path)
        if rows:
            return rows
    return []


def load_discrepancy_decisions(extraction_dir: Path) -> dict[str, dict[str, Any]]:
    state = _load_json(_review_dir(extraction_dir) / "review_state.json")
    out: dict[str, dict[str, Any]] = {}
    for d in state.get("discrepancy_decisions") or []:
        key = d.get("item_id")
        if key:
            out[str(key)] = d
    return out


def upsert_discrepancy_decision(
    extraction_dir: Path,
    *,
    item_id: str,
    action: str,
    final_value: Any = None,
    notes: str | None = None,
    reviewer: str | None = None,
) -> dict[str, Any]:
    allowed = {
        "adopt_pdf",
        "adopt_excel",
        "keep_real_discrepancy",
        "pdf_evidence_unreliable",
    }
    if action not in allowed:
        raise ValueError(f"unsupported discrepancy action: {action}")
    path = _review_dir(extraction_dir) / "review_state.json"
    state = _load_json(path) if path.exists() else {"decisions": [], "updated_at_utc": None}
    decisions = [d for d in (state.get("discrepancy_decisions") or []) if d.get("item_id") != item_id]
    decisions.append(
        {
            "item_id": item_id,
            "action": action,
            "final_value": final_value,
            "notes": notes,
            "reviewer": reviewer,
            "decided_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    state["discrepancy_decisions"] = decisions
    state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return state


def _company_pages(companies: list[dict[str, Any]], name: str | None) -> list[int]:
    if not name:
        return []
    for c in companies:
        if c.get("company_name") != name:
            continue
        pages = c.get("pages") or []
        if isinstance(pages, list):
            out: list[int] = []
            for p in pages:
                try:
                    out.append(int(p))
                except (TypeError, ValueError):
                    continue
            return out
        try:
            return [int(pages)]
        except (TypeError, ValueError):
            return []
    return []


def build_discrepancy_items(
    *,
    extraction_dir: Path,
    amount_rows: list[dict[str, Any]] | None = None,
    locate_evidence: bool = True,
) -> list[dict[str, Any]]:
    """Build side-by-side PDF/Excel proposals for primary amount fields."""
    extraction_dir = Path(extraction_dir)
    amount_rows = amount_rows if amount_rows is not None else load_amount_rows(extraction_dir)
    companies = _load_jsonl(extraction_dir / "company_summary.jsonl")
    route = _load_json(extraction_dir / "route.json")
    as_of = route.get("as_of_date")
    decided = load_discrepancy_decisions(extraction_dir)
    pdf_path = resolve_pdf_path(extraction_dir, route)

    items: list[dict[str, Any]] = []
    for row in amount_rows:
        logical = row.get("logical_field")
        if logical not in PRIMARY_LOGICAL_FIELDS:
            continue
        status = row.get("status")
        if status not in {"mismatch", "match", "csv_missing", "pdf_missing"}:
            continue

        pdf_val = _to_float(row.get("pdf_value"))
        excel_val = _to_float(row.get("csv_value"))
        tol = _to_float(row.get("tolerance")) or 1.0
        diff = None
        pct = None
        if pdf_val is not None and excel_val is not None:
            diff = pdf_val - excel_val
            if excel_val != 0:
                pct = abs(diff) / abs(excel_val)

        pages = _company_pages(companies, row.get("pdf_company_name"))
        evidence: dict[str, Any] = {"status": "not_searched", "highlights": [], "page": pages[0] if pages else None}
        if locate_evidence and pdf_path and pdf_val is not None and pages:
            evidence = locate_amount_evidence(
                pdf_path=pdf_path,
                pages=pages,
                value=pdf_val,
                label=str(row.get("pdf_field") or logical),
                company=row.get("pdf_company_name"),
            )
        located = evidence.get("status") == "located"
        suggestion = "none"
        suggestion_en = "Evidence is insufficient to recommend a value."
        suggestion_zh = "证据不足，不能自动建议采用哪一侧。"
        if status == "match":
            suggestion = "auto_match"
            suggestion_en = "PDF and Excel already match within tolerance."
            suggestion_zh = "PDF 与 Excel 在容差内一致。"
        elif located and status == "mismatch":
            suggestion = "adopt_pdf"
            suggestion_en = "PDF amount is uniquely located on the page — prefer PDF extraction."
            suggestion_zh = "PDF 金额在页面上唯一定位 — 建议采用 PDF 提取值。"
        elif status == "mismatch" and not located:
            suggestion = "needs_human"
            suggestion_en = "Amounts disagree and PDF evidence could not be uniquely located."
            suggestion_zh = "金额不一致，且 PDF 证据未能唯一定位。"

        item_id = _item_id(row)
        user = decided.get(item_id)
        final_value = None
        if user:
            action = user.get("action")
            if action == "adopt_pdf":
                final_value = pdf_val
            elif action == "adopt_excel":
                final_value = excel_val
            elif action == "keep_real_discrepancy":
                final_value = user.get("final_value")
            else:
                final_value = None
        elif status == "match":
            final_value = pdf_val if pdf_val is not None else excel_val

        needs_action = status == "mismatch" and not user
        items.append(
            {
                "item_id": item_id,
                "review_kind": "amount_discrepancy",
                "needs_action": needs_action,
                "status": status,
                "pdf_company_name": row.get("pdf_company_name"),
                "vendor_source_asset": row.get("vendor_source_asset"),
                "logical_field": logical,
                "pdf_field": row.get("pdf_field"),
                "excel_field": row.get("vendor_field"),
                "pdf_value": pdf_val,
                "excel_value": excel_val,
                "difference": diff,
                "difference_pct": pct,
                "tolerance": tol,
                "as_of_date": as_of,
                "pages": pages,
                "evidence": evidence,
                "suggestion": suggestion,
                "suggestion_en": suggestion_en,
                "suggestion_zh": suggestion_zh,
                "user_decision": user,
                "final_value": final_value,
                "formula_en": f"PDF − Excel = {diff}" if diff is not None else None,
                "formula_zh": f"PDF − Excel = {diff}" if diff is not None else None,
            }
        )
    return items


def load_unresolved_discrepancies(extraction_dir: Path) -> list[dict[str, Any]]:
    """Mismatches still waiting on a human decision."""
    return [i for i in build_discrepancy_items(extraction_dir=extraction_dir, locate_evidence=False) if i.get("needs_action")]


def build_review_items_payload(
    *,
    extraction_dir: Path,
    fund_id: str | None = None,
) -> dict[str, Any]:
    items = build_discrepancy_items(extraction_dir=extraction_dir, locate_evidence=True)
    pending = [i for i in items if i.get("needs_action")]
    matched = [i for i in items if i.get("status") == "match"]
    return {
        "fund_id": fund_id,
        "item_count": len(items),
        "pending_count": len(pending),
        "matched_count": len(matched),
        "items": items,
        "pending": pending,
    }
