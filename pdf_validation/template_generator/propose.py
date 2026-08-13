"""Turn a sample PDF into a DRAFT template proposal.

This is the only module that talks to an LLM, and it runs once per fund (or
again if a GP changes their statement layout) — not once per quarter.

The output is always a draft. Nothing here can produce numbers that reach a
report: the proposal has to pass structural validation, then be replayed and
reviewed by a human, then explicitly promoted via approval.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .backends import LLMBackend
from .page_reader import find_schedule_pages, sample_for_prompt
from .schema import (
    Evidence,
    GeneratedTemplate,
    LineRule,
    VALID_CAPTURE_FIELDS,
    new_template_id,
    require_valid,
    stamp_generation,
    validate,
)

PROMPT_VERSION = "1"

SYSTEM_PROMPT = """\
You convert the text of a private-fund financial statement's holdings schedule \
into a declarative extraction template.

You do NOT extract the data yourself. You describe the LINE PATTERNS that a \
deterministic regex engine will use to extract it, every quarter, without you.

The engine walks each text line of the schedule pages in order and applies your \
rules first-match-wins. Rule kinds:

  "skip"             boilerplate to ignore (headers, footers, page numbers,
                     fund name banners, date lines, column headings, totals)
  "company_header"   a line that NAMES a portfolio company. Must capture
                     company_name. Sets the "active" company.
  "company_subtotal" a line carrying that company's TOTAL Cost and Fair Value.
                     Must capture cost and fair_value. Clears the active company.
  "position_lot"     a line carrying ONE lot/position of the active company.
                     Must capture cost and fair_value. Keeps the active company.

Rules:
- Use Python `re` syntax with NAMED GROUPS only.
- Allowed group names: {allowed}
- Order matters. Put "skip" rules first, then the most specific patterns.
- Anchor patterns (^ and $) where you can, so they do not over-match.
- Amount groups should capture the raw text including commas and $ — the engine
  normalises them. Do not try to strip formatting in the regex.
- Prefer a small number of robust rules over many brittle ones.
- If a company's total appears on its own line with no name, rely on
  company_header setting the active company first.

Return ONLY a JSON object, no prose, no markdown fence:

{{
  "schedule_pages": [6, 7, 8],
  "fund_name_hints": ["..."],
  "currency_expected": "USD",
  "unit_expected": "ones",
  "line_rules": [
    {{"rule_id": "skip_boilerplate", "kind": "skip",
      "prefixes": ["schedule of", "december 31"], "notes": "why"}},
    {{"rule_id": "company_header", "kind": "company_header",
      "pattern": "^(?P<company_name>...)\\\\s+(?P<industry>...)$", "notes": "why"}},
    {{"rule_id": "company_subtotal", "kind": "company_subtotal",
      "pattern": "^\\\\$?\\\\s*(?P<cost>[\\\\d,]+)\\\\s+\\\\$?\\\\s*(?P<fair_value>[\\\\d,]+)\\\\s*$",
      "notes": "why"}}
  ]
}}
""".format(allowed=", ".join(sorted(VALID_CAPTURE_FIELDS)))

USER_TEMPLATE = """\
Fund Allocator ID: {fund_id}
Source PDF: {pdf_name}

Below are the text lines of the holdings schedule pages, exactly as the \
extraction engine will read them (page markers included, whitespace collapsed).

Write the extraction template for THIS layout.

{sample}
"""


def _strip_fence(text: str) -> str:
    """Tolerate a ```json fence even though the prompt asks for bare JSON."""
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, flags=re.S)
    return fence.group(1).strip() if fence else text.strip()


def parse_response(raw: str) -> dict[str, Any]:
    """Parse the model's reply into a dict, with a clear error if it is not JSON."""
    cleaned = _strip_fence(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to the outermost {...} span; models occasionally add a
        # trailing sentence despite instructions.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError as exc:
                raise ValueError(f"Model response was not valid JSON: {exc}") from exc
        raise ValueError("Model response contained no JSON object.")


def collect_evidence(
    template: GeneratedTemplate,
    pdf_path: Path,
    *,
    per_rule: int = 3,
) -> list[Evidence]:
    """Record the first few real lines each rule matches, and what it captured.

    This is what makes human review tractable: instead of judging a regex in the
    abstract, the reviewer sees the actual statement line next to the values the
    rule pulled out of it.
    """
    from .page_reader import read_pages  # local import keeps module import cheap

    evidence: list[Evidence] = []
    counts: dict[str, int] = {}
    compiled = {r.rule_id: (r, r.compiled()) for r in template.line_rules}

    for pl in read_pages(pdf_path, template.schedule_pages):
        for line in pl.lines:
            for rule_id, (rule, pattern) in compiled.items():
                if rule.kind == "skip" or pattern is None:
                    continue
                if counts.get(rule_id, 0) >= per_rule:
                    continue
                m = pattern.search(line)
                if not m:
                    continue
                evidence.append(
                    Evidence(
                        rule_id=rule_id,
                        page=pl.page,
                        line=line,
                        captured={k: v for k, v in m.groupdict().items() if v is not None},
                    )
                )
                counts[rule_id] = counts.get(rule_id, 0) + 1
                break
    return evidence


def propose_template(
    *,
    fund_id: str,
    pdf_path: Path | str,
    backend: LLMBackend,
    schedule_pages: list[int] | None = None,
) -> tuple[GeneratedTemplate, list[str]]:
    """Ask the backend for a template proposal.

    Returns (template, problems). `problems` is empty when the proposal passed
    structural validation. The template is returned either way so a reviewer can
    inspect a rejected proposal instead of just seeing an error.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pages = schedule_pages or find_schedule_pages(pdf_path)
    if not pages:
        raise ValueError(
            f"Could not locate holdings schedule pages in {pdf_path.name}. "
            "Pass schedule_pages explicitly."
        )

    sample = sample_for_prompt(pdf_path, pages)
    user = USER_TEMPLATE.format(fund_id=fund_id, pdf_name=pdf_path.name, sample=sample)

    raw = backend.complete(SYSTEM_PROMPT, user)
    payload = parse_response(raw)

    rules = [
        LineRule(
            rule_id=r.get("rule_id") or f"rule_{i}",
            kind=r.get("kind", "skip"),
            pattern=r.get("pattern"),
            prefixes=r.get("prefixes") or [],
            notes=r.get("notes", ""),
        )
        for i, r in enumerate(payload.get("line_rules", []))
    ]

    template = GeneratedTemplate(
        fund_id=fund_id,
        template_id=new_template_id(fund_id, pdf_path),
        schedule_pages=payload.get("schedule_pages") or pages,
        line_rules=rules,
        fund_name_hints=payload.get("fund_name_hints") or [],
        currency_expected=payload.get("currency_expected") or "USD",
        unit_expected=payload.get("unit_expected") or "ones",
        status="draft",
    )
    stamp_generation(
        template,
        backend=backend.describe(),
        source_pdf=pdf_path,
        prompt_version=PROMPT_VERSION,
    )

    problems = validate(template)
    if not problems:
        template.evidence = collect_evidence(template, pdf_path)
    return template, problems


def propose_and_require_valid(
    *,
    fund_id: str,
    pdf_path: Path | str,
    backend: LLMBackend,
    schedule_pages: list[int] | None = None,
) -> GeneratedTemplate:
    template, _ = propose_template(
        fund_id=fund_id, pdf_path=pdf_path, backend=backend, schedule_pages=schedule_pages
    )
    require_valid(template)
    return template
