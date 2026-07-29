"""Numeric and text normalization for reported PDF values."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from pdf_validation.schemas import numeric_field_bundle

_CURRENCY_RE = re.compile(r"[$,%\s]")


def clean_cell_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\n", " ").replace("\xa0", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def is_blank(text: str, blank_tokens: list[str]) -> bool:
    return text.strip().lower() in {t.lower() for t in blank_tokens}


def is_dash(text: str, dash_tokens: list[str]) -> bool:
    compact = re.sub(r"\s+", "", text)
    return compact in dash_tokens or text.strip() in dash_tokens


def is_currency_token(text: str, currency_tokens: list[str]) -> bool:
    return text.strip() in currency_tokens


def parse_reported_number(
    value: Any,
    *,
    field: str,
    field_semantics: dict[str, dict[str, str]],
    dash_tokens: list[str],
    blank_tokens: list[str],
    source_page: int | None,
    source_bbox: list[float] | None,
) -> dict[str, Any]:
    raw = clean_cell_text(value)
    semantics = field_semantics.get(field, {})
    dash_means = semantics.get("dash_means", "not_disclosed")

    if is_blank(raw, blank_tokens):
        return numeric_field_bundle(
            field=field,
            raw=raw,
            normalized=None,
            parse_status="blank",
            source_page=source_page,
            source_bbox=source_bbox,
        )

    if is_dash(raw, dash_tokens):
        if dash_means == "zero":
            return numeric_field_bundle(
                field=field,
                raw=raw,
                normalized="0",
                parse_status="dash",
                source_page=source_page,
                source_bbox=source_bbox,
            )
        return numeric_field_bundle(
            field=field,
            raw=raw,
            normalized=None,
            parse_status="dash",
            source_page=source_page,
            source_bbox=source_bbox,
        )

    # Remove currency symbols/commas/whitespace first, then interpret accounting negatives.
    text = _CURRENCY_RE.sub("", raw)
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = text.strip()
    if text in {"", "-"}:
        return numeric_field_bundle(
            field=field,
            raw=raw,
            normalized=None,
            parse_status="parse_error",
            source_page=source_page,
            source_bbox=source_bbox,
        )

    try:
        number = Decimal(text)
    except InvalidOperation:
        return numeric_field_bundle(
            field=field,
            raw=raw,
            normalized=None,
            parse_status="parse_error",
            source_page=source_page,
            source_bbox=source_bbox,
        )

    if negative:
        number = -number

    status = "zero" if number == 0 else "ok"
    normalized = format_decimal(number)
    return numeric_field_bundle(
        field=field,
        raw=raw,
        normalized=normalized,
        parse_status=status,
        source_page=source_page,
        source_bbox=source_bbox,
    )


def format_decimal(number: Decimal) -> str:
    """Format Decimal without scientific notation, preserving integer trailing zeros."""
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None
