"""CLI entrypoint for PDF validation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf_validation.batch_runner import run_batch
from pdf_validation.document_router import load_registry, route_document
from pdf_validation.pipeline import run_extract
from pdf_validation.vendor_comparison import compare_with_vendor


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pdf_validation")
    sub = parser.add_subparsers(dest="command", required=True)

    detect = sub.add_parser("detect", help="Classify PDF document class / template family")
    detect.add_argument("--pdf", required=True, type=Path)

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

    compare = sub.add_parser("compare", help="Compare extraction output with vendor CSV")
    compare.add_argument("--extraction-dir", required=True, type=Path)
    compare.add_argument("--vendor-csv", required=True, type=Path)
    compare.add_argument("--mapping-config", required=True, type=Path)
    compare.add_argument("--out", required=False, type=Path)

    batch = sub.add_parser("batch", help="Route/extract/compare all PDFs under an input directory")
    batch.add_argument("--input", required=True, type=Path)
    batch.add_argument("--vendor-csv", required=False, type=Path)
    batch.add_argument("--out", required=True, type=Path)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = _repo_root()

    if args.command == "detect":
        route = route_document(args.pdf.resolve(), load_registry())
        print(json.dumps(route, indent=2))
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

    parser.error(f"Unknown command: {args.command}")
    return 2
