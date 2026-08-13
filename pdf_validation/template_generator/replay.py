"""Deterministic extraction from an approved template. No LLM in this path.

This is the code that actually runs every quarter. Given a template and a PDF,
it walks the schedule pages line by line, applies the template's ordered rules
(first match wins), and emits company-level rows in exactly the shape
src/pdf_validation/text_fallback.py::_company_row produces — so the output
drops straight into the existing reconciliation and vendor-comparison stages
without touching either.

Determinism is the whole point: same PDF plus same template always yields the
same numbers, which is what makes the results auditable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .page_reader import read_pages
from .schema import GeneratedTemplate, LineRule

# Matches a trailing negative expressed as parentheses, e.g. "(1,234)".
_PAREN_NEG = re.compile(r"^\((.*)\)$")


def normalize_amount(raw: str | None) -> str | None:
    """'$ 13,466,158' -> '13466158' ; '(1,234)' -> '-1234' ; '-' -> '0'."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text in {"", "-", "—", "–"}:
        return "0"
    negative = False
    m = _PAREN_NEG.match(text)
    if m:
        negative = True
        text = m.group(1)
    if text.strip().startswith("-"):
        negative = True
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    return f"-{digits}" if negative else digits


def _company_row(
    name: str,
    page_num: int,
    cost: str | None,
    fv: str | None,
    gain_raw: str | None,
    gain: str | None,
    cost_raw: str | None,
    fv_raw: str | None,
    *,
    rule_id: str,
    source_line: str,
) -> dict[str, Any]:
    """Mirror of text_fallback._company_row, plus provenance fields.

    The extra rule_id / source_line make every emitted number traceable back to
    the exact template rule and PDF line that produced it, which is what a
    reviewer needs when a comparison later flags a mismatch.
    """
    return {
        "company_name": name,
        "entity_grain": "company",
        "lot_count": 0,
        "pages": [page_num],
        "cost_calculated": None,
        "fair_value_calculated": None,
        "unrealized_gain_loss_calculated": None,
        "cost_reported_raw": cost_raw,
        "cost_reported_normalized": cost,
        "fair_value_reported_raw": fv_raw,
        "fair_value_reported_normalized": fv,
        "unrealized_gain_loss_reported_raw": gain_raw,
        "unrealized_gain_loss_reported_normalized": gain,
        "subtotal_event": "company_subtotal_generated_template",
        "subtotal_page": page_num,
        "cost_status": "PASS",
        "fair_value_status": "PASS",
        "extracted_by_rule": rule_id,
        "source_line": source_line,
    }


@dataclass
class ReplayResult:
    company_summary: list[dict[str, Any]] = field(default_factory=list)
    unmatched_lines: list[dict[str, Any]] = field(default_factory=list)
    rule_hit_counts: dict[str, int] = field(default_factory=dict)
    pages_read: list[int] = field(default_factory=list)

    @property
    def company_count(self) -> int:
        return len(self.company_summary)

    def coverage(self) -> float:
        """Share of non-skipped lines that some rule matched.

        Low coverage is the signal that a template has gone stale — e.g. the GP
        changed the statement layout — and should be regenerated.
        """
        matched = sum(self.rule_hit_counts.values())
        total = matched + len(self.unmatched_lines)
        return matched / total if total else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "companies": self.company_count,
            "pages_read": self.pages_read,
            "rule_hits": self.rule_hit_counts,
            "unmatched_line_count": len(self.unmatched_lines),
            "coverage": round(self.coverage(), 4),
        }


def _matches_skip(rule: LineRule, text: str, low: str) -> bool:
    if rule.prefixes and any(low.startswith(p.lower()) for p in rule.prefixes):
        return True
    compiled = rule.compiled()
    return bool(compiled and compiled.search(text))


def replay(template: GeneratedTemplate, pdf_path: Path | str) -> ReplayResult:
    """Execute a template against a PDF. Pure function of (template, PDF)."""
    result = ReplayResult()
    compiled: dict[str, re.Pattern[str] | None] = {
        r.rule_id: r.compiled() for r in template.line_rules
    }
    active: str | None = None

    for pl in read_pages(pdf_path, template.schedule_pages):
        result.pages_read.append(pl.page)
        for text in pl.lines:
            low = text.lower()
            handled = False

            for rule in template.line_rules:
                if rule.kind == "skip":
                    if _matches_skip(rule, text, low):
                        handled = True
                        result.rule_hit_counts[rule.rule_id] = (
                            result.rule_hit_counts.get(rule.rule_id, 0) + 1
                        )
                        break
                    continue

                pattern = compiled.get(rule.rule_id)
                if pattern is None:
                    continue
                m = pattern.search(text)
                if not m:
                    continue

                captured = {k: v for k, v in m.groupdict().items() if v is not None}
                result.rule_hit_counts[rule.rule_id] = (
                    result.rule_hit_counts.get(rule.rule_id, 0) + 1
                )
                handled = True

                if rule.kind == "company_header":
                    name = (captured.get("company_name") or "").strip()
                    if name:
                        active = name
                    break

                # company_subtotal / position_lot both emit a row.
                cost_raw = captured.get("cost")
                fv_raw = captured.get("fair_value")
                gain_raw = captured.get("unrealized_gain_loss")
                name = (captured.get("company_name") or active or "").strip()

                if not name:
                    # Amounts with no company attached would be unattributable;
                    # record rather than guess.
                    result.unmatched_lines.append(
                        {"page": pl.page, "line": text, "reason": "amounts_without_active_company"}
                    )
                    break

                row = _company_row(
                    name,
                    pl.page,
                    normalize_amount(cost_raw),
                    normalize_amount(fv_raw),
                    gain_raw,
                    normalize_amount(gain_raw) if gain_raw else None,
                    cost_raw,
                    fv_raw,
                    rule_id=rule.rule_id,
                    source_line=text,
                )
                if "shares" in captured:
                    row["shares_reported_normalized"] = normalize_amount(captured["shares"])
                result.company_summary.append(row)

                if rule.kind == "company_subtotal":
                    active = None
                break

            if not handled:
                result.unmatched_lines.append(
                    {"page": pl.page, "line": text, "reason": "no_rule_matched"}
                )

    return result


def replay_from_file(template_path: Path | str, pdf_path: Path | str) -> ReplayResult:
    return replay(GeneratedTemplate.load(Path(template_path)), pdf_path)
