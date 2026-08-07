"""Extraction pipeline orchestration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pdf_validation.adapters import resolve_adapter_kind, run_native_adapter, run_ocr_adapter
from pdf_validation.document_router import load_registry, route_document
from pdf_validation.export import export_results
from pdf_validation.manifest import build_run_manifest
from pdf_validation.metadata import extract_metadata
from pdf_validation.parser_decisions import decide_parsers
from pdf_validation.deal_status import infer_deal_statuses, infer_realized_deal_statuses
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
        # OCR informational checks should not become validation blockers.
        if row.get("severity") == "info":
            continue
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
            if company.get(f"{field}_status") not in (None, "PASS", "not_applicable"):
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
        "recommended_adapter": route.get("recommended_adapter"),
        "text_source": route.get("text_source"),
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
        "statement_entities": [],
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
    (output_dir / "route.json").write_text(json.dumps(route, indent=2), encoding="utf-8")
    if fund_aggregate:
        (output_dir / "fund_aggregate.json").write_text(json.dumps(fund_aggregate, indent=2), encoding="utf-8")
    payload["export_paths"] = {key: str(path) for key, path in export_paths.items()}
    return payload


def _finalize_and_export(
    *,
    payload: dict[str, Any],
    output_dir: Path,
    route: dict[str, Any] | None,
) -> dict[str, Any]:
    export_paths = export_results(output_dir, payload)
    (output_dir / "extraction_quality.json").write_text(
        json.dumps(payload.get("extraction_quality") or {}, indent=2),
        encoding="utf-8",
    )
    if payload.get("fund_aggregate"):
        (output_dir / "fund_aggregate.json").write_text(
            json.dumps(payload["fund_aggregate"], indent=2),
            encoding="utf-8",
        )
    if route:
        (output_dir / "route.json").write_text(json.dumps(route, indent=2), encoding="utf-8")
        inferred = route.get("inferred_schema")
        if inferred:
            (output_dir / "schema_inference.json").write_text(json.dumps(inferred, indent=2), encoding="utf-8")
        onboarding = {
            "onboarding_status": route.get("onboarding_status")
            or (
                "inferred_ready"
                if route.get("extraction_mode") == "position_level_inferred"
                else "known_family"
            ),
            "compare_allowed": bool(route.get("compare_allowed", route.get("extraction_mode") in {"position_level", "scanned_financial_statements"})),
            "extraction_mode": route.get("extraction_mode"),
            "template_family": route.get("template_family"),
            "route_confidence": route.get("confidence"),
            "inference_confidence": (inferred or {}).get("inference_confidence") if inferred else None,
            "company_count": len(payload.get("company_summary") or []),
            "statement_entity_count": len(payload.get("statement_entities") or []),
            "selected_parser": payload.get("selected_parser"),
            "extraction_quality": payload.get("extraction_quality"),
            "recommended_adapter": route.get("recommended_adapter"),
            "text_source": route.get("text_source"),
            "review_tasks": [],
        }
        if route.get("extraction_mode") == "position_level_inferred":
            onboarding["review_tasks"] = [
                {
                    "task_id": "confirm_inferred_schema",
                    "severity": "high",
                    "blocking_compare": True,
                    "summary": "Confirm inferred column mapping before vendor amount comparison.",
                    "evidence_refs": ["schema_inference.json", "company_summary"],
                },
                {
                    "task_id": "confirm_entity_mappings",
                    "severity": "high",
                    "blocking_compare": True,
                    "summary": "Approve explicit entity aliases for PDF company names vs vendor assets.",
                },
            ]
            onboarding["compare_allowed"] = False
        (output_dir / "onboarding_summary.json").write_text(json.dumps(onboarding, indent=2), encoding="utf-8")
        payload["onboarding_summary"] = onboarding
    if payload.get("scan_detection"):
        (output_dir / "scan_detection.json").write_text(
            json.dumps(payload["scan_detection"], indent=2),
            encoding="utf-8",
        )
    if payload.get("ocr_artifact"):
        (output_dir / "ocr_artifact.json").write_text(
            json.dumps(payload["ocr_artifact"], indent=2, default=str),
            encoding="utf-8",
        )
    payload["export_paths"] = {key: str(path) for key, path in export_paths.items()}
    return payload


def _run_ocr_extract(
    *,
    pdf_path: Path,
    config: dict[str, Any],
    config_path: Path,
    output_dir: Path,
    repo_root: Path,
    cli_args: dict[str, Any],
    route: dict[str, Any] | None,
    started: datetime,
) -> dict[str, Any]:
    route = dict(route or {})
    if cli_args.get("as_of_expected"):
        config.setdefault("document", {})["as_of_date_expected"] = cli_args["as_of_expected"]
        route["as_of_date"] = cli_args["as_of_expected"]

    canonical = run_ocr_adapter(
        pdf_path=pdf_path,
        config=config,
        route=route,
        render_dir=output_dir / "ocr_pages",
    )
    route = canonical.get("route") or route
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
        run_manifest["selected_parser"] = canonical.get("selected_parser")
        run_manifest["extraction_quality"] = canonical.get("extraction_quality")
        run_manifest["adapter_kind"] = "ocr"
        run_manifest["text_source"] = route.get("text_source")
        run_manifest["recommended_adapter"] = "ocr"

    payload = {
        **canonical,
        "run_manifest": run_manifest,
        "statement_entities": canonical.get("statement_entities") or [],
    }
    return _finalize_and_export(payload=payload, output_dir=output_dir, route=route)


def _run_native_extract(
    *,
    pdf_path: Path,
    config: dict[str, Any],
    config_path: Path,
    output_dir: Path,
    repo_root: Path,
    cli_args: dict[str, Any],
    route: dict[str, Any] | None,
    started: datetime,
) -> dict[str, Any]:
    metadata = extract_metadata(str(pdf_path), config)
    # Prefer route/layout as_of when metadata pattern misses.
    if route and route.get("as_of_date"):
        for field in metadata.get("fields", []):
            if field.get("field") == "as_of_date" and not field.get("normalized"):
                field["normalized"] = route["as_of_date"]
                field["parse_status"] = "ok"

    native = run_native_adapter(pdf_path=pdf_path, config=config)
    camelot_raw = native["camelot_raw"]
    pdfplumber_raw = native["pdfplumber_raw"]
    selected = native["selected"]

    investment_classified = selected["investment_classified"]
    realized_classified = selected["realized_classified"]
    company_summary = selected["company_summary"]
    reconciliation = selected["reconciliation"]

    # Prefer text companies for families that opt in, or inferred schemas.
    family = config.get("template_family")
    inferred = config.get("inferred_schema") or (route or {}).get("inferred_schema") or {}
    if config.get("prefer_text_fallback") or family in {
        "condensed_hedge_schedule",
        "audited_portfolio_schedule",
        "generic_holdings_schedule",
    }:
        inv_pages = config.get("pages", {}).get("schedule_of_investments") or []
        if family == "condensed_hedge_schedule":
            from pdf_validation.text_fallback import parse_condensed_positions_from_text

            text_companies = parse_condensed_positions_from_text(pdf_path, inv_pages)
        elif family == "audited_portfolio_schedule":
            from pdf_validation.text_fallback import parse_audited_portfolio_from_text

            text_companies = parse_audited_portfolio_from_text(pdf_path, inv_pages)
        elif inferred or family == "generic_holdings_schedule":
            from pdf_validation.generic_schedule import parse_inferred_schedule_companies

            text_companies = parse_inferred_schedule_companies(
                pdf_path,
                inv_pages,
                inferred_schema=inferred,
            )
        else:
            text_companies = parse_company_subtotals_from_text(pdf_path, inv_pages)
        if text_companies:
            company_summary = text_companies
            selected["selected_parser"] = "text_fallback"
            selected["extraction_quality"] = {
                "status": "REVIEW_REQUIRED" if inferred or family == "generic_holdings_schedule" else "PASS",
                "selected_parser": "text_fallback",
                "reason": (
                    "Schema-inferred schedule extraction; vendor amount comparison requires approved mapping."
                    if inferred or family == "generic_holdings_schedule"
                    else "Family prefers text fallback for reliable company Cost/FV."
                ),
                "company_count": len(text_companies),
            }

    # Annotate grain defaults.
    for row in company_summary:
        row.setdefault("entity_grain", "company")

    # Infer Deal Status by cross-referencing Schedule of Investments and Schedule of Realized.
    realized_lots_for_ds = realized_classified["realized_lots"]
    fund_id = (config.get("route") or {}).get("fund_id") or (route or {}).get("fund_id")
    company_summary = infer_deal_statuses(
        company_summary,
        realized_lots_for_ds,
        fund_id=fund_id,
    )
    realized_lots_with_ds = infer_realized_deal_statuses(
        realized_lots_for_ds,
        company_summary,
        fund_id=fund_id,
    )
    realized_classified = dict(realized_classified)
    realized_classified["realized_lots"] = realized_lots_with_ds

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
        run_manifest["adapter_kind"] = "native"
        run_manifest["text_source"] = (route or {}).get("text_source")
        run_manifest["recommended_adapter"] = (route or {}).get("recommended_adapter") or "native"

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
        "statement_entities": [],
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
    return _finalize_and_export(payload=payload, output_dir=output_dir, route=route)


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
    registry = load_registry()

    route = None
    if auto_template or config_path is None:
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
        if mode == "scanned_financial_statements" or route.get("recommended_adapter") == "ocr":
            base = route.get("base_config") or route.get("adapter_config_ref")
            if not base:
                raise ValueError("OCR route missing base_config")
            config_path = (repo_root / "pdf_validation" / base).resolve() if not Path(base).is_absolute() else Path(base)
            # Prefer package-relative path.
            pkg_candidate = Path(__file__).resolve().parents[2] / base
            if pkg_candidate.exists():
                config_path = pkg_candidate
            config = load_config(config_path)
            config["template_family"] = route.get("template_family") or config.get("template_family")
            config["requires_ocr"] = True
            generated = output_dir / "generated_config.json"
            generated.write_text(json.dumps(config, indent=2), encoding="utf-8")
            return _run_ocr_extract(
                pdf_path=pdf_path,
                config=config,
                config_path=config_path,
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

    adapter = resolve_adapter_kind(route=route, config=config, registry=registry)
    if adapter == "ocr":
        return _run_ocr_extract(
            pdf_path=pdf_path,
            config=config,
            config_path=Path(config_path),
            output_dir=output_dir,
            repo_root=repo_root,
            cli_args=cli_args,
            route=route,
            started=started,
        )

    return _run_native_extract(
        pdf_path=pdf_path,
        config=config,
        config_path=Path(config_path),
        output_dir=output_dir,
        repo_root=repo_root,
        cli_args=cli_args,
        route=route,
        started=started,
    )
