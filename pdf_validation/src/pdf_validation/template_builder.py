"""Build per-PDF template configs from family base + detected layout."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from pdf_validation.document_router import load_registry
from pdf_validation.layout_detector import detect_layout


def _pkg_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_family_config(family_id: str, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or load_registry()
    rel = registry["template_families"][family_id]["base_config"]
    path = _pkg_root() / rel
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def build_config_from_route(
    pdf_path: Path,
    route: dict[str, Any],
    *,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    registry = registry or load_registry()
    family_id = route.get("template_family")
    if not family_id:
        raise ValueError(f"No template_family in route for {pdf_path}")
    cfg = deepcopy(load_family_config(family_id, registry))
    layout = detect_layout(pdf_path, route, registry)
    as_of = layout.get("as_of") or route.get("as_of_date")
    cfg["template_id"] = f"{family_id}_{as_of or 'unknown'}"
    cfg["template_family"] = family_id
    cfg["document"]["as_of_date_expected"] = as_of
    cfg["route"] = route
    cfg["layout"] = {
        "inv_pages": layout.get("inv_pages"),
        "real_pages": layout.get("real_pages"),
        "header": layout.get("header"),
    }

    inferred = route.get("inferred_schema") or {}
    if inferred:
        cfg["inferred_schema"] = inferred
        if inferred.get("column_names"):
            cfg["camelot"]["investments"]["column_names"] = list(inferred["column_names"])
            cfg["camelot"]["investments"]["expected_columns"] = len(inferred["column_names"])
        if inferred.get("comparison_grain"):
            cfg["comparison_grain"] = inferred["comparison_grain"]
        if inferred.get("company_row_headers"):
            cfg["prefer_text_fallback"] = True
            cfg["row_classification"]["mode"] = "dynamic_column_map"

    inv_pages = layout.get("inv_pages") or route.get("schedule_pages") or []
    real_pages = layout.get("real_pages") or route.get("realized_pages") or []
    cfg["pages"]["schedule_of_investments"] = inv_pages
    cfg["pages"]["schedule_of_realized"] = real_pages[0] if real_pages else None

    if inv_pages and layout.get("inv_cols"):
        page_expr = f"{inv_pages[0]}-{inv_pages[-1]}" if len(inv_pages) > 1 else str(inv_pages[0])
        cfg["camelot"]["investments"]["pages"] = page_expr
        cfg["camelot"]["investments"]["table_areas"] = [layout["inv_area"]]
        # Prefer inferred separators when available.
        if inferred.get("separators") and len(inferred["separators"]) >= 2:
            seps = inferred["separators"]
            inv_cols = ",".join(f"{s:.1f}" for s in seps)
            cfg["camelot"]["investments"]["columns"] = [inv_cols]
            page_w = layout["inv_page_size"][0]
            cfg["pdfplumber"]["investments"]["table_settings"]["explicit_vertical_lines"] = [
                10.0,
                *seps,
                page_w - 5,
            ]
        else:
            cfg["camelot"]["investments"]["columns"] = [layout["inv_cols"]]
            cfg["pdfplumber"]["investments"]["table_settings"]["explicit_vertical_lines"] = layout["inv_vlines"]
        cfg["pdfplumber"]["investments"]["pages"] = inv_pages
        iw, ih = layout["inv_page_size"]
        cfg["pdfplumber"]["investments"]["crop_bbox"] = [10, min(120, ih * 0.15), iw - 5, ih - 30]

    if real_pages and layout.get("real_cols"):
        cfg["camelot"]["realized"]["pages"] = str(real_pages[0])
        cfg["camelot"]["realized"]["table_areas"] = [layout["real_area"]]
        cfg["camelot"]["realized"]["columns"] = [layout["real_cols"]]
        cfg["pdfplumber"]["realized"]["pages"] = real_pages
        rw, rh = layout["real_page_size"]
        cfg["pdfplumber"]["realized"]["crop_bbox"] = [10, min(140, rh * 0.18), rw - 5, rh - 30]
        cfg["pdfplumber"]["realized"]["table_settings"]["explicit_vertical_lines"] = layout["real_vlines"]
    else:
        cfg["camelot"]["realized"]["pages"] = ""
        cfg["pdfplumber"]["realized"]["pages"] = []

    # Metadata date pattern from as_of.
    if as_of:
        y, m, d = as_of.split("-")
        month_name = {
            "01": "January",
            "02": "February",
            "03": "March",
            "04": "April",
            "05": "May",
            "06": "June",
            "07": "July",
            "08": "August",
            "09": "September",
            "10": "October",
            "11": "November",
            "12": "December",
        }[m]
        cfg["metadata_patterns"]["as_of_date"] = rf"{month_name}\s+{int(d)},\s*{y}"
        cfg["headers"]["ignore_row_patterns"] = [
            rf"^{month_name.lower()}\\s+{int(d)},\\s*{y}$",
            *cfg["headers"].get("ignore_row_patterns", []),
        ]

    soa = cfg.setdefault("reconciliation", {}).setdefault("statement_of_assets", {})
    if layout.get("investments_cost"):
        soa["investments_cost"] = layout["investments_cost"]
    if layout.get("investments_fv"):
        soa["investments_fair_value"] = layout["investments_fv"]
    if layout.get("unrealized"):
        soa["net_unrealized_appreciation"] = layout["unrealized"]

    # Only keep golden totals when explicitly required (SYN regression).
    if not cfg["reconciliation"].get("golden_required", False):
        cfg["reconciliation"]["golden_schedule_investments"] = {
            "cost": soa.get("investments_cost"),
            "fair_value": soa.get("investments_fair_value"),
            "unrealized_gain_loss": soa.get("net_unrealized_appreciation"),
        }
        cfg["reconciliation"]["golden_schedule_realized"] = {
            "cost": None,
            "cash_proceeds": None,
            "realized_gain_loss": soa.get("net_realized_gain_loss"),
        }

    cfg["cross_page"] = {"continuation_companies": []}
    return cfg


def write_generated_config(cfg: dict[str, Any], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return out_path
