"""CLI entrypoint for PDF validation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pdf_validation.batch_runner import run_batch
from pdf_validation.castanea_runner import run_scanned_ocr_case
from pdf_validation.document_router import load_registry, route_document
from pdf_validation.mapping_registry import load_vendor_mapping_registry, resolve_mapping_path_for_fund
from pdf_validation.page_content import detect_pdf_text_source
from pdf_validation.pipeline import run_extract
from pdf_validation.vendor_comparison import compare_with_vendor


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _pkg_root() -> Path:
    return _repo_root() / "pdf_validation"


def _resolve_template_config_for_family(family_id: str) -> Path | None:
    registry = load_registry()
    fam = (registry.get("template_families") or {}).get(family_id) or {}
    base = fam.get("base_config")
    if not base:
        return None
    path = Path(base)
    if not path.is_absolute():
        path = _pkg_root() / base
    return path if path.exists() else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pdf_validation")
    sub = parser.add_subparsers(dest="command", required=True)

    detect = sub.add_parser("detect", help="Classify PDF document class / template family")
    detect.add_argument("--pdf", required=True, type=Path)

    scan = sub.add_parser("scan-detect", help="Detect whether a PDF is native/scanned/mixed")
    scan.add_argument("--pdf", required=True, type=Path)

    extract = sub.add_parser("extract", help="Extract and reconcile a PDF using a template config")
    extract.add_argument("--pdf", required=True, type=Path)
    extract.add_argument("--config", required=False, type=Path)
    extract.add_argument("--out", required=True, type=Path)
    extract.add_argument(
        "--template",
        choices=["auto", "config"],
        default="config",
        help="Use --template auto to route + build config automatically",
    )
    extract.add_argument("--fund-id", required=False, type=str, help="When set, build mapping proposal after extract")
    extract.add_argument("--vendor-csv", required=False, type=Path)
    extract.add_argument("--as-of", required=False, type=str)
    extract.add_argument(
        "--open-review",
        action="store_true",
        help="After extract, open review UI if manual_review or entity needs_review (requires --fund-id)",
    )
    extract.add_argument("--review-port", type=int, default=8765)

    compare = sub.add_parser("compare", help="Compare extraction output with vendor CSV")
    compare.add_argument("--extraction-dir", required=True, type=Path)
    compare.add_argument("--vendor-csv", required=True, type=Path)
    compare.add_argument("--mapping-config", required=True, type=Path)
    compare.add_argument("--out", required=False, type=Path)

    batch = sub.add_parser("batch", help="Route/extract/compare all PDFs under an input directory")
    batch.add_argument("--input", required=True, type=Path)
    batch.add_argument("--vendor-csv", required=False, type=Path)
    batch.add_argument("--out", required=True, type=Path)

    doc_survey = sub.add_parser(
        "document-survey",
        help="Process every PDF independently (document_id output; never collapse report periods)",
    )
    doc_survey.add_argument("--input", required=True, type=Path)
    doc_survey.add_argument("--out", required=True, type=Path)
    doc_survey.add_argument(
        "--funds",
        required=False,
        type=str,
        help="Optional comma-separated fund folder names to include",
    )
    doc_survey.add_argument(
        "--no-compare",
        action="store_true",
        help="Route/extract only; skip vendor compare",
    )
    doc_survey.add_argument(
        "--vendor-csv",
        required=False,
        type=Path,
        help="Vendor holdings CSV (needed with --open-review)",
    )
    doc_survey.add_argument(
        "--open-review",
        action="store_true",
        help="After survey, open review UI for the first PDF that needs human review",
    )
    doc_survey.add_argument("--review-port", type=int, default=8765)

    scanned = sub.add_parser(
        "scanned-ocr",
        help="Run config-driven scanned FS OCR + full vendor amount-field matrix",
    )
    scanned.add_argument("--pdf", required=True, type=Path)
    scanned.add_argument("--role", required=True, choices=["train", "test", "validate"])
    scanned.add_argument("--out", required=True, type=Path)
    scanned.add_argument("--fund-id", required=False, type=str, help="Fund Allocator ID; resolves mapping/template from registry")
    scanned.add_argument("--config", required=False, type=Path)
    scanned.add_argument("--mapping-config", required=False, type=Path)
    scanned.add_argument("--vendor-csv", required=False, type=Path)
    scanned.add_argument("--as-of", required=False, type=str, help="Expected ISO as-of date, e.g. 2025-12-31")

    # Backwards-compatible alias; prefer scanned-ocr + --fund-id.
    castanea = sub.add_parser(
        "castanea-ocr",
        help="Alias of scanned-ocr (defaults fund-id=A103ce5 via vendor_mapping_registry.json)",
    )
    castanea.add_argument("--pdf", required=True, type=Path)
    castanea.add_argument("--role", required=True, choices=["train", "test", "validate"])
    castanea.add_argument("--out", required=True, type=Path)
    castanea.add_argument("--fund-id", required=False, type=str, default="A103ce5")
    castanea.add_argument("--config", required=False, type=Path)
    castanea.add_argument("--mapping-config", required=False, type=Path)
    castanea.add_argument("--vendor-csv", required=False, type=Path)
    castanea.add_argument("--as-of", required=False, type=str, help="Expected ISO as-of date, e.g. 2025-12-31")

    review = sub.add_parser(
        "review-mapping",
        help="Open localhost browser UI to review entity mappings and run draft compare",
    )
    review.add_argument("--extraction-dir", required=True, type=Path)
    review.add_argument("--vendor-csv", required=False, type=Path)
    review.add_argument("--fund-id", required=True, type=str)
    review.add_argument("--as-of", required=False, type=str)
    review.add_argument("--port", type=int, default=8765)
    review.add_argument("--no-browser", action="store_true")

    draft = sub.add_parser(
        "draft-compare",
        help="Build mapping proposal + draft mapping and run compare (no browser)",
    )
    draft.add_argument("--extraction-dir", required=True, type=Path)
    draft.add_argument("--vendor-csv", required=False, type=Path)
    draft.add_argument("--fund-id", required=True, type=str)
    draft.add_argument("--as-of", required=False, type=str)
    draft.add_argument("--rebuild-proposal", action="store_true")
    draft.add_argument("--llm", action="store_true", help="Enable optional LLM entity ranking")
    draft.add_argument(
        "--open-review",
        action="store_true",
        default=None,
        help="Force open localhost review UI after draft compare",
    )
    draft.add_argument(
        "--no-open-review",
        action="store_true",
        help="Never auto-open review UI even when needs_review remains",
    )
    draft.add_argument("--review-port", type=int, default=8765)

    promote = sub.add_parser(
        "promote-mapping",
        help="Explicitly promote draft mapping into approved_mappings + registry",
    )
    promote.add_argument("--extraction-dir", required=True, type=Path)
    promote.add_argument("--fund-id", required=True, type=str)
    promote.add_argument("--reviewer", required=True, type=str)
    promote.add_argument("--confirm", action="store_true", help="Required to write files")
    promote.add_argument("--preview-only", action="store_true")

    llm_usage = sub.add_parser(
        "llm-usage",
        help="Register current user / show LLM spend, or run a one-shot router smoke test",
    )
    llm_usage.add_argument("--register", action="store_true", help="Register current OS user (idempotent)")
    llm_usage.add_argument("--user", type=str, default=None, help="Override identifier (else PDF_VALIDATION_USER / getpass)")
    llm_usage.add_argument("--summary", action="store_true", help="Print usage summary")
    llm_usage.add_argument("--test", action="store_true", help="Run one recorded echo (or --live) call")
    llm_usage.add_argument("--live", action="store_true", help="With --test, call live OpenAI/Anthropic backend")
    llm_usage.add_argument(
        "--open-review-demo",
        action="store_true",
        help="After test, draft-compare a survey fund and auto-open review if needs approval",
    )
    llm_usage.add_argument("--extraction-dir", type=Path, default=None)
    llm_usage.add_argument("--fund-id", type=str, default="A9ffbf3")
    llm_usage.add_argument("--as-of", type=str, default="2025-12-31")
    llm_usage.add_argument("--vendor-csv", type=Path, default=None)
    llm_usage.add_argument("--review-port", type=int, default=8765)

    return parser


def _run_scanned_ocr_command(args: argparse.Namespace, repo_root: Path) -> int:
    pkg = _pkg_root()
    fund_id = getattr(args, "fund_id", None)
    mapping_registry = load_vendor_mapping_registry()
    entry = (mapping_registry.get("by_fund_id") or {}).get(fund_id or "") or {}

    config = args.config
    if config is None and entry.get("template_family"):
        config = _resolve_template_config_for_family(entry["template_family"])
    if config is None:
        raise SystemExit("--config is required when fund-id has no template_family in vendor_mapping_registry.json")

    mapping = args.mapping_config
    if mapping is None and fund_id:
        mapping = resolve_mapping_path_for_fund(fund_id, registry=mapping_registry, pkg_root=pkg)
    if mapping is None:
        raise SystemExit("--mapping-config is required when fund-id has no mapping in vendor_mapping_registry.json")

    vendor = args.vendor_csv or (repo_root / "holdings_anonymized.csv")
    as_of = args.as_of
    if as_of is None:
        if args.role == "train":
            as_of = "2025-12-31"
        elif args.role == "test":
            as_of = "2026-03-31"
        else:
            as_of = None
    result = run_scanned_ocr_case(
        pdf_path=args.pdf.resolve(),
        config_path=Path(config).resolve(),
        mapping_path=Path(mapping).resolve(),
        vendor_csv=Path(vendor).resolve(),
        output_dir=args.out.resolve(),
        role=args.role,
        as_of_expected=as_of,
    )
    print(json.dumps(result, indent=2, default=str))
    if result.get("alarms"):
        print("\nALARM")
        for alarm in result["alarms"]:
            print(json.dumps(alarm, ensure_ascii=False))
    return 0 if result.get("overall_status") == "PASS" else 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = _repo_root()

    if args.command == "detect":
        route = route_document(args.pdf.resolve(), load_registry())
        print(json.dumps(route, indent=2))
        return 0

    if args.command == "scan-detect":
        result = detect_pdf_text_source(args.pdf.resolve())
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "extract":
        auto = args.template == "auto" or args.config is None
        payload = run_extract(
            pdf_path=args.pdf.resolve(),
            config_path=None if auto else args.config.resolve(),
            output_dir=args.out.resolve(),
            repo_root=repo_root,
            cli_args={
                "command": "extract",
                "pdf": str(args.pdf),
                "config": str(args.config) if args.config else None,
                "out": str(args.out),
                "template": args.template,
            },
            auto_template=auto,
        )
        summary = {
            "route": payload.get("route"),
            "selected_parser": payload.get("selected_parser"),
            "extraction_quality": payload.get("extraction_quality"),
            "investment_lots": len(payload.get("investment_lots") or []),
            "company_summary": len(payload.get("company_summary") or []),
            "realized_lots": len(payload.get("realized_lots") or []),
            "reconciliation_pass": sum(1 for r in payload.get("reconciliation") or [] if r.get("status") == "PASS"),
            "reconciliation_fail": sum(1 for r in payload.get("reconciliation") or [] if r.get("status") == "FAIL"),
            "validation_issues": len(payload.get("validation_issues") or []),
            "fund_aggregate": payload.get("fund_aggregate"),
            "export_paths": payload.get("export_paths"),
        }
        # Wire extract → mapping proposal (+ optional review UI)
        if args.fund_id:
            from pdf_validation.mapping_onboarding import (
                build_proposal_for_extraction,
                maybe_open_mapping_review,
                review_open_reason,
            )

            vendor = args.vendor_csv or (repo_root / "holdings_anonymized.csv")
            as_of = args.as_of or (payload.get("route") or {}).get("as_of_date")
            proposal = build_proposal_for_extraction(
                extraction_dir=args.out.resolve(),
                vendor_csv=Path(vendor).resolve(),
                fund_id=args.fund_id,
                as_of_date=as_of,
                llm_enabled=str(os.environ.get("PDF_VALIDATION_LLM", "")).strip().lower() in {"1", "true", "yes", "on"},
            )
            decision = review_open_reason(
                extraction_dir=args.out.resolve(),
                vendor_csv=Path(vendor).resolve(),
                fund_id=args.fund_id,
                as_of_date=as_of,
            )
            needs = decision.get("needs_review_unresolved") or []
            summary["mapping_proposal"] = {
                "proposal_id": proposal.get("proposal_id"),
                "summary": proposal.get("summary"),
                "needs_review_unresolved": needs,
                "extraction_gate": decision.get("extraction_gate"),
                "open_reason": decision.get("reason"),
                "proposal_path": str(args.out.resolve() / "mapping_review" / "mapping_proposal.json"),
            }
            print(json.dumps(summary, indent=2, default=str))
            if args.open_review and decision.get("should_open"):
                maybe_open_mapping_review(
                    extraction_dir=args.out.resolve(),
                    vendor_csv=Path(vendor).resolve(),
                    fund_id=args.fund_id,
                    as_of_date=as_of,
                    repo_root=repo_root,
                    pkg_root=_pkg_root(),
                    port=args.review_port,
                    open_browser=True,
                    block=True,
                )
            elif args.open_review:
                print(f"No human review needed ({decision.get('reason')}); skipping review UI.")
        else:
            print(json.dumps(summary, indent=2, default=str))
        mode = (payload.get("route") or {}).get("extraction_mode")
        if mode in {"blocked_narrative", "fund_aggregate_only"}:
            return 0
        return 0 if not payload.get("validation_issues") else 1

    if args.command == "compare":
        out_dir = args.out.resolve() if args.out else (args.extraction_dir.resolve() / "vendor_comparison")
        result = compare_with_vendor(
            extraction_dir=args.extraction_dir.resolve(),
            vendor_csv=args.vendor_csv.resolve(),
            mapping_config=args.mapping_config.resolve(),
            output_dir=out_dir,
            repo_root=repo_root,
        )
        summary = {
            "comparability_status": result["comparability_status"],
            "gates_failed": result["summary"]["gates_failed"],
            "gates_passed": result["summary"]["gates_passed"],
            "amount_comparison_executed": result["summary"]["amount_comparison_executed"],
            "reason_codes": result["summary"]["reason_codes"],
            "export_paths": result["export_paths"],
        }
        print(json.dumps(summary, indent=2))
        status = result["comparability_status"]
        return 0 if status in {"comparable", "aggregate_only_comparable"} else 2

    if args.command == "batch":
        report = run_batch(sample_root=args.input.resolve(), out_root=args.out.resolve())
        n = len(report.get("results") or [])
        print(json.dumps({"results": n, "out": str(args.out)}, indent=2))
        return 0

    if args.command == "document-survey":
        from pdf_validation.document_survey import open_first_pending_review, run_document_survey

        funds = [x.strip() for x in (args.funds or "").split(",") if x.strip()] or None
        report = run_document_survey(
            args.input.resolve(),
            out_root=args.out.resolve(),
            fund_ids=funds,
            run_compare=not args.no_compare,
        )
        review_info = None
        if args.open_review:
            vendor = args.vendor_csv or (repo_root / "holdings_anonymized.csv")
            review_info = open_first_pending_review(
                report,
                vendor_csv=Path(vendor).resolve(),
                port=args.review_port,
                open_browser=True,
                block=False,
            )
        print(
            json.dumps(
                {
                    "input_pdf_count": report.get("input_pdf_count"),
                    "result_count": report.get("result_count"),
                    "out": str(args.out),
                    "review": review_info,
                },
                indent=2,
                default=str,
            )
        )
        return 0 if report.get("input_pdf_count") == report.get("result_count") else 2

    if args.command in {"scanned-ocr", "castanea-ocr"}:
        return _run_scanned_ocr_command(args, repo_root)

    if args.command == "review-mapping":
        from pdf_validation.mapping_review_server import serve_mapping_review

        vendor = args.vendor_csv or (repo_root / "holdings_anonymized.csv")
        server = serve_mapping_review(
            extraction_dir=args.extraction_dir.resolve(),
            vendor_csv=Path(vendor).resolve(),
            fund_id=args.fund_id,
            as_of_date=args.as_of,
            repo_root=repo_root,
            pkg_root=_pkg_root(),
            port=args.port,
            open_browser=not args.no_browser,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            server.server_close()
        return 0

    if args.command == "draft-compare":
        from pdf_validation.mapping_onboarding import maybe_open_mapping_review, run_draft_compare

        vendor = args.vendor_csv or (repo_root / "holdings_anonymized.csv")
        if args.llm:
            os.environ.setdefault("PDF_VALIDATION_LLM", "1")
        summary = run_draft_compare(
            extraction_dir=args.extraction_dir.resolve(),
            vendor_csv=Path(vendor).resolve(),
            fund_id=args.fund_id,
            as_of_date=args.as_of,
            repo_root=repo_root,
            pkg_root=_pkg_root(),
            rebuild_proposal=args.rebuild_proposal,
            llm_enabled=args.llm,
        )
        print(json.dumps(summary, indent=2, default=str))
        should_open = False
        if args.no_open_review:
            should_open = False
        elif args.open_review:
            should_open = True
        else:
            should_open = bool(summary.get("needs_human_review"))
        if should_open:
            maybe_open_mapping_review(
                extraction_dir=args.extraction_dir.resolve(),
                vendor_csv=Path(vendor).resolve(),
                fund_id=args.fund_id,
                as_of_date=args.as_of,
                repo_root=repo_root,
                pkg_root=_pkg_root(),
                port=args.review_port,
                open_browser=True,
                block=True,
                force=bool(args.open_review),
            )
        return 0 if summary.get("comparability_status") == "comparable" else 2

    if args.command == "llm-usage":
        from pdf_validation.llm.usage_router import smoke_test_router
        from pdf_validation.llm.usage_store import current_user_identifier, register_user, usage_summary

        if args.user:
            os.environ["PDF_VALIDATION_USER"] = args.user
        out: dict = {"current_user": current_user_identifier()}
        if args.register or not (args.summary or args.test or args.open_review_demo):
            out["register"] = register_user(args.user)
        if args.test or args.live:
            out["smoke"] = smoke_test_router(live=bool(args.live))
        if args.summary or not (args.test or args.open_review_demo):
            out["summary"] = usage_summary(user=args.user)
        print(json.dumps(out, indent=2, default=str))
        if args.open_review_demo:
            from pdf_validation.mapping_onboarding import maybe_open_mapping_review, run_draft_compare
            from pdf_validation.mapping_review_server import serve_mapping_review

            extr = args.extraction_dir or (
                _pkg_root() / "outputs" / "five_fund_survey" / "A9ffbf3_(mgr_Ad7e703)"
            )
            vendor = args.vendor_csv or (repo_root / "holdings_anonymized.csv")
            review_state = Path(extr) / "mapping_review" / "review_state.json"
            if review_state.exists():
                review_state.unlink()
            summary = run_draft_compare(
                extraction_dir=Path(extr).resolve(),
                vendor_csv=Path(vendor).resolve(),
                fund_id=args.fund_id,
                as_of_date=args.as_of,
                repo_root=repo_root,
                pkg_root=_pkg_root(),
                rebuild_proposal=True,
                llm_enabled=False,
            )
            print(json.dumps({"draft_compare": summary}, indent=2, default=str))
            if summary.get("needs_human_review"):
                maybe_open_mapping_review(
                    extraction_dir=Path(extr).resolve(),
                    vendor_csv=Path(vendor).resolve(),
                    fund_id=args.fund_id,
                    as_of_date=args.as_of,
                    repo_root=repo_root,
                    pkg_root=_pkg_root(),
                    port=args.review_port,
                    open_browser=True,
                    block=True,
                )
            else:
                print("No unresolved needs_review; opening review UI for inspection anyway.")
                server = serve_mapping_review(
                    extraction_dir=Path(extr).resolve(),
                    vendor_csv=Path(vendor).resolve(),
                    fund_id=args.fund_id,
                    as_of_date=args.as_of,
                    repo_root=repo_root,
                    pkg_root=_pkg_root(),
                    port=args.review_port,
                    open_browser=True,
                )
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    print("\nStopped.")
                finally:
                    server.server_close()
        return 0 if out.get("smoke", {}).get("ok", True) else 1

    if args.command == "promote-mapping":
        from pdf_validation.mapping_onboarding import preview_promote_diff, promote_draft_mapping

        if args.preview_only or not args.confirm:
            preview = preview_promote_diff(
                extraction_dir=args.extraction_dir.resolve(),
                fund_id=args.fund_id,
                pkg_root=_pkg_root(),
            )
            print(json.dumps(preview, indent=2, default=str))
            if not args.confirm:
                print("\nRe-run with --confirm to write approved mapping + registry.")
            return 0
        result = promote_draft_mapping(
            extraction_dir=args.extraction_dir.resolve(),
            fund_id=args.fund_id,
            reviewer=args.reviewer,
            pkg_root=_pkg_root(),
            confirm=True,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2
