"""Extraction adapters: native-text vs OCR, both produce canonical payloads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pdf_validation.canonical import ocr_extraction_to_canonical
from pdf_validation.camelot_extractor import extract_camelot_tables
from pdf_validation.ocr_balance_sheet import extract_scanned_financial_statements
from pdf_validation.page_content import detect_pdf_text_source
from pdf_validation.parser_selector import select_extraction_path
from pdf_validation.pdfplumber_fallback import extract_pdfplumber_tables
from pdf_validation.reconciliation import run_reconciliation


def resolve_adapter_kind(
    *,
    route: dict[str, Any] | None,
    config: dict[str, Any] | None,
    registry: dict[str, Any] | None = None,
) -> str:
    """Decide native vs ocr without inventing fund semantics."""
    route = route or {}
    config = config or {}
    if route.get("recommended_adapter") in {"native", "ocr"}:
        return str(route["recommended_adapter"])
    if config.get("requires_ocr") or config.get("extraction_mode") == "scanned_financial_statements":
        return "ocr"
    family = config.get("template_family") or route.get("template_family")
    if family and registry:
        fam = (registry.get("template_families") or {}).get(family) or {}
        if fam.get("requires_ocr"):
            return "ocr"
    # Config-driven scanned FS: OCR provider + line specs, no fund-family special cases.
    if (config.get("ocr") or {}).get("provider") and config.get("balance_sheet_lines"):
        return "ocr"
    return "native"


def run_native_adapter(
    *,
    pdf_path: Path | str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Native table extraction only — postprocess (deal status etc.) stays in pipeline."""
    camelot_raw = extract_camelot_tables(str(pdf_path), config)
    pdfplumber_raw = extract_pdfplumber_tables(str(pdf_path), config)
    selected = select_extraction_path(
        pdf_path=str(pdf_path),
        config=config,
        camelot_raw=camelot_raw,
        pdfplumber_raw=pdfplumber_raw,
        run_reconciliation_fn=run_reconciliation,
    )
    return {
        "adapter_kind": "native",
        "camelot_raw": camelot_raw,
        "pdfplumber_raw": pdfplumber_raw,
        "selected": selected,
    }


def run_ocr_adapter(
    *,
    pdf_path: Path | str,
    config: dict[str, Any],
    route: dict[str, Any] | None = None,
    render_dir: Path | None = None,
) -> dict[str, Any]:
    """OCR scanned FS extraction mapped to canonical payload fields."""
    pdf_path = Path(pdf_path)
    scan = detect_pdf_text_source(pdf_path)
    extraction = extract_scanned_financial_statements(
        pdf_path,
        config,
        render_dir=render_dir,
        dpi=int((config.get("ocr") or {}).get("dpi") or 300),
    )
    extraction["scan_detection"] = scan
    route = dict(route or {})
    route.setdefault("document_class", "financial_statements_with_schedule")
    if config.get("template_family"):
        route.setdefault("template_family", config["template_family"])
    route["extraction_mode"] = "scanned_financial_statements"
    route["comparison_grain"] = config.get("comparison_grain") or route.get("comparison_grain") or "statement_and_portfolio"
    route["recommended_adapter"] = "ocr"
    route["text_source"] = scan.get("source")
    route["requires_ocr"] = True
    route["compare_allowed"] = True
    route["schedule_pages"] = (extraction.get("schedule_pages") or {}).get("portfolio_investments") or []
    route["as_of_date"] = route.get("as_of_date")
    # Prefer OCR-parsed as-of when route missing.
    meta = {f["field"]: f for f in (extraction.get("metadata") or {}).get("fields") or []}
    if not route.get("as_of_date") and (meta.get("as_of_date") or {}).get("normalized"):
        route["as_of_date"] = meta["as_of_date"]["normalized"]

    canonical = ocr_extraction_to_canonical(extraction=extraction, route=route, config=config)
    canonical["adapter_kind"] = "ocr"
    canonical["scan_detection"] = scan
    return canonical
