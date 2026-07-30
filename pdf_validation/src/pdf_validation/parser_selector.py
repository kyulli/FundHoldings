"""Select among Camelot / pdfplumber / text-fallback extraction paths."""

from __future__ import annotations

from typing import Any

from pdf_validation.row_classification import (
    build_company_summary,
    classify_investment_tables,
    classify_realized_tables,
)
from pdf_validation.text_fallback import maybe_apply_text_company_summary


def _score_path(
    *,
    company_summary: list[dict[str, Any]],
    reconciliation: list[dict[str, Any]],
    investment_lots: list[dict[str, Any]],
) -> dict[str, Any]:
    recon_fail = sum(1 for row in reconciliation if row.get("status") == "FAIL")
    recon_pass = sum(1 for row in reconciliation if row.get("status") == "PASS")
    company_fail = sum(
        1
        for c in company_summary
        if c.get("cost_status") == "FAIL" or c.get("fair_value_status") == "FAIL"
    )
    score = recon_pass * 2 - recon_fail - company_fail + min(len(investment_lots), 50) * 0.01
    return {
        "score": score,
        "recon_pass": recon_pass,
        "recon_fail": recon_fail,
        "company_fail": company_fail,
        "lot_count": len(investment_lots),
        "company_count": len(company_summary),
    }


def select_extraction_path(
    *,
    pdf_path: str,
    config: dict[str, Any],
    camelot_raw: dict[str, Any],
    pdfplumber_raw: dict[str, Any],
    run_reconciliation_fn,
) -> dict[str, Any]:
    """Classify Camelot first; apply text fallback when shattered; optionally try pdfplumber."""
    candidates: list[dict[str, Any]] = []

    camelot_inv = classify_investment_tables(camelot_raw.get("investments") or [], config)
    camelot_real = classify_realized_tables(camelot_raw.get("realized") or [], config)
    camelot_summary = build_company_summary(
        camelot_inv["investment_lots"],
        camelot_inv["company_events"],
        config,
    )
    camelot_recon = run_reconciliation_fn(
        investment_lots=camelot_inv["investment_lots"],
        company_summary=camelot_summary,
        company_events=camelot_inv["company_events"],
        realized_lots=camelot_real["realized_lots"],
        realized_totals=camelot_real["realized_totals"],
        config=config,
    )
    inv_pages = config.get("pages", {}).get("schedule_of_investments") or []
    camelot_summary2, text_meta = maybe_apply_text_company_summary(
        pdf_path=pdf_path,
        inv_pages=inv_pages,
        company_summary=camelot_summary,
        reconciliation=camelot_recon,
        camelot_raw=camelot_raw,
    )
    if text_meta.get("applied"):
        # Re-run recon against text company summary for score fairness.
        camelot_recon2 = run_reconciliation_fn(
            investment_lots=camelot_inv["investment_lots"],
            company_summary=camelot_summary2,
            company_events=camelot_inv["company_events"],
            realized_lots=camelot_real["realized_lots"],
            realized_totals=camelot_real["realized_totals"],
            config=config,
        )
        selected_parser = "text_fallback"
        summary = camelot_summary2
        recon = camelot_recon2
        score = _score_path(
            company_summary=summary,
            reconciliation=recon,
            investment_lots=camelot_inv["investment_lots"],
        )
        score["score"] += 50  # hard prefer intentional text fallback
        best = {
            "selected_parser": selected_parser,
            "investment_classified": camelot_inv,
            "realized_classified": camelot_real,
            "company_summary": summary,
            "reconciliation": recon,
            "score": score,
            "text_fallback": text_meta,
            "candidates": [
                {
                    "selected_parser": selected_parser,
                    "score": score,
                    "text_fallback": text_meta,
                }
            ],
            "extraction_quality": {
                "status": "PASS",
                "selected_parser": "text_fallback",
                "recon_fail": score["recon_fail"],
                "reason": "Text fallback applied for shattered Camelot amounts.",
            },
        }
        return best

    selected_parser = "camelot"
    summary = camelot_summary
    recon = camelot_recon
    score = _score_path(
        company_summary=summary,
        reconciliation=recon,
        investment_lots=camelot_inv["investment_lots"],
    )
    candidates.append(
        {
            "selected_parser": selected_parser,
            "investment_classified": camelot_inv,
            "realized_classified": camelot_real,
            "company_summary": summary,
            "reconciliation": recon,
            "score": score,
            "text_fallback": text_meta,
        }
    )

    # Optional pdfplumber classify if camelot path is weak and plumber tables exist.
    plumber_tables = pdfplumber_raw.get("investments") or []
    if plumber_tables and score["recon_fail"] >= 20:
        try:
            plumber_inv = classify_investment_tables(plumber_tables, config)
            plumber_real = classify_realized_tables(pdfplumber_raw.get("realized") or [], config)
            plumber_summary = build_company_summary(
                plumber_inv["investment_lots"],
                plumber_inv["company_events"],
                config,
            )
            plumber_recon = run_reconciliation_fn(
                investment_lots=plumber_inv["investment_lots"],
                company_summary=plumber_summary,
                company_events=plumber_inv["company_events"],
                realized_lots=plumber_real["realized_lots"],
                realized_totals=plumber_real["realized_totals"],
                config=config,
            )
            plumber_score = _score_path(
                company_summary=plumber_summary,
                reconciliation=plumber_recon,
                investment_lots=plumber_inv["investment_lots"],
            )
            candidates.append(
                {
                    "selected_parser": "pdfplumber",
                    "investment_classified": plumber_inv,
                    "realized_classified": plumber_real,
                    "company_summary": plumber_summary,
                    "reconciliation": plumber_recon,
                    "score": plumber_score,
                    "text_fallback": {"applied": False},
                }
            )
        except Exception as exc:  # noqa: BLE001
            candidates.append(
                {
                    "selected_parser": "pdfplumber",
                    "error": str(exc),
                    "score": {"score": -999},
                }
            )

    candidates.sort(key=lambda c: c.get("score", {}).get("score", -999), reverse=True)
    best = candidates[0]
    best["candidates"] = [
        {
            "selected_parser": c.get("selected_parser"),
            "score": c.get("score"),
            "error": c.get("error"),
            "text_fallback": c.get("text_fallback"),
        }
        for c in candidates
    ]
    # Quality gate for compare.
    critical_fail = best.get("score", {}).get("recon_fail", 999)
    best["extraction_quality"] = {
        "status": "PASS" if critical_fail < 40 or best.get("text_fallback", {}).get("applied") else "FAIL",
        "selected_parser": best.get("selected_parser"),
        "recon_fail": critical_fail,
        "reason": (
            "Selected parser path acceptable for comparison."
            if critical_fail < 40 or best.get("text_fallback", {}).get("applied")
            else "Too many reconciliation failures; company amount comparison should be blocked."
        ),
    }
    return best
