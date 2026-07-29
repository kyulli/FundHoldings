"""Detect schedule pages, headers, and column separators for a template family."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import pdfplumber

from pdf_validation.document_router import load_registry


def _clean_num(value: str | None) -> str | None:
    if not value:
        return None
    neg = "(" in value or value.strip().startswith("-")
    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        return None
    return f"-{digits}" if neg else digits


def _alias_lookup(registry: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for logical, aliases in registry.get("header_aliases", {}).items():
        for alias in aliases:
            out[alias.lower()] = logical
    return out


def detect_header_band(page: pdfplumber.page.Page, alias_map: dict[str, str]) -> dict[str, Any]:
    words = page.extract_words() or []
    by_top: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        by_top[round(float(word["top"]), 0)].append(word)

    best_rows: list[tuple[float, list[dict[str, Any]], list[str]]] = []
    for top, row in by_top.items():
        texts = [w["text"] for w in row]
        joined = " ".join(texts).lower()
        hits = []
        for alias, logical in alias_map.items():
            if alias in joined or any(alias == t.lower() for t in texts):
                hits.append(logical)
        if len(set(hits)) >= 3:
            best_rows.append((top, row, sorted(set(hits))))
    if not best_rows:
        return {"found": False}
    best_rows.sort(key=lambda item: (-len(item[2]), item[0]))
    top, row, hits = best_rows[0]
    # Merge adjacent header band within 18pt.
    merged = list(row)
    for other_top, other_row, _ in best_rows[1:]:
        if abs(other_top - top) <= 18:
            merged.extend(other_row)
    mids: dict[str, float] = {}
    for word in sorted(merged, key=lambda w: w["x0"]):
        token = word["text"].lower()
        logical = alias_map.get(token)
        if logical is None:
            # multi-token aliases handled via joined scan later
            continue
        mids.setdefault(logical, (float(word["x0"]) + float(word["x1"])) / 2)
    # Recover multi-token aliases from joined band text positions using first token.
    joined_tokens = [(w["text"].lower(), (float(w["x0"]) + float(w["x1"])) / 2) for w in merged]
    joined = " ".join(t for t, _ in joined_tokens)
    for alias, logical in alias_map.items():
        if logical in mids:
            continue
        if alias in joined:
            first = alias.split()[0]
            for token, mid in joined_tokens:
                if token == first or token.startswith(first[:4]):
                    mids[logical] = mid
                    break
    ordered = sorted(mids.items(), key=lambda kv: kv[1])
    seps = []
    if len(ordered) >= 2:
        seps = [(ordered[i][1] + ordered[i + 1][1]) / 2 for i in range(len(ordered) - 1)]
    return {
        "found": True,
        "top": top,
        "logical_columns": [name for name, _ in ordered],
        "mids": {name: mid for name, mid in ordered},
        "separators": seps,
        "hits": hits,
    }


def detect_layout(pdf_path: str | Any, route: dict[str, Any], registry: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or load_registry()
    alias_map = _alias_lookup(registry)
    family_id = route.get("template_family")
    family = registry["template_families"].get(family_id or "", {})

    with pdfplumber.open(pdf_path) as doc:
        cover = (doc.pages[0].extract_text() or "") if doc.pages else ""
        statement_page_idx = 1 if len(doc.pages) > 1 else 0
        statement_text = doc.pages[statement_page_idx].extract_text() or ""

        inv_pages = list(route.get("schedule_pages") or [])
        if not inv_pages and family.get("schedule_titles"):
            titles = [t.lower() for t in family["schedule_titles"]]
            for i, page in enumerate(doc.pages):
                text = (page.extract_text() or "").lower()
                if any(t in text for t in titles):
                    inv_pages.append(i + 1)

        real_pages = list(route.get("realized_pages") or [])
        header = {"found": False}
        inv_cols = None
        inv_area = None
        inv_vlines = None
        inv_page_size = None
        if inv_pages:
            page = doc.pages[inv_pages[0] - 1]
            inv_page_size = [page.width, page.height]
            header = detect_header_band(page, alias_map)
            seps = header.get("separators") or []
            if len(seps) < 4:
                # Relative fallbacks by family.
                defaults = {
                    "vc_lot_schedule": [250, 360, 500, 575, 655, 740, 830, 940],
                    "simple_lot_schedule": [220, 310, 400, 520, 640, 760],
                    "audited_portfolio_schedule": [280, 380, 470, 560, 650, 760],
                    "condensed_hedge_schedule": [300, 420, 520, 620, 720],
                }
                base = defaults.get(family_id or "", [250, 360, 500, 575, 655, 740, 830, 940])
                seps = [x * page.width / 1070.0 for x in base]
            inv_cols = ",".join(f"{s:.1f}" for s in seps)
            inv_area = f"10,30,{max(page.width - 5, 20):.1f},{max(page.height - 40, 50):.1f}"
            inv_vlines = [10.0, *seps, page.width - 5]

        real_cols = None
        real_area = None
        real_vlines = None
        real_page_size = None
        if real_pages:
            page = doc.pages[real_pages[0] - 1]
            real_page_size = [page.width, page.height]
            rseps = [x * page.width / 990.0 for x in [180, 250, 320, 450, 545, 665, 735]]
            real_cols = ",".join(f"{s:.1f}" for s in rseps)
            real_area = f"10,30,{max(page.width - 5, 20):.1f},{max(page.height - 40, 50):.1f}"
            real_vlines = [10.0, *rseps, page.width - 5]

        # Statement anchors (common patterns).
        cost = None
        fv = None
        # Imaginary / Perry style: Investment(s), at fair value (cost $X) $ Y
        m = re.search(
            r"investments?, at fair value\s*\(cost[:\s]*\$?\s*([0-9,\s]+)\)\s*\$?\s*([0-9,\s]+)",
            statement_text,
            flags=re.I,
        )
        if m:
            cost = _clean_num(m.group(1))
            fv = _clean_num(m.group(2))
        if not cost:
            m = re.search(r"cost equal to \$?\s*([0-9,\s]+)", statement_text, flags=re.I)
            if m:
                cost = _clean_num(m.group(1))
        if not fv:
            m = re.search(r"fair value \$?\s*([0-9,\s]+)", statement_text, flags=re.I)
            if m:
                fv = _clean_num(m.group(1))

        unrealized = None
        if cost and fv:
            try:
                unrealized = str(int(fv) - int(cost))
            except ValueError:
                unrealized = None

        as_of = route.get("as_of_date")
        if not as_of:
            m_date = re.search(
                r"(March|June|September|December)\s+(\d{1,2}),\s*(\d{4})",
                cover + "\n" + statement_text,
            )
            if m_date:
                month = {"March": "03", "June": "06", "September": "09", "December": "12"}[m_date.group(1)]
                as_of = f"{m_date.group(3)}-{month}-{int(m_date.group(2)):02d}"

    return {
        "as_of": as_of,
        "inv_pages": inv_pages,
        "real_pages": real_pages,
        "inv_cols": inv_cols,
        "inv_area": inv_area,
        "inv_vlines": inv_vlines,
        "inv_page_size": inv_page_size,
        "real_cols": real_cols,
        "real_area": real_area,
        "real_vlines": real_vlines,
        "real_page_size": real_page_size,
        "header": header,
        "investments_cost": cost,
        "investments_fv": fv,
        "unrealized": unrealized,
        "family_id": family_id,
    }
