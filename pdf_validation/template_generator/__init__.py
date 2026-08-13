"""Teach-once extraction templates for fund holdings schedules.

Problem this solves
-------------------
Today a fund can only be spot-checked against its PDF if someone hand-writes a
bespoke parser for that fund's statement layout (see the three fund-specific
regex parsers in src/pdf_validation/text_fallback.py). That is roughly 100 lines
of Python per fund, and it breaks whenever a GP changes their template. With 23
funds holding PDFs and 2 currently supported, that cost is what caps coverage.

Approach
--------
An LLM reads one sample statement and proposes a DECLARATIVE template — ordered
line rules with named-group regexes — which a human reviews once. After that,
every quarterly run executes the template deterministically with no LLM in the
loop.

    propose  (LLM, once per fund)  ->  draft/
    review   (human, once)         ->  approved/
    replay   (deterministic, every quarter, no LLM)

Guardrails
----------
Generated templates get no more trust than any other parser: replayed rows must
satisfy the statement's own arithmetic (Fair Value - Cost = Unrealized Gain)
before a template can be promoted, and a template only becomes visible to the
runtime when a person promotes it.

Nothing under src/pdf_validation/ is modified by this package.
"""

from .schema import GeneratedTemplate, LineRule, Evidence, validate, require_valid
from .replay import replay, replay_from_file, ReplayResult
from .approval import dry_run, check_arithmetic, approve, save_draft, list_templates

__all__ = [
    "GeneratedTemplate",
    "LineRule",
    "Evidence",
    "validate",
    "require_valid",
    "replay",
    "replay_from_file",
    "ReplayResult",
    "dry_run",
    "check_arithmetic",
    "approve",
    "save_draft",
    "list_templates",
]
