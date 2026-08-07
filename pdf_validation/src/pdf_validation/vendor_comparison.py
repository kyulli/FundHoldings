"""PDF extraction vs vendor CSV comparison with hard comparability gates."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd

from pdf_validation.export import _records_to_df, _write_jsonl
from pdf_validation.manifest import file_sha256, git_commit, package_versions
from pdf_validation.metric_semantics import (
    build_pdf_comparable_metrics,
    build_portfolio_company_vendor_metrics,
    build_statement_line_metrics,
)


MAPPING_TYPES = {
    "exact",
    "derived",
    "approximate",
    "ambiguous",
    "not_disclosed",
    "not_applicable",
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_mapping(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _metadata_map(fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["field"]: row for row in fields if "field" in row}


def _parse_vendor_date(value: Any, formats: list[str]) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        parsed = pd.to_datetime(text, dayfirst=True)
        if pd.isna(parsed):
            return None
        return parsed.date().isoformat()
    except Exception:  # noqa: BLE001
        return None


def _normalize_name(value: str) -> str:
    text = value.lower()
    text = re.sub(r"[\"'().,]", " ", text)
    text = re.sub(r"\b(inc|incorporated|ltd|limited|llc|corp|corporation|co|company|dba|the)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _fuzzy_candidates(
    pdf_names: list[str],
    vendor_names: list[str],
    *,
    threshold: float = 0.84,
    limit: int = 3,
) -> list[dict[str, Any]]:
    vendor_norm = [(_normalize_name(name), name) for name in vendor_names if isinstance(name, str) and name.strip()]
    candidates: list[dict[str, Any]] = []
    for pdf_name in pdf_names:
        pdf_norm = _normalize_name(pdf_name)
        scored: list[tuple[float, str]] = []
        for vendor_n, vendor_raw in vendor_norm:
            score = SequenceMatcher(None, pdf_norm, vendor_n).ratio()
            if score >= threshold:
                scored.append((score, vendor_raw))
        scored.sort(reverse=True)
        for score, vendor_raw in scored[:limit]:
            candidates.append(
                {
                    "pdf_company_name": pdf_name,
                    "vendor_source_asset_candidate": vendor_raw,
                    "similarity": round(score, 4),
                    "status": "candidate_only",
                    "confirmed": False,
                    "note": "Fuzzy match may only generate candidates; never auto-confirm entity mapping.",
                }
            )
    return candidates


def evaluate_gates(
    *,
    metadata: dict[str, dict[str, Any]],
    mapping: dict[str, Any],
    vendor_columns: list[str],
    vendor_dates_for_fund: list[str],
    confirmed_entity_mappings: int,
    extraction_quality: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
    fund_aggregate: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    gates_cfg = mapping["comparability"]
    gates: list[dict[str, Any]] = []
    route = route or {}
    extraction_mode = route.get("extraction_mode") or mapping.get("extraction_mode") or "position_level"

    # Extraction quality gate
    eq = extraction_quality or {}
    if extraction_mode == "blocked_narrative":
        gates.append(
            {
                "gate": "extraction_quality",
                "status": "FAIL",
                "extraction_mode": extraction_mode,
                "reason": "Investor letter / narrative document; holdings extraction blocked.",
                "blocks_amount_comparison": True,
            }
        )
    elif extraction_mode == "manual_review":
        gates.append(
            {
                "gate": "extraction_quality",
                "status": "FAIL",
                "extraction_mode": extraction_mode,
                "reason": "Document requires manual review; no automatic amount comparison.",
                "blocks_amount_comparison": True,
            }
        )
    elif extraction_mode == "position_level_inferred":
        eq_status = eq.get("status")
        gates.append(
            {
                "gate": "extraction_quality",
                "status": "FAIL" if eq_status in {None, "FAIL", "REVIEW_REQUIRED", "BLOCKED"} else "PASS",
                "extraction_mode": extraction_mode,
                "selected_parser": eq.get("selected_parser"),
                "reason": (
                    eq.get("reason")
                    or "Inferred schema extraction requires approved mapping before amount comparison."
                ),
                "blocks_amount_comparison": True,
            }
        )
    elif extraction_mode == "fund_aggregate_only":
        agg_ok = bool(fund_aggregate and fund_aggregate.get("parse_status") == "ok")
        gates.append(
            {
                "gate": "extraction_quality",
                "status": "PASS" if agg_ok else "FAIL",
                "extraction_mode": extraction_mode,
                "fund_aggregate": fund_aggregate,
                "reason": (
                    "Fund aggregate parsed from statement."
                    if agg_ok
                    else "Aggregate-only document but statement totals could not be parsed."
                ),
                "blocks_amount_comparison": True,
            }
        )
    elif extraction_mode == "scanned_financial_statements":
        eq_pass = eq.get("status") == "PASS"
        gates.append(
            {
                "gate": "extraction_quality",
                "status": "PASS" if eq_pass else "FAIL",
                "extraction_mode": extraction_mode,
                "selected_parser": eq.get("selected_parser"),
                "reason": eq.get("reason") or "OCR scanned financial statements extraction.",
                "blocks_amount_comparison": True,
            }
        )
    else:
        eq_pass = eq.get("status") == "PASS"
        gates.append(
            {
                "gate": "extraction_quality",
                "status": "PASS" if eq_pass or eq.get("status") is None else "FAIL",
                "extraction_mode": extraction_mode,
                "selected_parser": eq.get("selected_parser"),
                "reason": eq.get("reason") or "Extraction quality not recorded; treating as pass for legacy extracts.",
                "blocks_amount_comparison": True,
            }
        )

    # Schedule grain gate
    grain_cfg = gates_cfg.get("grain") or {}
    pdf_grain = route.get("comparison_grain") or grain_cfg.get("pdf_comparison_grain") or "company"
    if extraction_mode == "fund_aggregate_only":
        gates.append(
            {
                "gate": "schedule_grain",
                "status": "PASS",
                "pdf_extraction_mode": extraction_mode,
                "pdf_comparison_grain": "fund",
                "vendor_comparison_grain": grain_cfg.get("vendor_comparison_grain"),
                "allowed_comparison_modes": ["fund_aggregate_only"],
                "blocks_company_amount_comparison": True,
                "blocks_amount_comparison": False,
                "reason": "PDF has no position schedule; only fund-level aggregate comparison is allowed.",
            }
        )
    elif extraction_mode in {"blocked_narrative", "manual_review"}:
        gates.append(
            {
                "gate": "schedule_grain",
                "status": "FAIL",
                "pdf_extraction_mode": extraction_mode,
                "pdf_comparison_grain": pdf_grain,
                "blocks_amount_comparison": True,
                "reason": f"Extraction mode {extraction_mode} is not company-comparable.",
            }
        )
    elif extraction_mode == "scanned_financial_statements" or pdf_grain == "statement_and_portfolio":
        gates.append(
            {
                "gate": "schedule_grain",
                "status": "PASS",
                "pdf_extraction_mode": extraction_mode,
                "pdf_comparison_grain": "statement_and_portfolio",
                "vendor_comparison_grain": grain_cfg.get("vendor_comparison_grain"),
                "blocks_company_amount_comparison": False,
                "blocks_amount_comparison": False,
                "reason": "Scanned FS statement-line + portfolio company comparison grain.",
            }
        )
    else:
        gates.append(
            {
                "gate": "schedule_grain",
                "status": "PASS",
                "pdf_extraction_mode": extraction_mode,
                "pdf_comparison_grain": pdf_grain,
                "vendor_comparison_grain": grain_cfg.get("vendor_comparison_grain"),
                "blocks_company_amount_comparison": pdf_grain not in {"company", "mixed_condensed"},
                "blocks_amount_comparison": False,
                "reason": f"Position-level extraction with comparison grain={pdf_grain}.",
            }
        )

    # Metric availability
    has_realized = bool(mapping.get("has_realized_schedule", True))
    if extraction_mode == "fund_aggregate_only":
        has_realized = False
    gates.append(
        {
            "gate": "metric_availability",
            "status": "PASS",
            "has_realized_schedule": has_realized,
            "company_metrics_available": extraction_mode in {"position_level", "scanned_financial_statements"},
            "fund_metrics_available": extraction_mode in {"position_level", "fund_aggregate_only", "scanned_financial_statements"},
            "reason": (
                "Company metrics available."
                if extraction_mode == "position_level"
                else "Statement-line and portfolio metrics available."
                if extraction_mode == "scanned_financial_statements"
                else "Only fund aggregate metrics available."
                if extraction_mode == "fund_aggregate_only"
                else "No amount metrics available for this document class."
            ),
            "blocks_amount_comparison": False,
        }
    )

    fund_cfg = gates_cfg["fund_identity"]
    pdf_fund = metadata.get(fund_cfg["pdf_fund_name_field"], {})
    vendor_fund_id = fund_cfg.get("vendor_fund_id")
    if fund_cfg.get("require_explicit_fund_id", True):
        fund_pass = vendor_fund_id is not None and str(vendor_fund_id).strip() != ""
    else:
        fund_pass = True
    gates.append(
        {
            "gate": "fund_identity",
            "status": "PASS" if fund_pass else "FAIL",
            "pdf_value": pdf_fund.get("normalized") or pdf_fund.get("raw"),
            "vendor_fund_id": vendor_fund_id,
            "vendor_fund_id_field": fund_cfg.get("vendor_fund_id_field"),
            "reason": (
                fund_cfg.get("identity_confirmation_note")
                or "Explicit vendor Fund Allocator ID provided in mapping config."
                if fund_pass
                else "No explicit vendor_fund_id in mapping config; anonymized CSV cannot confirm fund identity."
            ),
            "blocks_amount_comparison": True,
        }
    )

    date_cfg = gates_cfg["as_of_date"]
    pdf_date = metadata.get(date_cfg["pdf_field"], {}).get("normalized") or date_cfg.get("pdf_normalized_expected")
    vendor_has_date = pdf_date in set(vendor_dates_for_fund) if pdf_date else False
    date_pass = bool(pdf_date) and vendor_has_date and fund_pass
    gates.append(
        {
            "gate": "as_of_date",
            "status": "PASS" if date_pass else "FAIL",
            "pdf_value": pdf_date,
            "vendor_field": date_cfg["vendor_field"],
            "vendor_dates_for_fund": vendor_dates_for_fund,
            "vendor_has_pdf_as_of_date": vendor_has_date,
            "reason": (
                f"PDF as-of date {pdf_date} present for mapped fund {vendor_fund_id}."
                if date_pass
                else f"PDF as-of date {pdf_date} not available for mapped fund rows."
            ),
            "blocks_amount_comparison": True,
        }
    )

    def _gate_with_waiver(name: str, cfg: dict[str, Any], pdf_meta: dict[str, Any]) -> dict[str, Any]:
        disclosed = pdf_meta.get("parse_status") == "ok" and pdf_meta.get("normalized")
        vendor_present = cfg["vendor_field"] in vendor_columns
        waiver = cfg.get("waiver") or {}
        if disclosed and vendor_present and fund_pass:
            return {
                "gate": name,
                "status": "PASS",
                "pdf_value": pdf_meta.get("normalized"),
                "pdf_parse_status": pdf_meta.get("parse_status"),
                "vendor_field": cfg["vendor_field"],
                "vendor_field_present": vendor_present,
                "waiver_applied": False,
                "reason": f"PDF {name} disclosed and vendor field available.",
                "blocks_amount_comparison": True,
            }
        if waiver.get("enabled") and fund_pass:
            return {
                "gate": name,
                "status": "PASS",
                "pdf_value": pdf_meta.get("normalized"),
                "pdf_parse_status": pdf_meta.get("parse_status"),
                "vendor_field": cfg["vendor_field"],
                "vendor_field_present": vendor_present,
                "waiver_applied": True,
                "assumed_value": waiver.get("assumed_value"),
                "reason": waiver.get("reason"),
                "blocks_amount_comparison": True,
            }
        return {
            "gate": name,
            "status": "FAIL",
            "pdf_value": pdf_meta.get("normalized"),
            "pdf_parse_status": pdf_meta.get("parse_status"),
            "vendor_field": cfg["vendor_field"],
            "vendor_field_present": vendor_present,
            "waiver_applied": False,
            "reason": f"PDF {name} not disclosed and no enabled waiver in mapping config.",
            "blocks_amount_comparison": True,
        }

    gates.append(_gate_with_waiver("currency", gates_cfg["currency"], metadata.get("currency", {})))
    gates.append(_gate_with_waiver("unit", gates_cfg["unit"], metadata.get("unit", {})))

    amount_critical = {"current_cost", "unrealized_value", "realized_proceeds", "capital_invested", "total_value"}
    field_dict_rows = mapping.get("field_dictionary") or []
    ambiguous_fields = [
        row for row in field_dict_rows if row.get("mapping_type") in {"ambiguous", "not_disclosed"}
    ]
    unresolved_critical = [
        row
        for row in field_dict_rows
        if row.get("logical_field") in amount_critical
        and row.get("mapping_type") in {"ambiguous", "not_disclosed"}
        and not fund_pass
    ]
    semantics_pass = fund_pass and date_pass and not unresolved_critical
    gates.append(
        {
            "gate": "field_semantics",
            "status": "PASS" if semantics_pass else "FAIL",
            "ambiguous_or_blocked_fields": [
                {
                    "logical_field": row["logical_field"],
                    "mapping_type": row["mapping_type"],
                    "pdf_field": row.get("pdf_field"),
                    "vendor_field": row.get("vendor_field"),
                    "notes": row.get("notes"),
                }
                for row in ambiguous_fields
            ],
            "reason": (
                "Fund/date mapped; approximate amount fields may be compared with explicit mapping_type labels."
                if semantics_pass
                else "Fund/date unmapped or amount-critical fields blocked."
            ),
            "blocks_amount_comparison": True,
        }
    )

    grain_pass = bool(fund_pass and date_pass and confirmed_entity_mappings > 0 and extraction_mode == "position_level")
    if extraction_mode == "fund_aggregate_only":
        grain_pass = bool(fund_pass and date_pass)
    if extraction_mode == "scanned_financial_statements" or pdf_grain == "statement_and_portfolio":
        grain_pass = bool(fund_pass and date_pass and confirmed_entity_mappings > 0)
    gates.append(
        {
            "gate": "data_grain",
            "status": "PASS" if grain_pass else "FAIL",
            "pdf_comparison_grain": pdf_grain,
            "vendor_comparison_grain": grain_cfg.get("vendor_comparison_grain"),
            "confirmed_entity_mappings": confirmed_entity_mappings,
            "allow_automatic_entity_confirmation": grain_cfg.get("allow_automatic_entity_confirmation"),
            "reason": (
                f"Company-level comparison enabled with {confirmed_entity_mappings} explicit entity mappings."
                if grain_pass and extraction_mode == "position_level"
                else f"Statement/portfolio comparison enabled with {confirmed_entity_mappings} explicit entity mappings."
                if grain_pass and (extraction_mode == "scanned_financial_statements" or pdf_grain == "statement_and_portfolio")
                else "Fund-level aggregate comparison enabled."
                if grain_pass and extraction_mode == "fund_aggregate_only"
                else "Need fund/date identity plus at least one explicit entity mapping; fuzzy candidates cannot confirm."
            ),
            "blocks_amount_comparison": True,
        }
    )
    return gates


def build_field_dictionary(mapping: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in mapping.get("field_dictionary") or []:
        mapping_type = item.get("mapping_type")
        if mapping_type not in MAPPING_TYPES:
            raise ValueError(f"Invalid mapping_type: {mapping_type}")
        rows.append(
            {
                "attribute_class": item.get("attribute_class"),
                "logical_field": item.get("logical_field"),
                "pdf_field": item.get("pdf_field"),
                "pdf_location": item.get("pdf_location"),
                "vendor_field": item.get("vendor_field"),
                "mapping_type": mapping_type,
                "comparison_grain": item.get("comparison_grain"),
                "notes": item.get("notes"),
            }
        )
    return rows


def _load_vendor_fund_slice(
    vendor_csv: Path,
    mapping: dict[str, Any],
) -> pd.DataFrame:
    fund_cfg = mapping["comparability"]["fund_identity"]
    date_cfg = mapping["comparability"]["as_of_date"]
    grain_cfg = mapping["comparability"]["grain"]
    fund_id = fund_cfg["vendor_fund_id"]
    needed = {
        fund_cfg["vendor_fund_id_field"],
        date_cfg["vendor_field"],
        grain_cfg["vendor_entity_field"],
        "Deal Status",
        "Current Cost",
        "Unrealized Value",
        "Capital Invested",
        "Realized Cost",
        "Realized Proceeds",
        "Realized Gain/Loss",
        "Total Value",
        "Unrealized Gain/Loss",
        "Sector",
        "Geographic Focus",
        "Reporting Currency",
        "Reporting Currency Unit",
        "Investment Manager Allocator ID",
    }
    all_cols = list(pd.read_csv(vendor_csv, nrows=0).columns)
    usecols = [c for c in needed if c in all_cols]
    df = pd.read_csv(vendor_csv, usecols=usecols, low_memory=False)
    df = df[df[fund_cfg["vendor_fund_id_field"]].astype(str) == str(fund_id)].copy()
    df["_as_of_iso"] = df[date_cfg["vendor_field"]].map(
        lambda v: _parse_vendor_date(v, date_cfg.get("vendor_date_formats", []))
    )
    return df


def run_amount_comparisons(
    *,
    pdf_metrics: list[dict[str, Any]],
    vendor_df: pd.DataFrame,
    mapping: dict[str, Any],
) -> list[dict[str, Any]]:
    date_cfg = mapping["comparability"]["as_of_date"]
    grain_cfg = mapping["comparability"]["grain"]
    pdf_date = date_cfg.get("pdf_normalized_expected")
    entity_field = grain_cfg["vendor_entity_field"]
    exclude_re = grain_cfg.get("exclude_vendor_source_asset_regex")

    slice_df = vendor_df[vendor_df["_as_of_iso"] == pdf_date].copy()
    if exclude_re:
        slice_df = slice_df[~slice_df[entity_field].astype(str).str.contains(exclude_re, na=False)]

    vendor_by_name = {
        str(row[entity_field]): row for _, row in slice_df.iterrows() if pd.notna(row.get(entity_field))
    }
    pdf_by_vendor = {row["vendor_source_asset"]: row for row in pdf_metrics}

    results: list[dict[str, Any]] = []
    for vendor_name, pdf_row in pdf_by_vendor.items():
        vendor_row = vendor_by_name.get(vendor_name)
        if vendor_row is None:
            results.append(
                {
                    "pdf_company_name": pdf_row.get("pdf_company_name"),
                    "pdf_realized_company_name": pdf_row.get("pdf_realized_company_name"),
                    "vendor_source_asset": vendor_name,
                    "status": "entity_unresolved",
                    "reason": "Confirmed mapping present but vendor row missing on as-of date slice.",
                    "pdf_found": True,
                    "vendor_found": False,
                }
            )
            continue

        # Deal status derived vs CSV (requires realized schedule to be meaningful).
        csv_deal = None if pd.isna(vendor_row.get("Deal Status")) else vendor_row.get("Deal Status")
        if mapping.get("has_realized_schedule", True):
            derived_deal = pdf_row.get("deal_status_derived")
            results.append(
                {
                    "pdf_company_name": pdf_row.get("pdf_company_name"),
                    "pdf_realized_company_name": pdf_row.get("pdf_realized_company_name"),
                    "vendor_source_asset": vendor_name,
                    "logical_field": "deal_status",
                    "mapping_type": "derived",
                    "pdf_field": "deal_status_derived",
                    "vendor_field": "Deal Status",
                    "pdf_value": derived_deal,
                    "csv_value": csv_deal,
                    "difference": None,
                    "tolerance": None,
                    "status": "match" if derived_deal == csv_deal else "mismatch",
                    "entity_mapping_status": "confirmed_explicit",
                    "pdf_value_source": "derived_from_dictionary_rules",
                    "deal_status_csv": csv_deal,
                    "notes": pdf_row.get("deal_status_inference_rule"),
                }
            )

        for field in mapping.get("amount_fields", []):
            metric_key = field["pdf_metric"]
            pdf_val = _to_decimal(pdf_row.get(metric_key))
            vendor_val = _to_decimal(vendor_row.get(field["vendor_field"]))
            tolerance = Decimal(str(field.get("tolerance_absolute", "1")))
            source_key = field.get("pdf_source_field")
            pdf_source = pdf_row.get(source_key) if source_key else field.get("mapping_type")

            if pdf_val is None or vendor_val is None:
                status = "csv_missing" if vendor_val is None and pdf_val is not None else "pdf_missing"
                if pdf_val is None and vendor_val is None:
                    status = "both_missing"
                results.append(
                    {
                        "pdf_company_name": pdf_row.get("pdf_company_name"),
                        "pdf_realized_company_name": pdf_row.get("pdf_realized_company_name"),
                        "vendor_source_asset": vendor_name,
                        "logical_field": field["logical_field"],
                        "mapping_type": field.get("mapping_type"),
                        "pdf_field": metric_key,
                        "vendor_field": field["vendor_field"],
                        "pdf_value": None if pdf_val is None else format(pdf_val, "f"),
                        "csv_value": None if vendor_val is None else format(vendor_val, "f"),
                        "difference": None,
                        "tolerance": format(tolerance, "f"),
                        "status": status,
                        "entity_mapping_status": "confirmed_explicit",
                        "pdf_value_source": pdf_source,
                        "deal_status_csv": csv_deal,
                        "notes": field.get("notes"),
                    }
                )
                continue

            diff = vendor_val - pdf_val
            results.append(
                {
                    "pdf_company_name": pdf_row.get("pdf_company_name"),
                    "pdf_realized_company_name": pdf_row.get("pdf_realized_company_name"),
                    "vendor_source_asset": vendor_name,
                    "logical_field": field["logical_field"],
                    "mapping_type": field.get("mapping_type"),
                    "pdf_field": metric_key,
                    "vendor_field": field["vendor_field"],
                    "pdf_value": format(pdf_val, "f"),
                    "csv_value": format(vendor_val, "f"),
                    "difference": format(diff, "f"),
                    "tolerance": format(tolerance, "f"),
                    "status": "match" if abs(diff) <= tolerance else "mismatch",
                    "entity_mapping_status": "confirmed_explicit",
                    "pdf_value_source": pdf_source,
                    "deal_status_csv": csv_deal,
                    "notes": field.get("notes"),
                }
            )

    mapped_vendor = set(pdf_by_vendor)
    for vendor_name in vendor_by_name:
        if vendor_name not in mapped_vendor:
            results.append(
                {
                    "pdf_company_name": None,
                    "vendor_source_asset": vendor_name,
                    "logical_field": None,
                    "status": "vendor_only_unmapped",
                    "entity_mapping_status": "unresolved",
                    "deal_status_csv": None
                    if pd.isna(vendor_by_name[vendor_name].get("Deal Status"))
                    else vendor_by_name[vendor_name].get("Deal Status"),
                }
            )
    return results


def build_spot_checks(
    *,
    gates: list[dict[str, Any]],
    field_dictionary: list[dict[str, Any]],
    metadata: dict[str, dict[str, Any]],
    company_summary: list[dict[str, Any]],
    amount_comparisons: list[dict[str, Any]],
    comparability_status: str,
) -> list[dict[str, Any]]:
    failing = [g["gate"] for g in gates if g["status"] != "PASS"]
    rows: list[dict[str, Any]] = []

    for gate in gates:
        rows.append(
            {
                "check_scope": "comparability_gate",
                "logical_field": gate["gate"],
                "pdf_value": gate.get("pdf_value"),
                "csv_value": gate.get("vendor_fund_id") or gate.get("assumed_value") or gate.get("vendor_field"),
                "comparison_result": "gate_pass" if gate["status"] == "PASS" else "not_comparable",
                "entity_mapping_status": "not_applicable",
                "evidence": gate.get("reason"),
                "waiver_applied": gate.get("waiver_applied"),
            }
        )

    for field in field_dictionary:
        pdf_field = field.get("pdf_field")
        pdf_value = None
        if pdf_field in metadata:
            pdf_value = metadata[pdf_field].get("normalized") or metadata[pdf_field].get("raw")
        elif pdf_field and company_summary and pdf_field in company_summary[0]:
            pdf_value = f"available_on_{len(company_summary)}_companies"

        if comparability_status == "not_comparable":
            result = "not_comparable"
        elif field["mapping_type"] == "not_disclosed":
            result = "pdf_not_disclosed"
        elif field["mapping_type"] == "ambiguous":
            result = "ambiguous_mapping"
        else:
            related = [
                a
                for a in amount_comparisons
                if a.get("logical_field") == field.get("logical_field") and a.get("status") in {"match", "mismatch"}
            ]
            if not related:
                result = "pending_or_not_applicable"
            elif all(a["status"] == "match" for a in related):
                result = "match"
            else:
                result = "mismatch"

        rows.append(
            {
                "check_scope": "field",
                "attribute_class": field.get("attribute_class"),
                "logical_field": field.get("logical_field"),
                "pdf_field": pdf_field,
                "pdf_value": pdf_value,
                "pdf_location": field.get("pdf_location"),
                "csv_field": field.get("vendor_field"),
                "mapping_type": field.get("mapping_type"),
                "comparison_result": result,
                "entity_mapping_status": "confirmed_explicit"
                if comparability_status == "comparable"
                else "unresolved",
                "blocking_gates": failing,
                "evidence": field.get("notes"),
            }
        )

    for amount in amount_comparisons:
        if amount.get("logical_field") is None:
            continue
        rows.append(
            {
                "check_scope": "amount",
                "logical_field": amount.get("logical_field"),
                "pdf_company_name": amount.get("pdf_company_name"),
                "vendor_source_asset": amount.get("vendor_source_asset"),
                "pdf_value": amount.get("pdf_value"),
                "csv_value": amount.get("csv_value"),
                "difference": amount.get("difference"),
                "comparison_result": amount.get("status"),
                "entity_mapping_status": amount.get("entity_mapping_status"),
                "mapping_type": amount.get("mapping_type"),
                "deal_status_csv": amount.get("deal_status_csv"),
                "evidence": amount.get("notes"),
            }
        )
    return rows


DEFAULT_STATEMENT_AMOUNT_FIELDS = [
    "Current Cost",
    "Unrealized Value",
    "Capital Invested",
    "Realized Proceeds",
    "Realized Cost",
    "Total Value",
    "Unrealized Gain/Loss",
]


def _build_statement_portfolio_pdf_rows(
    *,
    statement_entities: list[dict[str, Any]],
    company_summary: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entity in build_statement_line_metrics(statement_entities):
        rows.append(
            {
                "entity_kind": "statement_line",
                "pdf_entity": entity.get("pdf_label") or entity.get("pdf_line_id"),
                "pdf_line_id": entity.get("pdf_line_id"),
                "vendor_source_asset": entity.get("vendor_source_asset"),
                "metrics": entity.get("metrics") or {},
                "source_page": entity.get("source_page"),
                "ocr_confidence": entity.get("ocr_confidence"),
            }
        )
    for company in build_portfolio_company_vendor_metrics(company_summary):
        rows.append(
            {
                "entity_kind": "portfolio_company",
                "pdf_entity": company.get("pdf_company_name"),
                "pdf_line_id": None,
                "vendor_source_asset": company.get("vendor_source_asset"),
                "metrics": company.get("metrics") or {},
                "source_page": company.get("source_page"),
                "ocr_confidence": company.get("ocr_confidence"),
            }
        )
    return rows


def _match_statement_portfolio_row(
    pdf_rows: list[dict[str, Any]],
    vendor_name: str,
    entity_mappings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for mapping in entity_mappings:
        if mapping.get("vendor_source_asset") != vendor_name or not mapping.get("confirmed"):
            continue
        if mapping.get("pdf_line_id"):
            hit = next((r for r in pdf_rows if r.get("pdf_line_id") == mapping.get("pdf_line_id")), None)
            if hit:
                return hit
        aliases = [
            mapping.get("pdf_label"),
            mapping.get("pdf_company_name"),
            mapping.get("vendor_source_asset"),
        ]
        alias_norms = {_normalize_name(str(a)) for a in aliases if a}
        for row in pdf_rows:
            entity = str(row.get("pdf_entity") or "")
            entity_n = _normalize_name(entity)
            vendor_n = _normalize_name(str(row.get("vendor_source_asset") or ""))
            if entity_n in alias_norms or vendor_n in alias_norms:
                return row
            if any(entity_n and (entity_n in an or an in entity_n) for an in alias_norms if an):
                return row
    # Fuzzy candidates are generated elsewhere for review queues only.
    # Unconfirmed aliases must not auto-pass amount comparison.
    return None


def run_statement_portfolio_matrix(
    *,
    statement_entities: list[dict[str, Any]],
    company_summary: list[dict[str, Any]],
    vendor_df: pd.DataFrame,
    mapping: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Full-field matrix: every populated vendor amount field must exact-match PDF."""
    amount_fields = mapping.get("vendor_amount_fields") or DEFAULT_STATEMENT_AMOUNT_FIELDS
    entity_mappings = [e for e in (mapping.get("entity_mappings") or []) if e.get("confirmed")]
    pdf_rows = _build_statement_portfolio_pdf_rows(
        statement_entities=statement_entities,
        company_summary=company_summary,
    )
    grain_cfg = mapping["comparability"]["grain"]
    entity_field = grain_cfg["vendor_entity_field"]
    date_cfg = mapping["comparability"]["as_of_date"]
    pdf_date = date_cfg.get("pdf_normalized_expected")
    slice_df = vendor_df[vendor_df["_as_of_iso"] == pdf_date].copy() if pdf_date else vendor_df.iloc[0:0].copy()

    comparisons: list[dict[str, Any]] = []
    alarms: list[dict[str, Any]] = []
    populated_total = 0
    matched_total = 0

    for _, vendor_row in slice_df.iterrows():
        vendor_name = str(vendor_row.get(entity_field) or "").strip()
        if not vendor_name:
            continue
        pdf_row = _match_statement_portfolio_row(pdf_rows, vendor_name, entity_mappings)
        if pdf_row is None:
            populated = [f for f in amount_fields if _to_decimal(vendor_row.get(f)) is not None]
            if populated:
                alarms.append(
                    {
                        "alarm_id": "pdf_entity_missing",
                        "severity": "high",
                        "vendor_source_asset": vendor_name,
                        "message": f"Vendor row has populated fields {populated} but no PDF entity matched",
                        "populated_fields": populated,
                    }
                )
            comparisons.append(
                {
                    "status": "pdf_missing_entity",
                    "vendor_source_asset": vendor_name,
                    "populated_vendor_fields": populated,
                }
            )
            continue

        for field in amount_fields:
            csv_val = _to_decimal(vendor_row.get(field))
            pdf_val = _to_decimal((pdf_row.get("metrics") or {}).get(field))
            if csv_val is None and pdf_val is None:
                comparisons.append(
                    {
                        "status": "both_blank",
                        "vendor_source_asset": vendor_name,
                        "logical_field": field,
                        "pdf_entity": pdf_row.get("pdf_entity"),
                        "pdf_line_id": pdf_row.get("pdf_line_id"),
                    }
                )
                continue
            if csv_val is None and pdf_val is not None:
                comparisons.append(
                    {
                        "status": "vendor_blank_pdf_present",
                        "vendor_source_asset": vendor_name,
                        "logical_field": field,
                        "pdf_entity": pdf_row.get("pdf_entity"),
                        "pdf_value": format(pdf_val, "f"),
                        "csv_value": None,
                        "severity": "info",
                    }
                )
                continue
            populated_total += 1
            if pdf_val is None:
                alarms.append(
                    {
                        "alarm_id": "pdf_field_missing",
                        "severity": "high",
                        "vendor_source_asset": vendor_name,
                        "logical_field": field,
                        "csv_value": format(csv_val, "f"),
                        "message": f"Vendor {field} populated but PDF metric missing",
                    }
                )
                comparisons.append(
                    {
                        "status": "pdf_missing_field",
                        "vendor_source_asset": vendor_name,
                        "logical_field": field,
                        "pdf_entity": pdf_row.get("pdf_entity"),
                        "pdf_value": None,
                        "csv_value": format(csv_val, "f"),
                    }
                )
                continue
            diff = csv_val - pdf_val
            status = "match" if abs(diff) <= Decimal("0") else "mismatch"
            if status == "match":
                matched_total += 1
            else:
                alarms.append(
                    {
                        "alarm_id": "amount_mismatch",
                        "severity": "high",
                        "vendor_source_asset": vendor_name,
                        "logical_field": field,
                        "pdf_value": format(pdf_val, "f"),
                        "csv_value": format(csv_val, "f"),
                        "difference": format(diff, "f"),
                        "message": f"{field}: PDF {pdf_val} != vendor {csv_val}",
                    }
                )
            comparisons.append(
                {
                    "status": status,
                    "vendor_source_asset": vendor_name,
                    "logical_field": field,
                    "pdf_entity": pdf_row.get("pdf_entity"),
                    "pdf_line_id": pdf_row.get("pdf_line_id"),
                    "pdf_value": format(pdf_val, "f"),
                    "csv_value": format(csv_val, "f"),
                    "difference": format(diff, "f"),
                    "tolerance": "0",
                    "source_page": pdf_row.get("source_page"),
                    "ocr_confidence": pdf_row.get("ocr_confidence"),
                    "mapping_type": "exact",
                    "entity_mapping_status": "confirmed_explicit",
                }
            )

    summary = {
        "vendor_row_count": int(len(slice_df)),
        "pdf_metric_entities": len(pdf_rows),
        "populated_vendor_fields": populated_total,
        "matched_vendor_fields": matched_total,
        "mismatch_fields": sum(1 for c in comparisons if c.get("status") == "mismatch"),
        "alarm_count": len(alarms),
        "blocking_alarm_count": len([a for a in alarms if a.get("severity") in {"high", "critical"}]),
        "amount_fields_tested": amount_fields,
    }
    return comparisons, alarms, summary


def compare_with_vendor(
    *,
    extraction_dir: Path,
    vendor_csv: Path,
    mapping_config: Path | None = None,
    output_dir: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    extraction_dir = Path(extraction_dir)
    vendor_csv = Path(vendor_csv)
    if mapping_config is None:
        raise ValueError("mapping_config is required; refusing to invent fund identity mappings.")
    mapping_config = Path(mapping_config)
    output_dir = Path(output_dir) if output_dir else extraction_dir / "vendor_comparison"
    repo_root = Path(repo_root) if repo_root else extraction_dir.parents[1]

    mapping = _load_mapping(mapping_config)
    metadata_fields = _read_jsonl(extraction_dir / "metadata.jsonl")
    metadata = _metadata_map(metadata_fields)
    company_summary = _read_jsonl(extraction_dir / "company_summary.jsonl")
    statement_entities = _read_jsonl(extraction_dir / "statement_entities.jsonl")
    realized_lots = _read_jsonl(extraction_dir / "realized_lots.jsonl")
    run_manifest_rows = _read_jsonl(extraction_dir / "run_manifest.jsonl")
    extraction_manifest = run_manifest_rows[0] if run_manifest_rows else {}

    route = {}
    route_path = extraction_dir / "route.json"
    if route_path.exists():
        route = json.loads(route_path.read_text(encoding="utf-8"))
    elif extraction_manifest.get("route"):
        route = extraction_manifest["route"]

    extraction_quality = extraction_manifest.get("extraction_quality") or {}
    eq_path = extraction_dir / "extraction_quality.json"
    if eq_path.exists():
        extraction_quality = json.loads(eq_path.read_text(encoding="utf-8"))

    fund_aggregate = None
    fa_path = extraction_dir / "fund_aggregate.json"
    if fa_path.exists():
        fund_aggregate = json.loads(fa_path.read_text(encoding="utf-8"))

    vendor_columns = list(pd.read_csv(vendor_csv, nrows=0).columns)
    all_confirmed = [e for e in mapping.get("entity_mappings", []) if e.get("confirmed")]
    extraction_mode = route.get("extraction_mode") or mapping.get("extraction_mode") or "position_level"
    if extraction_mode in {"scanned_castanea_full", "scanned_castanea"}:
        # Legacy aliases from early Castanea experiments → unified mode.
        extraction_mode = "scanned_financial_statements"
        route = dict(route)
        route["extraction_mode"] = extraction_mode
    pdf_grain = (
        route.get("comparison_grain")
        or (mapping.get("comparability") or {}).get("grain", {}).get("pdf_comparison_grain")
        or "company"
    )
    statement_mode = extraction_mode == "scanned_financial_statements" or pdf_grain == "statement_and_portfolio"

    confirmed_entities = [
        e
        for e in all_confirmed
        if e.get("entity_grain", "company") in {"company", "security", None}
        or (statement_mode and e.get("pdf_company_name"))
    ]
    confirmed_entity_mappings_count = len(all_confirmed) if statement_mode else len(confirmed_entities)

    pdf_metrics = []
    if not statement_mode:
        pdf_metrics = build_pdf_comparable_metrics(
            company_summary=company_summary,
            realized_lots=realized_lots,
            entity_mappings=confirmed_entities,
        )

    vendor_dates_for_fund: list[str] = []
    vendor_df = pd.DataFrame()
    fund_id = mapping["comparability"]["fund_identity"].get("vendor_fund_id")
    if fund_id:
        vendor_df = _load_vendor_fund_slice(vendor_csv, mapping)
        vendor_dates_for_fund = sorted({d for d in vendor_df["_as_of_iso"].dropna().unique()})

    date_cfg = mapping["comparability"]["as_of_date"]
    meta_as_of = (metadata.get("as_of_date") or {}).get("normalized")
    if not date_cfg.get("pdf_normalized_expected") and meta_as_of:
        mapping = dict(mapping)
        mapping["comparability"] = dict(mapping["comparability"])
        mapping["comparability"]["as_of_date"] = dict(date_cfg)
        mapping["comparability"]["as_of_date"]["pdf_normalized_expected"] = meta_as_of

    gates = evaluate_gates(
        metadata=metadata,
        mapping=mapping,
        vendor_columns=vendor_columns,
        vendor_dates_for_fund=vendor_dates_for_fund,
        confirmed_entity_mappings=confirmed_entity_mappings_count,
        extraction_quality=extraction_quality,
        route=route,
        fund_aggregate=fund_aggregate,
    )
    hard_fail = any(g["status"] == "FAIL" and g.get("blocks_amount_comparison") for g in gates)
    if extraction_mode == "fund_aggregate_only" and not hard_fail:
        comparability_status = "aggregate_only_comparable"
    elif hard_fail:
        comparability_status = "not_comparable"
    else:
        comparability_status = "comparable"

    field_dictionary = build_field_dictionary(mapping)
    grain_cfg = mapping["comparability"]["grain"]
    entity_candidates: list[dict[str, Any]] = []
    amount_comparisons: list[dict[str, Any]] = []
    alarms: list[dict[str, Any]] = []
    matrix_summary: dict[str, Any] = {}

    if fund_id and not vendor_df.empty:
        pdf_date = mapping["comparability"]["as_of_date"].get("pdf_normalized_expected")
        slice_df = vendor_df[vendor_df["_as_of_iso"] == pdf_date].copy()
        exclude_re = grain_cfg.get("exclude_vendor_source_asset_regex")
        if exclude_re and not statement_mode:
            slice_df = slice_df[
                ~slice_df[grain_cfg["vendor_entity_field"]].astype(str).str.contains(exclude_re, na=False)
            ]
        vendor_names = slice_df[grain_cfg["vendor_entity_field"]].dropna().astype(str).tolist()
        pdf_names = [c["company_name"] for c in company_summary if c.get("company_name")]
        realized_names = sorted({lot["company_name"] for lot in realized_lots if lot.get("company_name")})
        if grain_cfg.get("fuzzy_candidate_generation"):
            entity_candidates = _fuzzy_candidates(pdf_names + realized_names, vendor_names)
        for entity in all_confirmed:
            entity_candidates.append(
                {
                    "pdf_company_name": entity.get("pdf_company_name"),
                    "pdf_line_id": entity.get("pdf_line_id"),
                    "pdf_realized_company_name": entity.get("pdf_realized_company_name"),
                    "vendor_source_asset_candidate": entity["vendor_source_asset"],
                    "similarity": 1.0,
                    "status": "confirmed_explicit",
                    "confirmed": True,
                    "entity_grain": entity.get("entity_grain")
                    or ("statement_line" if entity.get("pdf_line_id") else "company"),
                    "note": entity.get("notes") or "Confirmed in versioned mapping config; not fuzzy auto-match.",
                }
            )

    if comparability_status == "comparable" and statement_mode:
        amount_comparisons, alarms, matrix_summary = run_statement_portfolio_matrix(
            statement_entities=statement_entities,
            company_summary=company_summary,
            vendor_df=vendor_df,
            mapping=mapping,
        )
    elif comparability_status == "comparable":
        amount_comparisons = run_amount_comparisons(
            pdf_metrics=pdf_metrics,
            vendor_df=vendor_df,
            mapping=mapping,
        )
    elif comparability_status == "aggregate_only_comparable":
        amount_comparisons = []
        if fund_aggregate and fund_aggregate.get("parse_status") == "ok":
            amount_comparisons.append(
                {
                    "logical_field": "fund_total_cost",
                    "pdf_value": fund_aggregate.get("fund_total_cost"),
                    "csv_value": None,
                    "status": "informational",
                    "mapping_type": "exact",
                    "notes": "Fund aggregate Cost from Statement; company-level compare blocked by schedule_grain.",
                    "entity_mapping_status": "fund_aggregate_only",
                }
            )
            amount_comparisons.append(
                {
                    "logical_field": "fund_total_fair_value",
                    "pdf_value": fund_aggregate.get("fund_total_fair_value"),
                    "csv_value": None,
                    "status": "informational",
                    "mapping_type": "exact",
                    "notes": "Fund aggregate Fair Value from Statement; company-level compare blocked by schedule_grain.",
                    "entity_mapping_status": "fund_aggregate_only",
                }
            )
    else:
        amount_comparisons = [
            {
                "status": "not_executed",
                "reason": "Comparability gates failed; amount comparison intentionally not executed.",
                "blocking_gates": [g["gate"] for g in gates if g["status"] != "PASS"],
            }
        ]

    spot_checks = build_spot_checks(
        gates=gates,
        field_dictionary=field_dictionary,
        metadata=metadata,
        company_summary=company_summary,
        amount_comparisons=amount_comparisons if comparability_status == "comparable" else [],
        comparability_status=comparability_status,
    )

    match_count = sum(1 for a in amount_comparisons if a.get("status") == "match")
    mismatch_count = sum(1 for a in amount_comparisons if a.get("status") == "mismatch")
    overall_status = None
    if statement_mode and comparability_status == "comparable":
        blocking = [a for a in alarms if a.get("severity") in {"high", "critical"}]
        if blocking or mismatch_count:
            overall_status = "REVIEW_REQUIRED"
        elif matrix_summary.get("populated_vendor_fields", 0) > 0 and matrix_summary.get(
            "matched_vendor_fields"
        ) == matrix_summary.get("populated_vendor_fields"):
            overall_status = "PASS"
        else:
            overall_status = "REVIEW_REQUIRED"

    started = datetime.now(timezone.utc)
    reason_codes = []
    if not fund_id:
        reason_codes.append("missing_explicit_vendor_fund_id")
    for gate in gates:
        if gate["status"] != "PASS":
            reason_codes.append(f"gate_failed:{gate['gate']}")
        elif gate.get("waiver_applied"):
            reason_codes.append(f"gate_waiver:{gate['gate']}")

    report = {
        "comparability_status": comparability_status,
        "overall_status": overall_status,
        "summary": {
            "extraction_dir": str(extraction_dir),
            "vendor_csv": str(vendor_csv),
            "mapping_config": str(mapping_config),
            "pdf_fund_name": (metadata.get("fund_name") or {}).get("normalized"),
            "pdf_as_of_date": (metadata.get("as_of_date") or {}).get("normalized"),
            "vendor_fund_id": fund_id,
            "vendor_rows_for_fund": int(len(vendor_df)) if fund_id else 0,
            "vendor_dates_for_fund": vendor_dates_for_fund,
            "confirmed_entity_mappings": confirmed_entity_mappings_count,
            "gates_failed": [g["gate"] for g in gates if g["status"] != "PASS"],
            "gates_passed": [g["gate"] for g in gates if g["status"] == "PASS"],
            "amount_comparison_executed": comparability_status == "comparable",
            "amount_match_count": match_count,
            "amount_mismatch_count": mismatch_count,
            "pdf_company_count": len(company_summary),
            "pdf_statement_entity_count": len(statement_entities),
            "pdf_comparable_metric_count": len(pdf_metrics)
            if not statement_mode
            else matrix_summary.get("pdf_metric_entities", 0),
            "metric_basis": (
                "statement_and_portfolio_full_field_matrix"
                if statement_mode
                else "allocator_holdings_calculations_dictionary"
            ),
            "reason_codes": reason_codes,
            **({k: v for k, v in matrix_summary.items()} if matrix_summary else {}),
        },
        "gates": gates,
        "field_dictionary": field_dictionary,
        "spot_checks": spot_checks,
        "entity_candidates": entity_candidates,
        "entity_mappings_confirmed": all_confirmed,
        "pdf_comparable_metrics": pdf_metrics,
        "amount_comparisons": amount_comparisons,
        "alarms": alarms,
        "comparison_manifest": {
            "created_at_utc": started.isoformat(),
            "mapping_id": mapping.get("mapping_id"),
            "mapping_version": mapping.get("mapping_version"),
            "mapping_sha256": file_sha256(mapping_config),
            "vendor_csv_sha256": file_sha256(vendor_csv),
            "extraction_pdf_sha256": extraction_manifest.get("pdf_sha256"),
            "extraction_config_sha256": extraction_manifest.get("config_sha256"),
            "git_commit": git_commit(repo_root),
            "package_versions": package_versions(),
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "comparison_summary": [
            report["summary"]
            | {"comparability_status": comparability_status, "overall_status": overall_status}
        ],
        "comparability_gates": gates,
        "field_dictionary": field_dictionary,
        "spot_checks": spot_checks,
        "entity_candidates": entity_candidates,
        "pdf_comparable_metrics": pdf_metrics,
        "amount_comparisons": amount_comparisons,
        "alarms": alarms,
        "comparison_manifest": [report["comparison_manifest"]],
    }
    export_paths: dict[str, str] = {}
    for name, rows in artifacts.items():
        path = output_dir / f"{name}.jsonl"
        _write_jsonl(path, rows)
        export_paths[name] = str(path)

    report_name = (
        "not_comparable_report.json" if comparability_status == "not_comparable" else "comparison_report.json"
    )
    report_path = output_dir / report_name
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    export_paths["report_json"] = str(report_path)

    if statement_mode:
        alarm_path = output_dir / "alarm_summary.json"
        alarm_path.write_text(
            json.dumps({"overall_status": overall_status, "alarms": alarms}, indent=2, default=str),
            encoding="utf-8",
        )
        export_paths["alarm_summary"] = str(alarm_path)

    excel_path = output_dir / "vendor_comparison_review.xlsx"
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        _records_to_df(
            [
                {"section": "status", "text": comparability_status},
                {"section": "overall_status", "text": overall_status},
                {
                    "section": "rule",
                    "text": (
                        "Full-field matrix: every populated vendor amount field must exact-match PDF; blank vendor fields are skipped."
                        if statement_mode
                        else "Amount comparison executes only after fund identity, as-of date, currency, unit, field semantics, and grain gates pass."
                    ),
                },
                {
                    "section": "entity_matching",
                    "text": "Fuzzy name matching may only create candidates; confirmed pairs must be explicit in mapping config.",
                },
            ]
        ).to_excel(writer, sheet_name="README", index=False)
        for sheet, rows in artifacts.items():
            _records_to_df(rows).to_excel(writer, sheet_name=sheet[:31], index=False)
    export_paths["excel"] = str(excel_path)

    report["export_paths"] = export_paths
    return report
