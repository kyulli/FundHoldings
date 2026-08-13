"""Guardrails between a generated draft and production use.

Two independent gates stand between an LLM proposal and any number reaching a
report:

  1. Arithmetic. Every replayed row is checked against the statement's own
     internal identity (Fair Value - Cost = Unrealized Gain) wherever the
     schedule reports all three. A template that reads columns in the wrong
     order, or misreads digits, breaks this identity and is rejected. This is
     the same class of check the existing pipeline already applies to
     Camelot/pdfplumber output — generated templates get no special trust.

  2. Human promotion. Templates are written to draft/ and are invisible to the
     runtime. Someone has to read the evidence and explicitly promote to
     approved/. This mirrors how vendor_mapping_registry.json already gates
     which funds may be compared at all.

Coverage is also reported: the share of schedule lines that no rule matched. It
is not a pass/fail gate on its own (schedules legitimately contain prose), but a
sharp drop in coverage between quarters is the signal that a GP changed their
layout and the template needs regenerating.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .replay import ReplayResult, replay
from .schema import GeneratedTemplate, validate

# A template must explain at least this share of schedule lines to be
# promotable. Deliberately lenient: audited statements carry footnotes and
# narrative that no extraction rule should match.
MIN_COVERAGE = 0.35

# Arithmetic tolerance in whole currency units, matching the existing
# reconciliation configs (see configs/*.json "tolerance").
AMOUNT_TOLERANCE = 1


@dataclass
class CheckResult:
    passed: bool
    checks_run: int = 0
    checks_failed: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    coverage: float = 0.0
    company_count: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks_run": self.checks_run,
            "checks_failed": self.checks_failed,
            "failures": self.failures[:25],
            "coverage": round(self.coverage, 4),
            "company_count": self.company_count,
            "notes": self.notes,
        }


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def check_arithmetic(result: ReplayResult) -> CheckResult:
    """Verify Fair Value - Cost = Unrealized Gain on every row that reports all three."""
    out = CheckResult(passed=True)
    out.coverage = result.coverage()
    out.company_count = result.company_count

    for row in result.company_summary:
        cost = _as_int(row.get("cost_reported_normalized"))
        fv = _as_int(row.get("fair_value_reported_normalized"))
        gain = _as_int(row.get("unrealized_gain_loss_reported_normalized"))
        if cost is None or fv is None or gain is None:
            continue
        out.checks_run += 1
        diff = abs((fv - cost) - gain)
        if diff > AMOUNT_TOLERANCE:
            out.checks_failed += 1
            out.failures.append(
                {
                    "company_name": row.get("company_name"),
                    "rule": row.get("extracted_by_rule"),
                    "cost": cost,
                    "fair_value": fv,
                    "reported_gain": gain,
                    "implied_gain": fv - cost,
                    "difference": diff,
                    "source_line": row.get("source_line"),
                }
            )

    if out.checks_run == 0:
        out.notes.append(
            "No row reported Cost, Fair Value and Unrealized Gain together, so the "
            "arithmetic identity could not be tested. Review the evidence block manually."
        )
    elif out.checks_failed:
        out.passed = False
        out.notes.append(
            f"{out.checks_failed} of {out.checks_run} rows break "
            "Fair Value - Cost = Unrealized Gain. The template is reading at least one "
            "column incorrectly."
        )

    if out.company_count == 0:
        out.passed = False
        out.notes.append("Template extracted zero companies.")

    if out.coverage < MIN_COVERAGE:
        out.passed = False
        out.notes.append(
            f"Coverage {out.coverage:.0%} is below the {MIN_COVERAGE:.0%} floor — "
            "most schedule lines matched no rule."
        )

    return out


def dry_run(template: GeneratedTemplate, pdf_path: Path | str) -> tuple[ReplayResult, CheckResult]:
    """Replay a template and run the guardrail, without promoting anything."""
    structural = validate(template)
    if structural:
        return ReplayResult(), CheckResult(
            passed=False, notes=[f"Structural validation failed: {p}" for p in structural]
        )
    result = replay(template, pdf_path)
    return result, check_arithmetic(result)


# --- draft / approved storage ---------------------------------------------


def draft_dir(configs_root: Path) -> Path:
    return Path(configs_root) / "generated_templates" / "draft"


def approved_dir(configs_root: Path) -> Path:
    return Path(configs_root) / "generated_templates" / "approved"


def save_draft(template: GeneratedTemplate, configs_root: Path) -> Path:
    template.status = "draft"
    path = draft_dir(configs_root) / f"{template.template_id}.json"
    return template.save(path)


def load_draft(template_id: str, configs_root: Path) -> GeneratedTemplate:
    return GeneratedTemplate.load(draft_dir(configs_root) / f"{template_id}.json")


def approve(
    template_id: str,
    configs_root: Path,
    *,
    reviewer: str,
    check: CheckResult | None = None,
    force: bool = False,
) -> Path:
    """Promote draft/<id>.json to approved/<id>.json.

    Refuses to promote a template that failed its guardrail unless `force` is
    set, and records who approved it and what the checks said at that moment.
    """
    template = load_draft(template_id, configs_root)

    if check is not None and not check.passed and not force:
        raise RuntimeError(
            "Refusing to approve a template that failed its checks:\n  - "
            + "\n  - ".join(check.notes)
            + "\nRe-generate the template, or pass force=True with a written justification."
        )

    template.status = "approved"
    template.review = {
        "reviewer": reviewer,
        "approved_at_utc": datetime.now(timezone.utc).isoformat(),
        "checks": check.to_dict() if check else None,
        "forced": bool(force and check is not None and not check.passed),
    }
    path = approved_dir(configs_root) / f"{template_id}.json"
    return template.save(path)


def list_templates(configs_root: Path) -> dict[str, list[str]]:
    def _ids(d: Path) -> list[str]:
        return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []

    return {
        "draft": _ids(draft_dir(configs_root)),
        "approved": _ids(approved_dir(configs_root)),
    }


def approved_template_for_fund(fund_id: str, configs_root: Path) -> GeneratedTemplate | None:
    """Look up the approved template for a fund, if one exists."""
    d = approved_dir(configs_root)
    if not d.exists():
        return None
    for path in sorted(d.glob("*.json")):
        try:
            template = GeneratedTemplate.load(path)
        except Exception:  # noqa: BLE001 - a corrupt file should not break lookup
            continue
        if template.fund_id == fund_id and template.status == "approved":
            return template
    return None
