"""Cross-schedule Deal Status inference.

Rules derived from standard private fund financial statement structure:

    Schedule of Investments (pages 3-5) = currently held positions
    Schedule of Realized Gain/(Loss)    = positions that have been (partially or fully) sold

Inference logic:
    Company in Investments only                          -> Current (or Written Down / Written Off)
    Company in both Investments and Realized             -> Partially Exited (or Partially Exited, Remainder Written Down)
    Company in Realized only                             -> Fully Exited

Written Down / Written Off thresholds apply only to companies still in Investments:
    FMV / Cost == 0 or FMV == 0                         -> Written Off
    FMV / Cost < WRITTEN_DOWN_RATIO                      -> Written Down

Company name normalization uses entity_aliases.json to resolve spelling variants
across the two schedules (e.g. "Oomnitz" in Realized vs "Oomnitza, Inc." in Investments).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pdf_validation.entity_mapping import load_aliases, resolve_alias

WRITTEN_DOWN_RATIO = Decimal("0.25")


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def _canonical(name: str, *, fund_id: str | None = None, aliases: dict[str, Any] | None = None) -> str:
    """Return the canonical company name using entity aliases, falling back to the raw name."""
    resolved = resolve_alias(name, fund_id=fund_id, aliases=aliases)
    return resolved if resolved else name


def infer_deal_statuses(
    company_summary: list[dict[str, Any]],
    realized_lots: list[dict[str, Any]],
    *,
    fund_id: str | None = None,
    aliases: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Infer Deal Status for each company in company_summary.

    Cross-references Schedule of Investments (company_summary rows) against
    Schedule of Realized Gain/(Loss) (realized_lots rows) using canonical names.

    Parameters
    ----------
    company_summary:
        Output of the investment schedule parser — one row per company.
    realized_lots:
        Output of the realized schedule parser — one row per lot/transaction.
    fund_id:
        Optional fund identifier for fund-scoped alias lookups.
    aliases:
        Pre-loaded alias dict. Loaded from entity_aliases.json if None.

    Returns
    -------
    Updated company_summary list with deal_status_inferred, inference_rule,
    inference_evidence, and inference_confidence populated.
    """
    aliases = aliases or load_aliases()

    realized_canonical: set[str] = {
        _canonical(lot["company_name"], fund_id=fund_id, aliases=aliases)
        for lot in realized_lots
        if lot.get("company_name")
    }

    updated: list[dict[str, Any]] = []
    for company in company_summary:
        name = company.get("company_name", "")
        canonical = _canonical(name, fund_id=fund_id, aliases=aliases)

        cost = _decimal_or_none(company.get("cost_reported_normalized"))
        fv = _decimal_or_none(company.get("fair_value_reported_normalized"))
        in_realized = canonical in realized_canonical

        if in_realized:
            # Company sold at least some shares — check if remainder is impaired
            if cost and fv is not None and cost > 0:
                ratio = fv / cost
                if fv == 0 or ratio == 0:
                    status = "Partially Exited, Remainder Written Down"
                    rule = "in_both_schedules_remainder_fmv_zero"
                elif ratio < WRITTEN_DOWN_RATIO:
                    status = "Partially Exited, Remainder Written Down"
                    rule = "in_both_schedules_remainder_below_threshold"
                else:
                    status = "Partially Exited"
                    rule = "in_both_schedules"
            else:
                status = "Partially Exited"
                rule = "in_both_schedules_no_fmv"
        else:
            # Company only in Schedule of Investments
            if cost and fv is not None and cost > 0:
                ratio = fv / cost
                if fv == 0 or ratio == 0:
                    status = "Written Off"
                    rule = "investments_only_fmv_zero"
                elif ratio < WRITTEN_DOWN_RATIO:
                    status = "Written Down"
                    rule = "investments_only_fmv_below_threshold"
                else:
                    status = "Current"
                    rule = "investments_only"
            else:
                status = "Current"
                rule = "investments_only_no_fmv"

        row = dict(company)
        row["deal_status_inferred"] = status
        row["inference_rule"] = rule
        row["inference_evidence"] = (
            f"canonical_name={canonical!r}; in_realized_schedule={in_realized}"
        )
        row["inference_confidence"] = 0.90
        updated.append(row)

    return updated


def infer_realized_deal_statuses(
    realized_lots: list[dict[str, Any]],
    company_summary: list[dict[str, Any]],
    *,
    fund_id: str | None = None,
    aliases: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Infer Deal Status for each realized lot.

    If the company also appears in Schedule of Investments -> Partially Exited.
    If it appears only in Schedule of Realized             -> Fully Exited.

    Parameters
    ----------
    realized_lots:
        Output of the realized schedule parser.
    company_summary:
        Output of the investment schedule parser (used to check current holdings).
    fund_id:
        Optional fund identifier for fund-scoped alias lookups.
    aliases:
        Pre-loaded alias dict. Loaded from entity_aliases.json if None.

    Returns
    -------
    Updated realized_lots list with deal_status_inferred populated.
    """
    aliases = aliases or load_aliases()

    inv_canonical: set[str] = {
        _canonical(c["company_name"], fund_id=fund_id, aliases=aliases)
        for c in company_summary
        if c.get("company_name")
    }

    updated: list[dict[str, Any]] = []
    for lot in realized_lots:
        name = lot.get("company_name", "")
        canonical = _canonical(name, fund_id=fund_id, aliases=aliases)
        in_investments = canonical in inv_canonical

        if in_investments:
            status = "Partially Exited"
            rule = "in_both_schedules"
        else:
            status = "Fully Exited"
            rule = "realized_only"

        row = dict(lot)
        row["deal_status_inferred"] = status
        row["inference_rule"] = rule
        row["inference_evidence"] = (
            f"canonical_name={canonical!r}; in_investment_schedule={in_investments}"
        )
        row["inference_confidence"] = 0.90
        updated.append(row)

    return updated
