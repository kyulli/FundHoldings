"""CLI entrypoint for PDF validation."""

from __future__ import annotations

import argparse
import json
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

    compare = sub.add_parser("compare", help="Compare extraction output with vendor CSV")
    compare.add_argument("--extraction-dir", required=True, type=Path)
    compare.add_argument("--vendor-csv", required=True, type=Path)
    compare.add_argument("--mapping-config", required=True, type=Path)
    compare.add_argument("--out", required=False, type=Path)

    batch = sub.add_parser("batch", help="Route/extract/compare all PDFs under an input directory")
    batch.add_argument("--input", required=True, type=Path)
    batch.add_argument("--vendor-csv", required=False, type=Path)
    batch.add_argument("--out", required=True, type=Path)

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

    if args.command in {"scanned-ocr", "castanea-ocr"}:
        return _run_scanned_ocr_command(args, repo_root)

    parser.error(f"Unknown command: {args.command}")
    return 2
