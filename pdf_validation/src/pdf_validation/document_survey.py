"""Document-first survey: every uploaded PDF is routed/extracted independently."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pdf_validation.batch_runner import _run_one
from pdf_validation.document_identity import describe_document, output_stem_for_document
from pdf_validation.document_router import classify_sample_tree, load_registry, route_document
from pdf_validation.pipeline import run_extract
from pdf_validation.result_contract import build_document_result


ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "pdf_validation"


def process_pdf(
    pdf: Path,
    *,
    out_root: Path,
    fund_id_hint: str | None = None,
    repo_root: Path | None = None,
    run_compare: bool = True,
) -> dict[str, Any]:
    """Route + extract (+ optional compare) one PDF into an isolated output dir."""
    pdf = Path(pdf)
    repo_root = Path(repo_root or ROOT)
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    doc = describe_document(pdf, fund_id_hint=fund_id_hint)
    route = route_document(pdf)
    stem = output_stem_for_document(
        document_id=doc["document_id"],
        as_of_date=route.get("as_of_date"),
        pdf_name=pdf.name,
        fund_id_hint=fund_id_hint,
    )
    doc_dir = out_root / stem
    extract_dir = doc_dir / "extract"
    extract_dir.mkdir(parents=True, exist_ok=True)

    payload = run_extract(
        pdf_path=pdf,
        config_path=None,
        output_dir=extract_dir,
        repo_root=repo_root,
        cli_args={"command": "document-survey", "pdf": str(pdf), "auto_template": True},
        auto_template=True,
    )
    # Prefer as-of recovered during extraction.
    as_of = (payload.get("route") or {}).get("as_of_date") or route.get("as_of_date")
    if as_of and as_of != route.get("as_of_date"):
        route = dict(route)
        route["as_of_date"] = as_of

    compare_summary: dict[str, Any] | None = None
    if run_compare and fund_id_hint:
        try:
            compare_summary = _run_one(pdf, fund_id_hint)
        except Exception as exc:  # noqa: BLE001
            compare_summary = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    result = build_document_result(
        document=doc,
        route=payload.get("route") or route,
        extraction=payload,
        compare=compare_summary,
    )
    result["output_dir"] = str(doc_dir)
    # Auto-skip non-holdings so survey never queues a confirm click.
    try:
        from pdf_validation.mapping_onboarding import maybe_auto_skip_extraction_gate

        auto = maybe_auto_skip_extraction_gate(extract_dir)
        if auto and auto.get("auto_skipped"):
            result["auto_skipped"] = True
            result["review_kind"] = None
            grains = result.get("grains") or {}
            company = dict(grains.get("company_holdings") or {})
            company["auto_skipped"] = True
            company["status"] = company.get("status") or "not_a_holdings_pdf"
            grains["company_holdings"] = company
            result["grains"] = grains
    except Exception:
        pass
    (doc_dir / "document_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return result


def run_document_survey(
    sample_root: Path,
    *,
    out_root: Path | None = None,
    fund_ids: list[str] | None = None,
    run_compare: bool = True,
) -> dict[str, Any]:
    """Process every PDF under sample_root; one result per file, never collapsing periods."""
    sample_root = Path(sample_root)
    out_root = Path(out_root or (PKG / "outputs" / "document_survey"))
    out_root.mkdir(parents=True, exist_ok=True)

    classified = classify_sample_tree(sample_root, load_registry())
    if fund_ids:
        allow = {str(x) for x in fund_ids}
        classified = [c for c in classified if c.get("fund_id_dir") in allow or c.get("fund_id_hint") in allow]

    results: list[dict[str, Any]] = []
    for item in classified:
        pdf = Path(item["pdf_path"] if item.get("pdf_path") else item.get("pdf"))
        fund_hint = item.get("fund_id_dir") or item.get("fund_id_hint")
        print(f"\n=== {fund_hint} :: {pdf.name} :: {item.get('extraction_mode')} ===")
        try:
            summary = process_pdf(
                pdf,
                out_root=out_root,
                fund_id_hint=str(fund_hint) if fund_hint else None,
                run_compare=run_compare,
            )
            print(summary.get("human_summary"), "|", summary.get("grains"))
            results.append(summary)
        except Exception as exc:  # noqa: BLE001
            err = {
                "document_id": item.get("document_id"),
                "pdf_path": str(pdf),
                "fund_id_hint": fund_hint,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "extraction_mode": item.get("extraction_mode"),
            }
            print("FAILED", err)
            results.append(err)

    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "sample_root": str(sample_root),
        "out_root": str(out_root),
        "input_pdf_count": len(classified),
        "result_count": len(results),
        "unique_document_ids": sorted({r.get("document_id") for r in results if r.get("document_id")}),
        "results": results,
    }
    path = out_root / "document_survey_report.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("\nWrote", path)
    return report


def pending_review_queue(report: dict[str, Any]) -> list[dict[str, Any]]:
    """List survey results that should open the human review UI."""
    queue: list[dict[str, Any]] = []
    for row in report.get("results") or []:
        if row.get("status") == "failed" or not row.get("output_dir"):
            continue
        if row.get("auto_skipped"):
            continue
        mode = row.get("extraction_mode")
        company = ((row.get("grains") or {}).get("company_holdings") or {})
        company_status = company.get("status")
        if company_status in {"not_a_holdings_pdf", "blocked_narrative", "source_has_no_company_schedule"}:
            continue
        if mode == "blocked_narrative":
            continue
        needs = (
            mode == "manual_review"
            or company_status
            in {
                "parse_blocked_insufficient_evidence",
                "extracted_needs_mapping_review",
            }
            or bool(row.get("review_kind"))
        )
        if not needs:
            continue
        extract_dir = Path(row["output_dir"]) / "extract"
        if not extract_dir.exists():
            continue
        try:
            from pdf_validation.mapping_onboarding import extraction_requires_human_review, load_review_state, maybe_auto_skip_extraction_gate
            from pdf_validation.discrepancy_review import load_unresolved_discrepancies

            maybe_auto_skip_extraction_gate(extract_dir)
            state = load_review_state(extract_dir)
            if (state.get("extraction_ack") or {}).get("action") in {
                "skip_no_holdings_schedule",
                "skip_not_comparable",
                "acknowledged",
                "auto_skipped_not_holdings",
            } and company_status != "extracted_needs_mapping_review":
                # Still allow name/amount review when extract succeeded.
                if not load_unresolved_discrepancies(extract_dir):
                    if mode == "manual_review" and extraction_requires_human_review(extract_dir) is None:
                        continue
            if mode == "manual_review" and extraction_requires_human_review(extract_dir) is None:
                if company_status != "extracted_needs_mapping_review" and not load_unresolved_discrepancies(extract_dir):
                    continue
        except Exception:
            pass
        queue.append(
            {
                "document_id": row.get("document_id"),
                "pdf_name": row.get("pdf_name") or Path(str(row.get("pdf_path") or "")).name,
                "fund_id": row.get("fund_id_hint"),
                "as_of_date": row.get("as_of_date"),
                "extraction_mode": mode,
                "company_status": company_status,
                "extraction_dir": str(extract_dir),
                "human_summary": row.get("human_summary"),
                "next_steps": row.get("next_steps") or [],
                "review_kind": row.get("review_kind") or company.get("review_kind"),
            }
        )
    return queue


def open_first_pending_review(
    report: dict[str, Any],
    *,
    vendor_csv: Path,
    port: int = 8765,
    open_browser: bool = True,
    block: bool = False,
) -> dict[str, Any]:
    """Open review UI for the first pending survey document; return queue + open info."""
    from pdf_validation.mapping_onboarding import maybe_open_mapping_review, review_open_reason

    queue = pending_review_queue(report)
    if not queue:
        print("No pending human-review documents in survey report.")
        return {"opened": False, "reason": "empty_queue", "queue": []}

    print(f"Pending human review queue ({len(queue)}):")
    for i, item in enumerate(queue, 1):
        print(
            f"  {i}. {item.get('fund_id')} · {item.get('pdf_name')} · "
            f"{item.get('extraction_mode')} · {item.get('company_status')}"
        )

    first = queue[0]
    fund_id = first.get("fund_id")
    if not fund_id:
        return {
            "opened": False,
            "reason": "missing_fund_id",
            "queue": queue,
            "first": first,
        }

    decision = review_open_reason(
        extraction_dir=Path(first["extraction_dir"]),
        vendor_csv=Path(vendor_csv),
        fund_id=str(fund_id),
        as_of_date=first.get("as_of_date"),
    )
    # Always open the first queued doc when caller asked --open-review, even if
    # entity TODOs are empty (manual_review) — review_open_reason already covers that.
    info = maybe_open_mapping_review(
        extraction_dir=Path(first["extraction_dir"]),
        vendor_csv=Path(vendor_csv),
        fund_id=str(fund_id),
        as_of_date=first.get("as_of_date"),
        port=port,
        open_browser=open_browser,
        block=block,
        force=True if not decision.get("should_open") else False,
    )
    info["queue"] = queue
    info["opened_document"] = first
    info["decision"] = {
        "reason": decision.get("reason"),
        "should_open": decision.get("should_open"),
    }
    return info
