"""Per-document result contract: grain, comparability, evidence, next steps."""

from __future__ import annotations

from typing import Any


def build_document_result(
    *,
    document: dict[str, Any],
    route: dict[str, Any],
    extraction: dict[str, Any] | None = None,
    compare: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize one PDF's outcome into an explicit multi-grain contract.

    Never collapses company-level and fund-aggregate success into a single flag.
    """
    extraction = extraction or {}
    compare = compare or {}
    mode = route.get("extraction_mode") or "manual_review"
    company_count = len(extraction.get("company_summary") or [])
    if company_count == 0 and extraction.get("onboarding_summary"):
        company_count = int((extraction.get("onboarding_summary") or {}).get("company_count") or 0)
    statement_entities = len(extraction.get("statement_entities") or [])
    fund_aggregate = extraction.get("fund_aggregate") or {}
    inferred = route.get("inferred_schema") or {}
    evidence = route.get("routing_evidence") or _evidence_from_route(route)

    company_status = _company_grain_status(
        mode=mode,
        company_count=company_count,
        inferred=inferred,
        compare=compare,
        route=route,
    )
    aggregate_status = _aggregate_grain_status(
        mode=mode,
        fund_aggregate=fund_aggregate,
        statement_entities=statement_entities,
        compare=compare,
        route=route,
    )

    next_steps = _next_steps(company_status, aggregate_status, mode=mode, route=route)
    # Auto-skip non-holdings during survey so review queue stays clean.
    auto_skipped = bool(company_status.get("auto_skipped"))
    if auto_skipped:
        try:
            from pdf_validation.mapping_onboarding import maybe_auto_skip_extraction_gate

            # extraction dir is not known here; survey process_pdf will call this separately.
        except Exception:
            pass
    return {
        "document_id": document.get("document_id"),
        "pdf_path": document.get("pdf_path") or route.get("pdf_path"),
        "pdf_name": document.get("pdf_name"),
        "pdf_sha256": document.get("pdf_sha256"),
        "fund_id_hint": document.get("fund_id_hint"),
        "as_of_date": route.get("as_of_date"),
        "extraction_mode": mode,
        "template_family": route.get("template_family"),
        "text_source": route.get("text_source"),
        "routing_evidence": evidence,
        "grains": {
            "company_holdings": company_status,
            "fund_aggregate": aggregate_status,
        },
        "company_count": company_count,
        "statement_entity_count": statement_entities,
        "selected_parser": extraction.get("selected_parser"),
        "compare_summary": {
            "comparability_status": compare.get("comparability_status"),
            "overall_status": compare.get("overall_status"),
            "match": compare.get("match"),
            "mismatch": compare.get("mismatch"),
        }
        if compare
        else None,
        "next_steps": next_steps,
        "human_summary": _human_summary(company_status, aggregate_status, mode=mode),
        "auto_skipped": auto_skipped,
        "review_kind": company_status.get("review_kind"),
    }


def _evidence_from_route(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "reasons": list(route.get("reasons") or []),
        "schedule_pages": list(route.get("schedule_pages") or []),
        "confidence": route.get("confidence"),
        "family_candidates": route.get("family_candidates"),
        "inferred_schema": route.get("inferred_schema"),
        "onboarding_status": route.get("onboarding_status"),
        "text_source": route.get("text_source"),
    }


def _company_grain_status(
    *,
    mode: str,
    company_count: int,
    inferred: dict[str, Any],
    compare: dict[str, Any],
    route: dict[str, Any],
) -> dict[str, Any]:
    if mode == "fund_aggregate_only":
        return {
            "status": "source_has_no_company_schedule",
            "comparable": False,
            "reason": "PDF is fund-aggregate / statement-only; no investments schedule to extract companies from.",
            "company_count": 0,
        }
    if mode == "scanned_financial_statements":
        if company_count > 0:
            return {
                "status": "extracted",
                "comparable": compare.get("comparability_status") == "comparable",
                "reason": "Scanned FS pathway returned portfolio companies.",
                "company_count": company_count,
            }
        return {
            "status": "source_or_ocr_has_no_portfolio_schedule",
            "comparable": False,
            "reason": "Scanned financial statements extracted statement lines but no portfolio company schedule.",
            "company_count": 0,
            "statement_comparable_possible": True,
        }
    if mode == "blocked_narrative":
        return {
            "status": "not_a_holdings_pdf",
            "comparable": False,
            "auto_skipped": True,
            "reason": "Investor letter / narrative / strategy document without holdings schedule.",
            "company_count": 0,
            "review_kind": None,
        }
    if mode == "manual_review":
        missing = list((inferred or {}).get("missing_required") or [])
        pages = list(route.get("schedule_pages") or [])
        if not pages and not missing:
            return {
                "status": "not_a_holdings_pdf",
                "comparable": False,
                "auto_skipped": True,
                "reason": "No schedule or aggregate signal; treated as non-holdings and auto-skipped.",
                "company_count": company_count,
                "review_kind": None,
            }
        return {
            "status": "parse_blocked_insufficient_evidence",
            "comparable": False,
            "auto_skipped": False,
            "reason": "Schedule suspected but Cost/Fair Value columns were not recovered with enough evidence.",
            "company_count": company_count,
            "missing_required": missing,
            "inferred_columns": list((inferred or {}).get("logical_columns") or []),
            "schedule_pages": pages,
            "review_kind": "pdf_evidence",
        }
    if mode in {"position_level", "position_level_inferred"}:
        if company_count <= 0:
            return {
                "status": "extract_empty",
                "comparable": False,
                "reason": "Routed for company holdings but no company rows were extracted.",
                "company_count": 0,
            }
        if not compare:
            return {
                "status": "extracted",
                "comparable": False,
                "reason": "Company holdings extracted; vendor compare was not run for this document.",
                "company_count": company_count,
            }
        if compare.get("comparability_status") == "comparable":
            return {
                "status": "comparable",
                "comparable": True,
                "reason": "Company holdings extracted and vendor compare succeeded.",
                "company_count": company_count,
            }
        if mode == "position_level_inferred" and not route.get("compare_allowed", False):
            return {
                "status": "extracted_needs_mapping_review",
                "comparable": False,
                "reason": "Company holdings extracted via generic schema; approved name mapping required before amount compare.",
                "company_count": company_count,
            }
        return {
            "status": "extracted_not_comparable",
            "comparable": False,
            "reason": compare.get("blocked_reason")
            or compare.get("comparability_status")
            or "Extracted but vendor compare did not reach comparable.",
            "company_count": company_count,
        }
    return {
        "status": "unknown",
        "comparable": False,
        "reason": f"Unhandled extraction_mode={mode}",
        "company_count": company_count,
    }


def _aggregate_grain_status(
    *,
    mode: str,
    fund_aggregate: dict[str, Any],
    statement_entities: int,
    compare: dict[str, Any],
    route: dict[str, Any],
) -> dict[str, Any]:
    has_agg = bool(fund_aggregate) and fund_aggregate.get("parse_status") == "ok"
    if mode == "scanned_financial_statements":
        if statement_entities > 0 or has_agg:
            return {
                "status": "statement_extracted",
                "comparable": compare.get("comparability_status") == "comparable",
                "reason": "Statement / balance-sheet lines available for fund-level checks.",
                "statement_entity_count": statement_entities,
            }
        return {
            "status": "statement_missing",
            "comparable": False,
            "reason": "Scanned pathway did not yield statement entities.",
            "statement_entity_count": 0,
        }
    if mode == "fund_aggregate_only":
        if has_agg:
            return {
                "status": "aggregate_extracted",
                "comparable": compare.get("comparability_status") == "comparable",
                "reason": "Fund aggregate totals extracted; company holdings are not in this PDF.",
                "fund_aggregate_status": fund_aggregate.get("parse_status"),
            }
        return {
            "status": "aggregate_extract_failed",
            "comparable": False,
            "reason": "Routed as aggregate-only but totals were not parsed.",
        }
    if has_agg:
        return {
            "status": "aggregate_available_secondary",
            "comparable": False,
            "reason": "Aggregate totals also present; primary grain is company holdings when available.",
            "fund_aggregate_status": fund_aggregate.get("parse_status"),
        }
    return {
        "status": "not_applicable",
        "comparable": False,
        "reason": "No fund-aggregate compare required for this document class.",
    }


def _next_steps(
    company: dict[str, Any],
    aggregate: dict[str, Any],
    *,
    mode: str,
    route: dict[str, Any],
) -> list[str]:
    steps: list[str] = []
    cs = company.get("status")
    if cs == "source_has_no_company_schedule":
        steps.append("Use fund-aggregate compare for this PDF; do not expect company Cost/FV rows.")
    elif cs == "not_a_holdings_pdf" or cs == "blocked_narrative":
        steps.append("Auto-skipped: not a holdings PDF. Use an audited FS / SOI for company Cost/FV.")
    elif cs == "source_or_ocr_has_no_portfolio_schedule":
        steps.append("Review statement totals; supply a PDF that includes a portfolio schedule for company-level compare.")
    elif cs == "parse_blocked_insufficient_evidence":
        missing = company.get("missing_required") or []
        steps.append(
            "Improve generic table recovery or confirm schedule pages; missing fields: "
            + (", ".join(missing) if missing else "unknown")
            + "."
        )
        pages = company.get("schedule_pages") or route.get("schedule_pages") or []
        if pages:
            steps.append(f"Inspect schedule page candidates: {pages}.")
    elif cs == "extracted_needs_mapping_review":
        steps.append("Run mapping review / save name matches before amount compare.")
    elif cs == "extracted_not_comparable":
        steps.append("Inspect compare gates and entity mappings for this document_id.")
    elif cs == "comparable":
        steps.append("Company-level compare complete for this PDF.")
    if aggregate.get("status") in {"aggregate_extracted", "statement_extracted"} and not aggregate.get("comparable"):
        steps.append("Optional: attach approved aggregate/statement mapping to enable fund-level amount compare.")
    if not steps:
        steps.append(f"No automatic next step for mode={mode}.")
    return steps


def _human_summary(company: dict[str, Any], aggregate: dict[str, Any], *, mode: str) -> str:
    return (
        f"mode={mode}; company_holdings={company.get('status')}; "
        f"fund_aggregate={aggregate.get('status')}"
    )
