from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pdf_validation.vendor_comparison import compare_with_vendor
from tests.conftest import ROOT

EXTRACTION = ROOT / "outputs" / "syn_q3_2025_run"
MAPPING = ROOT / "configs" / "syn_ventures_fund_ii_q3_2025_vendor_mapping.json"
VENDOR_CSV = ROOT.parent / "holdings_anonymized.csv"
COMPARE_OUT = ROOT / "outputs" / "syn_q3_2025_vendor_comparison"


def test_syn_a0d2a71_dictionary_aligned_comparison():
    assert EXTRACTION.exists(), "Run extraction before comparison tests"
    assert VENDOR_CSV.exists(), f"Missing vendor CSV: {VENDOR_CSV}"

    report = compare_with_vendor(
        extraction_dir=EXTRACTION,
        vendor_csv=VENDOR_CSV,
        mapping_config=MAPPING,
        output_dir=COMPARE_OUT,
        repo_root=ROOT.parent,
    )

    assert report["comparability_status"] == "comparable"
    assert report["summary"]["vendor_fund_id"] == "A0d2a71"
    assert report["summary"]["amount_comparison_executed"] is True
    assert report["summary"]["amount_mismatch_count"] == 0

    # Oomnitza partial exit must match under dictionary formulas.
    oomnitza = [
        a
        for a in report["amount_comparisons"]
        if a.get("vendor_source_asset") == "Oomnitza, Inc." and a.get("logical_field")
    ]
    by_field = {a["logical_field"]: a for a in oomnitza}
    assert by_field["current_cost"]["status"] == "match"
    assert by_field["unrealized_value"]["status"] == "match"
    assert by_field["realized_cost"]["status"] == "match"
    assert by_field["realized_proceeds"]["status"] == "match"
    assert by_field["capital_invested"]["status"] == "match"
    assert by_field["total_value"]["status"] == "match"
    assert Decimal(by_field["capital_invested"]["pdf_value"]) == Decimal("28825179")
    assert Decimal(by_field["total_value"]["pdf_value"]) == Decimal("28825179")
    assert by_field["deal_status"]["pdf_value"] == "Partially Exited"
    assert by_field["deal_status"]["status"] == "match"

    # Fully exited names are comparable via realized schedule.
    for name in ("Talon Cyber Security", "Paladin Data Insurance Corp."):
        rows = [
            a
            for a in report["amount_comparisons"]
            if a.get("vendor_source_asset") == name and a.get("logical_field") == "deal_status"
        ]
        assert rows and rows[0]["pdf_value"] == "Fully Exited"
        assert rows[0]["status"] == "match"

    assert Path(report["export_paths"]["report_json"]).exists()
    assert any(m["vendor_source_asset"] == "Oomnitza, Inc." for m in report["pdf_comparable_metrics"])
