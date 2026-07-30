"""Parser selection and structural decision records."""

from __future__ import annotations

from typing import Any


def _header_coverage(classified_rows: list[dict[str, Any]], required_tokens: list[str]) -> dict[str, Any]:
    header_rows = [r for r in classified_rows if r.get("row_type") == "repeated_header"]
    joined = " ".join(" ".join(r.get("raw_cells") or []) for r in header_rows).lower()
    missing = [token for token in required_tokens if token.lower() not in joined]
    return {
        "header_rows_found": len(header_rows),
        "missing_required_tokens": missing,
        "pass": len(header_rows) > 0 and not missing,
    }


def _column_check(raw_tables: list[dict[str, Any]], expected_columns: int) -> dict[str, Any]:
    shapes = [tuple(t.get("shape") or [0, 0]) for t in raw_tables]
    cols = [shape[1] for shape in shapes]
    return {
        "shapes": shapes,
        "expected_columns": expected_columns,
        "pass": bool(cols) and all(c == expected_columns for c in cols),
    }


def _key_field_parse_rate(lots: list[dict[str, Any]], fields: list[str]) -> dict[str, Any]:
    if not lots:
        return {"pass": False, "rates": {}, "lot_count": 0}
    rates = {}
    for field in fields:
        ok = 0
        for lot in lots:
            status = lot.get(f"{field}_parse_status")
            if status in {"ok", "zero", "dash"}:
                ok += 1
        rates[field] = ok / len(lots)
    return {
        "lot_count": len(lots),
        "rates": rates,
        "pass": all(rate >= 0.95 for rate in rates.values()),
    }


def _row_structure(classified_rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in classified_rows:
        counts[row["row_type"]] = counts.get(row["row_type"], 0) + 1
    return {
        "counts": counts,
        "pass": counts.get("investment_lot", 0) > 0 and counts.get("grand_total", 0) >= 1,
    }


def _subtotal_and_continuation(
    company_summary: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    expected = config.get("cross_page", {}).get("continuation_companies", [])
    issues = []
    for item in expected:
        company = item["company_name"]
        match = next((row for row in company_summary if row["company_name"] == company), None)
        if match is None:
            issues.append(f"missing_company:{company}")
            continue
        if match.get("subtotal_event") != "page_continuation":
            issues.append(f"expected_page_continuation:{company}:got:{match.get('subtotal_event')}")
        if match.get("subtotal_page") != item["subtotal_on_page"]:
            issues.append(
                f"subtotal_page_mismatch:{company}:expected:{item['subtotal_on_page']}:got:{match.get('subtotal_page')}"
            )
        if item["starts_on_page"] not in (match.get("pages") or []):
            issues.append(f"start_page_missing:{company}")

    companies_with_subtotals = [
        row for row in company_summary if row.get("cost_reported_normalized") is not None
    ]
    return {
        "companies_with_reported_subtotals": len(companies_with_subtotals),
        "continuation_issues": issues,
        "pass": not issues and len(companies_with_subtotals) == len(company_summary),
    }


def decide_parsers(
    *,
    camelot_raw: dict[str, Any],
    pdfplumber_raw: dict[str, Any],
    investment_classified: dict[str, Any],
    realized_classified: dict[str, Any],
    company_summary: list[dict[str, Any]],
    reconciliation_rows: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []

    inv_checks = {
        "header_coverage": _header_coverage(
            investment_classified["classified_rows"],
            config["headers"]["investments_required"],
        ),
        "column_count": _column_check(
            camelot_raw["investments"],
            config["camelot"]["investments"]["expected_columns"],
        ),
        "key_field_parse_rate": _key_field_parse_rate(
            investment_classified["investment_lots"],
            ["cost", "fair_value", "unrealized_gain_loss"],
        ),
        "row_structure": _row_structure(investment_classified["classified_rows"]),
        "subtotal_and_continuation": _subtotal_and_continuation(company_summary, config),
        "reconciliation": {
            "failing_checks": [
                row["check_id"]
                for row in reconciliation_rows
                if row.get("schedule") == "schedule_of_investments" and row.get("status") == "FAIL"
            ],
            "pass": all(
                row.get("status") == "PASS"
                for row in reconciliation_rows
                if row.get("schedule") == "schedule_of_investments"
            ),
        },
    }
    inv_pass = all(check.get("pass") for check in inv_checks.values())
    decisions.append(
        {
            "schedule": "schedule_of_investments",
            "selected_parser": "camelot" if inv_pass else "review_required",
            "fallback_parser_available": "pdfplumber",
            "reason": (
                "Camelot stream with template columns/areas passed header, column, parse-rate, "
                "row-structure, subtotal/continuation, and reconciliation checks."
                if inv_pass
                else "Camelot failed one or more structural/financial checks; pdfplumber raw preserved for review."
            ),
            "checks": inv_checks,
            "camelot_tables": len(camelot_raw.get("investments") or []),
            "pdfplumber_tables": len(pdfplumber_raw.get("investments") or []),
            "status": "PASS" if inv_pass else "FAIL",
        }
    )

    realized_checks = {
        "header_coverage": _header_coverage(
            realized_classified["classified_rows"],
            config["headers"]["realized_required"],
        ),
        "column_count": _column_check(
            camelot_raw["realized"],
            config["camelot"]["realized"]["expected_columns"],
        ),
        "key_field_parse_rate": _key_field_parse_rate(
            realized_classified["realized_lots"],
            ["cost", "cash_proceeds", "realized_gain_loss"],
        ),
        "row_structure": _row_structure(
            [
                {**row, "row_type": "investment_lot" if row["row_type"] == "investment_lot" else row["row_type"]}
                for row in realized_classified["classified_rows"]
            ]
        ),
        "reconciliation": {
            "failing_checks": [
                row["check_id"]
                for row in reconciliation_rows
                if row.get("schedule") == "schedule_of_realized" and row.get("status") == "FAIL"
            ],
            "pass": all(
                row.get("status") == "PASS"
                for row in reconciliation_rows
                if row.get("schedule") == "schedule_of_realized"
            ),
        },
    }
    # realized row_structure uses investment_lot label for lots; ensure grand_total exists.
    realized_pass = all(check.get("pass") for check in realized_checks.values())
    decisions.append(
        {
            "schedule": "schedule_of_realized",
            "selected_parser": "camelot" if realized_pass else "review_required",
            "fallback_parser_available": "pdfplumber",
            "reason": (
                "Camelot stream with template columns/areas passed structural and reconciliation checks."
                if realized_pass
                else "Camelot failed one or more checks; pdfplumber raw preserved for review."
            ),
            "checks": realized_checks,
            "camelot_tables": len(camelot_raw.get("realized") or []),
            "pdfplumber_tables": len(pdfplumber_raw.get("realized") or []),
            "status": "PASS" if realized_pass else "FAIL",
        }
    )
    return decisions
