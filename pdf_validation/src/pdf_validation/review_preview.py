"""PDF page preview + amount highlight helpers for the review UI."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pdfplumber

from pdf_validation.page_content import render_page_png


def _format_amount_variants(value: Any) -> list[str]:
    """Produce searchable text forms for a numeric amount."""
    if value is None:
        return []
    try:
        num = float(value)
    except (TypeError, ValueError):
        raw = str(value).strip()
        return [raw] if raw else []
    if abs(num - round(num)) < 1e-9:
        as_int = str(int(round(num)))
    else:
        as_int = f"{num:.2f}".rstrip("0").rstrip(".")
    variants = {
        as_int,
        f"{num:,.2f}",
        f"{num:,.0f}",
        f"{num:.2f}",
        f"{num:.0f}",
    }
    # Parenthetical negatives sometimes appear as (1,234)
    if num < 0:
        pos = abs(num)
        variants.add(f"({pos:,.0f})")
        variants.add(f"({pos:,.2f})")
    return [v for v in variants if v]


def _word_hits_on_page(page, needles: list[str]) -> list[dict[str, Any]]:
    if not needles:
        return []
    words = page.extract_words(use_text_flow=True, keep_blank_chars=False) or []
    norm_needles = []
    for n in needles:
        cleaned = re.sub(r"[,\s]", "", str(n))
        if cleaned:
            norm_needles.append((str(n), cleaned))
    hits: list[dict[str, Any]] = []
    for w in words:
        text = str(w.get("text") or "")
        compact = re.sub(r"[,\s$]", "", text)
        for original, cleaned in norm_needles:
            if compact == cleaned or cleaned in compact:
                hits.append(
                    {
                        "text": text,
                        "matched": original,
                        "bbox_pdf": [
                            float(w["x0"]),
                            float(w["top"]),
                            float(w["x1"]),
                            float(w["bottom"]),
                        ],
                    }
                )
                break
    return hits


def resolve_pdf_path(extraction_dir: Path, route: dict[str, Any] | None = None) -> Path | None:
    route = route or {}
    candidates = [
        route.get("pdf_path"),
        (route.get("routing_evidence") or {}).get("pdf_path"),
    ]
    run_manifest = Path(extraction_dir) / "run_manifest.jsonl"
    if run_manifest.exists():
        import json

        for line in run_manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("pdf_path"):
                candidates.append(row["pdf_path"])
            if (row.get("cli_args") or {}).get("pdf"):
                candidates.append(row["cli_args"]["pdf"])
    for raw in candidates:
        if not raw:
            continue
        path = Path(str(raw))
        if path.exists():
            return path
    return None


def locate_text_on_pages(
    *,
    pdf_path: Path,
    pages: list[int],
    needles: list[str],
    label: str,
    company: str | None = None,
    value: Any = None,
) -> dict[str, Any]:
    """Search pages for needles; return located / unlocated / ambiguous."""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists() or not pages or not needles:
        return {"status": "unlocated", "highlights": [], "page": pages[0] if pages else None, "label": label}

    hits: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as doc:
        page_count = len(doc.pages)
        for p in pages:
            if p < 1 or p > page_count:
                continue
            page = doc.pages[p - 1]
            width = float(page.width or 1)
            height = float(page.height or 1)
            for hit in _word_hits_on_page(page, needles):
                x0, top, x1, bottom = hit["bbox_pdf"]
                hits.append(
                    {
                        "page": p,
                        "label": label,
                        "company": company,
                        "value": value,
                        "text": hit["text"],
                        "bbox_pdf": hit["bbox_pdf"],
                        "norm": [x0 / width, top / height, x1 / width, bottom / height],
                    }
                )

    if not hits:
        return {"status": "unlocated", "highlights": [], "page": pages[0], "label": label, "company": company, "value": value}
    # Unique by rounded norm box to avoid duplicate word hits.
    uniq: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for h in hits:
        key = (h["page"], tuple(round(x, 4) for x in h["norm"]))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(h)
    if len(uniq) == 1:
        return {
            "status": "located",
            "highlights": uniq,
            "page": uniq[0]["page"],
            "label": label,
            "company": company,
            "value": value,
        }
    return {
        "status": "ambiguous",
        "highlights": uniq[:8],
        "page": uniq[0]["page"],
        "label": label,
        "company": company,
        "value": value,
    }


def locate_amount_evidence(
    *,
    pdf_path: Path,
    pages: list[int],
    value: Any,
    label: str,
    company: str | None = None,
) -> dict[str, Any]:
    return locate_text_on_pages(
        pdf_path=pdf_path,
        pages=pages,
        needles=_format_amount_variants(value),
        label=label,
        company=company,
        value=value,
    )


def build_review_preview(
    *,
    extraction_dir: Path,
    route: dict[str, Any] | None = None,
    companies: list[dict[str, Any]] | None = None,
    focus_company: str | None = None,
    focus_page: int | None = None,
) -> dict[str, Any]:
    """Describe pages/highlights for the right-hand preview pane."""
    extraction_dir = Path(extraction_dir)
    route = route or {}
    pdf_path = resolve_pdf_path(extraction_dir, route)
    if pdf_path is None:
        return {"available": False, "reason": "pdf_not_found"}

    schedule_pages = [int(p) for p in (route.get("schedule_pages") or []) if str(p).isdigit() or isinstance(p, int)]
    companies = companies or []
    company_pages: list[int] = []
    for c in companies:
        pages = c.get("pages") or []
        if isinstance(pages, list):
            for p in pages:
                try:
                    company_pages.append(int(p))
                except (TypeError, ValueError):
                    continue
        elif pages is not None:
            try:
                company_pages.append(int(pages))
            except (TypeError, ValueError):
                pass

    pages_of_interest = sorted({*schedule_pages, *company_pages})
    with pdfplumber.open(pdf_path) as doc:
        page_count = len(doc.pages)

    if focus_page and 1 <= focus_page <= page_count:
        default_page = focus_page
    elif focus_company:
        match = next((c for c in companies if c.get("company_name") == focus_company), None)
        mp = (match or {}).get("pages") or []
        if isinstance(mp, list) and mp:
            default_page = int(mp[0])
        elif mp:
            default_page = int(mp)
        elif pages_of_interest:
            default_page = pages_of_interest[0]
        else:
            default_page = 1
    elif pages_of_interest:
        default_page = pages_of_interest[0]
    else:
        default_page = 1

    highlights: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    focus_rows = [c for c in companies if c.get("company_name") == focus_company] if focus_company else companies[:12]
    for c in focus_rows:
        name = c.get("company_name")
        pages = c.get("pages") or []
        page_list = pages if isinstance(pages, list) else ([pages] if pages is not None else [])
        page_ints: list[int] = []
        for p in page_list or pages_of_interest[:1] or [default_page]:
            try:
                page_ints.append(int(p))
            except (TypeError, ValueError):
                continue
        if name and page_ints:
            ev = locate_text_on_pages(
                pdf_path=pdf_path,
                pages=page_ints,
                needles=[str(name), str(name).split("(")[0].strip()],
                label="Company",
                company=name,
                value=name,
            )
            evidence_rows.append(ev)
            if ev["status"] == "located":
                highlights.extend(ev["highlights"])
            elif ev["status"] == "ambiguous":
                # Do not auto-pick; surface candidates only for UI debug.
                pass
        for label, field in (
            ("Cost", "cost_reported_normalized"),
            ("Fair Value", "fair_value_reported_normalized"),
        ):
            val = c.get(field)
            if val is None or not page_ints:
                continue
            ev = locate_amount_evidence(
                pdf_path=pdf_path,
                pages=page_ints,
                value=val,
                label=label,
                company=name,
            )
            evidence_rows.append(ev)
            if ev["status"] == "located":
                highlights.extend(ev["highlights"])

    return {
        "available": True,
        "pdf_path": str(pdf_path),
        "pdf_name": pdf_path.name,
        "page_count": page_count,
        "default_page": default_page,
        "pages_of_interest": pages_of_interest,
        "highlights": highlights,
        "evidence": evidence_rows,
        "focus_company": focus_company,
    }


def render_preview_page_png(
    *,
    pdf_path: Path,
    page_number: int,
    out_path: Path,
    dpi: int = 144,
) -> Path:
    return render_page_png(pdf_path, page_number, out_path, dpi=dpi)
