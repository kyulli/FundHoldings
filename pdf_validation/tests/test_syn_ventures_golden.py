from __future__ import annotations

from decimal import Decimal

import pytest

from pdf_validation.pipeline import run_extract
from tests.conftest import CONFIG, OUTPUT, PDF, ROOT


@pytest.fixture(scope="module")
def extraction():
    assert PDF.exists(), f"Missing sample PDF: {PDF}"
    assert CONFIG.exists(), f"Missing config: {CONFIG}"
    payload = run_extract(
        pdf_path=PDF,
        config_path=CONFIG,
        output_dir=OUTPUT,
        repo_root=ROOT.parent,
        cli_args={"command": "extract", "pdf": str(PDF), "config": str(CONFIG), "out": str(OUTPUT)},
    )
    return payload


def test_schedule_pages(extraction):
    inv_pages = sorted({lot["page"] for lot in extraction["investment_lots"]})
    realized_pages = sorted({lot["page"] for lot in extraction["realized_lots"]})
    assert inv_pages == [3, 4, 5]
    assert realized_pages == [8]

    grand = next(e for e in extraction["company_events"] if e["event"] == "grand_total")
    assert grand["page"] == 5


def test_miggo_cross_page_continuation(extraction):
    miggo = next(c for c in extraction["company_summary"] if c["company_name"] == "Miggo Security Ltd.")
    assert 3 in miggo["pages"]
    assert miggo["subtotal_page"] == 4
    assert miggo["subtotal_event"] == "page_continuation"
    assert miggo["cost_status"] == "PASS"
    assert miggo["fair_value_status"] == "PASS"
    assert Decimal(miggo["cost_reported_normalized"]) == Decimal("11000001")


def test_investment_golden_totals(extraction):
    golden = {
        "cost": Decimal("177475405"),
        "fair_value": Decimal("207461318"),
        "unrealized_gain_loss": Decimal("29985913"),
    }
    for field, expected in golden.items():
        check = next(r for r in extraction["reconciliation"] if r["check_id"] == f"golden_investments_{field}")
        assert check["status"] == "PASS"
        assert Decimal(check["calculated"]) == expected


def test_realized_golden_totals(extraction):
    golden = {
        "cost": Decimal("21000000"),
        "cash_proceeds": Decimal("18989970"),
        "realized_gain_loss": Decimal("-2010029"),
    }
    for field, expected in golden.items():
        check = next(r for r in extraction["reconciliation"] if r["check_id"] == f"golden_realized_{field}")
        assert check["status"] == "PASS"
        assert Decimal(check["calculated"]) == expected


def test_parser_decisions_pass(extraction):
    assert extraction["parser_decisions"]
    for decision in extraction["parser_decisions"]:
        assert decision["status"] == "PASS"
        assert decision["selected_parser"] == "camelot"


def test_no_unconditional_ffill_artifacts(extraction):
    # Lots with blank company_name_raw must still have inherited company set via active_company.
    inherited = [lot for lot in extraction["investment_lots"] if lot["company_name_inherited"]]
    assert inherited
    assert all(lot["company_name"] for lot in inherited)


def test_reconciliation_core_checks_pass(extraction):
    required = {
        "company_sum_vs_grand_cost",
        "company_sum_vs_grand_fair_value",
        "company_sum_vs_grand_unrealized",
        "schedule_vs_statement_cost",
        "schedule_vs_statement_fair_value",
        "schedule_vs_statement_unrealized",
        "realized_vs_statement_gain",
        "grand_fv_minus_cost",
    }
    by_id = {row["check_id"]: row for row in extraction["reconciliation"]}
    for check_id in required:
        assert by_id[check_id]["status"] == "PASS", by_id[check_id]


def test_validation_issues_empty(extraction):
    assert extraction["validation_issues"] == []
