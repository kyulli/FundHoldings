"""Schema for LLM-generated extraction templates.

A template is DATA, not code. It describes how to turn the text lines of a
fund's schedule pages into company-level Cost / Fair Value rows, using ordered
line rules with named-group regexes.

This matters for two reasons:

  1. Auditability. A reviewer can read the regex and the captured evidence and
     decide whether it is right. There is no generated Python to audit.
  2. Determinism. Once approved, replay.py executes these rules with no LLM in
     the loop, so the same PDF always produces the same numbers.

The shape deliberately mirrors what the hand-written parsers in
src/pdf_validation/text_fallback.py do today (skip prefixes, a company-header
regex, a subtotal regex, a lot regex). The difference is that those live as
~100 lines of bespoke Python per fund; this expresses the same thing as
reviewable configuration.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = "1.0.0"

# Rule kinds, evaluated in array order with first-match-wins semantics.
#   skip             -> line is boilerplate; ignore it entirely
#   company_header   -> line names a company; becomes the "active" company
#   company_subtotal -> line carries the company's total Cost/FV; emits a row
#                       and clears the active company
#   position_lot     -> line carries one lot/position; emits a row and keeps
#                       the active company (a company may have several lots)
RuleKind = Literal["skip", "company_header", "company_subtotal", "position_lot"]

VALID_RULE_KINDS = {"skip", "company_header", "company_subtotal", "position_lot"}

# Named groups a rule is allowed to capture. Anything else is rejected at
# validation time so a hallucinated field name cannot silently reach the
# comparison stage.
VALID_CAPTURE_FIELDS = {
    "company_name",
    "cost",
    "fair_value",
    "unrealized_gain_loss",
    "shares",
    "industry",
    "geography",
    "security_type",
    "date",
}


@dataclass
class LineRule:
    """One ordered rule applied to a text line."""

    rule_id: str
    kind: RuleKind
    pattern: str | None = None           # regex with named groups (skip rules may omit)
    prefixes: list[str] = field(default_factory=list)  # for kind="skip"
    notes: str = ""

    def compiled(self) -> re.Pattern[str] | None:
        if not self.pattern:
            return None
        return re.compile(self.pattern)


@dataclass
class Evidence:
    """A concrete line the generator saw, and what the rule captured from it.

    This is the reviewer's primary artifact: it turns "does this regex look
    right?" into "did this regex read this line correctly?", which a
    non-engineer can answer.
    """

    rule_id: str
    page: int
    line: str
    captured: dict[str, str] = field(default_factory=dict)


@dataclass
class GeneratedTemplate:
    fund_id: str
    template_id: str
    schedule_pages: list[int]
    line_rules: list[LineRule]
    template_version: str = "1.0.0"
    schema_version: str = SCHEMA_VERSION
    status: Literal["draft", "approved"] = "draft"
    currency_expected: str = "USD"
    unit_expected: str = "ones"
    fund_name_hints: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    generated_by: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)

    # --- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GeneratedTemplate:
        rules = [LineRule(**r) for r in payload.get("line_rules", [])]
        evidence = [Evidence(**e) for e in payload.get("evidence", [])]
        known = {f for f in cls.__dataclass_fields__}  # noqa: SLF001
        kwargs = {k: v for k, v in payload.items() if k in known}
        kwargs["line_rules"] = rules
        kwargs["evidence"] = evidence
        return cls(**kwargs)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> GeneratedTemplate:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


class TemplateValidationError(ValueError):
    """Raised when a template is structurally unusable."""


def validate(template: GeneratedTemplate) -> list[str]:
    """Return a list of problems. Empty list means structurally valid.

    This runs on every generated template before it is written to draft/, so a
    malformed or hallucinated response fails loudly at generation time rather
    than producing wrong numbers at comparison time.
    """
    problems: list[str] = []

    if not template.fund_id:
        problems.append("fund_id is empty")
    if not template.schedule_pages:
        problems.append("schedule_pages is empty — nothing would be read")
    if not template.line_rules:
        problems.append("line_rules is empty — template would extract nothing")

    seen_ids: set[str] = set()
    for rule in template.line_rules:
        if rule.rule_id in seen_ids:
            problems.append(f"duplicate rule_id: {rule.rule_id}")
        seen_ids.add(rule.rule_id)

        if rule.kind not in VALID_RULE_KINDS:
            problems.append(f"{rule.rule_id}: unknown kind {rule.kind!r}")

        if rule.kind == "skip":
            if not rule.prefixes and not rule.pattern:
                problems.append(f"{rule.rule_id}: skip rule has neither prefixes nor pattern")
            continue

        if not rule.pattern:
            problems.append(f"{rule.rule_id}: kind={rule.kind} requires a pattern")
            continue

        try:
            compiled = re.compile(rule.pattern)
        except re.error as exc:
            problems.append(f"{rule.rule_id}: invalid regex ({exc})")
            continue

        groups = set(compiled.groupindex)
        if not groups:
            problems.append(f"{rule.rule_id}: pattern has no named groups, nothing would be captured")
        unknown = groups - VALID_CAPTURE_FIELDS
        if unknown:
            problems.append(f"{rule.rule_id}: unknown capture field(s): {sorted(unknown)}")

        if rule.kind == "company_header" and "company_name" not in groups:
            problems.append(f"{rule.rule_id}: company_header must capture company_name")
        if rule.kind in {"company_subtotal", "position_lot"}:
            if "cost" not in groups or "fair_value" not in groups:
                problems.append(f"{rule.rule_id}: {rule.kind} must capture both cost and fair_value")

    kinds = {r.kind for r in template.line_rules}
    if "company_header" not in kinds:
        problems.append("no company_header rule — companies would never be named")
    if not ({"company_subtotal", "position_lot"} & kinds):
        problems.append("no company_subtotal or position_lot rule — no amounts would be captured")

    return problems


def require_valid(template: GeneratedTemplate) -> None:
    problems = validate(template)
    if problems:
        raise TemplateValidationError(
            "Template failed validation:\n  - " + "\n  - ".join(problems)
        )


def new_template_id(fund_id: str, source_pdf: str | Path) -> str:
    stem = re.sub(r"[^\w]+", "_", Path(source_pdf).stem)[:40].strip("_")
    return f"{fund_id}_{stem}"


def stamp_generation(
    template: GeneratedTemplate,
    *,
    backend: str,
    source_pdf: Path,
    prompt_version: str,
) -> GeneratedTemplate:
    template.generated_by = {
        "backend": backend,
        "source_pdf": str(source_pdf),
        "prompt_version": prompt_version,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "schema_version": SCHEMA_VERSION,
    }
    return template
