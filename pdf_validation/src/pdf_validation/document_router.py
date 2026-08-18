"""Route PDFs to document class + template family before extraction."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pdfplumber


def load_registry(registry_path: Path | None = None) -> dict[str, Any]:
    if registry_path is None:
        registry_path = Path(__file__).resolve().parents[2] / "configs" / "template_registry.json"
    with registry_path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _page_texts(pdf_path: Path, max_pages: int = 80) -> list[tuple[int, str]]:
    from pdf_validation.watermark import extract_clean_page_text

    pages: list[tuple[int, str]] = []
    with pdfplumber.open(pdf_path) as doc:
        limit = min(len(doc.pages), max_pages)
        for idx, page in enumerate(doc.pages[:limit]):
            text, _, _ = extract_clean_page_text(page)
            pages.append((idx + 1, (text or "").lower()))
    return pages


def _full_text(pages: list[tuple[int, str]]) -> str:
    return "\n".join(text for _, text in pages)


def _is_false_schedule_page(head: str, text: str) -> bool:
    """Reject notes/TOC/auditor pages that only mention a schedule title."""
    first = (head.strip().splitlines() or [""])[0].strip()
    if first.startswith("contents") or first == "contents":
        return True
    if "table of contents" in head or "table of contents" in text[:400]:
        return True
    # Notes pages often mention that a schedule is attached; title is not the page topic.
    if first.startswith("notes to") or "notes to financial" in first or "notes to consolidated" in first:
        return True
    if "notes to" in head[:80] and "schedule of investments" not in first and "condensed schedule" not in first:
        return True
    if "independent auditor" in head or "we have audited" in text[:500]:
        return True
    if "page(s)" in head:
        return True
    return False


def _find_schedule_pages(pages: list[tuple[int, str]], titles: list[str]) -> list[int]:
    primary: list[int] = []
    secondary: list[int] = []
    for page_num, text in pages:
        head = "\n".join(text.splitlines()[:10])
        if _is_false_schedule_page(head, text):
            continue
        # Prefer pages where a schedule title appears near the top.
        if any(title in head for title in titles):
            primary.append(page_num)
            continue
        # Secondary: title in body only when the page still looks like a schedule
        # (numeric grid + not a narrative notes page).
        if any(title in text for title in titles) and any(ch.isdigit() for ch in text):
            if "notes to financial" in head or "notes to consolidated" in head:
                continue
            # Require at least one amount-like token density signal.
            digit_lines = sum(1 for line in text.splitlines() if sum(ch.isdigit() for ch in line) >= 4)
            if digit_lines < 3:
                continue
            secondary.append(page_num)
    return primary or secondary


def _fingerprint_hits(text: str, fingerprints: list[str]) -> list[str]:
    return [token for token in fingerprints if token in text]


def _apply_score_boosters(
    *,
    text: str,
    filename: str,
    family: dict[str, Any],
    has_audit: bool,
) -> tuple[int, list[str]]:
    """Apply registry-declared score boosters. Returns (bonus, reason codes)."""
    bonus = 0
    reasons: list[str] = []
    for booster in family.get("score_boosters") or []:
        all_of = [str(x).lower() for x in (booster.get("all_of") or [])]
        any_of = [str(x).lower() for x in (booster.get("any_of") or [])]
        none_of = [str(x).lower() for x in (booster.get("none_of") or [])]
        if all_of and not all(tok in text for tok in all_of):
            continue
        if any_of and not any(tok in text for tok in any_of):
            continue
        if none_of and any(tok in text for tok in none_of):
            continue
        if booster.get("require_audit_or_filename") and not (
            has_audit or "audited" in filename or "afs" in filename or "final -" in filename
        ):
            continue
        weight = int(booster.get("weight") or 0)
        if weight:
            bonus += weight
            reasons.append(f"boost+{weight}")
    return bonus, reasons


def _has_audit_signal(text: str) -> bool:
    # Avoid matching the substring inside "unaudited financial statements".
    if "unaudited" in text and "independent auditor" not in text and "report of independent" not in text:
        return False
    return any(
        token in text
        for token in (
            "independent auditor",
            "independent auditors",
            "audited financial statements",
            "report of independent",
        )
    )


def _detect_as_of(text: str) -> str | None:
    month_map = {
        "january": "01",
        "february": "02",
        "march": "03",
        "april": "04",
        "may": "05",
        "june": "06",
        "july": "07",
        "august": "08",
        "september": "09",
        "october": "10",
        "november": "11",
        "december": "12",
    }

    def _iso(month: str, day: str, year: str) -> str:
        return f"{year}-{month_map[month.lower()]}-{int(day):02d}"

    # Prefer explicit period end / as-of statements over period start.
    preferred = [
        r"as of\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2}),\s*(\d{4})",
        r"ended\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2}),\s*(\d{4})",
        r"to\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2}),\s*(\d{4})",
        r"march\s+31,\s*2026",
    ]
    for pattern in preferred:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        if match.lastindex and match.lastindex >= 3:
            return _iso(match.group(1), match.group(2), match.group(3))

    matches = list(
        re.finditer(
            r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2}),\s*(\d{4})",
            text,
            flags=re.I,
        )
    )
    if matches:
        # If multiple dates (period from/to), take the last one as period end.
        m = matches[-1]
        return _iso(m.group(1), m.group(2), m.group(3))

    m = re.search(r"(\d{2})-(\d{2})-(\d{4})", text)
    if m:
        return f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
    return None


def _annotate_adapter_fields(
    route: dict[str, Any],
    *,
    text_source: str,
    pdf_path: Path,
    registry: dict[str, Any],
) -> dict[str, Any]:
    """Additive adapter routing fields; does not change existing extraction_mode semantics."""
    route = dict(route)
    route["text_source"] = text_source
    family = route.get("template_family")
    family_cfg = (registry.get("template_families") or {}).get(family or "") or {}
    if family_cfg.get("requires_ocr") or route.get("extraction_mode") == "scanned_financial_statements":
        route["recommended_adapter"] = "ocr"
        route["requires_ocr"] = True
        route["adapter_config_ref"] = family_cfg.get("base_config") or route.get("base_config")
    else:
        route["recommended_adapter"] = "native"
        route["requires_ocr"] = False
        route["adapter_config_ref"] = family_cfg.get("base_config") or route.get("base_config")
    route.setdefault("pdf_path", str(pdf_path))
    return route


def _route_ocr_family(
    *,
    pdf_path: Path,
    filename: str,
    text_source: str,
    text: str,
    registry: dict[str, Any],
    reasons: list[str],
    as_of: str | None,
    require_strong_match: bool = False,
) -> dict[str, Any] | None:
    """Route scanned/mixed PDFs to requires_ocr families using content evidence.

    Filename hints are optional boosters only — never required. Pure scanned PDFs
    with no native text still enter the OCR pathway via requires_ocr families.
    """
    if text_source not in {"scanned", "mixed"} and not require_strong_match:
        # allow caller to force OCR fallback with text_source rewritten
        pass
    if text_source not in {"scanned", "mixed"}:
        return None
    ocr_families = [
        (fid, fam)
        for fid, fam in (registry.get("template_families") or {}).items()
        if fam.get("requires_ocr")
    ]
    if not ocr_families:
        return None

    scored: list[tuple[int, str, list[str]]] = []
    for family_id, family in ocr_families:
        hits = _fingerprint_hits(text, family.get("header_fingerprint_any") or [])
        score = len(hits)
        hints = [h.lower() for h in (family.get("default_fund_hints") or []) if h]
        # Filename is a weak booster only when content is empty/sparse.
        if hints and any(h in filename for h in hints):
            score += 1
            hits = list(hits) + ["filename_hint"]
        min_hits = int(family.get("min_fingerprint_hits") or 2)
        scored.append((score, family_id, hits if score else [], min_hits))
    scored.sort(key=lambda row: row[0], reverse=True)
    best_score, family_id, hits, min_hits = scored[0]
    family = dict((registry.get("template_families") or {}).get(family_id) or {})

    native_empty = len((text or "").strip()) < 40
    if require_strong_match and best_score < max(2, min_hits):
        return None
    # Pure scanned, or mostly-scanned packages with no OCR fingerprint hits:
    # still allow the requires_ocr pathway instead of inventing native aggregate rows.
    allow_empty_score = native_empty or (not require_strong_match and text_source in {"scanned", "mixed"})
    if best_score <= 0 and not allow_empty_score:
        return None
    if best_score <= 0 and text_source == "mixed" and "scanned_page_majority" not in reasons and not native_empty:
        # Mixed docs without an explicit scanned-majority signal need fingerprints.
        return None

    reasons.append(f"ocr_family={family_id}")
    reasons.append(f"text_source={text_source}")
    if hits:
        reasons.append(f"ocr_fingerprint_hits={hits}")
    if native_empty:
        reasons.append("scanned_native_text_empty")
    return _annotate_adapter_fields(
        {
            "document_class": "financial_statements_with_schedule",
            "template_family": family_id,
            "extraction_mode": "scanned_financial_statements",
            "schedules_detected": ["balance_sheet", "portfolio_investments"],
            "schedule_pages": [],
            "realized_pages": [],
            "as_of_date": as_of,
            "comparison_grain": family.get("comparison_grain") or "statement_and_portfolio",
            "base_config": family.get("base_config"),
            "reasons": reasons,
            "confidence": 0.85 if best_score > 0 or native_empty else 0.6,
            "aggregate_fallback_available": False,
            "compare_allowed": True,
            "routing_evidence": {
                "ocr_family_scores": [
                    {"family": fid, "score": sc, "hits": ht} for sc, fid, ht, _ in scored[:5]
                ],
                "native_text_empty": native_empty,
                "selection": "content_fingerprint" if best_score > 0 else "scanned_fallback",
            },
        },
        text_source=text_source,
        pdf_path=pdf_path,
        registry=registry,
    )


def _finalize_route(
    route: dict[str, Any],
    *,
    pdf_path: Path,
    text_source: str,
    scan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach document identity + standardized routing evidence to every route."""
    from pdf_validation.document_identity import describe_document

    out = dict(route)
    doc = describe_document(pdf_path)
    out["document_id"] = doc["document_id"]
    out["pdf_sha256"] = doc["pdf_sha256"]
    out.setdefault("pdf_path", doc["pdf_path"])
    out.setdefault("text_source", text_source)
    evidence = dict(out.get("routing_evidence") or {})
    evidence.setdefault("reasons", list(out.get("reasons") or []))
    evidence.setdefault("schedule_pages", list(out.get("schedule_pages") or []))
    evidence.setdefault("confidence", out.get("confidence"))
    evidence.setdefault("text_source", text_source)
    evidence.setdefault("extraction_mode", out.get("extraction_mode"))
    evidence.setdefault("template_family", out.get("template_family"))
    if out.get("inferred_schema") is not None:
        evidence.setdefault("inferred_schema", out.get("inferred_schema"))
    if out.get("family_candidates") is not None:
        evidence.setdefault("family_candidates", out.get("family_candidates"))
    if scan:
        evidence.setdefault("scan_detection", {k: scan.get(k) for k in ("source", "native_pages", "scanned_pages")})
    out["routing_evidence"] = evidence
    return out


def route_document(pdf_path: Path, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify PDF and pick a template family when position-level extraction is possible."""
    registry = registry or load_registry()
    pdf_path = Path(pdf_path)
    routed = _route_document_body(pdf_path, registry)
    try:
        from pdf_validation.page_content import detect_pdf_text_source

        scan = detect_pdf_text_source(pdf_path)
        text_source = scan.get("source") or routed.get("text_source") or "native"
    except Exception:  # noqa: BLE001
        scan = {"source": routed.get("text_source") or "native"}
        text_source = scan["source"]
    return _finalize_route(routed, pdf_path=pdf_path, text_source=str(text_source), scan=scan)


def _route_document_body(pdf_path: Path, registry: dict[str, Any]) -> dict[str, Any]:
    """Internal router without document-id finalization."""
    pages = _page_texts(pdf_path)
    text = _full_text(pages)
    filename = pdf_path.name.lower()
    reasons: list[str] = []

    try:
        from pdf_validation.page_content import detect_pdf_text_source

        scan = detect_pdf_text_source(pdf_path)
        text_source = scan.get("source") or "native"
    except Exception:  # noqa: BLE001
        text_source = "native" if any(t.strip() for _, t in pages) else "scanned"
        scan = {"source": text_source}

    as_of = _detect_as_of(text)
    native_empty = len((text or "").strip()) < 40

    # Pure scanned PDFs: OCR pathway first (no usable native schedule text).
    if text_source == "scanned" or native_empty:
        ocr_route = _route_ocr_family(
            pdf_path=pdf_path,
            filename=filename,
            text_source=text_source if text_source in {"scanned", "mixed"} else "scanned",
            text=text,
            registry=registry,
            reasons=list(reasons),
            as_of=as_of,
            require_strong_match=False,
        )
        if ocr_route is not None:
            return ocr_route

    schedule_titles = registry["document_classes"]["financial_statements_with_schedule"]["schedule_title_patterns"]
    schedule_pages = _find_schedule_pages(pages, schedule_titles)
    has_schedule = bool(schedule_pages)

    # Investor letter / narrative first.
    letter_cfg = registry["document_classes"]["investor_letter"]
    letter_hit = any(sig in text or sig in filename for sig in letter_cfg["signals_any"])
    if letter_hit and (letter_cfg.get("require_no_schedule") is False or not has_schedule):
        if not has_schedule or "investor letter" in filename:
            reasons.append("investor_letter_signal")
            return _annotate_adapter_fields(
                {
                    "document_class": "investor_letter",
                    "template_family": None,
                    "extraction_mode": "blocked_narrative",
                    "schedules_detected": [],
                    "schedule_pages": [],
                    "realized_pages": [],
                    "as_of_date": as_of,
                    "reasons": reasons,
                    "confidence": 0.95,
                    "aggregate_fallback_available": False,
                },
                text_source=text_source,
                pdf_path=pdf_path,
                registry=registry,
            )

    if has_schedule:
        reasons.append(f"schedule_pages={schedule_pages}")
        family_scores: list[tuple[int, str, list[str], list[str]]] = []
        for family_id, family in registry["template_families"].items():
            if family.get("fallback_only"):
                continue
            if family.get("requires_ocr"):
                continue
            hits = _fingerprint_hits(text, family["header_fingerprint_any"])
            score = len(hits)
            has_audit = _has_audit_signal(text) or "audited" in filename or "afs" in filename
            if family.get("require_audit_signal") and not has_audit:
                continue
            boost, boost_reasons = _apply_score_boosters(
                text=text,
                filename=filename,
                family=family,
                has_audit=has_audit,
            )
            score += boost
            if score >= int(family.get("min_fingerprint_hits", 2)):
                family_scores.append((score, family_id, hits, boost_reasons))
        family_scores.sort(reverse=True)
        if family_scores:
            score, family_id, hits, boost_reasons = family_scores[0]
            family = registry["template_families"][family_id]
            realized_pages = _find_schedule_pages(pages, [t.lower() for t in family.get("realized_titles", [])])
            reasons.append(f"family={family_id}")
            reasons.append(f"fingerprint_hits={hits}")
            if boost_reasons:
                reasons.extend(boost_reasons)
            margin = None
            if len(family_scores) > 1:
                margin = score - family_scores[1][0]
                reasons.append(f"score_margin={margin}")
            return _annotate_adapter_fields(
                {
                    "document_class": "financial_statements_with_schedule",
                    "template_family": family_id,
                    "extraction_mode": "position_level",
                    "schedules_detected": ["investments"] + (["realized"] if realized_pages else []),
                    "schedule_pages": schedule_pages,
                    "realized_pages": realized_pages,
                    "as_of_date": as_of,
                    "comparison_grain": family.get("comparison_grain", "company"),
                    "base_config": family.get("base_config"),
                    "reasons": reasons,
                    "confidence": min(0.99, 0.55 + 0.1 * score),
                    "aggregate_fallback_available": True,
                    "family_score": score,
                    "family_score_margin": margin,
                    "family_candidates": [
                        {
                            "family": fid,
                            "score": sc,
                            "hits": ht,
                            "boosts": br,
                        }
                        for sc, fid, ht, br in family_scores[:5]
                    ],
                },
                text_source=text_source,
                pdf_path=pdf_path,
                registry=registry,
            )
        reasons.append("schedule_found_but_family_unmapped")
        from pdf_validation.schema_inference import infer_schema

        inferred = infer_schema(pdf_path, schedule_pages=schedule_pages, registry=registry)
        reasons.append(f"inferred_confidence={inferred.get('inference_confidence')}")
        reasons.append(f"inferred_columns={inferred.get('logical_columns')}")
        can_infer = (
            inferred.get("found")
            and float(inferred.get("inference_confidence") or 0) >= 0.55
            and (
                not inferred.get("missing_required")
                or (
                    list(inferred.get("missing_required") or []) == ["cost"]
                    and "fair_value" in (inferred.get("logical_columns") or [])
                )
            )
        )
        if can_infer:
            reasons.append("generic_schema_inference")
            missing = list(inferred.get("missing_required") or [])
            return _annotate_adapter_fields(
                {
                    "document_class": "financial_statements_with_schedule",
                    "template_family": "generic_holdings_schedule",
                    "extraction_mode": "position_level_inferred",
                    "schedules_detected": ["investments"],
                    "schedule_pages": schedule_pages,
                    "realized_pages": [],
                    "as_of_date": as_of,
                    "comparison_grain": inferred.get("comparison_grain") or "company",
                    "base_config": "configs/families/generic_holdings_schedule.json",
                    "inferred_schema": inferred,
                    "reasons": reasons,
                    "confidence": float(inferred.get("inference_confidence") or 0.55),
                    "aggregate_fallback_available": True,
                    "onboarding_status": "inferred_ready" if not missing else "inferred_partial_fields",
                    "compare_allowed": False,
                    "routing_evidence": {
                        "partial_compare_fields": inferred.get("partial_compare_fields") or [],
                        "missing_required": missing,
                        "recovery_notes": inferred.get("recovery_notes") or [],
                    },
                },
                text_source=text_source,
                pdf_path=pdf_path,
                registry=registry,
            )
        return _annotate_adapter_fields(
            {
                "document_class": "financial_statements_with_schedule",
                "template_family": None,
                "extraction_mode": "manual_review",
                "schedules_detected": ["investments"],
                "schedule_pages": schedule_pages,
                "realized_pages": [],
                "as_of_date": as_of,
                "inferred_schema": inferred,
                "reasons": reasons,
                "confidence": float(inferred.get("inference_confidence") or 0.4),
                "aggregate_fallback_available": True,
                "onboarding_status": "needs_review",
                "compare_allowed": False,
            },
            text_source=text_source,
            pdf_path=pdf_path,
            registry=registry,
        )

    # Aggregate-only financials (Perry quarterlies).
    # If the PDF is mostly scanned with a thin native text layer (common for
    # OCR packages that embed a cover/auditor page), prefer OCR over aggregate.
    scanned_majority = (
        text_source == "mixed"
        and int(scan.get("scanned_pages") or 0) >= 3
        and int(scan.get("scanned_pages") or 0) >= 2 * max(1, int(scan.get("native_pages") or 0))
    )
    if scanned_majority:
        ocr_mixed = _route_ocr_family(
            pdf_path=pdf_path,
            filename=filename,
            text_source="mixed",
            text=text,
            registry=registry,
            reasons=list(reasons) + ["scanned_page_majority"],
            as_of=as_of,
            require_strong_match=False,
        )
        if ocr_mixed is not None:
            return ocr_mixed

    agg_cfg = registry["document_classes"]["financial_statements_aggregate_only"]
    if any(sig in text for sig in agg_cfg["signals_any"]):
        reasons.append("aggregate_statement_without_schedule")
        return _annotate_adapter_fields(
            {
                "document_class": "financial_statements_aggregate_only",
                "template_family": None,
                "extraction_mode": "fund_aggregate_only",
                "schedules_detected": [],
                "schedule_pages": [],
                "realized_pages": [],
                "as_of_date": as_of,
                "comparison_grain": "fund",
                "reasons": reasons,
                "confidence": 0.9,
                "aggregate_fallback_available": True,
            },
            text_source=text_source,
            pdf_path=pdf_path,
            registry=registry,
        )

    reasons.append("no_schedule_or_aggregate_signal")
    if text_source in {"scanned", "mixed"}:
        reasons.append("scanned_without_native_schedule")
        ocr_fallback = _route_ocr_family(
            pdf_path=pdf_path,
            filename=filename,
            text_source=text_source,
            text=text,
            registry=registry,
            reasons=list(reasons),
            as_of=as_of,
            require_strong_match=(text_source == "mixed"),
        )
        if ocr_fallback is not None:
            return ocr_fallback
    return _annotate_adapter_fields(
        {
            "document_class": "unknown",
            "template_family": None,
            "extraction_mode": "manual_review",
            "schedules_detected": [],
            "schedule_pages": [],
            "realized_pages": [],
            "as_of_date": as_of,
            "reasons": reasons,
            "confidence": 0.2,
            "aggregate_fallback_available": False,
            "scan_detection": scan,
        },
        text_source=text_source,
        pdf_path=pdf_path,
        registry=registry,
    )


def classify_sample_tree(sample_root: Path, registry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Discover every PDF under a tree; each file is an independent document."""
    from pdf_validation.document_identity import describe_document

    registry = registry or load_registry()
    results: list[dict[str, Any]] = []
    for pdf in sorted(sample_root.rglob("*.pdf")):
        route = route_document(pdf, registry)
        doc = describe_document(pdf, fund_id_hint=pdf.parent.name)
        results.append(
            {
                **doc,
                "pdf": str(pdf),
                "fund_id_dir": pdf.parent.name,
                **route,
            }
        )
    return results
