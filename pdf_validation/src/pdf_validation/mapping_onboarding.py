"""Draft mapping onboarding: proposal → review state → draft compare → optional promote.

Draft artifacts live only under ``<extraction_dir>/mapping_review/`` and never write
the formal vendor_mapping_registry until an explicit promote action.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from pdf_validation.entity_mapping import apply_review_decisions, build_mapping_proposal, load_aliases
from pdf_validation.mapping_registry import load_mapping_for_fund, load_vendor_mapping_registry
from pdf_validation.vendor_comparison import _parse_vendor_date, compare_with_vendor


REVIEW_DIRNAME = "mapping_review"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _metadata_as_of(extraction_dir: Path) -> str | None:
    meta_path = extraction_dir / "metadata.jsonl"
    for row in _read_jsonl(meta_path):
        if row.get("field") == "as_of_date" and row.get("normalized"):
            return str(row["normalized"])
    route_path = extraction_dir / "route.json"
    if route_path.exists():
        route = json.loads(route_path.read_text(encoding="utf-8"))
        if route.get("as_of_date"):
            return str(route["as_of_date"])
    return None


def _load_route(extraction_dir: Path) -> dict[str, Any]:
    path = extraction_dir / "route.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def review_dir(extraction_dir: Path) -> Path:
    path = Path(extraction_dir) / REVIEW_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_review_state(extraction_dir: Path) -> dict[str, Any]:
    path = review_dir(extraction_dir) / "review_state.json"
    if not path.exists():
        return {"decisions": [], "updated_at_utc": None}
    return json.loads(path.read_text(encoding="utf-8"))


def save_review_state(extraction_dir: Path, state: dict[str, Any]) -> Path:
    path = review_dir(extraction_dir) / "review_state.json"
    state = dict(state)
    state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def upsert_decision(
    extraction_dir: Path,
    *,
    pdf_company_name: str,
    action: str,
    vendor_source_asset: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    state = load_review_state(extraction_dir)
    decisions = [d for d in (state.get("decisions") or []) if d.get("pdf_company_name") != pdf_company_name]
    decisions.append(
        {
            "pdf_company_name": pdf_company_name,
            "action": action,
            "vendor_source_asset": vendor_source_asset,
            "notes": notes,
        }
    )
    state["decisions"] = decisions
    save_review_state(extraction_dir, state)
    return state


def build_proposal_for_extraction(
    *,
    extraction_dir: Path,
    vendor_csv: Path,
    fund_id: str,
    as_of_date: str | None = None,
    llm_enabled: bool = False,
) -> dict[str, Any]:
    extraction_dir = Path(extraction_dir)
    vendor_csv = Path(vendor_csv)
    route = _load_route(extraction_dir)
    as_of = as_of_date or _metadata_as_of(extraction_dir) or route.get("as_of_date")
    companies = _read_jsonl(extraction_dir / "company_summary.jsonl")
    realized = _read_jsonl(extraction_dir / "realized_lots.jsonl")

    pdf_names = [
        c["company_name"]
        for c in companies
        if c.get("company_name") and c.get("entity_grain", "company") in {"company", "security", None}
    ]
    # Exclude Non-Investment SOA rollup rows from entity matching
    pdf_names = [n for n in pdf_names if not str(n).lower().startswith("non-investment")]
    realized_names = sorted({r["company_name"] for r in realized if r.get("company_name")})

    evidence = {
        c["company_name"]: {
            "pages": c.get("pages"),
            "entity_grain": c.get("entity_grain"),
            "subtotal_event": c.get("subtotal_event"),
            "industry": c.get("industry"),
            "geography": c.get("geography"),
        }
        for c in companies
        if c.get("company_name")
    }

    df = pd.read_csv(vendor_csv, usecols=["Fund Allocator ID", "As At Date", "Source Asset"], low_memory=False)
    slice_df = df[df["Fund Allocator ID"].astype(str) == str(fund_id)].copy()
    slice_df["_iso"] = slice_df["As At Date"].map(lambda v: _parse_vendor_date(v, ["%d-%b-%y", "%d-%b-%Y", "%Y-%m-%d"]))
    if as_of:
        slice_df = slice_df[slice_df["_iso"] == as_of]
    # Exclude Non-Investment rows from matching pool (still available as statement rollups)
    vendor_names = [
        str(v)
        for v in slice_df["Source Asset"].dropna().astype(str).tolist()
        if not str(v).lower().startswith("non-investment")
    ]

    proposal = build_mapping_proposal(
        pdf_companies=pdf_names,
        vendor_names=vendor_names,
        fund_id=str(fund_id),
        as_of_date=as_of,
        pdf_realized=realized_names,
        company_evidence=evidence,
        aliases=load_aliases(),
        llm_enabled=llm_enabled,
    )
    proposal["route"] = {
        "extraction_mode": route.get("extraction_mode"),
        "template_family": route.get("template_family"),
        "comparison_grain": route.get("comparison_grain"),
    }
    proposal["vendor_row_count"] = int(len(slice_df))
    out = review_dir(extraction_dir) / "mapping_proposal.json"
    out.write_text(json.dumps(proposal, indent=2, ensure_ascii=False), encoding="utf-8")
    return proposal


def load_or_build_proposal(
    *,
    extraction_dir: Path,
    vendor_csv: Path,
    fund_id: str,
    as_of_date: str | None = None,
    rebuild: bool = False,
    llm_enabled: bool = False,
) -> dict[str, Any]:
    path = review_dir(extraction_dir) / "mapping_proposal.json"
    if path.exists() and not rebuild:
        return json.loads(path.read_text(encoding="utf-8"))
    return build_proposal_for_extraction(
        extraction_dir=extraction_dir,
        vendor_csv=vendor_csv,
        fund_id=fund_id,
        as_of_date=as_of_date,
        llm_enabled=llm_enabled,
    )


def _base_mapping_template(fund_id: str, *, pkg_root: Path) -> dict[str, Any]:
    mapping, _, _ = load_mapping_for_fund(
        fund_id,
        pkg_root=pkg_root,
        allow_default_native_template=True,
    )
    if mapping is None:
        raise ValueError(f"No mapping template available for fund {fund_id}")
    mapping = json.loads(json.dumps(mapping))  # deep copy
    mapping["comparability"]["fund_identity"]["vendor_fund_id"] = fund_id
    mapping["entity_mappings"] = []
    mapping["comparability"]["currency"]["waiver"]["enabled"] = True
    mapping["comparability"]["unit"]["waiver"]["enabled"] = True
    mapping["comparability"]["grain"]["fuzzy_candidate_generation"] = True
    return mapping


def materialize_draft_mapping(
    *,
    extraction_dir: Path,
    proposal: dict[str, Any],
    review_state: dict[str, Any] | None = None,
    pkg_root: Path | None = None,
) -> Path:
    """Write draft_vendor_mapping.json from proposal + review decisions."""
    extraction_dir = Path(extraction_dir)
    pkg_root = pkg_root or Path(__file__).resolve().parents[2]
    fund_id = str(proposal.get("fund_id") or "")
    as_of = proposal.get("as_of_date")
    route = proposal.get("route") or _load_route(extraction_dir)

    mapping = _base_mapping_template(fund_id, pkg_root=pkg_root)
    mapping["mapping_id"] = f"draft_{fund_id}_{proposal.get('proposal_id')}"
    mapping["mapping_version"] = "draft"
    mapping["draft"] = True
    mapping["proposal_id"] = proposal.get("proposal_id")
    mapping["policy_version"] = proposal.get("policy_version")
    if as_of:
        mapping["comparability"]["as_of_date"]["pdf_normalized_expected"] = as_of
    mode = route.get("extraction_mode") or "position_level"
    mapping["extraction_mode"] = mode
    if mode == "position_level_inferred":
        mapping["allow_inferred_compare"] = True
    mapping["comparability"]["grain"]["pdf_comparison_grain"] = route.get("comparison_grain") or "company"
    mapping["entity_mappings"] = apply_review_decisions(proposal, review_state)

    unresolved = [
        r["pdf_company_name"]
        for r in (proposal.get("entities") or [])
        if r.get("status") == "needs_review"
        and not any(
            d.get("pdf_company_name") == r["pdf_company_name"] and d.get("action") in {"accept", "reject", "not_vendor_holding", "leave_unmapped"}
            for d in ((review_state or {}).get("decisions") or [])
        )
    ]
    mapping["draft_unresolved_entities"] = unresolved

    out = review_dir(extraction_dir) / "draft_vendor_mapping.json"
    out.write_text(json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def unresolved_needs_review(
    proposal: dict[str, Any],
    review_state: dict[str, Any] | None = None,
) -> list[str]:
    """PDF entities still waiting on human accept/reject/etc."""
    decisions = {
        d.get("pdf_company_name"): d
        for d in ((review_state or {}).get("decisions") or [])
        if d.get("pdf_company_name")
    }
    unresolved: list[str] = []
    for row in proposal.get("entities") or []:
        name = row.get("pdf_company_name")
        if not name:
            continue
        if row.get("status") != "needs_review":
            continue
        action = (decisions.get(name) or {}).get("action")
        if action in {"accept", "reject", "not_vendor_holding", "leave_unmapped"}:
            continue
        unresolved.append(name)
    return unresolved


AUTO_SKIP_EXTRACTION_CASES = {"not_a_holdings_pdf"}
AUTO_SKIP_ACK_ACTIONS = {
    "skip_no_holdings_schedule",
    "skip_not_comparable",
    "acknowledged",
    "auto_skipped_not_holdings",
}


def classify_extraction_gate(extraction_dir: Path) -> dict[str, Any] | None:
    """Classify extract-gate case without auto-acking or human-ack filtering."""
    extraction_dir = Path(extraction_dir)
    route = _load_route(extraction_dir)
    mode = route.get("extraction_mode")
    if mode not in {"manual_review", "blocked_narrative"}:
        return None

    inferred = route.get("inferred_schema") or {}
    reasons = [str(r) for r in (route.get("reasons") or [])]
    human = _humanize_extraction_gate(mode=mode, reasons=reasons, route=route, inferred=inferred)
    return {
        "kind": "extraction_manual_review" if mode == "manual_review" else "blocked_narrative",
        "review_kind": "pdf_evidence" if human["case"] == "columns_unclear" else "document_triage",
        "extraction_mode": mode,
        "case": human["case"],
        "auto_skippable": human["case"] in AUTO_SKIP_EXTRACTION_CASES or mode == "blocked_narrative",
        "verdict_en": human["verdict_en"],
        "verdict_zh": human["verdict_zh"],
        "why_en": human["why_en"],
        "why_zh": human["why_zh"],
        "you_should_en": human["you_should_en"],
        "you_should_zh": human["you_should_zh"],
        "you_should_not_en": human["you_should_not_en"],
        "you_should_not_zh": human["you_should_not_zh"],
        "primary_action": human["primary_action"],
        "as_of_date": route.get("as_of_date"),
        "schedule_pages": list(route.get("schedule_pages") or []),
        "reasons": reasons,
        "inferred_columns": list(inferred.get("logical_columns") or []),
        "missing_required": list(inferred.get("missing_required") or []),
        "recovery_notes": list(inferred.get("recovery_notes") or []),
        "inference_confidence": inferred.get("inference_confidence"),
        "routing_evidence": route.get("routing_evidence") or {},
        "document_id": route.get("document_id"),
        "pdf_path": route.get("pdf_path"),
        "next_steps": human["next_steps"],
    }


def maybe_auto_skip_extraction_gate(extraction_dir: Path) -> dict[str, Any] | None:
    """Auto-ack clear non-holdings PDFs; never open a confirm UI for them."""
    extraction_dir = Path(extraction_dir)
    state = load_review_state(extraction_dir)
    ack = state.get("extraction_ack") or {}
    if ack.get("action") in AUTO_SKIP_ACK_ACTIONS:
        return {
            "auto_skipped": True,
            "already_acked": True,
            "action": ack.get("action"),
            "case": (ack.get("case") or "not_a_holdings_pdf"),
        }

    gate = classify_extraction_gate(extraction_dir)
    if not gate:
        return None
    case = gate.get("case")
    # Auto-skip narrative / empty extracts without schedule pages.
    auto = bool(gate.get("auto_skippable")) or (
        case == "extract_uncertain" and not (gate.get("schedule_pages") or [])
    )
    if not auto:
        return None

    acknowledge_extraction_gate(
        extraction_dir,
        action="auto_skipped_not_holdings",
        notes=f"auto: case={case} mode={gate.get('extraction_mode')}",
        reviewer="system",
    )
    state = load_review_state(extraction_dir)
    ack = dict(state.get("extraction_ack") or {})
    ack["case"] = case
    ack["auto"] = True
    state["extraction_ack"] = ack
    save_review_state(extraction_dir, state)
    return {
        "auto_skipped": True,
        "already_acked": False,
        "action": "auto_skipped_not_holdings",
        "case": case,
        "gate": gate,
    }


def extraction_requires_human_review(extraction_dir: Path) -> dict[str, Any] | None:
    """Return gate info only when a human must look at PDF extract evidence.

    Clear non-holdings PDFs are auto-skipped and never returned here.
    """
    extraction_dir = Path(extraction_dir)
    maybe_auto_skip_extraction_gate(extraction_dir)
    state = load_review_state(extraction_dir)
    ack = state.get("extraction_ack") or {}
    if ack.get("action") in AUTO_SKIP_ACK_ACTIONS:
        return None

    gate = classify_extraction_gate(extraction_dir)
    if not gate:
        return None
    # Only suspected holdings tables with unclear Cost/FV need a human extract gate.
    if gate.get("case") == "columns_unclear":
        return gate
    # Everything else in triage is either auto-skipped already or not a human gate.
    return None


def _humanize_extraction_gate(
    *,
    mode: str,
    reasons: list[str],
    route: dict[str, Any],
    inferred: dict[str, Any],
) -> dict[str, Any]:
    """Plain-language case for non-engineers."""
    joined = " ".join(reasons).lower()
    pages = list(route.get("schedule_pages") or [])
    missing = list(inferred.get("missing_required") or [])

    narrativeish = mode == "blocked_narrative" or any(
        x in joined
        for x in (
            "investor_letter",
            "no_schedule_or_aggregate_signal",
            "strategy",
            "narrative",
        )
    )
    if narrativeish and not pages:
        return {
            "case": "not_a_holdings_pdf",
            "verdict_en": "This PDF is not a holdings statement — auto-skipped.",
            "verdict_zh": "这份 PDF 不是公司持仓报表 — 系统已自动跳过。",
            "why_en": "It looks like a strategy / outlook / investor letter (not a Schedule of Investments or audited FS with a portfolio table).",
            "why_zh": "它更像策略季报 / 市场展望 / LP 信函，不是「Schedule of Investments」或带持仓表的审计报表。",
            "you_should_en": [
                "Nothing to do — the system already skipped this PDF for holdings compare.",
                "Process a real holdings PDF next (audited FS / SOI / portfolio schedule).",
            ],
            "you_should_zh": [
                "无需操作 — 系统已自动跳过这份非持仓 PDF。",
                "下一份改用真正的持仓 PDF（审计报表 / SOI / 投资明细表）。",
            ],
            "you_should_not_en": [
                "Do not try to map company names.",
                "Do not hunt for Cost / Fair Value columns on this file.",
            ],
            "you_should_not_zh": [
                "不用做公司名称匹配。",
                "不用在这份文件里找 Cost / Fair Value 列。",
            ],
            "primary_action": "auto_skipped_not_holdings",
            "next_steps": [
                "Auto-skipped: not a holdings schedule.",
                "Use a holdings / audited financial-statement PDF instead.",
            ],
        }

    if pages or missing:
        miss = ", ".join(missing) if missing else "Cost and/or Fair Value"
        page_txt = ", ".join(str(p) for p in pages) if pages else "unknown"
        return {
            "case": "columns_unclear",
            "verdict_en": "A holdings-looking table was found, but Cost / Fair Value columns are unclear.",
            "verdict_zh": "找到了像持仓表的页面，但 Cost / Fair Value 列还不清楚。",
            "why_en": f"Candidate pages: {page_txt}. Still missing or uncertain: {miss}.",
            "why_zh": f"候选页：{page_txt}。仍缺或不明确：{miss}。",
            "you_should_en": [
                f"Open the PDF and look at page(s) {page_txt}.",
                "Tell an engineer which columns are Cost and Fair Value (or that only Fair Value exists).",
                "Or click “Needs engineer help” if you are unsure.",
            ],
            "you_should_zh": [
                f"打开 PDF，查看第 {page_txt} 页。",
                "告诉工程师哪几列是 Cost / Fair Value（或是否只有 Fair Value）。",
                "如果不确定，点「需要工程师帮忙」。",
            ],
            "you_should_not_en": ["Do not save name matches until extract clears."],
            "you_should_not_zh": ["抽取通过之前，不要保存名称对应。"],
            "primary_action": "needs_engineer",
            "next_steps": [
                f"Inspect schedule page candidates: {page_txt}.",
                f"Confirm columns for: {miss}.",
            ],
        }

    return {
        "case": "extract_uncertain",
        "verdict_en": "Automatic extract could not use this PDF for company holdings.",
        "verdict_zh": "自动抽取没法用这份 PDF 做公司持仓。",
        "why_en": "The system did not find a clear holdings table or Cost / Fair Value columns.",
        "why_zh": "系统没找到清晰的持仓表，也没认出 Cost / Fair Value 列。",
        "you_should_en": [
            "Quickly open the PDF: if it is only outlook / metrics / letter text → skip.",
            "If you see a real company holdings table → mark “Needs engineer help”.",
        ],
        "you_should_zh": [
            "先快速打开 PDF：如果只是展望 / 指标 / 信函文字 → 跳过。",
            "如果能看到公司持仓明细表 → 点「需要工程师帮忙」。",
        ],
        "you_should_not_en": ["Do not invent name matches on an empty extract."],
        "you_should_not_zh": ["抽取是空的时候，不要硬做名称匹配。"],
        "primary_action": "skip_no_holdings_schedule",
        "next_steps": [
            "Decide whether this PDF even has a holdings schedule.",
            "Skip if not; escalate to engineer if yes.",
        ],
    }


def acknowledge_extraction_gate(
    extraction_dir: Path,
    *,
    action: str,
    notes: str | None = None,
    reviewer: str | None = None,
) -> dict[str, Any]:
    """Record that a human finished the extract-gate decision for this PDF."""
    allowed = {
        "skip_no_holdings_schedule",
        "skip_not_comparable",
        "needs_engineer",
        "acknowledged",
        "auto_skipped_not_holdings",
    }
    if action not in allowed:
        raise ValueError(f"unsupported extraction ack action: {action}")
    state = load_review_state(extraction_dir)
    state["extraction_ack"] = {
        "action": action,
        "notes": notes,
        "reviewer": reviewer,
        "acked_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    save_review_state(extraction_dir, state)
    return state


def review_open_reason(
    *,
    extraction_dir: Path,
    vendor_csv: Path,
    fund_id: str,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Decide whether the localhost review UI should open for this extraction."""
    extraction_dir = Path(extraction_dir)
    # Auto-skip non-holdings PDFs before any UI decision.
    auto = maybe_auto_skip_extraction_gate(extraction_dir)
    gate = extraction_requires_human_review(extraction_dir)
    proposal = load_or_build_proposal(
        extraction_dir=extraction_dir,
        vendor_csv=vendor_csv,
        fund_id=fund_id,
        as_of_date=as_of_date,
    )
    needs = unresolved_needs_review(proposal, load_review_state(extraction_dir))

    from pdf_validation.discrepancy_review import load_unresolved_discrepancies

    discrepancies = load_unresolved_discrepancies(extraction_dir)

    if gate:
        return {
            "should_open": True,
            "reason": "pdf_evidence_review",
            "review_kind": "pdf_evidence",
            "extraction_gate": gate,
            "needs_review_unresolved": needs,
            "discrepancies": discrepancies,
            "auto_skip": auto,
            "proposal": proposal,
        }
    if needs:
        return {
            "should_open": True,
            "reason": "entity_needs_review",
            "review_kind": "name_mapping",
            "extraction_gate": None,
            "needs_review_unresolved": needs,
            "discrepancies": discrepancies,
            "auto_skip": auto,
            "proposal": proposal,
        }
    if discrepancies:
        return {
            "should_open": True,
            "reason": "amount_discrepancy",
            "review_kind": "amount_discrepancy",
            "extraction_gate": None,
            "needs_review_unresolved": needs,
            "discrepancies": discrepancies,
            "auto_skip": auto,
            "proposal": proposal,
        }
    return {
        "should_open": False,
        "reason": "auto_skipped_not_holdings" if auto and auto.get("auto_skipped") else "no_human_review_needed",
        "review_kind": None,
        "extraction_gate": None,
        "needs_review_unresolved": [],
        "discrepancies": [],
        "auto_skip": auto,
        "proposal": proposal,
    }


def run_draft_compare(
    *,
    extraction_dir: Path,
    vendor_csv: Path,
    fund_id: str,
    as_of_date: str | None = None,
    repo_root: Path | None = None,
    pkg_root: Path | None = None,
    rebuild_proposal: bool = False,
    llm_enabled: bool = False,
) -> dict[str, Any]:
    """Build/load proposal, materialize draft mapping, run compare into mapping_review/compare."""
    extraction_dir = Path(extraction_dir)
    vendor_csv = Path(vendor_csv)
    repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[3]
    pkg_root = Path(pkg_root) if pkg_root else Path(__file__).resolve().parents[2]

    proposal = load_or_build_proposal(
        extraction_dir=extraction_dir,
        vendor_csv=vendor_csv,
        fund_id=fund_id,
        as_of_date=as_of_date,
        rebuild=rebuild_proposal,
        llm_enabled=llm_enabled,
    )
    state = load_review_state(extraction_dir)
    draft_path = materialize_draft_mapping(
        extraction_dir=extraction_dir,
        proposal=proposal,
        review_state=state,
        pkg_root=pkg_root,
    )
    compare_out = review_dir(extraction_dir) / "compare"
    report = compare_with_vendor(
        extraction_dir=extraction_dir,
        vendor_csv=vendor_csv,
        mapping_config=draft_path,
        output_dir=compare_out,
        repo_root=repo_root,
    )
    needs = unresolved_needs_review(proposal, state)
    extraction_gate = extraction_requires_human_review(extraction_dir)
    summary = {
        "proposal_id": proposal.get("proposal_id"),
        "policy_version": proposal.get("policy_version"),
        "fund_id": fund_id,
        "as_of_date": proposal.get("as_of_date"),
        "proposal_summary": proposal.get("summary"),
        "accepted_mappings": len(json.loads(draft_path.read_text(encoding="utf-8")).get("entity_mappings") or []),
        "unresolved_entities": json.loads(draft_path.read_text(encoding="utf-8")).get("draft_unresolved_entities") or [],
        "needs_review_unresolved": needs,
        "extraction_gate": extraction_gate,
        "needs_human_review": bool(needs) or bool(extraction_gate),
        "comparability_status": report.get("comparability_status"),
        "overall_status": report.get("overall_status"),
        "amount_mismatch_count": (report.get("summary") or {}).get("amount_mismatch_count"),
        "confirmed_entity_mappings": (report.get("summary") or {}).get("confirmed_entity_mappings"),
        "draft_mapping_path": str(draft_path),
        "compare_dir": str(compare_out),
        "gates": report.get("gates") or [],
    }
    (review_dir(extraction_dir) / "compare_summary.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )
    return summary


def maybe_open_mapping_review(
    *,
    extraction_dir: Path,
    vendor_csv: Path,
    fund_id: str,
    as_of_date: str | None = None,
    repo_root: Path | None = None,
    pkg_root: Path | None = None,
    port: int = 8765,
    open_browser: bool = True,
    block: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """Start localhost review UI when extract is blocked or entity names need review.

    ``block=True`` keeps the server in this process (CLI default).
    ``block=False`` spawns a lasting ``review-mapping`` subprocess — never a
    daemon thread (those die when the parent exits and leave Safari unable to connect).
    """
    from pdf_validation.mapping_review_server import serve_mapping_review

    decision = review_open_reason(
        extraction_dir=extraction_dir,
        vendor_csv=vendor_csv,
        fund_id=fund_id,
        as_of_date=as_of_date,
    )
    needs = list(decision.get("needs_review_unresolved") or [])
    gate = decision.get("extraction_gate")
    open_reason = str(decision.get("reason") or "no_human_review_needed")
    if not decision.get("should_open") and not force:
        return {
            "opened": False,
            "reason": open_reason,
            "needs_review_unresolved": needs,
            "extraction_gate": gate,
        }
    if force and not decision.get("should_open"):
        open_reason = "forced"

    if not block:
        return launch_review_subprocess(
            extraction_dir=extraction_dir,
            vendor_csv=vendor_csv,
            fund_id=fund_id,
            as_of_date=as_of_date,
            port=port,
            open_browser=open_browser,
            needs_review_unresolved=needs,
            open_reason=open_reason,
            extraction_gate=gate,
        )

    server = serve_mapping_review(
        extraction_dir=Path(extraction_dir),
        vendor_csv=Path(vendor_csv),
        fund_id=fund_id,
        as_of_date=as_of_date,
        repo_root=repo_root,
        pkg_root=pkg_root,
        port=port,
        open_browser=open_browser,
    )
    bound_port = int(server.server_address[1])
    info = {
        "opened": True,
        "url": f"http://127.0.0.1:{bound_port}/",
        "needs_review_unresolved": needs,
        "extraction_gate": gate,
        "open_reason": open_reason,
        "port": bound_port,
        "blocking": True,
        "mode": "in_process",
    }
    try:
        extra = f" for {len(needs)} unresolved entit(y/ies)" if needs else ""
        print(f"Opening review UI ({open_reason}){extra}: {info['url']}")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped review server.")
    finally:
        server.server_close()
    return info


def launch_review_subprocess(
    *,
    extraction_dir: Path,
    vendor_csv: Path,
    fund_id: str,
    as_of_date: str | None = None,
    port: int = 8765,
    open_browser: bool = True,
    needs_review_unresolved: list[str] | None = None,
    open_reason: str | None = None,
    extraction_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Spawn ``python -m pdf_validation review-mapping`` and wait until the port accepts connections."""
    import socket
    import subprocess
    import sys
    import time

    extraction_dir = Path(extraction_dir).resolve()
    vendor_csv = Path(vendor_csv).resolve()
    cmd = [
        sys.executable,
        "-m",
        "pdf_validation",
        "review-mapping",
        "--extraction-dir",
        str(extraction_dir),
        "--vendor-csv",
        str(vendor_csv),
        "--fund-id",
        str(fund_id),
        "--port",
        str(port),
    ]
    if as_of_date:
        cmd.extend(["--as-of", str(as_of_date)])
    if not open_browser:
        cmd.append("--no-browser")

    env = dict(os.environ)
    pkg_src = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = pkg_src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    # Free stale listeners on the same port from prior crashed demos.
    _terminate_listeners_on_port(port)

    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    url = f"http://127.0.0.1:{port}/"
    ready = False
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"review-mapping exited early (code={proc.returncode}): {out[:800]}")
        listener_pids = _listener_pids_on_port(port)
        if proc.pid in listener_pids or any(_is_child_of(pid, proc.pid) for pid in listener_pids):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    ready = True
                    break
            except OSError:
                pass
        time.sleep(0.2)
    if not ready:
        proc.terminate()
        raise RuntimeError(
            f"review-mapping did not listen on {url} within 20s "
            f"(proc={proc.pid}, listeners={_listener_pids_on_port(port)})"
        )

    print(f"Review server ready ({open_reason or 'review'}): {url} (pid={proc.pid})")
    return {
        "opened": True,
        "url": url,
        "needs_review_unresolved": needs_review_unresolved or [],
        "extraction_gate": extraction_gate,
        "open_reason": open_reason,
        "port": port,
        "blocking": False,
        "mode": "subprocess",
        "pid": proc.pid,
    }


def _listener_pids_on_port(port: int) -> set[int]:
    import subprocess

    try:
        out = subprocess.check_output(["lsof", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    pids: set[int] = set()
    for raw in out.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            pids.add(int(raw))
        except ValueError:
            continue
    return pids


def _is_child_of(pid: int, ancestor: int) -> bool:
    """True if pid is the same as ancestor or a descendant (best-effort via ppid walk)."""
    if pid == ancestor:
        return True
    try:
        import psutil  # optional

        cur = psutil.Process(pid)
        for _ in range(8):
            if cur.pid == ancestor:
                return True
            cur = cur.parent()
            if cur is None:
                break
    except Exception:
        # Fallback: single-level ppid via ps
        import subprocess

        try:
            out = subprocess.check_output(["ps", "-o", "ppid=", "-p", str(pid)], text=True).strip()
            return int(out) == ancestor
        except Exception:
            return False
    return False


def _terminate_listeners_on_port(port: int) -> None:
    """Best-effort clear of an occupied localhost port."""
    import signal
    import subprocess
    import time

    pids = _listener_pids_on_port(port)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            continue
    deadline = time.time() + 2.0
    while time.time() < deadline and _listener_pids_on_port(port):
        time.sleep(0.1)
    for pid in _listener_pids_on_port(port):
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ValueError):
            continue


def preview_promote_diff(
    *,
    extraction_dir: Path,
    fund_id: str,
    pkg_root: Path | None = None,
) -> dict[str, Any]:
    """Preview files that would change on promote. Does not write."""
    extraction_dir = Path(extraction_dir)
    pkg_root = Path(pkg_root) if pkg_root else Path(__file__).resolve().parents[2]
    draft_path = review_dir(extraction_dir) / "draft_vendor_mapping.json"
    if not draft_path.exists():
        raise FileNotFoundError("draft_vendor_mapping.json missing; run draft compare first")
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    target = pkg_root / "configs" / "approved_mappings" / f"{fund_id}_vendor_mapping.json"
    registry_path = pkg_root / "configs" / "vendor_mapping_registry.json"
    registry = load_vendor_mapping_registry(registry_path)
    existing_entry = (registry.get("by_fund_id") or {}).get(str(fund_id))

    alias_updates = {}
    for m in draft.get("entity_mappings") or []:
        pdf = m.get("pdf_company_name")
        vendor = m.get("vendor_source_asset")
        if pdf and vendor and m.get("confirmation") in {"user_review", "alias", "fuzzy", "fuzzy+llm", "exact"}:
            from pdf_validation.entity_mapping import normalize_entity_name

            alias_updates[normalize_entity_name(pdf)] = vendor

    return {
        "target_mapping_path": str(target),
        "target_exists": target.exists(),
        "registry_path": str(registry_path),
        "registry_entry_exists": existing_entry is not None,
        "proposed_registry_entry": {
            "mapping_config": f"configs/approved_mappings/{fund_id}_vendor_mapping.json",
            "template_family": (draft.get("route") or {}).get("template_family")
            or (_load_route(extraction_dir).get("template_family")),
            "extraction_mode": draft.get("extraction_mode"),
        },
        "entity_mapping_count": len(draft.get("entity_mappings") or []),
        "fund_scoped_alias_updates": alias_updates,
        "draft_proposal_id": draft.get("proposal_id"),
    }


def promote_draft_mapping(
    *,
    extraction_dir: Path,
    fund_id: str,
    reviewer: str,
    pkg_root: Path | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Explicitly promote draft mapping into approved_mappings + registry.

    Requires confirm=True. Writes fund-scoped aliases only.
    """
    if not confirm:
        raise ValueError("promote_draft_mapping requires confirm=True")
    extraction_dir = Path(extraction_dir)
    pkg_root = Path(pkg_root) if pkg_root else Path(__file__).resolve().parents[2]
    preview = preview_promote_diff(extraction_dir=extraction_dir, fund_id=fund_id, pkg_root=pkg_root)
    draft_path = review_dir(extraction_dir) / "draft_vendor_mapping.json"
    draft = json.loads(draft_path.read_text(encoding="utf-8"))

    # Strip draft markers for approved copy
    approved = dict(draft)
    approved.pop("draft", None)
    approved.pop("draft_unresolved_entities", None)
    approved["mapping_id"] = f"{fund_id}_approved"
    approved["mapping_version"] = datetime.now(timezone.utc).strftime("%Y.%m.%d")
    approved["review"] = {
        "reviewer": reviewer,
        "approved_at_utc": datetime.now(timezone.utc).isoformat(),
        "proposal_id": draft.get("proposal_id"),
        "policy_version": draft.get("policy_version"),
        "source_draft": str(draft_path),
    }

    target = Path(preview["target_mapping_path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(approved, indent=2, ensure_ascii=False), encoding="utf-8")

    # Update registry
    registry_path = Path(preview["registry_path"])
    registry = load_vendor_mapping_registry(registry_path)
    registry.setdefault("by_fund_id", {})[str(fund_id)] = preview["proposed_registry_entry"]
    # Backup registry
    backup = registry_path.with_suffix(".json.bak")
    shutil.copy2(registry_path, backup)
    registry_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Fund-scoped aliases only
    from pdf_validation.entity_mapping import load_aliases, normalize_entity_name

    aliases_path = pkg_root / "configs" / "entity_aliases.json"
    aliases = load_aliases(aliases_path)
    aliases.setdefault("by_fund", {}).setdefault(str(fund_id), {})
    for pdf_norm, vendor in (preview.get("fund_scoped_alias_updates") or {}).items():
        aliases["by_fund"][str(fund_id)][pdf_norm] = vendor
    aliases_path.write_text(json.dumps(aliases, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    result = {
        "status": "promoted",
        "mapping_path": str(target),
        "registry_path": str(registry_path),
        "registry_backup": str(backup),
        "aliases_path": str(aliases_path),
        "reviewer": reviewer,
        "preview": preview,
    }
    (review_dir(extraction_dir) / "promotion_result.json").write_text(
        json.dumps(result, indent=2, default=str),
        encoding="utf-8",
    )
    return result


def review_payload(
    *,
    extraction_dir: Path,
    vendor_csv: Path,
    fund_id: str,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Aggregate payload for the browser review UI."""
    from pdf_validation.review_glossary import enrich_entity_for_ui, glossary_payload, label_extraction_mode

    proposal = load_or_build_proposal(
        extraction_dir=extraction_dir,
        vendor_csv=vendor_csv,
        fund_id=fund_id,
        as_of_date=as_of_date,
    )
    state = load_review_state(extraction_dir)
    compare_summary = None
    cs_path = review_dir(extraction_dir) / "compare_summary.json"
    if cs_path.exists():
        compare_summary = json.loads(cs_path.read_text(encoding="utf-8"))
    onboarding = {}
    onb_path = Path(extraction_dir) / "onboarding_summary.json"
    if onb_path.exists():
        onboarding = json.loads(onb_path.read_text(encoding="utf-8"))
    route = _load_route(extraction_dir)

    companies = {
        c.get("company_name"): c
        for c in _read_jsonl(Path(extraction_dir) / "company_summary.jsonl")
        if c.get("company_name")
    }
    amount_rows = _read_jsonl(review_dir(extraction_dir) / "compare" / "amount_comparisons.jsonl")
    amounts_by_pdf: dict[str, list[dict[str, Any]]] = {}
    for row in amount_rows:
        key = row.get("pdf_company_name")
        if key:
            amounts_by_pdf.setdefault(str(key), []).append(row)

    # Cover / metadata fund name — used to detect fund-self false positives.
    fund_name = None
    for meta in _read_jsonl(Path(extraction_dir) / "metadata.jsonl"):
        if meta.get("field") == "fund_name" and meta.get("raw"):
            fund_name = str(meta.get("normalized") or meta.get("raw"))
            break

    # Merge user decisions into entities for display
    decisions = {d["pdf_company_name"]: d for d in (state.get("decisions") or [])}
    entities = []
    for row in proposal.get("entities") or []:
        item = dict(row)
        user = decisions.get(row["pdf_company_name"])
        if user:
            item["user_decision"] = user.get("action")
            item["user_vendor_source_asset"] = user.get("vendor_source_asset")
            item["user_notes"] = user.get("notes")
        name = row.get("pdf_company_name")
        entities.append(
            enrich_entity_for_ui(
                item,
                company=companies.get(name),
                amounts=amounts_by_pdf.get(str(name) if name else "", []),
                fund_name=fund_name,
            )
        )

    mode_info = label_extraction_mode(route.get("extraction_mode"))
    summary = proposal.get("summary") or {}
    needs_n = sum(1 for e in entities if (e.get("ui") or {}).get("needs_action"))
    auto_n = len(entities) - needs_n

    pkg_root = Path(__file__).resolve().parents[2]
    approved_path = pkg_root / "configs" / "approved_mappings" / f"{fund_id}_vendor_mapping.json"
    promotion_path = review_dir(extraction_dir) / "promotion_result.json"
    promotion: dict[str, Any] | None = None
    if promotion_path.exists():
        try:
            promotion = json.loads(promotion_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            promotion = {"status": "promoted", "mapping_path": str(approved_path)}
    elif approved_path.exists():
        try:
            approved = json.loads(approved_path.read_text(encoding="utf-8"))
            review_meta = approved.get("review") or {}
            promotion = {
                "status": "promoted",
                "mapping_path": str(approved_path),
                "reviewer": review_meta.get("reviewer"),
                "approved_at_utc": review_meta.get("approved_at_utc"),
                "entity_mapping_count": len(approved.get("entity_mappings") or []),
            }
        except json.JSONDecodeError:
            promotion = {"status": "promoted", "mapping_path": str(approved_path)}
    promoted = bool(promotion) or approved_path.exists()
    maybe_auto_skip_extraction_gate(extraction_dir)
    state = load_review_state(extraction_dir)
    extraction_gate = extraction_requires_human_review(extraction_dir)
    extraction_ack = (state.get("extraction_ack") or None)

    from pdf_validation.discrepancy_review import build_review_items_payload
    from pdf_validation.review_preview import build_review_preview

    discrepancy_payload = build_review_items_payload(extraction_dir=extraction_dir, fund_id=fund_id)
    pending_disc = discrepancy_payload.get("pending_count") or 0
    preview_meta = build_review_preview(
        extraction_dir=extraction_dir,
        route=route,
        companies=list(companies.values()),
    )

    if extraction_gate:
        review_kind = "pdf_evidence"
        open_reason = "pdf_evidence_review"
    elif needs_n:
        review_kind = "name_mapping"
        open_reason = "entity_needs_review"
    elif pending_disc:
        review_kind = "amount_discrepancy"
        open_reason = "amount_discrepancy"
    elif extraction_ack and (extraction_ack.get("action") in AUTO_SKIP_ACK_ACTIONS):
        review_kind = None
        open_reason = "auto_skipped_not_holdings"
    else:
        review_kind = None
        open_reason = "acked" if extraction_ack else "none"

    return {
        "fund_id": fund_id,
        "as_of_date": proposal.get("as_of_date"),
        "fund_name": fund_name,
        "proposal_id": proposal.get("proposal_id"),
        "policy_version": proposal.get("policy_version"),
        "summary": summary,
        "summary_ui": {
            "auto_accept": f"建议自动通过 {summary.get('auto_accept', 0)}",
            "needs_review": f"待你确认 {summary.get('needs_review', 0)}",
            "unmapped": f"暂无匹配 {summary.get('unmapped', 0)}",
            "auto_accept_en": f"Auto-matched {summary.get('auto_accept', 0)}",
            "needs_review_en": f"Needs decision {summary.get('needs_review', 0)}",
            "unmapped_en": f"No Excel match {summary.get('unmapped', 0)}",
            "todo_count": needs_n,
            "auto_count": auto_n,
            "discrepancy_pending": pending_disc,
        },
        "route": route,
        "route_ui": {
            "extraction_label": mode_info["label"],
            "extraction_label_en": {
                "position_level": "Known-template extract",
                "position_level_inferred": "Inferred-layout extract",
                "fund_aggregate_only": "Fund aggregate only",
                "manual_review": "Manual extract review",
                "blocked_narrative": "Not a holdings PDF",
                "scanned_financial_statements": "Scanned OCR extract",
            }.get(str(route.get("extraction_mode") or ""), str(route.get("extraction_mode") or "")),
            "extraction_explanation": mode_info["explanation"],
            "template_family": route.get("template_family"),
        },
        "extraction_gate": extraction_gate,
        "extraction_ack": extraction_ack,
        "review_kind": review_kind,
        "needs_human_review": bool(needs_n) or bool(extraction_gate) or bool(pending_disc),
        "open_reason": open_reason,
        "onboarding": onboarding,
        "entities": entities,
        "discrepancy_items": discrepancy_payload.get("items") or [],
        "discrepancy_pending": discrepancy_payload.get("pending") or [],
        "preview": preview_meta,
        "review_state": state,
        "compare_summary": compare_summary,
        "glossary": glossary_payload(),
        "promote_ready": bool(
            not promoted
            and not extraction_gate
            and not pending_disc
            and compare_summary
            and compare_summary.get("comparability_status") == "comparable"
        ),
        "promoted": promoted,
        "promotion": promotion,
        "reviewed_excel_path": str(Path(extraction_dir) / "reviewed_extraction.xlsx"),
    }
