#!/usr/bin/env python3
"""Command line for the teach-once template workflow.

    # 1. Propose a template from one sample statement (this is the only LLM call)
    python -m template_generator.cli propose --fund A41fefa --pdf path/to/statement.pdf

    # 2. See what it extracted, and whether the numbers hold together
    python -m template_generator.cli review --template A41fefa_2025_Imaginary --pdf path/to/statement.pdf

    # 3. Promote it once you are satisfied
    python -m template_generator.cli approve --template A41fefa_2025_Imaginary --reviewer "Kyulli"

    # 4. Every quarter after that: deterministic, no LLM, no API key
    python -m template_generator.cli run --fund A41fefa --pdf path/to/new_quarter.pdf

    # Housekeeping
    python -m template_generator.cli list
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent                      # pdf_validation/
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from template_generator import approval  # noqa: E402
from template_generator.backends import ManualStepRequired, get_backend  # noqa: E402
from template_generator.propose import propose_template  # noqa: E402
from template_generator.replay import replay  # noqa: E402
from template_generator.schema import GeneratedTemplate  # noqa: E402

CONFIGS_ROOT = PKG_ROOT / "configs"


def _print_check(check: approval.CheckResult) -> None:
    mark = "PASS" if check.passed else "FAIL"
    print(f"\n  checks: {mark}")
    print(f"    companies extracted : {check.company_count}")
    print(f"    line coverage       : {check.coverage:.0%}")
    print(f"    arithmetic checks   : {check.checks_run} run, {check.checks_failed} failed")
    for note in check.notes:
        print(f"    note: {note}")
    for f in check.failures[:5]:
        print(
            f"    MISMATCH {f['company_name']!r}: "
            f"FV {f['fair_value']:,} - Cost {f['cost']:,} = {f['implied_gain']:,}, "
            f"but statement reports {f['reported_gain']:,}"
        )


def cmd_propose(args: argparse.Namespace) -> int:
    workdir = CONFIGS_ROOT / "generated_templates" / "_manual" / args.fund
    kwargs = {"workdir": workdir, "model": args.model}
    if args.backend == "echo":
        kwargs["canned_response"] = Path(args.canned).read_text(encoding="utf-8")

    try:
        backend = get_backend(args.backend, **kwargs)
    except (RuntimeError, KeyError) as exc:
        print(f"Backend error: {exc}")
        return 1

    pages = [int(p) for p in args.pages.split(",")] if args.pages else None

    try:
        template, problems = propose_template(
            fund_id=args.fund, pdf_path=Path(args.pdf), backend=backend, schedule_pages=pages
        )
    except ManualStepRequired as exc:
        print(exc)
        return 0
    except (ValueError, FileNotFoundError) as exc:
        print(f"Proposal failed: {exc}")
        return 1

    if problems:
        print("Proposal did NOT pass structural validation:")
        for p in problems:
            print(f"  - {p}")
        print("\nNot saved. Re-run to try again, or inspect the model response.")
        return 1

    path = approval.save_draft(template, CONFIGS_ROOT)
    print(f"Draft written to {path}")
    print(f"  rules    : {len(template.line_rules)}")
    print(f"  pages    : {template.schedule_pages}")
    print(f"  evidence : {len(template.evidence)} captured line(s)")

    result, check = approval.dry_run(template, Path(args.pdf))
    _print_check(check)
    print(f"\nNext: review it\n  python -m template_generator.cli review "
          f"--template {template.template_id} --pdf {args.pdf}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    try:
        template = approval.load_draft(args.template, CONFIGS_ROOT)
    except FileNotFoundError:
        print(f"No draft named {args.template!r}. Run `list` to see what exists.")
        return 1

    result, check = approval.dry_run(template, Path(args.pdf))

    print(f"Template : {template.template_id}  (fund {template.fund_id})")
    print(f"Generated: {template.generated_by.get('backend')} at "
          f"{template.generated_by.get('generated_at_utc')}")

    print("\nRules:")
    for rule in template.line_rules:
        detail = rule.pattern or f"prefixes={rule.prefixes}"
        print(f"  [{rule.kind:16}] {rule.rule_id}")
        print(f"       {detail}")
        if rule.notes:
            print(f"       note: {rule.notes}")

    print("\nEvidence — what each rule read off a real line:")
    for ev in template.evidence[: args.max_evidence]:
        print(f"  (p{ev.page}) {ev.line[:100]}")
        print(f"       -> {ev.captured}")

    print(f"\nExtracted {result.company_count} companies:")
    for row in result.company_summary[: args.max_rows]:
        print(f"  {row['company_name'][:44]:46} "
              f"cost={row['cost_reported_normalized'] or '-':>14} "
              f"fv={row['fair_value_reported_normalized'] or '-':>14}")

    if result.unmatched_lines and args.show_unmatched:
        print(f"\nUnmatched lines ({len(result.unmatched_lines)} total, first 15):")
        for u in result.unmatched_lines[:15]:
            print(f"  (p{u['page']}) {u['line'][:100]}")

    _print_check(check)

    if check.passed:
        print(f"\nIf these numbers match the PDF, approve it:\n"
              f"  python -m template_generator.cli approve --template {template.template_id} "
              f"--reviewer \"Your Name\"")
    else:
        print("\nDo not approve this template as-is. Re-generate, or fix the rules by hand "
              "in the draft JSON and re-review.")
    return 0 if check.passed else 1


def cmd_approve(args: argparse.Namespace) -> int:
    try:
        template = approval.load_draft(args.template, CONFIGS_ROOT)
    except FileNotFoundError:
        print(f"No draft named {args.template!r}.")
        return 1

    check = None
    if args.pdf:
        _, check = approval.dry_run(template, Path(args.pdf))

    try:
        path = approval.approve(
            args.template, CONFIGS_ROOT, reviewer=args.reviewer, check=check, force=args.force
        )
    except RuntimeError as exc:
        print(exc)
        return 1

    print(f"Approved: {path}")
    print(f"Fund {template.fund_id} will now be extracted deterministically from this template.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """The quarterly path. No LLM, no API key, no network."""
    template = approval.approved_template_for_fund(args.fund, CONFIGS_ROOT)
    if template is None:
        print(f"No approved template for fund {args.fund}. Propose and approve one first.")
        return 1

    result = replay(template, Path(args.pdf))
    check = approval.check_arithmetic(result)

    print(f"Fund {args.fund} — template {template.template_id}")
    print(json.dumps(result.summary(), indent=2))
    _print_check(check)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for row in result.company_summary:
                fh.write(json.dumps(row) + "\n")
        print(f"\nWrote {result.company_count} rows to {out}")
    return 0 if check.passed else 2


def cmd_list(args: argparse.Namespace) -> int:
    items = approval.list_templates(CONFIGS_ROOT)
    for status in ("draft", "approved"):
        print(f"{status}:")
        for tid in items[status] or []:
            print(f"  {tid}")
        if not items[status]:
            print("  (none)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("propose", help="Generate a draft template from a sample PDF (uses the LLM)")
    p.add_argument("--fund", required=True, help="Fund Allocator ID, e.g. A41fefa")
    p.add_argument("--pdf", required=True)
    p.add_argument("--backend", default="anthropic", choices=["anthropic", "manual", "echo"])
    p.add_argument("--model", default="claude-sonnet-5")
    p.add_argument("--pages", help="Comma-separated schedule pages, e.g. 6,7,8 (else auto-detect)")
    p.add_argument("--canned", help="Path to a canned response file (--backend echo)")
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("review", help="Replay a draft and show what it extracted")
    p.add_argument("--template", required=True)
    p.add_argument("--pdf", required=True)
    p.add_argument("--max-rows", type=int, default=25)
    p.add_argument("--max-evidence", type=int, default=12)
    p.add_argument("--show-unmatched", action="store_true")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("approve", help="Promote a reviewed draft to approved")
    p.add_argument("--template", required=True)
    p.add_argument("--reviewer", required=True)
    p.add_argument("--pdf", help="Re-run checks against this PDF before approving")
    p.add_argument("--force", action="store_true", help="Approve despite failing checks")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("run", help="Quarterly deterministic extraction (no LLM)")
    p.add_argument("--fund", required=True)
    p.add_argument("--pdf", required=True)
    p.add_argument("--out", help="Write company rows as JSONL here")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("list", help="List draft and approved templates")
    p.set_defaults(func=cmd_list)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
