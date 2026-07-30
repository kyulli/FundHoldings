"""Row classification and cross-page active_company state machine."""

from __future__ import annotations

import re
from typing import Any

from pdf_validation.normalization import (
    clean_cell_text,
    is_blank,
    is_currency_token,
    is_dash,
    parse_reported_number,
)
from pdf_validation.schemas import empty_deal_status


def _row_texts(row: list[str]) -> list[str]:
    return [clean_cell_text(cell) for cell in row]


def _is_header_row(texts: list[str], header_patterns: list[str]) -> bool:
    joined = " ".join(t.lower() for t in texts if t)
    if not joined:
        return False
    hits = sum(1 for pattern in header_patterns if pattern in joined)
    return hits >= 3


def _is_ignore_noise(texts: list[str], ignore_patterns: list[str]) -> bool:
    joined = " ".join(t for t in texts if t).strip()
    if not joined:
        return True
    for pattern in ignore_patterns:
        if re.fullmatch(pattern, joined, flags=re.IGNORECASE):
            return True
    # Lone Unrealized / September date fragments.
    if len([t for t in texts if t]) <= 2 and re.fullmatch(r"unrealized|september\s+30,\s*2025", joined, re.I):
        return True
    return False


def _nonempty(texts: list[str], blank_tokens: list[str], currency_tokens: list[str]) -> list[str]:
    out = []
    for text in texts:
        if is_blank(text, blank_tokens):
            continue
        if is_currency_token(text, currency_tokens):
            continue
        out.append(text)
    return out


def _has_date(text: str, date_pattern: str) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return False
    if re.fullmatch(date_pattern, cleaned):
        return True
    # Camelot stream often glues the next token onto the date cell ("3/8/2024Se").
    return bool(re.match(r"^\d{1,2}/\d{1,2}/\d{4}", cleaned))


def classify_investment_tables(
    raw_tables: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    row_cfg = config["row_classification"]
    headers_cfg = config["headers"]
    field_semantics = config["field_semantics"]
    date_pattern = row_cfg["date_pattern"]
    blank_tokens = row_cfg["blank_tokens"]
    dash_tokens = row_cfg["dash_tokens"]
    currency_tokens = row_cfg["currency_tokens"]
    column_names = config["camelot"]["investments"]["column_names"]

    active_company: str | None = None
    active_company_page: int | None = None
    classified_rows: list[dict[str, Any]] = []
    lots: list[dict[str, Any]] = []
    company_events: list[dict[str, Any]] = []

    # Process pages in order.
    tables_sorted = sorted(raw_tables, key=lambda t: (t["page"], t.get("table_order", 1)))

    for table in tables_sorted:
        page = table["page"]
        records = table["dataframe_records"]
        cells = table["cells"]
        cell_lookup = {(c["row_index"], c["col_index"]): c for c in cells}

        for row_idx, row in enumerate(records):
            texts = _row_texts(row)
            expected_cols = max(len(column_names), 9)
            while len(texts) < expected_cols:
                texts.append("")

            # Canonical column access via configured names when possible.
            name_to_idx = {name: idx for idx, name in enumerate(column_names)}

            def col(*names: str, default_idx: int = 0) -> str:
                for name in names:
                    if name in name_to_idx:
                        return texts[name_to_idx[name]]
                return texts[default_idx] if default_idx < len(texts) else ""

            company_text = col("company_name", "company", "entity_name", default_idx=0)
            date_text = col("date", "investment_date", default_idx=1)
            round_text = col("round", "security_description", default_idx=2)
            shares_text = col("shares", default_idx=3)
            cost_share_text = col("cost_per_share", default_idx=4)
            fmv_share_text = col("fmv_per_share", default_idx=5)
            # Amount columns: support both 9-col VC layout and compact layouts.
            if "cost" in name_to_idx and name_to_idx["cost"] < 6:
                cost_text = col("cost", default_idx=3)
                fv_text = col("fair_value", default_idx=4)
                gain_text = col("unrealized_gain_loss", default_idx=5)
            else:
                cost_text = col("cost", default_idx=6)
                fv_text = col("fair_value", default_idx=7)
                gain_text = col("unrealized_gain_loss", default_idx=8)

            def bbox_for(col_idx: int) -> list[float] | None:
                cell = cell_lookup.get((row_idx, col_idx))
                return cell.get("bbox") if cell else None

            base = {
                "schedule": "schedule_of_investments",
                "parser": table["parser"],
                "page": page,
                "table_order": table.get("table_order"),
                "row_index": row_idx,
                "raw_cells": texts,
            }

            if _is_header_row(texts, headers_cfg["header_token_patterns"]):
                classified_rows.append({**base, "row_type": "repeated_header", "active_company_before": active_company})
                continue

            if _is_ignore_noise(texts, headers_cfg["ignore_row_patterns"]):
                classified_rows.append({**base, "row_type": "blank_or_noise", "active_company_before": active_company})
                continue

            nonempty = _nonempty(texts, blank_tokens, currency_tokens)
            company_blank = is_blank(company_text, blank_tokens)
            date_present = _has_date(date_text, date_pattern)
            identity_blank = company_blank and is_blank(date_text, blank_tokens) and is_blank(round_text, blank_tokens)
            amounts_present = any(
                not is_blank(t, blank_tokens) and not is_currency_token(t, currency_tokens)
                for t in (cost_text, fv_text, gain_text)
            )

            # Grand total: amounts present, company empty/total, no date/round/shares.
            company_token = company_text.strip().lower()
            if (
                amounts_present
                and identity_blank
                and is_blank(shares_text, blank_tokens)
                and (company_token in {"", "total"} or "$" in cost_text or "$" in fv_text)
                and not date_present
            ):
                # Prefer detecting the known schedule grand total by presence of $ on amount cols.
                if any("$" in t for t in (cost_text, fv_text, gain_text)) or company_token == "total":
                    active_company = None
                    active_company_page = None
                    classified_rows.append({**base, "row_type": "grand_total", "active_company_before": None})
                    company_events.append(
                        {
                            "event": "grand_total",
                            "page": page,
                            "row_index": row_idx,
                            "cost_raw": cost_text,
                            "fair_value_raw": fv_text,
                            "unrealized_gain_loss_raw": gain_text,
                            "cost_bbox": bbox_for(6),
                            "fair_value_bbox": bbox_for(7),
                            "unrealized_gain_loss_bbox": bbox_for(8),
                        }
                    )
                    continue

            # Company subtotal / page continuation subtotal:
            # no date, no round, no shares; amounts present; company blank.
            if amounts_present and identity_blank and is_blank(shares_text, blank_tokens) and not date_present:
                row_type = "page_continuation" if active_company and page != active_company_page else "company_subtotal"
                # If first data row on a continuation page with active company from previous page,
                # this is the prior company's delayed subtotal.
                if active_company and page != active_company_page and row_idx <= 3:
                    row_type = "page_continuation"

                event_company = active_company
                classified_rows.append(
                    {
                        **base,
                        "row_type": row_type,
                        "company_name": event_company,
                        "active_company_before": active_company,
                    }
                )
                company_events.append(
                    {
                        "event": row_type,
                        "company_name": event_company,
                        "page": page,
                        "row_index": row_idx,
                        "cost_raw": cost_text,
                        "fair_value_raw": fv_text,
                        "unrealized_gain_loss_raw": gain_text,
                        "cost_bbox": bbox_for(6),
                        "fair_value_bbox": bbox_for(7),
                        "unrealized_gain_loss_bbox": bbox_for(8),
                    }
                )
                # Close active company after its subtotal/continuation subtotal.
                active_company = None
                active_company_page = None
                continue

            # Investment lot
            if date_present or (not company_blank and not is_blank(round_text, blank_tokens)):
                if not company_blank:
                    active_company = company_text
                    active_company_page = page
                elif active_company is None:
                    classified_rows.append(
                        {
                            **base,
                            "row_type": "blank_or_noise",
                            "issue": "lot_without_active_company",
                        }
                    )
                    continue

                lot_company = active_company
                lot = {
                    "row_type": "investment_lot",
                    "company_name_raw": company_text if not company_blank else "",
                    "company_name": lot_company,
                    "company_name_inherited": company_blank,
                    "date_raw": date_text,
                    "round_raw": round_text,
                    "page": page,
                    "table_order": table.get("table_order"),
                    "row_index": row_idx,
                    "parser": table["parser"],
                    **empty_deal_status(),
                }

                for field, text, col_idx in (
                    ("shares", shares_text, 3),
                    ("cost_per_share", cost_share_text, 4),
                    ("fmv_per_share", fmv_share_text, 5),
                    ("cost", cost_text, 6),
                    ("fair_value", fv_text, 7),
                    ("unrealized_gain_loss", gain_text, 8),
                ):
                    lot.update(
                        parse_reported_number(
                            text,
                            field=field,
                            field_semantics=field_semantics,
                            dash_tokens=dash_tokens,
                            blank_tokens=blank_tokens,
                            source_page=page,
                            source_bbox=bbox_for(col_idx),
                        )
                    )

                lots.append(lot)
                classified_rows.append(
                    {
                        **base,
                        "row_type": "investment_lot",
                        "company_name": lot_company,
                        "active_company_before": lot_company,
                    }
                )
                active_company_page = page
                continue

            classified_rows.append(
                {
                    **base,
                    "row_type": "blank_or_noise",
                    "active_company_before": active_company,
                    "nonempty": nonempty,
                }
            )

    return {
        "classified_rows": classified_rows,
        "investment_lots": lots,
        "company_events": company_events,
    }


def classify_realized_tables(
    raw_tables: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    row_cfg = config["row_classification"]
    headers_cfg = config["headers"]
    field_semantics = config["field_semantics"]
    blank_tokens = row_cfg["blank_tokens"]
    dash_tokens = row_cfg["dash_tokens"]
    currency_tokens = row_cfg["currency_tokens"]
    column_names = config["camelot"]["realized"]["column_names"]

    lots: list[dict[str, Any]] = []
    classified_rows: list[dict[str, Any]] = []
    totals: list[dict[str, Any]] = []

    tables_sorted = sorted(raw_tables, key=lambda t: (t["page"], t.get("table_order", 1)))
    for table in tables_sorted:
        page = table["page"]
        records = table["dataframe_records"]
        cells = table["cells"]
        cell_lookup = {(c["row_index"], c["col_index"]): c for c in cells}

        for row_idx, row in enumerate(records):
            texts = _row_texts(row)
            while len(texts) < len(column_names):
                texts.append("")
            texts = texts[: len(column_names)]

            def bbox_for(col_idx: int) -> list[float] | None:
                cell = cell_lookup.get((row_idx, col_idx))
                return cell.get("bbox") if cell else None

            base = {
                "schedule": "schedule_of_realized",
                "parser": table["parser"],
                "page": page,
                "table_order": table.get("table_order"),
                "row_index": row_idx,
                "raw_cells": texts,
            }

            if _is_header_row(texts, headers_cfg["header_token_patterns"]):
                classified_rows.append({**base, "row_type": "repeated_header"})
                continue
            if _is_ignore_noise(texts, headers_cfg["ignore_row_patterns"]):
                classified_rows.append({**base, "row_type": "blank_or_noise"})
                continue

            company = texts[0]
            if company.strip().lower() == "total":
                classified_rows.append({**base, "row_type": "grand_total"})
                totals.append(
                    {
                        "page": page,
                        "row_index": row_idx,
                        "cost_raw": texts[4],
                        "cash_proceeds_raw": texts[5],
                        "escrow_receivable_raw": texts[6],
                        "realized_gain_loss_raw": texts[7],
                        "cost_bbox": bbox_for(4),
                        "cash_proceeds_bbox": bbox_for(5),
                        "escrow_receivable_bbox": bbox_for(6),
                        "realized_gain_loss_bbox": bbox_for(7),
                    }
                )
                continue

            if is_blank(company, blank_tokens) and all(
                is_blank(t, blank_tokens) or is_currency_token(t, currency_tokens) or is_dash(t, dash_tokens)
                for t in texts[1:]
            ):
                classified_rows.append({**base, "row_type": "blank_or_noise"})
                continue

            if is_blank(company, blank_tokens):
                classified_rows.append({**base, "row_type": "blank_or_noise"})
                continue

            lot = {
                "row_type": "realized_lot",
                "company_name": company,
                "purchase_date_raw": texts[1],
                "exit_date_raw": texts[2],
                "transaction_raw": texts[3],
                "page": page,
                "table_order": table.get("table_order"),
                "row_index": row_idx,
                "parser": table["parser"],
                **empty_deal_status(),
            }
            for field, text, col_idx in (
                ("cost", texts[4], 4),
                ("cash_proceeds", texts[5], 5),
                ("escrow_receivable", texts[6], 6),
                ("realized_gain_loss", texts[7], 7),
            ):
                lot.update(
                    parse_reported_number(
                        text,
                        field=field,
                        field_semantics=field_semantics,
                        dash_tokens=dash_tokens,
                        blank_tokens=blank_tokens,
                        source_page=page,
                        source_bbox=bbox_for(col_idx),
                    )
                )
            lots.append(lot)
            classified_rows.append({**base, "row_type": "investment_lot", "company_name": company})

    return {
        "classified_rows": classified_rows,
        "realized_lots": lots,
        "realized_totals": totals,
    }


def build_company_summary(
    investment_lots: list[dict[str, Any]],
    company_events: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    from collections import OrderedDict
    from decimal import Decimal

    from pdf_validation.normalization import decimal_or_none, format_decimal, parse_reported_number

    field_semantics = config["field_semantics"]
    dash_tokens = config["row_classification"]["dash_tokens"]
    blank_tokens = config["row_classification"]["blank_tokens"]
    tolerance = Decimal(config["reconciliation"]["tolerance_absolute"])

    lot_agg: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for lot in investment_lots:
        company = lot["company_name"]
        bucket = lot_agg.setdefault(
            company,
            {
                "company_name": company,
                "lot_count": 0,
                "pages": set(),
                "cost_calculated": Decimal("0"),
                "fair_value_calculated": Decimal("0"),
                "unrealized_gain_loss_calculated": Decimal("0"),
            },
        )
        bucket["lot_count"] += 1
        bucket["pages"].add(lot["page"])
        for field, key in (
            ("cost", "cost_calculated"),
            ("fair_value", "fair_value_calculated"),
            ("unrealized_gain_loss", "unrealized_gain_loss_calculated"),
        ):
            value = decimal_or_none(lot.get(f"{field}_normalized"))
            if value is not None and lot.get(f"{field}_parse_status") in {"ok", "zero", "dash"}:
                bucket[key] += value

    reported_by_company: dict[str, dict[str, Any]] = {}
    for event in company_events:
        if event["event"] not in {"company_subtotal", "page_continuation"}:
            continue
        company = event.get("company_name")
        if not company:
            continue
        parsed = {}
        for field, raw_key, bbox_key in (
            ("cost", "cost_raw", "cost_bbox"),
            ("fair_value", "fair_value_raw", "fair_value_bbox"),
            ("unrealized_gain_loss", "unrealized_gain_loss_raw", "unrealized_gain_loss_bbox"),
        ):
            parsed.update(
                parse_reported_number(
                    event[raw_key],
                    field=field,
                    field_semantics=field_semantics,
                    dash_tokens=dash_tokens,
                    blank_tokens=blank_tokens,
                    source_page=event["page"],
                    source_bbox=event.get(bbox_key),
                )
            )
        reported_by_company[company] = {
            "company_name": company,
            "subtotal_event": event["event"],
            "subtotal_page": event["page"],
            "subtotal_row_index": event["row_index"],
            **parsed,
        }

    summaries: list[dict[str, Any]] = []
    for company, calc in lot_agg.items():
        reported = reported_by_company.get(company, {})
        pages = set(calc["pages"])
        if reported.get("subtotal_page") is not None:
            pages.add(reported["subtotal_page"])
        row = {
            "company_name": company,
            "lot_count": calc["lot_count"],
            "pages": sorted(pages),
            "cost_calculated": format_decimal(calc["cost_calculated"]),
            "fair_value_calculated": format_decimal(calc["fair_value_calculated"]),
            "unrealized_gain_loss_calculated": format_decimal(calc["unrealized_gain_loss_calculated"]),
            "cost_reported_raw": reported.get("cost_raw"),
            "cost_reported_normalized": reported.get("cost_normalized"),
            "fair_value_reported_raw": reported.get("fair_value_raw"),
            "fair_value_reported_normalized": reported.get("fair_value_normalized"),
            "unrealized_gain_loss_reported_raw": reported.get("unrealized_gain_loss_raw"),
            "unrealized_gain_loss_reported_normalized": reported.get("unrealized_gain_loss_normalized"),
            "subtotal_event": reported.get("subtotal_event"),
            "subtotal_page": reported.get("subtotal_page"),
            "subtotal_row_index": reported.get("subtotal_row_index"),
            **empty_deal_status(),
        }

        for field in ("cost", "fair_value", "unrealized_gain_loss"):
            reported_value = decimal_or_none(row.get(f"{field}_reported_normalized"))
            calculated_value = decimal_or_none(row.get(f"{field}_calculated"))
            if reported_value is None or calculated_value is None:
                row[f"{field}_difference"] = None
                row[f"{field}_status"] = "FAIL"
                continue
            diff = calculated_value - reported_value
            row[f"{field}_difference"] = format(diff, "f")
            row[f"{field}_status"] = "PASS" if abs(diff) <= tolerance else "FAIL"

        summaries.append(row)

    return summaries
