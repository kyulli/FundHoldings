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


def _page_texts(pdf_path: Path, max_pages: int = 25) -> list[tuple[int, str]]:
    pages: list[tuple[int, str]] = []
    with pdfplumber.open(pdf_path) as doc:
        for idx, page in enumerate(doc.pages[:max_pages]):
            pages.append((idx + 1, (page.extract_text() or "").lower()))
    return pages


def _full_text(pages: list[tuple[int, str]]) -> str:
    return "\n".join(text for _, text in pages)


def _find_schedule_pages(pages: list[tuple[int, str]], titles: list[str]) -> list[int]:
    hits: list[int] = []
    for page_num, text in pages:
        head = "\n".join(text.splitlines()[:8])
        if "table of contents" in text:
            continue
        if "independent auditor" in head or "we have audited" in text[:500]:
            continue
        # Prefer pages where a schedule title appears near the top.
        if any(title in head for title in titles):
            hits.append(page_num)
            continue
        if any(title in text for title in titles) and any(ch.isdigit() for ch in text):
            # Secondary: title somewhere + numeric content, still skip TOC-like pages.
            if "page(s)" in head:
                continue
            hits.append(page_num)
    return hits


def _fingerprint_hits(text: str, fingerprints: list[str]) -> list[str]:
    return [token for token in fingerprints if token in text]


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
    registry: dict[str, Any],
    reasons: list[str],
    as_of: str | None,
) -> dict[str, Any] | None:
    """Route scanned/mixed PDFs to requires_ocr families via filename hints."""
    if text_source not in {"scanned", "mixed"}:
        return None
    for family_id, family in (registry.get("template_families") or {}).items():
        if not family.get("requires_ocr"):
            continue
        hints = [h.lower() for h in (family.get("default_fund_hints") or []) if h]
        if hints and not any(h in filename for h in hints):
            continue
        reasons.append(f"ocr_family={family_id}")
        reasons.append(f"text_source={text_source}")
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
                "confidence": 0.85,
                "aggregate_fallback_available": False,
                "compare_allowed": True,
            },
            text_source=text_source,
            pdf_path=pdf_path,
            registry=registry,
        )
    return None


def route_document(pdf_path: Path, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify PDF and pick a template family when position-level extraction is possible."""
    registry = registry or load_registry()
    pdf_path = Path(pdf_path)
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
    ocr_route = _route_ocr_family(
        pdf_path=pdf_path,
        filename=filename,
        text_source=text_source,
        registry=registry,
        reasons=list(reasons),
        as_of=as_of,
    )
    # Prefer OCR family for scanned/mixed PDFs that match OCR template hints,
    # even when native auditor-letter pages contain aggregate phrases.
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
        family_scores: list[tuple[int, str, list[str]]] = []
        for family_id, family in registry["template_families"].items():
            if family.get("fallback_only"):
                continue
            if family.get("requires_ocr"):
                continue
            hits = _fingerprint_hits(text, family["header_fingerprint_any"])
            score = len(hits)
            if family.get("require_audit_signal") and not (_has_audit_signal(text) or "audited" in filename or "afs" in filename):
                continue
            if family_id == "condensed_hedge_schedule" and "condensed schedule" in text:
                score += 3
            if family_id == "simple_lot_schedule" and "schedule of investment" in text and "schedule of investments" not in text:
                score += 2
            if family_id == "vc_lot_schedule" and "schedule of investments" in text and "cost/share" in text:
                score += 2
            if family_id == "audited_portfolio_schedule" and (_has_audit_signal(text) or "final -" in filename):
                score += 2
            if score >= int(family.get("min_fingerprint_hits", 2)):
                family_scores.append((score, family_id, hits))
        family_scores.sort(reverse=True)
        if family_scores:
            score, family_id, hits = family_scores[0]
            family = registry["template_families"][family_id]
            realized_pages = _find_schedule_pages(pages, [t.lower() for t in family.get("realized_titles", [])])
            reasons.append(f"family={family_id}")
            reasons.append(f"fingerprint_hits={hits}")
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
            and not inferred.get("missing_required")
        )
        if can_infer:
            reasons.append("generic_schema_inference")
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
                    "onboarding_status": "inferred_ready",
                    "compare_allowed": False,
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
        reasons.append("scanned_without_ocr_family_hint")
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
    registry = registry or load_registry()
    results: list[dict[str, Any]] = []
    for pdf in sorted(sample_root.rglob("*.pdf")):
        route = route_document(pdf, registry)
        results.append(
            {
                "pdf": str(pdf),
                "fund_id_dir": pdf.parent.name,
                **route,
            }
        )
    return results
