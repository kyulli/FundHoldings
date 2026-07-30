"""Build PDF-side metrics aligned to vendor CSV / calculations dictionary semantics."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pdf_validation.normalization import format_decimal


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def aggregate_realized_lots(realized_lots: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_company: dict[str, dict[str, Any]] = {}
    for lot in realized_lots:
        company = lot.get("company_name")
        if not company:
            continue
        bucket = by_company.setdefault(
            company,
            {
                "pdf_realized_company_name": company,
                "lot_count": 0,
                "realized_cost": Decimal("0"),
                "realized_proceeds": Decimal("0"),
                "realized_gain_loss": Decimal("0"),
                "pages": set(),
            },
        )
        bucket["lot_count"] += 1
        if lot.get("page") is not None:
            bucket["pages"].add(lot["page"])
        for field, key in (
            ("cost", "realized_cost"),
            ("cash_proceeds", "realized_proceeds"),
            ("realized_gain_loss", "realized_gain_loss"),
        ):
            value = _to_decimal(lot.get(f"{field}_normalized"))
            status = lot.get(f"{field}_parse_status")
            if value is not None and status in {"ok", "zero", "dash"}:
                bucket[key] += value
    for bucket in by_company.values():
        bucket["pages"] = sorted(bucket["pages"])
    return by_company


def derive_deal_status(
    *,
    unrealized_value: Decimal,
    realized_proceeds: Decimal,
) -> dict[str, Any]:
    """Dictionary rules from Allocator Holdings Metrics and Calculations."""
    if unrealized_value > 0 and realized_proceeds <= 0:
        status = "Current"
        rule = "Unrealized Value > 0 and Realized Proceeds <= 0"
    elif unrealized_value > 0 and realized_proceeds > 0:
        status = "Partially Exited"
        rule = "Unrealized Value > 0 and Realized Proceeds > 0"
    elif unrealized_value <= 0:
        status = "Fully Exited"
        rule = "Unrealized Value = 0 (or empty treated as 0) with realized activity/value allowed"
    else:
        status = None
        rule = "unresolved"
    return {
        "deal_status_derived": status,
        "deal_status_inference_rule": rule,
        "deal_status_reported": None,
    }


def _prefer_reported_or_lot_sum(
    reported: Decimal | None,
    calculated: Decimal | None,
    *,
    field: str,
) -> tuple[Decimal, str]:
    if reported is None and calculated is None:
        return Decimal("0"), f"assumed_zero_missing_{field}"
    if reported is None:
        return calculated or Decimal("0"), f"pdf_investments_{field}_lot_sum"
    if calculated is None:
        return reported, f"pdf_investments_{field}_reported"
    if abs(reported - calculated) <= Decimal("1"):
        return reported, f"pdf_investments_{field}_reported"
    if calculated > reported:
        return calculated, f"pdf_investments_{field}_lot_sum_exceeds_printed_subtotal"
    return reported, f"pdf_investments_{field}_reported_lot_sum_incomplete"


def build_pdf_comparable_metrics(
    *,
    company_summary: list[dict[str, Any]],
    realized_lots: list[dict[str, Any]],
    entity_mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Build company-level metrics using calculations-dictionary semantics:

    Current Cost      <- PDF investments Cost (remaining)
    Unrealized Value  <- PDF investments Fair Value (remaining)
    Realized Cost     <- sum PDF realized schedule Cost
    Realized Proceeds <- sum PDF realized schedule Cash Proceeds
    Capital Invested  <- Current Cost + Realized Cost
    Total Value       <- Unrealized Value + Realized Proceeds
    """
    unrealized_by_name = {row["company_name"]: row for row in company_summary}
    realized_by_name = aggregate_realized_lots(realized_lots)

    metrics: list[dict[str, Any]] = []
    for entity in entity_mappings:
        if not entity.get("confirmed"):
            continue
        pdf_name = entity.get("pdf_company_name")
        realized_name = entity.get("pdf_realized_company_name")
        if realized_name is None:
            realized_name = pdf_name
        vendor_name = entity["vendor_source_asset"]

        u = unrealized_by_name.get(pdf_name) if pdf_name else None
        r = realized_by_name.get(realized_name) if realized_name else None

        if u is not None:
            reported_cost = _to_decimal(u.get("cost_reported_normalized"))
            calculated_cost = _to_decimal(u.get("cost_calculated"))
            reported_fv = _to_decimal(u.get("fair_value_reported_normalized"))
            calculated_fv = _to_decimal(u.get("fair_value_calculated"))

            # Resolve printed company subtotal vs lot-sum:
            # - calculated > reported: printed subtotal often incomplete (later rounds omitted)
            # - calculated < reported: lot extraction incomplete; trust printed subtotal
            current_cost, current_cost_source = _prefer_reported_or_lot_sum(
                reported_cost, calculated_cost, field="cost"
            )
            unrealized_value, unrealized_source = _prefer_reported_or_lot_sum(
                reported_fv, calculated_fv, field="fair_value"
            )
        else:
            current_cost = Decimal("0")
            unrealized_value = Decimal("0")
            current_cost_source = "assumed_zero_fully_exited"
            unrealized_source = "assumed_zero_fully_exited"

        if r is not None:
            realized_cost = r["realized_cost"]
            realized_proceeds = r["realized_proceeds"]
            realized_gain_loss = r["realized_gain_loss"]
            realized_cost_source = "pdf_realized_schedule_cost_sum"
            realized_proceeds_source = "pdf_realized_schedule_proceeds_sum"
        else:
            realized_cost = Decimal("0")
            realized_proceeds = Decimal("0")
            realized_gain_loss = Decimal("0")
            realized_cost_source = "assumed_zero_no_realized_rows"
            realized_proceeds_source = "assumed_zero_no_realized_rows"

        capital_invested = current_cost + realized_cost
        total_value = unrealized_value + realized_proceeds
        deal = derive_deal_status(
            unrealized_value=unrealized_value,
            realized_proceeds=realized_proceeds,
        )

        metrics.append(
            {
                "vendor_source_asset": vendor_name,
                "pdf_company_name": pdf_name,
                "pdf_realized_company_name": realized_name if r else None,
                "has_unrealized_schedule": u is not None,
                "has_realized_schedule": r is not None,
                "current_cost": format_decimal(current_cost),
                "current_cost_source": current_cost_source,
                "unrealized_value": format_decimal(unrealized_value),
                "unrealized_value_source": unrealized_source,
                "realized_cost": format_decimal(realized_cost),
                "realized_cost_source": realized_cost_source,
                "realized_proceeds": format_decimal(realized_proceeds),
                "realized_proceeds_source": realized_proceeds_source,
                "realized_gain_loss": format_decimal(realized_gain_loss),
                "capital_invested": format_decimal(capital_invested),
                "capital_invested_formula": "current_cost + realized_cost",
                "capital_invested_source": "derived",
                "total_value": format_decimal(total_value),
                "total_value_formula": "unrealized_value + realized_proceeds",
                "total_value_source": "derived",
                **deal,
                "entity_confirmation": entity.get("confirmation"),
                "entity_notes": entity.get("notes"),
            }
        )
    return metrics
