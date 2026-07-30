"""Entity alias loading and explicit mapping helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pdf_validation.vendor_comparison import _normalize_name


def load_aliases(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        path = Path(__file__).resolve().parents[2] / "configs" / "entity_aliases.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def resolve_alias(pdf_name: str, *, fund_id: str | None = None, aliases: dict[str, Any] | None = None) -> str | None:
    aliases = aliases or load_aliases()
    norm = _normalize_name(pdf_name)
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
    aliases = aliases or load_aliases()
    vendor_by_norm = {_normalize_name(v): v for v in vendor_names}
    mappings: list[dict[str, Any]] = []
    used_vendor: set[str] = set()

    def resolve(pdf_name: str) -> str | None:
        aliased = resolve_alias(pdf_name, fund_id=fund_id, aliases=aliases)
        if aliased:
            return aliased
        n = _normalize_name(pdf_name)
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
