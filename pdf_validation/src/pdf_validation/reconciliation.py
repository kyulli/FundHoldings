"""Financial reconciliation checks."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pdf_validation.normalization import decimal_or_none, parse_reported_number


def _status(diff: Decimal, tolerance: Decimal) -> str:
    return "PASS" if abs(diff) <= tolerance else "FAIL"


def _check(
    *,
    check_id: str,
    schedule: str,
    description: str,
    reported: Decimal | None,
    calculated: Decimal | None,
    tolerance: Decimal,
    tolerance_reason: str,
    entity: str | None = None,
) -> dict[str, Any]:
    if reported is None or calculated is None:
        return {
            "check_id": check_id,
            "schedule": schedule,
            "entity": entity,
            "description": description,
            "reported": None if reported is None else format(reported, "f"),
            "calculated": None if calculated is None else format(calculated, "f"),
            "difference": None,
            "tolerance": format(tolerance, "f"),
            "tolerance_reason": tolerance_reason,
            "status": "FAIL",
            "issue": "missing_reported_or_calculated",
        }
    diff = calculated - reported
    return {
        "check_id": check_id,
        "schedule": schedule,
        "entity": entity,
        "description": description,
        "reported": format(reported, "f"),
        "calculated": format(calculated, "f"),
        "difference": format(diff, "f"),
        "tolerance": format(tolerance, "f"),
        "tolerance_reason": tolerance_reason,
        "status": _status(diff, tolerance),
    }


def run_reconciliation(
    *,
    investment_lots: list[dict[str, Any]],
    company_summary: list[dict[str, Any]],
    company_events: list[dict[str, Any]],
    realized_lots: list[dict[str, Any]],
    realized_totals: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    recon_cfg = config["reconciliation"]
    tolerance = Decimal(str(recon_cfg.get("tolerance_absolute", "1")))
    tolerance_reason = recon_cfg.get("tolerance_reason") or "Allow absolute tolerance from config"
    field_semantics = config["field_semantics"]
    dash_tokens = config["row_classification"]["dash_tokens"]
    blank_tokens = config["row_classification"]["blank_tokens"]
    checks: list[dict[str, Any]] = []

    # 1) Lot-level Fair Value - Cost = Unrealized Gain
    for idx, lot in enumerate(investment_lots):
        cost = decimal_or_none(lot.get("cost_normalized"))
        fair = decimal_or_none(lot.get("fair_value_normalized"))
        gain = decimal_or_none(lot.get("unrealized_gain_loss_normalized"))
        if cost is None or fair is None or gain is None:
            checks.append(
                _check(
                    check_id=f"lot_fv_minus_cost_{idx}",
                    schedule="schedule_of_investments",
                    description="Fair Value - Cost = Unrealized Gain (lot)",
                    reported=gain,
                    calculated=None if cost is None or fair is None else fair - cost,
                    tolerance=tolerance,
                    tolerance_reason=tolerance_reason,
                    entity=lot.get("company_name"),
                )
            )
            continue
        checks.append(
            _check(
                check_id=f"lot_fv_minus_cost_{idx}",
                schedule="schedule_of_investments",
                description="Fair Value - Cost = Unrealized Gain (lot)",
                reported=gain,
                calculated=fair - cost,
                tolerance=tolerance,
                tolerance_reason=tolerance_reason,
                entity=lot.get("company_name"),
            )
        )

    # 2) lot sum = company subtotal
    for company in company_summary:
        for field in ("cost", "fair_value", "unrealized_gain_loss"):
            checks.append(
                _check(
                    check_id=f"company_subtotal_{field}_{company['company_name']}",
                    schedule="schedule_of_investments",
                    description=f"lot sum = company reported subtotal ({field})",
                    reported=decimal_or_none(company.get(f"{field}_reported_normalized")),
                    calculated=decimal_or_none(company.get(f"{field}_calculated")),
                    tolerance=tolerance,
                    tolerance_reason=tolerance_reason,
                    entity=company["company_name"],
                )
            )

    # Parse grand total from company events
    grand = next((e for e in company_events if e["event"] == "grand_total"), None)
    grand_parsed: dict[str, Decimal | None] = {"cost": None, "fair_value": None, "unrealized_gain_loss": None}
    if grand:
        for field, raw_key, bbox_key in (
            ("cost", "cost_raw", "cost_bbox"),
            ("fair_value", "fair_value_raw", "fair_value_bbox"),
            ("unrealized_gain_loss", "unrealized_gain_loss_raw", "unrealized_gain_loss_bbox"),
        ):
            parsed = parse_reported_number(
                grand[raw_key],
                field=field,
                field_semantics=field_semantics,
                dash_tokens=dash_tokens,
                blank_tokens=blank_tokens,
                source_page=grand["page"],
                source_bbox=grand.get(bbox_key),
            )
            grand_parsed[field] = decimal_or_none(parsed.get(f"{field}_normalized"))

    company_cost_sum = sum(
        (decimal_or_none(c.get("cost_reported_normalized")) or Decimal("0")) for c in company_summary
    )
    company_fv_sum = sum(
        (decimal_or_none(c.get("fair_value_reported_normalized")) or Decimal("0")) for c in company_summary
    )
    company_gain_sum = sum(
        (decimal_or_none(c.get("unrealized_gain_loss_reported_normalized")) or Decimal("0"))
        for c in company_summary
    )

    # 3) company sum = schedule grand total
    checks.append(
        _check(
            check_id="company_sum_vs_grand_cost",
            schedule="schedule_of_investments",
            description="company reported subtotal sum = schedule grand total cost",
            reported=grand_parsed["cost"],
            calculated=company_cost_sum,
            tolerance=tolerance,
            tolerance_reason=tolerance_reason,
        )
    )
    checks.append(
        _check(
            check_id="company_sum_vs_grand_fair_value",
            schedule="schedule_of_investments",
            description="company reported subtotal sum = schedule grand total fair value",
            reported=grand_parsed["fair_value"],
            calculated=company_fv_sum,
            tolerance=tolerance,
            tolerance_reason=tolerance_reason,
        )
    )
    checks.append(
        _check(
            check_id="company_sum_vs_grand_unrealized",
            schedule="schedule_of_investments",
            description="company reported subtotal sum = schedule grand total unrealized gain",
            reported=grand_parsed["unrealized_gain_loss"],
            calculated=company_gain_sum,
            tolerance=tolerance,
            tolerance_reason=tolerance_reason,
        )
    )

    # Also identity on grand total itself
    if grand_parsed["cost"] is not None and grand_parsed["fair_value"] is not None:
        checks.append(
            _check(
                check_id="grand_fv_minus_cost",
                schedule="schedule_of_investments",
                description="Grand Fair Value - Cost = Unrealized Gain",
                reported=grand_parsed["unrealized_gain_loss"],
                calculated=grand_parsed["fair_value"] - grand_parsed["cost"],
                tolerance=tolerance,
                tolerance_reason=tolerance_reason,
            )
        )

    # 4) Schedule totals = Statement of Assets (skip missing anchors)
    soa = recon_cfg.get("statement_of_assets") or {}
    if soa.get("investments_cost") not in (None, "", "null"):
        checks.append(
            _check(
                check_id="schedule_vs_statement_cost",
                schedule="schedule_of_investments",
                description="Schedule Cost total = Statement of Assets Cost",
                reported=Decimal(str(soa["investments_cost"])),
                calculated=grand_parsed["cost"],
                tolerance=tolerance,
                tolerance_reason=tolerance_reason,
            )
        )
    if soa.get("investments_fair_value") not in (None, "", "null"):
        checks.append(
            _check(
                check_id="schedule_vs_statement_fair_value",
                schedule="schedule_of_investments",
                description="Schedule Fair Value total = Statement of Assets Fair Value",
                reported=Decimal(str(soa["investments_fair_value"])),
                calculated=grand_parsed["fair_value"],
                tolerance=tolerance,
                tolerance_reason=tolerance_reason,
            )
        )
    if soa.get("net_unrealized_appreciation") not in (None, "", "null"):
        checks.append(
            _check(
                check_id="schedule_vs_statement_unrealized",
                schedule="schedule_of_investments",
                description="Schedule Unrealized total = Statement Net Unrealized Appreciation",
                reported=Decimal(str(soa["net_unrealized_appreciation"])),
                calculated=grand_parsed["unrealized_gain_loss"],
                tolerance=tolerance,
                tolerance_reason=tolerance_reason,
            )
        )

    # Realized schedule
    realized_total = realized_totals[0] if realized_totals else None
    realized_parsed: dict[str, Decimal | None] = {
        "cost": None,
        "cash_proceeds": None,
        "realized_gain_loss": None,
    }
    if realized_total:
        for field, raw_key, bbox_key in (
            ("cost", "cost_raw", "cost_bbox"),
            ("cash_proceeds", "cash_proceeds_raw", "cash_proceeds_bbox"),
            ("realized_gain_loss", "realized_gain_loss_raw", "realized_gain_loss_bbox"),
        ):
            parsed = parse_reported_number(
                realized_total[raw_key],
                field=field,
                field_semantics=field_semantics,
                dash_tokens=dash_tokens,
                blank_tokens=blank_tokens,
                source_page=realized_total["page"],
                source_bbox=realized_total.get(bbox_key),
            )
            realized_parsed[field] = decimal_or_none(parsed.get(f"{field}_normalized"))

    lot_cost = sum(
        (decimal_or_none(lot.get("cost_normalized")) or Decimal("0")) for lot in realized_lots
    )
    lot_proceeds = sum(
        (decimal_or_none(lot.get("cash_proceeds_normalized")) or Decimal("0")) for lot in realized_lots
    )
    lot_gain = sum(
        (decimal_or_none(lot.get("realized_gain_loss_normalized")) or Decimal("0")) for lot in realized_lots
    )

    if realized_lots or realized_total:
        checks.append(
            _check(
                check_id="realized_lots_vs_total_cost",
                schedule="schedule_of_realized",
                description="realized lot cost sum = realized schedule total cost",
                reported=realized_parsed["cost"],
                calculated=lot_cost,
                tolerance=tolerance,
                tolerance_reason=tolerance_reason,
            )
        )
        checks.append(
            _check(
                check_id="realized_lots_vs_total_proceeds",
                schedule="schedule_of_realized",
                description="realized lot proceeds sum = realized schedule total proceeds",
                reported=realized_parsed["cash_proceeds"],
                calculated=lot_proceeds,
                tolerance=tolerance,
                tolerance_reason=tolerance_reason,
            )
        )
        checks.append(
            _check(
                check_id="realized_lots_vs_total_gain",
                schedule="schedule_of_realized",
                description="realized lot gain sum = realized schedule total gain/(loss)",
                reported=realized_parsed["realized_gain_loss"],
                calculated=lot_gain,
                tolerance=tolerance,
                tolerance_reason=tolerance_reason,
            )
        )

    # 5) Realized schedule = Statement realized gain/loss
    if soa.get("net_realized_gain_loss") not in (None, "", "null"):
        checks.append(
            _check(
                check_id="realized_vs_statement_gain",
                schedule="schedule_of_realized",
                description="Realized Schedule Gain/(Loss) = Statement Net Realized Gain/(Loss)",
                reported=Decimal(str(soa["net_realized_gain_loss"])),
                calculated=realized_parsed["realized_gain_loss"],
                tolerance=tolerance,
                tolerance_reason=tolerance_reason,
            )
        )

    # Golden totals (optional; skip null/missing anchors for non-regression runs)
    golden_inv = recon_cfg.get("golden_schedule_investments") or {}
    for field, key in (
        ("cost", "cost"),
        ("fair_value", "fair_value"),
        ("unrealized_gain_loss", "unrealized_gain_loss"),
    ):
        if golden_inv.get(key) in (None, "", "null"):
            continue
        checks.append(
            _check(
                check_id=f"golden_investments_{field}",
                schedule="schedule_of_investments",
                description=f"Extracted investments {field} matches golden total",
                reported=Decimal(str(golden_inv[key])),
                calculated=grand_parsed[field],
                tolerance=tolerance,
                tolerance_reason="Manually verified golden PDF total",
            )
        )

    golden_realized = recon_cfg.get("golden_schedule_realized") or {}
    for field, key in (
        ("cost", "cost"),
        ("cash_proceeds", "cash_proceeds"),
        ("realized_gain_loss", "realized_gain_loss"),
    ):
        if golden_realized.get(key) in (None, "", "null"):
            continue
        checks.append(
            _check(
                check_id=f"golden_realized_{field}",
                schedule="schedule_of_realized",
                description=f"Extracted realized {field} matches golden total",
                reported=Decimal(str(golden_realized[key])),
                calculated=realized_parsed[field],
                tolerance=tolerance,
                tolerance_reason="Manually verified golden PDF total",
            )
        )

    return checks
