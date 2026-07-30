"""A5a0710 is an unseen-template regression fixture, not a registered family.

These PDFs must be handled by schema inference + generic schedule extraction,
without Peak-XV / Portfolio Summary special-case parsers.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from pdf_validation.document_router import route_document
from pdf_validation.generic_schedule import parse_inferred_schedule_companies
from pdf_validation.pipeline import run_extract
from pdf_validation.schema_inference import infer_schema

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT.parent / "sample_data" / "A5a0710"
FS_PDF = SAMPLE_DIR / "INDSFII_3-31-2025_FS.pdf"
ML_PDF = SAMPLE_DIR / "INDSFII_06-30-25_ML.pdf"
OUT = ROOT / "outputs" / "a5a0710_unseen_test"


@pytest.mark.skipif(not FS_PDF.exists(), reason="A5a0710 sample PDF missing")
def test_a5_fs_routes_via_inference_not_specialized_family():
    route = route_document(FS_PDF)
    assert route["document_class"] == "financial_statements_with_schedule"
    assert route["template_family"] == "generic_holdings_schedule"
    assert route["extraction_mode"] == "position_level_inferred"
    assert route.get("compare_allowed") is False
    assert route["as_of_date"] == "2025-03-31"
    # Must not collapse into known specialized families.
    assert route["template_family"] not in {
        "vc_lot_schedule",
        "simple_lot_schedule",
        "audited_portfolio_schedule",
        "condensed_hedge_schedule",
    }


@pytest.mark.skipif(not FS_PDF.exists(), reason="A5a0710 sample PDF missing")
def test_a5_schema_inference_from_schedule_headers():
    route = route_document(FS_PDF)
    inferred = route.get("inferred_schema") or infer_schema(
        FS_PDF, schedule_pages=route["schedule_pages"]
    )
    assert inferred["found"] is True
    logical = set(inferred["logical_columns"])
    assert "cost" in logical
    assert "fair_value" in logical
    assert not inferred["missing_required"]
    assert inferred["inference_confidence"] >= 0.55
    assert inferred.get("company_row_headers") is True


@pytest.mark.skipif(not FS_PDF.exists(), reason="A5a0710 sample PDF missing")
def test_a5_generic_schedule_extraction_from_inferred_schema():
    route = route_document(FS_PDF)
    companies = parse_inferred_schedule_companies(
        FS_PDF,
        route["schedule_pages"],
        inferred_schema=route["inferred_schema"],
    )
    assert len(companies) >= 100
    by_name = {c["company_name"]: c for c in companies}
    assert "10MS" in by_name
    assert Decimal(by_name["10MS"]["cost_reported_normalized"]) == Decimal("2672262")
    assert "Absolute Foods" in by_name
    # Evidence must come from schedule pages, not a named secondary statement parser.
    assert all(c.get("subtotal_event") == "inferred_schedule_text" for c in companies)


@pytest.mark.skipif(not FS_PDF.exists(), reason="A5a0710 sample PDF missing")
def test_a5_extract_emits_review_onboarding_not_auto_compare():
    payload = run_extract(
        pdf_path=FS_PDF,
        config_path=None,
        output_dir=OUT,
        repo_root=ROOT.parent,
        cli_args={"command": "extract", "pdf": str(FS_PDF), "template": "auto"},
        auto_template=True,
    )
    assert payload["route"]["extraction_mode"] == "position_level_inferred"
    assert len(payload["company_summary"]) >= 100
    assert payload["extraction_quality"]["status"] == "REVIEW_REQUIRED"
    onboarding = payload.get("onboarding_summary") or {}
    assert onboarding.get("compare_allowed") is False
    assert onboarding.get("onboarding_status") == "inferred_ready"
    assert (OUT / "schema_inference.json").exists()


@pytest.mark.skipif(not ML_PDF.exists(), reason="A5a0710 ML PDF missing")
def test_a5_management_letter_is_not_position_comparable():
    route = route_document(ML_PDF)
    assert route.get("template_family") is None
    assert route["extraction_mode"] in {"manual_review", "blocked_narrative"}
