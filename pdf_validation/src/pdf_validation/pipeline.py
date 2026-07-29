"""Extraction pipeline orchestration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pdf_validation.camelot_extractor import extract_camelot_tables
from pdf_validation.document_router import load_registry, route_document
from pdf_validation.export import export_results
from pdf_validation.manifest import build_run_manifest
from pdf_validation.metadata import extract_metadata
from pdf_validation.parser_decisions import decide_parsers
from pdf_validation.parser_selector import select_extraction_path
from pdf_validation.pdfplumber_fallback import extract_pdfplumber_tables
from pdf_validation.reconciliation import run_reconciliation
from pdf_validation.statement_parser import parse_fund_aggregate
from pdf_validation.template_builder import build_config_from_route, write_generated_config
from pdf_validation.text_fallback import parse_company_subtotals_from_text


def load_config(config_path: Path) -> dict[str, Any]:
    with config_path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _flatten_raw_cells(camelot_raw: dict[str, Any], pdfplumber_raw: dict[str, Any]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for schedule in ("investments", "realized", "statement_of_assets"):
        for table in camelot_raw.get(schedule, []):
            cells.extend(table.get("cells") or [])
    for schedule in ("investments", "realized"):
        for table in pdfplumber_raw.get(schedule, []):
            cells.extend(table.get("cells") or [])
    return cells


def _validation_issues(
    parser_decisions: list[dict[str, Any]],
    reconciliation: list[dict[str, Any]],
    company_summary: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for decision in parser_decisions:
        if decision.get("status") != "PASS":
            issues.append(
                {
                    "issue_type": "parser_decision_fail",
                    "schedule": decision.get("schedule"),
                    "detail": decision.get("reason"),
                    "checks": decision.get("checks"),
                }
            )
    for row in reconciliation:
        if row.get("status") != "PASS":
            issues.append(
                {
                    "issue_type": "reconciliation_fail",
                    "check_id": row.get("check_id"),
                    "schedule": row.get("schedule"),
                    "entity": row.get("entity"),
                    "detail": row,
                }
            )
    for company in company_summary:
        for field in ("cost", "fair_value", "unrealized_gain_loss"):
            if company.get(f"{field}_status") not in (None, "PASS"):
                issues.append(
                    {
                        "issue_type": "company_subtotal_mismatch",
                        "company_name": company.get("company_name"),
                        "field": field,
                        "detail": {
                            "calculated": company.get(f"{field}_calculated"),
                            "reported": company.get(f"{field}_reported_normalized"),
                            "difference": company.get(f"{field}_difference"),
                        },
                    }
                )
    return issues


def _blocked_payload(
    *,
    pdf_path: Path,
    output_dir: Path,
    repo_root: Path,
    cli_args: dict[str, Any],
    route: dict[str, Any],
    started: datetime,
) -> dict[str, Any]:
    finished = datetime.now(timezone.utc)
    run_manifest = {
        "pdf_path": str(pdf_path),
        "started_at_utc": started.isoformat(),
        "finished_at_utc": finished.isoformat(),
        "route": route,
        "extraction_mode": route.get("extraction_mode"),
        "status": route.get("extraction_mode"),
        "cli_args": cli_args,
        "repo_root": str(repo_root),
    }
    fund_aggregate = None
    if route.get("extraction_mode") == "fund_aggregate_only":
        fund_aggregate = parse_fund_aggregate(pdf_path)
        if fund_aggregate.get("as_of_date") and not route.get("as_of_date"):
            route["as_of_date"] = fund_aggregate["as_of_date"]
    metadata = {"fields": []}
    if route.get("as_of_date"):
        metadata["fields"].append(
            {
                "field": "as_of_date",
                "raw": route.get("as_of_date"),
                "normalized": route.get("as_of_date"),
                "parse_status": "ok",
                "source_page": 1,
            }
        )
    if fund_aggregate and fund_aggregate.get("fund_name"):
        metadata["fields"].append(
            {
                "field": "fund_name",
                "raw": fund_aggregate["fund_name"],
                "normalized": fund_aggregate["fund_name"],
                "parse_status": "ok",
                "source_page": 1,
            }
        )
    payload = {
        "run_manifest": run_manifest,
        "metadata": metadata,
        "parser_decisions": [
            {
                "schedule": "all",
                "selected_parser": None,
                "status": "BLOCKED",
                "reason": route.get("extraction_mode"),
                "route": route,
            }
        ],
        "raw_cells": [],
        "investment_lots": [],
        "company_summary": [],
        "realized_lots": [],
        "reconciliation": [],
        "validation_issues": [],
        "classified_rows": [],
        "camelot_raw": {},
        "pdfplumber_raw": {},
        "company_events": [],
        "realized_totals": [],
        "route": route,
        "fund_aggregate": fund_aggregate,
        "extraction_quality": {
            "status": "PASS" if route.get("extraction_mode") == "fund_aggregate_only" and fund_aggregate and fund_aggregate.get("parse_status") == "ok" else "BLOCKED",
            "selected_parser": "statement_parser" if fund_aggregate else None,
            "reason": route.get("reasons"),
        },
    }
    export_paths = export_results(output_dir, payload)
    # Also persist route + aggregate explicitly
    (output_dir / "route.json").write_text(json.dumps(route, indent=2), encoding="utf-8")
    if fund_aggregate:
        (output_dir / "fund_aggregate.json").write_text(json.dumps(fund_aggregate, indent=2), encoding="utf-8")
    payload["export_paths"] = {key: str(path) for key, path in export_paths.items()}
    return payload


def run_extract(
    *,
    pdf_path: Path,
    config_path: Path | None = None,
    output_dir: Path,
    repo_root: Path,
    cli_args: dict[str, Any],
    auto_template: bool = False,
) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    route = None
    if auto_template or config_path is None:
        registry = load_registry()
        route = route_document(pdf_path, registry)
        (output_dir / "route.json").write_text(json.dumps(route, indent=2), encoding="utf-8")
        mode = route.get("extraction_mode")
        if mode in {"blocked_narrative", "manual_review", "fund_aggregate_only"}:
            return _blocked_payload(
                pdf_path=pdf_path,
                output_dir=output_dir,
                repo_root=repo_root,
                cli_args=cli_args,
                route=route,
                started=started,
            )
        config = build_config_from_route(pdf_path, route, registry=registry)
        generated = output_dir / "generated_config.json"
        write_generated_config(config, generated)
        config_path = generated
    else:
        config = load_config(Path(config_path))
        route = config.get("route")

    metadata = extract_metadata(str(pdf_path), config)
    # Prefer route/layout as_of when metadata pattern misses.
    if route and route.get("as_of_date"):
        for field in metadata.get("fields", []):
            if field.get("field") == "as_of_date" and not field.get("normalized"):
                field["normalized"] = route["as_of_date"]
                field["parse_status"] = "ok"

    camelot_raw = extract_camelot_tables(str(pdf_path), config)
    pdfplumber_raw = extract_pdfplumber_tables(str(pdf_path), config)

    selected = select_extraction_path(
        pdf_path=str(pdf_path),
        config=config,
        camelot_raw=camelot_raw,
        pdfplumber_raw=pdfplumber_raw,
        run_reconciliation_fn=run_reconciliation,
    )

    investment_classified = selected["investment_classified"]
    realized_classified = selected["realized_classified"]
    company_summary = selected["company_summary"]
    reconciliation = selected["reconciliation"]

    # Prefer text companies for families that opt in (Imaginary / shattered / condensed / audited).
    family = config.get("template_family")
    if config.get("prefer_text_fallback") or family in {"condensed_hedge_schedule", "audited_portfolio_schedule"}:
        inv_pages = config.get("pages", {}).get("schedule_of_investments") or []
        if family == "condensed_hedge_schedule":
            from pdf_validation.text_fallback import parse_condensed_positions_from_text

            text_companies = parse_condensed_positions_from_text(pdf_path, inv_pages)
        elif family == "audited_portfolio_schedule":
            from pdf_validation.text_fallback import parse_audited_portfolio_from_text

            text_companies = parse_audited_portfolio_from_text(pdf_path, inv_pages)
        else:
            text_companies = parse_company_subtotals_from_text(pdf_path, inv_pages)
        if text_companies:
            company_summary = text_companies
            selected["selected_parser"] = "text_fallback"
            selected["extraction_quality"] = {
                "status": "PASS",
                "selected_parser": "text_fallback",
                "reason": "Family prefers text fallback for reliable company Cost/FV.",
            }

    # Annotate grain defaults.
    for row in company_summary:
        row.setdefault("entity_grain", "company")

    parser_decisions = decide_parsers(
        camelot_raw=camelot_raw,
        pdfplumber_raw=pdfplumber_raw,
        investment_classified=investment_classified,
        realized_classified=realized_classified,
        company_summary=company_summary,
        reconciliation_rows=reconciliation,
        config=config,
    )
    for decision in parser_decisions:
        decision["selected_parser"] = selected.get("selected_parser") or decision.get("selected_parser")
        decision["extraction_quality"] = selected.get("extraction_quality")
        decision["selector_candidates"] = selected.get("candidates")

    finished = datetime.now(timezone.utc)
    run_manifest = build_run_manifest(
        pdf_path=pdf_path,
        config_path=Path(config_path),
        config=config,
        cli_args=cli_args,
        started_at=started,
        finished_at=finished,
        repo_root=repo_root,
    )
    if isinstance(run_manifest, dict):
        run_manifest["route"] = route
        run_manifest["selected_parser"] = selected.get("selected_parser")
        run_manifest["extraction_quality"] = selected.get("extraction_quality")

    classified_rows = (
        investment_classified["classified_rows"] + realized_classified["classified_rows"]
    )
    validation_issues = _validation_issues(parser_decisions, reconciliation, company_summary)

    fund_aggregate = parse_fund_aggregate(pdf_path)

    payload = {
        "run_manifest": run_manifest,
        "metadata": metadata,
        "parser_decisions": parser_decisions,
        "raw_cells": _flatten_raw_cells(camelot_raw, pdfplumber_raw),
        "investment_lots": investment_classified["investment_lots"],
        "company_summary": company_summary,
        "realized_lots": realized_classified["realized_lots"],
        "reconciliation": reconciliation,
        "validation_issues": validation_issues,
        "classified_rows": classified_rows,
        "camelot_raw": camelot_raw,
        "pdfplumber_raw": pdfplumber_raw,
        "company_events": investment_classified["company_events"],
        "realized_totals": realized_classified["realized_totals"],
        "route": route,
        "fund_aggregate": fund_aggregate,
        "extraction_quality": selected.get("extraction_quality"),
        "selected_parser": selected.get("selected_parser"),
    }

    export_paths = export_results(output_dir, payload)
    (output_dir / "extraction_quality.json").write_text(
        json.dumps(selected.get("extraction_quality") or {}, indent=2),
        encoding="utf-8",
    )
    if fund_aggregate:
        (output_dir / "fund_aggregate.json").write_text(json.dumps(fund_aggregate, indent=2), encoding="utf-8")
    payload["export_paths"] = {key: str(path) for key, path in export_paths.items()}
    return payload
