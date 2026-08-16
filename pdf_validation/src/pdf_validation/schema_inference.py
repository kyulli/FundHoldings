"""Infer holdings-schedule schema for unmapped / unseen PDF templates."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pdfplumber

from pdf_validation.document_router import load_registry
from pdf_validation.layout_detector import detect_header_band


REQUIRED_FOR_COMPARE = ("cost", "fair_value")
_COST_EMBEDDED = re.compile(r"\(\s*cost\s*\$?\s*[\d,]+", re.I)
_AMOUNT_TOKEN = re.compile(r"\$?\(?\d{1,3}(?:,\d{3})+\)?|\$?\(?\d{4,}\)?")


def infer_schema(
    pdf_path: Path | str,
    *,
    schedule_pages: list[int],
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Infer logical columns / grain from schedule header + amount geometry evidence.

    Does not invent Cost/Fair Value when evidence is weak. Embedded ``(cost $X)``
    on subtotal lines is recorded as evidence, not as a recovered company Cost column.
    """
    registry = registry or load_registry()
    alias_map: dict[str, str] = {}
    for logical, aliases in registry.get("header_aliases", {}).items():
        for alias in aliases:
            alias_map[alias.lower()] = logical

    header: dict[str, Any] = {"found": False}
    page_used = None
    header_source = "none"
    page_text = ""
    page_words: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as doc:
        for page_num in schedule_pages[:8]:
            if page_num < 1 or page_num > len(doc.pages):
                continue
            page = doc.pages[page_num - 1]
            from pdf_validation.watermark import read_clean_page_text

            text, words, _ = read_clean_page_text(page)
            candidate = detect_header_band(page, alias_map, min_hits=3, words=words)
            if candidate.get("found"):
                header = candidate
                page_used = page_num
                header_source = "header_band"
                page_text = text or ""
                page_words = words or []
                break
            soft = detect_header_band(page, alias_map, min_hits=2, words=words)
            if soft.get("found"):
                header = soft
                page_used = page_num
                header_source = "header_band_soft"
                page_text = text or ""
                page_words = words or []
                break
            # Last chance: text-only alias scan on schedule title pages.
            text_hits = _text_header_hits(text or "", alias_map)
            if len(text_hits) >= 2:
                header = {
                    "found": True,
                    "logical_columns": list(text_hits),
                    "mids": {},
                    "separators": [],
                    "hits": list(text_hits),
                    "min_hits": 2,
                }
                page_used = page_num
                header_source = "text_alias_scan"
                page_text = text or ""
                page_words = words or []
                break

    logical = list(header.get("logical_columns") or [])
    recovery_notes: list[str] = []

    # Recover Fair Value / Cost from header phrases when band midpoints missed them.
    low_page = page_text.lower()
    if "fair_value" not in logical and any(
        tok in low_page[:800]
        for tok in ("fair value ($)", "fair value($)", "fair value", "estimated fair value")
    ):
        logical.append("fair_value")
        recovery_notes.append("fair_value_from_header_text")
    if "cost" not in logical and re.search(r"\bcost\b", low_page[:800]) and "cost $" not in low_page[:800]:
        # Only count a Cost *column* when the word appears as a header-like token,
        # not solely inside "(cost $...)" subtotals.
        head = "\n".join(page_text.splitlines()[:12]).lower()
        if re.search(r"\bcost\b", head) and not _COST_EMBEDDED.search(head):
            logical.append("cost")
            recovery_notes.append("cost_from_header_text")

    amount_cols = _cluster_amount_columns(page_words, header_top=float(header.get("top") or 0))
    if amount_cols.get("fair_value_mid") and "fair_value" not in logical:
        logical.append("fair_value")
        recovery_notes.append("fair_value_from_amount_cluster")
    if amount_cols.get("cost_mid") and "cost" not in logical:
        logical.append("cost")
        recovery_notes.append("cost_from_amount_cluster")

    cost_embedded = bool(_COST_EMBEDDED.search(page_text))
    if cost_embedded:
        recovery_notes.append("cost_embedded_in_subtotal_labels")

    # Deduplicate while preserving order.
    seen: set[str] = set()
    logical = [x for x in logical if not (x in seen or seen.add(x))]

    missing = [f for f in REQUIRED_FOR_COMPARE if f not in logical]
    has_entity_header = "entity_name" in logical
    company_row_headers = (not has_entity_header) and bool(
        {"shares", "cost", "fair_value", "cost_per_share", "fmv_per_share"} & set(logical)
    )

    if "cost_per_share" in logical or "fmv_per_share" in logical:
        grain = "lot"
    elif "pct_of_capital" in logical or "shares" in logical:
        grain = "condensed"
    else:
        grain = "company"

    matched_required = sum(1 for f in REQUIRED_FOR_COMPARE if f in logical)
    confidence = 0.0
    if header.get("found") or recovery_notes:
        confidence = 0.35 + 0.2 * matched_required
        confidence += min(0.25, 0.05 * len(logical))
        if company_row_headers:
            confidence += 0.1
        if has_entity_header:
            confidence += 0.1
        if header_source in {"header_band_soft", "text_alias_scan"}:
            confidence = min(confidence, 0.72)
        if cost_embedded and "cost" not in logical:
            # Evidence that cost exists somewhere, but not as a company column.
            confidence = min(confidence, 0.58)
    confidence = round(min(0.95, confidence), 3)

    mids = dict(header.get("mids") or {})
    if amount_cols.get("cost_mid") and "cost" not in mids:
        mids["cost"] = amount_cols["cost_mid"]
    if amount_cols.get("fair_value_mid") and "fair_value" not in mids:
        mids["fair_value"] = amount_cols["fair_value_mid"]

    column_names = _to_column_names(logical, company_row_headers=company_row_headers)
    column_map = {name: idx for idx, name in enumerate(column_names)}

    return {
        "found": bool(header.get("found") or recovery_notes),
        "source_page": page_used,
        "logical_columns": logical,
        "column_names": column_names,
        "column_map": column_map,
        "separators": header.get("separators") or [],
        "mids": mids,
        "required_present": [f for f in REQUIRED_FOR_COMPARE if f in logical],
        "missing_required": missing,
        "comparison_grain": grain,
        "company_row_headers": company_row_headers,
        "inference_confidence": confidence,
        "header_hits": header.get("hits") or logical,
        "source": header_source if header.get("found") else ("recovery" if recovery_notes else "none"),
        "min_hits": header.get("min_hits"),
        "unmapped_headers": [],
        "recovery_notes": recovery_notes,
        "cost_embedded_in_subtotals": cost_embedded,
        "partial_compare_fields": [f for f in ("fair_value",) if f in logical and "cost" not in logical],
    }


def _text_header_hits(text: str, alias_map: dict[str, str]) -> list[str]:
    head = "\n".join((text or "").splitlines()[:15]).lower()
    hits: list[str] = []
    for alias, logical in sorted(alias_map.items(), key=lambda kv: -len(kv[0])):
        if alias in head and logical not in hits:
            hits.append(logical)
    return hits


def _cluster_amount_columns(words: list[dict[str, Any]], *, header_top: float) -> dict[str, float]:
    """Cluster numeric tokens below the header into right-side amount columns."""
    xs: list[float] = []
    for word in words or []:
        token = str(word.get("text") or "")
        if not _AMOUNT_TOKEN.fullmatch(token.replace(" ", "")):
            continue
        top = float(word.get("top") or 0)
        if header_top and top < header_top - 2:
            continue
        xs.append((float(word["x0"]) + float(word["x1"])) / 2)
    if len(xs) < 6:
        return {}
    xs.sort()
    # Simple 1-D gap split into up to 2 rightmost clusters.
    gaps = [(xs[i + 1] - xs[i], i) for i in range(len(xs) - 1)]
    gaps.sort(reverse=True)
    split_at = gaps[0][1] if gaps and gaps[0][0] > 25 else None
    if split_at is None:
        return {"fair_value_mid": sum(xs) / len(xs)}
    left = xs[: split_at + 1]
    right = xs[split_at + 1 :]
    if not left or not right:
        return {"fair_value_mid": sum(xs) / len(xs)}
    # Rightmost cluster is usually Fair Value; left may be Cost or Par.
    return {
        "cost_mid": sum(left) / len(left),
        "fair_value_mid": sum(right) / len(right),
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
    for required in ("cost", "fair_value"):
        if required not in names:
            names.append(required)
    return names
