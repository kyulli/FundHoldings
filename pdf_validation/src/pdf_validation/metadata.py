"""Cover/statement metadata extraction from the PDF."""

from __future__ import annotations

import re
from typing import Any

import pdfplumber

from pdf_validation.normalization import clean_cell_text


def _find_phrase(
    page: pdfplumber.page.Page,
    pattern: str,
    *,
    page_number: int,
    field: str,
) -> dict[str, Any]:
    words = page.extract_words() or []
    text = " ".join(w["text"] for w in words)
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return {
            "field": field,
            "raw": None,
            "normalized": None,
            "source_page": page_number,
            "source_bbox": None,
            "parse_status": "not_disclosed",
        }

    cursor = 0
    spans: list[tuple[int, int, dict[str, Any]]] = []
    for index, word in enumerate(words):
        if index:
            cursor += 1
        start = cursor
        end = cursor + len(word["text"])
        spans.append((start, end, word))
        cursor = end

    matched = [word for start, end, word in spans if not (end <= match.start() or start >= match.end())]
    bbox = None
    if matched:
        bbox = [
            float(min(w["x0"] for w in matched)),
            float(min(w["top"] for w in matched)),
            float(max(w["x1"] for w in matched)),
            float(max(w["bottom"] for w in matched)),
        ]

    return {
        "field": field,
        "raw": match.group(0),
        "normalized": clean_cell_text(match.group(0)),
        "source_page": page_number,
        "source_bbox": bbox,
        "parse_status": "ok",
    }


def extract_metadata(pdf_path: str, config: dict[str, Any]) -> dict[str, Any]:
    patterns = config["metadata_patterns"]
    pages_cfg = config["pages"]
    fields: list[dict[str, Any]] = []

    with pdfplumber.open(pdf_path) as pdf:
        cover = pdf.pages[pages_cfg["cover"] - 1]
        assets = pdf.pages[pages_cfg["statement_of_assets"] - 1]

        fund = _find_phrase(cover, patterns["fund_name"], page_number=pages_cfg["cover"], field="fund_name")
        report_type = _find_phrase(
            cover, patterns["report_type"], page_number=pages_cfg["cover"], field="report_type"
        )
        as_of = _find_phrase(cover, patterns["as_of_date"], page_number=pages_cfg["cover"], field="as_of_date")
        audit = _find_phrase(
            cover, patterns["audit_status"], page_number=pages_cfg["cover"], field="audit_status"
        )

        if as_of["parse_status"] == "ok":
            as_of["as_of_date_raw"] = as_of["raw"]
            # Prefer template expected date when the matched raw date corresponds to it.
            expected = config.get("document", {}).get("as_of_date_expected")
            as_of["normalized"] = expected or as_of["normalized"]

        fields.extend([fund, report_type, as_of, audit])

        currency_words = [
            w for w in (assets.extract_words() or []) if w["text"] in patterns.get("currency_hint_tokens", ["$"])
        ]
        if currency_words:
            word = currency_words[0]
            fields.append(
                {
                    "field": "currency_symbol",
                    "raw": word["text"],
                    "normalized": word["text"],
                    "source_page": pages_cfg["statement_of_assets"],
                    "source_bbox": [
                        float(word["x0"]),
                        float(word["top"]),
                        float(word["x1"]),
                        float(word["bottom"]),
                    ],
                    "parse_status": "ok",
                }
            )
        else:
            fields.append(
                {
                    "field": "currency_symbol",
                    "raw": None,
                    "normalized": None,
                    "source_page": pages_cfg["statement_of_assets"],
                    "source_bbox": None,
                    "parse_status": "not_disclosed",
                }
            )

        # Currency code and unit are not explicitly labeled in this PDF.
        fields.append(
            {
                "field": "currency",
                "raw": None,
                "normalized": None,
                "source_page": None,
                "source_bbox": None,
                "parse_status": "not_disclosed",
                "template_expected": config["document"].get("currency_expected"),
            }
        )
        fields.append(
            {
                "field": "unit",
                "raw": None,
                "normalized": None,
                "source_page": None,
                "source_bbox": None,
                "parse_status": "not_disclosed",
                "template_expected": config["document"].get("unit_expected"),
            }
        )

        fields.append(
            {
                "field": "statement_of_assets_text",
                "raw": assets.extract_text() or "",
                "normalized": None,
                "source_page": pages_cfg["statement_of_assets"],
                "source_bbox": None,
                "parse_status": "ok",
            }
        )

    return {
        "fields": fields,
        "document_expected": config.get("document", {}),
    }
