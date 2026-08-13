#!/usr/bin/env python3
"""Self-contained smoke test. No API key, no network, no real PDFs needed.

Builds a synthetic statement that reproduces the failure mode diagnosed in the
real funds (company names that wrap onto their own line, no ruling lines, loose
money columns), then runs the whole flow against it:

    propose (echo backend) -> validate -> replay -> arithmetic guardrail

It also runs the negative test: a template with the Cost and Fair Value columns
swapped must FAIL the guardrail and must be refused by approve().

Run:
    cd FundHoldings/pdf_validation
    python -m template_generator.smoke_test
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from template_generator.approval import approve, check_arithmetic, dry_run, save_draft  # noqa: E402
from template_generator.backends import EchoBackend  # noqa: E402
from template_generator.page_reader import find_schedule_pages  # noqa: E402
from template_generator.propose import propose_template  # noqa: E402
from template_generator.replay import replay  # noqa: E402

ROWS = [
    ("Northwind Robotics Technologies, Inc.", "Technology", "United States", 9399996, 13466158),
    ("Bluefin Health Systems Incorporated", "Healthcare", "United States", 6300000, 10326260),
    ("Cascade Logistics Holdings, Inc.", "Industrials", "United States", 1000000, 1039902),
    ("Meridian Payments Corporation", "Fintech", "United States", 2099996, 2099996),
    ("Solstice Materials Group, Inc.", "Materials", "Canada", 3462500, 5217524),
]

CANNED = {
    "schedule_pages": [2],
    "fund_name_hints": ["Synthetic Capital Partners III"],
    "currency_expected": "USD",
    "unit_expected": "ones",
    "line_rules": [
        {
            "rule_id": "skip_boilerplate",
            "kind": "skip",
            "prefixes": ["schedule of", "december", "company industry",
                         "total investments", "the accompanying"],
            "notes": "Titles, date banner, column headings, grand total, footnote.",
        },
        {
            "rule_id": "company_header",
            "kind": "company_header",
            "pattern": r"^(?P<company_name>[A-Z][A-Za-z0-9 .,&'()-]+?)\s+"
                       r"(?P<industry>Technology|Healthcare|Industrials|Fintech|Materials)\s+"
                       r"(?P<geography>United States|Canada)$",
            "notes": "Company name wraps onto its own line with industry and geography.",
        },
        {
            "rule_id": "company_subtotal",
            "kind": "company_subtotal",
            "pattern": r"^\$\s*(?P<cost>[\d,]+)\s+\$\s*(?P<fair_value>[\d,]+)\s+"
                       r"\$\s*(?P<unrealized_gain_loss>[\d,]+)$",
            "notes": "Amounts sit on the line beneath the company name.",
        },
    ],
}


def build_pdf(path: Path) -> None:
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        raise SystemExit("This smoke test needs reportlab:\n    pip install reportlab")

    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, 700, "Synthetic Capital Partners III, L.P.")
    c.drawString(72, 680, "Audited Financial Statements")
    c.drawString(72, 660, "December 31, 2025")
    c.showPage()

    y = 720
    c.setFont("Helvetica-Bold", 11)
    c.drawString(72, y, "Schedule of Investments"); y -= 16
    c.setFont("Helvetica", 9)
    c.drawString(72, y, "December 31, 2025"); y -= 22
    c.drawString(72, y, "Company Industry Geography Cost Fair Value Unrealized"); y -= 18
    for name, industry, geo, cost, fv in ROWS:
        c.drawString(72, y, f"{name} {industry} {geo}"); y -= 13
        c.drawString(90, y, f"$ {cost:,} $ {fv:,} $ {fv - cost:,}"); y -= 18
    c.drawString(72, y, "Total investments $ 22,262,492 $ 32,149,840"); y -= 20
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(72, y, "The accompanying notes are an integral part of these financial statements.")
    c.showPage()
    c.save()


def main() -> int:
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        pdf = tmpdir / "synthetic_fund.pdf"
        configs = tmpdir / "configs"

        print("Building synthetic statement...")
        build_pdf(pdf)

        print("\n1. Page detection")
        pages = find_schedule_pages(pdf)
        check("schedule page located", pages == [2], f"got {pages}")

        print("\n2. Proposal + structural validation")
        backend = EchoBackend(CANNED)
        template, problems = propose_template(
            fund_id="TEST001", pdf_path=pdf, backend=backend
        )
        check("proposal passes validation", not problems, "; ".join(problems))
        check("evidence captured", len(template.evidence) > 0, f"{len(template.evidence)} lines")

        print("\n3. Deterministic replay")
        result = replay(template, pdf)
        check("all companies extracted", result.company_count == len(ROWS),
              f"{result.company_count}/{len(ROWS)}")
        check("full line coverage", result.coverage() == 1.0, f"{result.coverage():.0%}")

        names_ok = all(
            any(r["company_name"] == expected[0] for r in result.company_summary)
            for expected in ROWS
        )
        check("company names intact (not shattered)", names_ok)

        amounts_ok = all(
            any(
                r["company_name"] == name
                and r["cost_reported_normalized"] == str(cost)
                and r["fair_value_reported_normalized"] == str(fv)
                for r in result.company_summary
            )
            for name, _, _, cost, fv in ROWS
        )
        check("amounts read correctly", amounts_ok)

        print("\n4. Arithmetic guardrail — clean template")
        good = check_arithmetic(result)
        check("guardrail passes a correct template", good.passed,
              f"{good.checks_run} checks, {good.checks_failed} failed")

        print("\n5. Arithmetic guardrail — NEGATIVE TEST (Cost/FV swapped)")
        bad = template.from_dict(json.loads(json.dumps(template.to_dict())))
        for r in bad.line_rules:
            if r.rule_id == "company_subtotal" and r.pattern:
                r.pattern = (r.pattern
                             .replace("(?P<cost>", "(?P<TMP>")
                             .replace("(?P<fair_value>", "(?P<cost>")
                             .replace("(?P<TMP>", "(?P<fair_value>"))
        bad.template_id = "TEST001_columns_swapped"
        bad.status = "draft"
        _, bad_check = dry_run(bad, pdf)
        check("guardrail rejects a swapped template", not bad_check.passed,
              f"{bad_check.checks_failed}/{bad_check.checks_run} rows caught")

        save_draft(bad, configs)
        refused = False
        try:
            approve("TEST001_columns_swapped", configs, reviewer="smoke", check=bad_check)
        except RuntimeError:
            refused = True
        check("approve() refuses a failed template", refused)

        print("\n6. Approve + quarterly run path")
        save_draft(template, configs)
        approve(template.template_id, configs, reviewer="smoke", check=good)
        rerun = replay(template, pdf)
        check("replay is deterministic",
              [r["cost_reported_normalized"] for r in rerun.company_summary]
              == [r["cost_reported_normalized"] for r in result.company_summary])

    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("All checks passed. The skeleton works on this machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
