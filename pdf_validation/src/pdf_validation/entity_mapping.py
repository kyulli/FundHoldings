"""Entity alias loading and mapping proposal builders.

Proposal policy (policy_version=mapping_proposal_v1):
  - exact / approved alias with unique 1:1 target → auto_accept
  - fuzzy/LLM with score≥0.95, margin≥0.10, no conflicts → auto_accept
  - 0.80–0.95, close top-2, risk words, multi-map → needs_review
  - <0.80 or no candidate → unmapped
Amounts never enter matching features. Confirmed=false until draft/review accepts.
"""

from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from pdf_validation.vendor_comparison import _normalize_name as _base_normalize_name

POLICY_VERSION = "mapping_proposal_v1"
AUTO_ACCEPT_SCORE = 0.95
AUTO_ACCEPT_MARGIN = 0.10
REVIEW_MIN_SCORE = 0.80
FUZZY_FLOOR = 0.70
FUZZY_LIMIT = 5

_RISK_TOKENS = (
    "spv",
    "feeder",
    "holding",
    "holdings",
    "partners",
    "partnership",
    "lp",
    "l.p",
    "llc",
    "trust",
    "nominee",
    "warehouse",
)

_LOCATION_PAREN_RE = re.compile(
    r"\s*\((?:[A-Za-z .]+(?:,\s*[A-Z]{2})?|[A-Z]{2})\)\s*$"
)
_AKA_SPLIT_RE = re.compile(r"\s*;\s*aka\s+", flags=re.I)


def load_aliases(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        path = Path(__file__).resolve().parents[2] / "configs" / "entity_aliases.json"
    if not Path(path).exists():
        return {"global": {}, "by_fund": {}}
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def normalize_entity_name(value: str) -> str:
    """Match-oriented normalize: strip legal suffixes, location parentheses, aka clauses."""
    text = str(value or "")
    # Drop trailing location parentheses: "Homeward, Inc. (Austin, TX)"
    text = _LOCATION_PAREN_RE.sub("", text)
    # Prefer left side of "; aka ..."
    text = _AKA_SPLIT_RE.split(text, maxsplit=1)[0]
    # Drop parenthetical aka / dba content for matching
    text = re.sub(r"\((?:dba|aka|fka)[^)]*\)", " ", text, flags=re.I)
    return _base_normalize_name(text)


def resolve_alias(pdf_name: str, *, fund_id: str | None = None, aliases: dict[str, Any] | None = None) -> str | None:
    aliases = aliases or load_aliases()
    norm = normalize_entity_name(pdf_name)
    if fund_id:
        fund_map = (aliases.get("by_fund") or {}).get(fund_id) or {}
        if norm in fund_map:
            return fund_map[norm]
    return (aliases.get("global") or {}).get(norm)


def build_entity_mappings(
    pdf_companies: list[str],
    pdf_realized: list[str],
    vendor_names: list[str],
    *,
    fund_id: str | None = None,
    aliases: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Legacy exact/alias builder used by batch_runner. Confirmed for exact/alias only."""
    aliases = aliases or load_aliases()
    vendor_by_norm = {normalize_entity_name(v): v for v in vendor_names}
    mappings: list[dict[str, Any]] = []
    used_vendor: set[str] = set()

    def resolve(pdf_name: str) -> str | None:
        aliased = resolve_alias(pdf_name, fund_id=fund_id, aliases=aliases)
        if aliased:
            return aliased
        n = normalize_entity_name(pdf_name)
        return vendor_by_norm.get(n)

    for pdf_name in pdf_companies:
        vendor = resolve(pdf_name)
        if not vendor or vendor in used_vendor:
            continue
        item: dict[str, Any] = {
            "pdf_company_name": pdf_name,
            "vendor_source_asset": vendor,
            "confirmation": "exact_or_alias",
            "confirmed": True,
            "entity_grain": "company",
        }
        for rname in pdf_realized:
            if resolve(rname) == vendor:
                item["pdf_realized_company_name"] = rname
        mappings.append(item)
        used_vendor.add(vendor)

    for rname in pdf_realized:
        vendor = resolve(rname)
        if not vendor or vendor in used_vendor:
            continue
        mappings.append(
            {
                "pdf_company_name": None,
                "pdf_realized_company_name": rname,
                "vendor_source_asset": vendor,
                "confirmation": "exact_or_alias",
                "confirmed": True,
                "entity_grain": "company",
                "notes": "realized-only / fully exited candidate",
            }
        )
        used_vendor.add(vendor)
    return mappings


def _risk_flags(pdf_name: str, vendor_name: str) -> list[str]:
    flags: list[str] = []
    for raw in (pdf_name, vendor_name):
        low = (raw or "").lower()
        for tok in _RISK_TOKENS:
            if re.search(rf"\b{re.escape(tok)}\b", low):
                flags.append(f"risk:{tok}")
    # Parenthetical content often encodes city or aka — surface for review when residual remains.
    if "(" in (pdf_name or "") and _LOCATION_PAREN_RE.search(pdf_name or ""):
        flags.append("location_paren")
    return sorted(set(flags))


def _score_vendor(pdf_norm: str, vendor_name: str) -> float:
    return SequenceMatcher(None, pdf_norm, normalize_entity_name(vendor_name)).ratio()


def _fuzzy_rank(pdf_name: str, vendor_names: list[str], *, floor: float = FUZZY_FLOOR, limit: int = FUZZY_LIMIT) -> list[dict[str, Any]]:
    pdf_norm = normalize_entity_name(pdf_name)
    scored: list[tuple[float, str]] = []
    for vendor in vendor_names:
        score = _score_vendor(pdf_norm, vendor)
        if score >= floor:
            scored.append((score, vendor))
    scored.sort(reverse=True)
    out = []
    for score, vendor in scored[:limit]:
        out.append(
            {
                "vendor_source_asset": vendor,
                "score": round(score, 4),
                "match_type": "fuzzy",
                "confirmed": False,
            }
        )
    return out


def build_mapping_proposal(
    *,
    pdf_companies: list[str],
    vendor_names: list[str],
    fund_id: str | None = None,
    as_of_date: str | None = None,
    pdf_realized: list[str] | None = None,
    company_evidence: dict[str, dict[str, Any]] | None = None,
    aliases: dict[str, Any] | None = None,
    llm_enabled: bool = False,
    llm_backend: Any | None = None,
) -> dict[str, Any]:
    """Build a reviewable mapping proposal for one fund/as-of slice.

    Returns proposal dict with per-PDF-entity rows and summary counters.
    No entity is confirmed=True here; auto_accept is a *default decision* only.
    """
    aliases = aliases or load_aliases()
    company_evidence = company_evidence or {}
    pdf_realized = list(pdf_realized or [])
    # Deduplicate while preserving order
    pdf_entities: list[str] = []
    seen_pdf: set[str] = set()
    for name in list(pdf_companies) + pdf_realized:
        if not name or name in seen_pdf:
            continue
        seen_pdf.add(name)
        pdf_entities.append(name)

    vendor_entities = [v for v in vendor_names if isinstance(v, str) and v.strip()]
    vendor_by_norm: dict[str, list[str]] = {}
    for v in vendor_entities:
        vendor_by_norm.setdefault(normalize_entity_name(v), []).append(v)

    rows: list[dict[str, Any]] = []
    for pdf_name in pdf_entities:
        evidence = dict(company_evidence.get(pdf_name) or {})
        # Strip amount-like keys defensively
        for banned in ("amount", "cost", "fair_value", "csv_value", "pdf_value", "target_amount"):
            evidence.pop(banned, None)

        candidates: list[dict[str, Any]] = []
        decision_source = "none"
        recommended: str | None = None
        match_type = "none"
        score = 0.0
        margin = None
        risk = _risk_flags(pdf_name, "")

        # 1) approved alias
        aliased = resolve_alias(pdf_name, fund_id=fund_id, aliases=aliases)
        if aliased and aliased in set(vendor_entities):
            candidates.append(
                {
                    "vendor_source_asset": aliased,
                    "score": 1.0,
                    "match_type": "alias",
                    "confirmed": False,
                }
            )
            recommended = aliased
            match_type = "alias"
            score = 1.0
            decision_source = "alias"
            risk = _risk_flags(pdf_name, aliased)

        # 2) exact normalized
        pdf_norm = normalize_entity_name(pdf_name)
        exact_hits = vendor_by_norm.get(pdf_norm) or []
        if len(exact_hits) == 1:
            vendor = exact_hits[0]
            if not any(c["vendor_source_asset"] == vendor for c in candidates):
                candidates.insert(
                    0,
                    {
                        "vendor_source_asset": vendor,
                        "score": 1.0,
                        "match_type": "exact",
                        "confirmed": False,
                    },
                )
            if recommended is None:
                recommended = vendor
                match_type = "exact"
                score = 1.0
                decision_source = "exact"
                risk = _risk_flags(pdf_name, vendor)
        elif len(exact_hits) > 1:
            for vendor in exact_hits:
                candidates.append(
                    {
                        "vendor_source_asset": vendor,
                        "score": 1.0,
                        "match_type": "exact_ambiguous",
                        "confirmed": False,
                    }
                )

        # 3) fuzzy
        fuzzy = _fuzzy_rank(pdf_name, vendor_entities)
        for item in fuzzy:
            if any(c["vendor_source_asset"] == item["vendor_source_asset"] for c in candidates):
                continue
            candidates.append(item)

        if recommended is None and fuzzy:
            recommended = fuzzy[0]["vendor_source_asset"]
            match_type = "fuzzy"
            score = float(fuzzy[0]["score"])
            decision_source = "fuzzy"
            if len(fuzzy) >= 2:
                margin = round(float(fuzzy[0]["score"]) - float(fuzzy[1]["score"]), 4)
            risk = _risk_flags(pdf_name, recommended)

        # 4) optional LLM enrichment (candidate only)
        llm_audit_ref = None
        if llm_enabled and candidates and match_type in {"fuzzy", "none", "exact_ambiguous"}:
            try:
                from pdf_validation.llm.entity_ranker import rank_entity_candidates

                shortlist = [c["vendor_source_asset"] for c in candidates[:FUZZY_LIMIT]]
                ranked, audit = rank_entity_candidates(
                    pdf_company_name=pdf_name,
                    vendor_candidates=shortlist,
                    context={"industry": evidence.get("industry"), "geography": evidence.get("geography")},
                    backend=llm_backend,
                    use_cache=True,
                )
                llm_audit_ref = audit.get("input_hash")
                if ranked:
                    top = ranked[0]
                    # Attach LLM scores onto existing candidates
                    by_vendor = {c["vendor_source_asset"]: c for c in candidates}
                    for r in ranked:
                        target = by_vendor.get(r["vendor_source_asset_candidate"])
                        if target is not None:
                            target["llm_score"] = r.get("llm_score")
                            target["llm_rationale"] = r.get("llm_rationale")
                            target["recommended_action"] = r.get("recommended_action")
                    # If LLM top agrees with fuzzy top and high score, keep; else force review.
                    if top.get("vendor_source_asset_candidate") == recommended and float(top.get("llm_score") or 0) >= AUTO_ACCEPT_SCORE:
                        decision_source = "fuzzy+llm"
                        score = max(score, float(top.get("llm_score") or 0))
                    elif top.get("vendor_source_asset_candidate") and top.get("vendor_source_asset_candidate") != recommended:
                        decision_source = "llm_disagrees_fuzzy"
                        # Keep fuzzy recommendation but mark for review below
            except Exception:  # noqa: BLE001
                llm_audit_ref = None

        # Policy decision
        status = "unmapped"
        default_decision = "leave_unmapped"
        if recommended and match_type in {"exact", "alias"} and "exact_ambiguous" not in {c.get("match_type") for c in candidates}:
            status = "auto_accept"
            default_decision = "accept"
        elif recommended and score >= AUTO_ACCEPT_SCORE and (margin is None or margin >= AUTO_ACCEPT_MARGIN) and not risk and decision_source in {"fuzzy", "fuzzy+llm"}:
            # Check one-to-one uniqueness later in global pass
            status = "auto_accept"
            default_decision = "accept"
        elif recommended and score >= REVIEW_MIN_SCORE:
            status = "needs_review"
            default_decision = "needs_review"
        elif recommended:
            status = "unmapped"
            default_decision = "leave_unmapped"
        else:
            status = "unmapped"
            default_decision = "leave_unmapped"

        if decision_source == "llm_disagrees_fuzzy" or (margin is not None and margin < AUTO_ACCEPT_MARGIN and match_type == "fuzzy"):
            status = "needs_review"
            default_decision = "needs_review"
        if risk and status == "auto_accept" and match_type == "fuzzy":
            status = "needs_review"
            default_decision = "needs_review"

        input_payload = {
            "pdf_company_name": pdf_name,
            "fund_id": fund_id,
            "as_of_date": as_of_date,
            "recommended": recommended,
            "policy_version": POLICY_VERSION,
        }
        digest = hashlib.sha256(json.dumps(input_payload, sort_keys=True).encode("utf-8")).hexdigest()

        rows.append(
            {
                "pdf_company_name": pdf_name,
                "pdf_normalized": pdf_norm,
                "recommended_vendor_source_asset": recommended,
                "match_type": match_type,
                "score": round(float(score), 4) if recommended else None,
                "top2_margin": margin,
                "status": status,
                "default_decision": default_decision,
                "decision_source": decision_source,
                "risk_flags": risk,
                "candidates": candidates,
                "evidence": evidence,
                "llm_audit_ref": llm_audit_ref,
                "input_hash": digest,
                "confirmed": False,
                "user_decision": None,
                "user_vendor_source_asset": None,
            }
        )

    # Global conflict pass: multiple PDF entities auto-accepting same vendor → needs_review
    vendor_owners: dict[str, list[int]] = {}
    for idx, row in enumerate(rows):
        if row["status"] == "auto_accept" and row.get("recommended_vendor_source_asset"):
            vendor_owners.setdefault(row["recommended_vendor_source_asset"], []).append(idx)
    for vendor, owners in vendor_owners.items():
        if len(owners) > 1:
            for idx in owners:
                rows[idx]["status"] = "needs_review"
                rows[idx]["default_decision"] = "needs_review"
                rows[idx]["risk_flags"] = sorted(set(rows[idx].get("risk_flags") or []) | {"multi_pdf_same_vendor"})

    summary = {
        "pdf_entity_count": len(rows),
        "vendor_entity_count": len(vendor_entities),
        "auto_accept": sum(1 for r in rows if r["status"] == "auto_accept"),
        "needs_review": sum(1 for r in rows if r["status"] == "needs_review"),
        "unmapped": sum(1 for r in rows if r["status"] == "unmapped"),
    }
    proposal_id = hashlib.sha256(
        json.dumps(
            {
                "fund_id": fund_id,
                "as_of_date": as_of_date,
                "policy_version": POLICY_VERSION,
                "entities": [r["pdf_company_name"] for r in rows],
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]

    return {
        "proposal_id": proposal_id,
        "policy_version": POLICY_VERSION,
        "fund_id": fund_id,
        "as_of_date": as_of_date,
        "summary": summary,
        "entities": rows,
    }


def apply_review_decisions(proposal: dict[str, Any], review_state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Convert proposal + optional user decisions into confirmed entity_mappings for a draft.

    Auto-accept defaults apply when the user has not overridden a row.
    """
    review_state = review_state or {}
    decisions = {d.get("pdf_company_name"): d for d in (review_state.get("decisions") or []) if d.get("pdf_company_name")}
    mappings: list[dict[str, Any]] = []
    used_vendor: set[str] = set()

    for row in proposal.get("entities") or []:
        pdf_name = row["pdf_company_name"]
        user = decisions.get(pdf_name) or {}
        action = user.get("action") or row.get("default_decision")
        vendor = user.get("vendor_source_asset") or row.get("recommended_vendor_source_asset")

        if action in {"reject", "not_vendor_holding", "leave_unmapped", "needs_review", None}:
            if action == "needs_review" and not user.get("action"):
                # Unresolved review item — skip
                continue
            if action in {"reject", "not_vendor_holding", "leave_unmapped"}:
                continue
            if action == "needs_review":
                continue

        if action != "accept":
            continue
        if not vendor or vendor in used_vendor:
            continue
        mappings.append(
            {
                "pdf_company_name": pdf_name,
                "vendor_source_asset": vendor,
                "confirmation": "user_review" if user.get("action") else (row.get("decision_source") or "auto_accept"),
                "confirmed": True,
                "entity_grain": "company",
                "decision_source": row.get("decision_source"),
                "score": row.get("score"),
                "input_hash": row.get("input_hash"),
                "notes": user.get("notes") or f"proposal:{proposal.get('proposal_id')}",
            }
        )
        used_vendor.add(vendor)
    return mappings
