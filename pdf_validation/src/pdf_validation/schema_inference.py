"""Infer holdings-schedule schema for unmapped / unseen PDF templates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pdfplumber

from pdf_validation.document_router import load_registry
from pdf_validation.layout_detector import detect_header_band


REQUIRED_FOR_COMPARE = ("cost", "fair_value")


def infer_schema(
    pdf_path: Path | str,
    *,
    schedule_pages: list[int],
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Infer logical columns / grain from schedule header evidence."""
    registry = registry or load_registry()
    alias_map: dict[str, str] = {}
    for logical, aliases in registry.get("header_aliases", {}).items():
        for alias in aliases:
            alias_map[alias.lower()] = logical

    header: dict[str, Any] = {"found": False}
    page_used = None
    with pdfplumber.open(pdf_path) as doc:
        for page_num in schedule_pages[:5]:
            if page_num < 1 or page_num > len(doc.pages):
                continue
            page = doc.pages[page_num - 1]
            candidate = detect_header_band(page, alias_map)
            if candidate.get("found"):
                header = candidate
                page_used = page_num
                break
            # Soften threshold: accept 2+ logical hits for compact headers.
            soft = _soft_header(page, alias_map)
            if soft.get("found"):
                header = soft
                page_used = page_num
                break

    logical = list(header.get("logical_columns") or [])
    missing = [f for f in REQUIRED_FOR_COMPARE if f not in logical]
    has_entity_header = "entity_name" in logical
    company_row_headers = (not has_entity_header) and bool(
        {"shares", "cost", "fair_value", "cost_per_share", "fmv_per_share"} & set(logical)
    )

    if "cost_per_share" in logical or "fmv_per_share" in logical:
        grain = "lot"
    elif "pct_of_capital" in logical:
        grain = "condensed"
    else:
        grain = "company"

    matched_required = sum(1 for f in REQUIRED_FOR_COMPARE if f in logical)
    confidence = 0.0
    if header.get("found"):
        confidence = 0.35 + 0.2 * matched_required
        confidence += min(0.25, 0.05 * len(logical))
        if company_row_headers:
            confidence += 0.1
        if has_entity_header:
            confidence += 0.1
    confidence = round(min(0.95, confidence), 3)

    column_names = _to_column_names(logical, company_row_headers=company_row_headers)
    column_map = {name: idx for idx, name in enumerate(column_names)}

    return {
        "found": bool(header.get("found")),
        "source_page": page_used,
        "logical_columns": logical,
        "column_names": column_names,
        "column_map": column_map,
        "separators": header.get("separators") or [],
        "mids": header.get("mids") or {},
        "required_present": [f for f in REQUIRED_FOR_COMPARE if f in logical],
        "missing_required": missing,
        "comparison_grain": grain,
        "company_row_headers": company_row_headers,
        "inference_confidence": confidence,
        "header_hits": header.get("hits") or logical,
        "source": "header_band" if header.get("found") else "none",
        "unmapped_headers": [],
    }


def _soft_header(page: Any, alias_map: dict[str, str]) -> dict[str, Any]:
    words = page.extract_words() or []
    if not words:
        return {"found": False}
    # Use top 25% of page for header candidates.
    max_top = max(float(w["top"]) for w in words)
    band = [w for w in words if float(w["top"]) <= max_top * 0.35]
    joined = " ".join(w["text"] for w in band).lower()
    mids: dict[str, float] = {}
    hits: list[str] = []
    for alias, logical in alias_map.items():
        if alias in joined:
            hits.append(logical)
            first = alias.split()[0]
            for word in band:
                token = word["text"].lower()
                if token == first or token.startswith(first[:4]):
                    mids.setdefault(logical, (float(word["x0"]) + float(word["x1"])) / 2)
                    break
    hits = sorted(set(hits))
    if len(hits) < 2:
        return {"found": False}
    ordered = sorted(mids.items(), key=lambda kv: kv[1])
    seps = []
    if len(ordered) >= 2:
        seps = [(ordered[i][1] + ordered[i + 1][1]) / 2 for i in range(len(ordered) - 1)]
    return {
        "found": True,
        "logical_columns": [name for name, _ in ordered] or hits,
        "mids": {name: mid for name, mid in ordered},
        "separators": seps,
        "hits": hits,
    }


def _to_column_names(logical: list[str], *, company_row_headers: bool) -> list[str]:
    """Map inferred logical fields to camelot/row_classification column_names."""
    rename = {
        "entity_name": "company_name",
        "investment_date": "date",
        "security_description": "round",
    }
    names = [rename.get(x, x) for x in logical]
    if company_row_headers and "company_name" not in names:
        names = ["company_name", *names]
    # Ensure amount fields exist even if only one side detected.
    for required in ("cost", "fair_value"):
        if required not in names:
            names.append(required)
    return names
