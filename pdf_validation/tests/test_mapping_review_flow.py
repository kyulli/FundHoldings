"""Tests for mapping proposal policy, draft compare, and review handlers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf_validation.entity_mapping import (
    apply_review_decisions,
    build_mapping_proposal,
    normalize_entity_name,
)
from pdf_validation.mapping_onboarding import (
    load_review_state,
    preview_promote_diff,
    promote_draft_mapping,
    review_dir,
    run_draft_compare,
    upsert_decision,
)
from pdf_validation.mapping_review_server import serve_mapping_review

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
CSV = REPO / "holdings_anonymized.csv"
SURVEY = ROOT / "outputs" / "five_fund_survey"


def test_normalize_strips_city_state_paren():
    assert normalize_entity_name("Homeward, Inc. (Austin, TX)") == normalize_entity_name("Homeward, Inc.")
    assert "austin" not in normalize_entity_name("Honey Homes, Inc. (Lafayette, CA)")


def test_exact_and_alias_auto_accept():
    proposal = build_mapping_proposal(
        pdf_companies=["Airbus SE", "House of Home AB"],
        vendor_names=["Airbus SE", "House of Home AB (Nordic Knots)", "Other Co"],
        fund_id="Aa7f6d4",
        as_of_date="2026-03-31",
        aliases={
            "global": {"house of home ab": "House of Home AB (Nordic Knots)"},
            "by_fund": {"Aa7f6d4": {"house of home ab": "House of Home AB (Nordic Knots)"}},
        },
    )
    by_name = {e["pdf_company_name"]: e for e in proposal["entities"]}
    assert by_name["Airbus SE"]["status"] == "auto_accept"
    assert by_name["Airbus SE"]["match_type"] == "exact"
    assert by_name["House of Home AB"]["status"] == "auto_accept"
    assert by_name["House of Home AB"]["match_type"] in {"alias", "exact"}


def test_city_suffix_fuzzy_or_exact_for_era_style_names():
    proposal = build_mapping_proposal(
        pdf_companies=["Homeward, Inc. (Austin, TX)", "Mystery Startup LLC"],
        vendor_names=["Homeward, Inc.", "Honey Homes, Inc.", "Completely Different Corp"],
        fund_id="A9ffbf3",
        as_of_date="2025-12-31",
        aliases={"global": {}, "by_fund": {}},
    )
    by_name = {e["pdf_company_name"]: e for e in proposal["entities"]}
    home = by_name["Homeward, Inc. (Austin, TX)"]
    assert home["recommended_vendor_source_asset"] == "Homeward, Inc."
    assert home["status"] in {"auto_accept", "needs_review"}
    assert home["score"] is not None and home["score"] >= 0.9
    mystery = by_name["Mystery Startup LLC"]
    assert mystery["status"] in {"unmapped", "needs_review"}


def test_multi_pdf_same_vendor_forces_review():
    proposal = build_mapping_proposal(
        pdf_companies=["Acme Inc", "Acme Incorporated"],
        vendor_names=["Acme Inc"],
        fund_id="TEST",
        aliases={"global": {}, "by_fund": {}},
    )
    statuses = {e["pdf_company_name"]: e["status"] for e in proposal["entities"]}
    # Both normalize to same vendor → conflict → needs_review
    assert statuses["Acme Inc"] == "needs_review"
    assert statuses["Acme Incorporated"] == "needs_review"


def test_apply_review_decisions_respects_user_override():
    proposal = build_mapping_proposal(
        pdf_companies=["Alpha Co", "Beta Co"],
        vendor_names=["Alpha Co", "Beta Company", "Gamma"],
        fund_id="TEST",
        aliases={"global": {}, "by_fund": {}},
    )
    # Force beta needs_review then accept manually
    for e in proposal["entities"]:
        if e["pdf_company_name"] == "Beta Co":
            e["status"] = "needs_review"
            e["default_decision"] = "needs_review"
            e["recommended_vendor_source_asset"] = "Beta Company"
    state = {
        "decisions": [
            {"pdf_company_name": "Beta Co", "action": "accept", "vendor_source_asset": "Beta Company"},
            {"pdf_company_name": "Alpha Co", "action": "reject"},
        ]
    }
    mappings = apply_review_decisions(proposal, state)
    vendors = {m["vendor_source_asset"] for m in mappings}
    assert "Beta Company" in vendors
    assert "Alpha Co" not in vendors


def test_draft_does_not_touch_registry(tmp_path: Path):
    # Minimal fake extraction dir
    extr = tmp_path / "extract"
    extr.mkdir()
    (extr / "route.json").write_text(
        json.dumps({"extraction_mode": "position_level", "template_family": "simple_lot_schedule", "as_of_date": "2026-03-31"}),
        encoding="utf-8",
    )
    (extr / "company_summary.jsonl").write_text(
        json.dumps(
            {
                "company_name": "House of Home AB",
                "entity_grain": "company",
                "cost_reported_normalized": "1",
                "fair_value_reported_normalized": "1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (extr / "metadata.jsonl").write_text(
        json.dumps({"field": "as_of_date", "normalized": "2026-03-31"}) + "\n",
        encoding="utf-8",
    )
    registry_before = (ROOT / "configs" / "vendor_mapping_registry.json").read_bytes()
    if not CSV.exists():
        pytest.skip("vendor csv missing")
    summary = run_draft_compare(
        extraction_dir=extr,
        vendor_csv=CSV,
        fund_id="Aa7f6d4",
        as_of_date="2026-03-31",
        repo_root=REPO,
        pkg_root=ROOT,
        rebuild_proposal=True,
    )
    assert (extr / "mapping_review" / "draft_vendor_mapping.json").exists()
    assert (extr / "mapping_review" / "mapping_proposal.json").exists()
    assert summary.get("proposal_id")
    assert (ROOT / "configs" / "vendor_mapping_registry.json").read_bytes() == registry_before


@pytest.mark.skipif(not (SURVEY / "A729d8b_retry").exists(), reason="survey extract missing")
def test_a729d8b_draft_compare_comparable():
    if not CSV.exists():
        pytest.skip("vendor csv missing")
    extr = SURVEY / "A729d8b_retry"
    # Reset review state for clean run
    rd = review_dir(extr)
    if (rd / "review_state.json").exists():
        (rd / "review_state.json").unlink()
    summary = run_draft_compare(
        extraction_dir=extr,
        vendor_csv=CSV,
        fund_id="A729d8b",
        repo_root=REPO,
        pkg_root=ROOT,
        rebuild_proposal=True,
    )
    assert summary["accepted_mappings"] >= 5
    assert summary["comparability_status"] == "comparable"


@pytest.mark.skipif(not (SURVEY / "Aa7f6d4").exists(), reason="survey extract missing")
def test_aa7f6d4_draft_compare_comparable():
    if not CSV.exists():
        pytest.skip("vendor csv missing")
    extr = SURVEY / "Aa7f6d4"
    rd = review_dir(extr)
    if (rd / "review_state.json").exists():
        (rd / "review_state.json").unlink()
    summary = run_draft_compare(
        extraction_dir=extr,
        vendor_csv=CSV,
        fund_id="Aa7f6d4",
        as_of_date="2026-03-31",
        repo_root=REPO,
        pkg_root=ROOT,
        rebuild_proposal=True,
    )
    assert summary["accepted_mappings"] >= 1
    assert summary["comparability_status"] == "comparable"


@pytest.mark.skipif(not (SURVEY / "A9ffbf3_(mgr_Ad7e703)").exists(), reason="survey extract missing")
def test_a9ffbf3_city_suffix_produces_candidates_and_can_accept():
    if not CSV.exists():
        pytest.skip("vendor csv missing")
    extr = SURVEY / "A9ffbf3_(mgr_Ad7e703)"
    from pdf_validation.mapping_onboarding import build_proposal_for_extraction

    proposal = build_proposal_for_extraction(
        extraction_dir=extr,
        vendor_csv=CSV,
        fund_id="A9ffbf3",
        as_of_date="2025-12-31",
    )
    homeward = next(e for e in proposal["entities"] if "Homeward" in e["pdf_company_name"])
    assert homeward["recommended_vendor_source_asset"]
    assert "Homeward" in homeward["recommended_vendor_source_asset"]
    # Accept all recommended for entities that have a recommendation
    for e in proposal["entities"]:
        if e.get("recommended_vendor_source_asset"):
            upsert_decision(
                extr,
                pdf_company_name=e["pdf_company_name"],
                action="accept",
                vendor_source_asset=e["recommended_vendor_source_asset"],
            )
    summary = run_draft_compare(
        extraction_dir=extr,
        vendor_csv=CSV,
        fund_id="A9ffbf3",
        as_of_date="2025-12-31",
        repo_root=REPO,
        pkg_root=ROOT,
        rebuild_proposal=False,
    )
    assert summary["accepted_mappings"] >= 3
    # May be comparable once mappings accepted
    assert summary["comparability_status"] in {"comparable", "not_comparable"}


def test_promote_requires_confirm_and_writes_approved(tmp_path: Path):
    if not CSV.exists():
        pytest.skip("vendor csv missing")
    extr = tmp_path / "extract"
    extr.mkdir()
    (extr / "route.json").write_text(
        json.dumps({"extraction_mode": "position_level", "template_family": "simple_lot_schedule", "as_of_date": "2026-03-31"}),
        encoding="utf-8",
    )
    (extr / "company_summary.jsonl").write_text(
        json.dumps({"company_name": "House of Home AB", "entity_grain": "company"}) + "\n",
        encoding="utf-8",
    )
    (extr / "metadata.jsonl").write_text(
        json.dumps({"field": "as_of_date", "normalized": "2026-03-31"}) + "\n",
        encoding="utf-8",
    )
    # Use isolated pkg root copy for registry write
    pkg = tmp_path / "pkg"
    (pkg / "configs" / "approved_mappings").mkdir(parents=True)
    # Copy needed templates
    import shutil

    shutil.copytree(ROOT / "configs", pkg / "configs", dirs_exist_ok=True)

    run_draft_compare(
        extraction_dir=extr,
        vendor_csv=CSV,
        fund_id="Aa7f6d4",
        as_of_date="2026-03-31",
        repo_root=REPO,
        pkg_root=pkg,
        rebuild_proposal=True,
    )
    with pytest.raises(ValueError):
        promote_draft_mapping(
            extraction_dir=extr,
            fund_id="TESTPROMOTE",
            reviewer="tester",
            pkg_root=pkg,
            confirm=False,
        )
    preview = preview_promote_diff(extraction_dir=extr, fund_id="TESTPROMOTE", pkg_root=pkg)
    assert "target_mapping_path" in preview
    result = promote_draft_mapping(
        extraction_dir=extr,
        fund_id="TESTPROMOTE",
        reviewer="tester",
        pkg_root=pkg,
        confirm=True,
    )
    assert Path(result["mapping_path"]).exists()
    registry = json.loads(Path(result["registry_path"]).read_text(encoding="utf-8"))
    assert "TESTPROMOTE" in registry["by_fund_id"]


def test_upsert_decision_persists(tmp_path: Path):
    extr = tmp_path / "e"
    extr.mkdir()
    upsert_decision(extr, pdf_company_name="Foo", action="accept", vendor_source_asset="Foo Inc")
    state = load_review_state(extr)
    assert state["decisions"][0]["pdf_company_name"] == "Foo"
    upsert_decision(extr, pdf_company_name="Foo", action="reject")
    state = load_review_state(extr)
    assert len(state["decisions"]) == 1
    assert state["decisions"][0]["action"] == "reject"


@pytest.mark.skipif(not (SURVEY / "A29c2b9").exists(), reason="survey extract missing")
def test_a29c2b9_still_blocked_by_extraction_not_mapping_ui():
    """Mapping UI must not fake comparability when extraction has zero companies."""
    if not CSV.exists():
        pytest.skip("vendor csv missing")
    extr = SURVEY / "A29c2b9"
    companies = [
        json.loads(line)
        for line in (extr / "company_summary.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    real_cos = [
        c
        for c in companies
        if c.get("company_name")
        and not str(c["company_name"]).lower().startswith("non-investment")
        and c.get("entity_grain", "company") in {"company", "security", None}
    ]
    assert len(real_cos) == 0
    summary = run_draft_compare(
        extraction_dir=extr,
        vendor_csv=CSV,
        fund_id="A29c2b9",
        as_of_date="2024-12-31",
        repo_root=REPO,
        pkg_root=ROOT,
        rebuild_proposal=True,
    )
    assert summary["comparability_status"] != "comparable"
    assert summary["accepted_mappings"] == 0


def test_review_server_localhost_only_and_handlers(tmp_path: Path):
    if not CSV.exists():
        pytest.skip("vendor csv missing")
    extr = tmp_path / "extract"
    extr.mkdir()
    (extr / "route.json").write_text(
        json.dumps(
            {
                "extraction_mode": "position_level",
                "template_family": "simple_lot_schedule",
                "as_of_date": "2026-03-31",
            }
        ),
        encoding="utf-8",
    )
    (extr / "company_summary.jsonl").write_text(
        json.dumps({"company_name": "House of Home AB", "entity_grain": "company"}) + "\n",
        encoding="utf-8",
    )
    (extr / "metadata.jsonl").write_text(
        json.dumps({"field": "as_of_date", "normalized": "2026-03-31"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="localhost"):
        serve_mapping_review(
            extraction_dir=extr,
            vendor_csv=CSV,
            fund_id="Aa7f6d4",
            as_of_date="2026-03-31",
            repo_root=REPO,
            pkg_root=ROOT,
            host="0.0.0.0",
            open_browser=False,
        )

    import json as _json
    import urllib.request

    server = serve_mapping_review(
        extraction_dir=extr,
        vendor_csv=CSV,
        fund_id="Aa7f6d4",
        as_of_date="2026-03-31",
        repo_root=REPO,
        pkg_root=ROOT,
        port=0,
        open_browser=False,
    )
    host, port = server.server_address
    assert host == "127.0.0.1"
    import threading

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        # Accept recommended entity via API
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/decide",
            data=_json.dumps(
                {
                    "pdf_company_name": "House of Home AB",
                    "action": "accept",
                    "vendor_source_asset": "House of Home AB (Nordic Knots)",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
        # Persist across "refresh"
        assert load_review_state(extr)["decisions"][0]["action"] == "accept"
        # Reject path leaves mapping unresolved for that entity if only reject
        upsert_decision(extr, pdf_company_name="House of Home AB", action="reject")
        state = load_review_state(extr)
        assert state["decisions"][0]["action"] == "reject"
        # Re-accept then compare
        upsert_decision(
            extr,
            pdf_company_name="House of Home AB",
            action="accept",
            vendor_source_asset="House of Home AB (Nordic Knots)",
        )
        req2 = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/compare",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req2, timeout=60) as resp:
            body = _json.loads(resp.read().decode("utf-8"))
        assert body.get("accepted_mappings", 0) >= 1
        assert (extr / "mapping_review" / "draft_vendor_mapping.json").exists()
    finally:
        server.shutdown()
        server.server_close()
