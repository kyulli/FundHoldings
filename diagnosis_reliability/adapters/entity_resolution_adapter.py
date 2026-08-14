"""
Adapter for entity-resolution outputs.

This module consumes canonical alias and review outputs produced by the
existing entity_resolution workflow. It does not perform entity resolution.
"""

from __future__ import annotations

import json

from diagnosis_reliability.config import (
    ENTITY_ALIAS_FILE,
    ENTITY_REVIEW_FILE,
)
from diagnosis_reliability.models import StandardIssue

# Reuse the upstream entity-resolution normalization logic so this adapter
# always interprets alias keys exactly the same way as the source pipeline.
from entity_resolution.resolve import normalize


def load_entity_aliases() -> dict:
    """
    Load the canonical alias mappings produced upstream.
    """

    if not ENTITY_ALIAS_FILE.exists():
        return {
            "global": {},
            "by_fund": {},
        }

    with ENTITY_ALIAS_FILE.open(
        encoding="utf-8"
    ) as handle:
        data = json.load(handle)

    return {
        "global": data.get("global", {}),
        "by_fund": data.get("by_fund", {}),
    }


def load_entity_review_records() -> list[dict]:
    """
    Load structured human-review records from entity resolution.
    """

    if not ENTITY_REVIEW_FILE.exists():
        return []

    records: list[dict] = []

    with ENTITY_REVIEW_FILE.open(
        encoding="utf-8"
    ) as handle:
        for line in handle:
            line = line.strip()

            if not line:
                continue

            records.append(
                json.loads(line)
            )

    return records


def _canonical_from_global_alias(
    source_asset: str | None,
    global_aliases: dict[str, str],
) -> str | None:
    """
    Resolve one Source Asset using the approved global alias map.
    """

    if not source_asset:
        return None

    normalized = normalize(source_asset)

    return global_aliases.get(normalized)


def enrich_issue_with_entity_resolution(
    issue: StandardIssue,
    aliases: dict,
) -> StandardIssue:
    """
    Attach canonical entity evidence to one standardized issue.
    """

    if not issue.source_asset:
        issue.entity_resolution_status = "NOT_APPLICABLE"
        return issue

    global_aliases = aliases.get(
        "global",
        {},
    )

    canonical = _canonical_from_global_alias(
        issue.source_asset,
        global_aliases,
    )

    if canonical is not None:
        issue.canonical_entity = canonical

        if canonical.strip() == issue.source_asset.strip():
            issue.entity_resolution_status = "CANONICAL_MATCHED"
            issue.entity_resolution_reason = (
                "Source Asset matched the upstream canonical entity map "
                "and already uses the canonical name."
            )
        else:
            issue.entity_resolution_status = "ALIAS_APPLIED"
            issue.entity_resolution_reason = (
                "Source Asset matched an upstream alias and was mapped "
                "to a canonical entity name."
            )

        return issue

    # No alias does not automatically mean uncertainty or failure.
    # The original name may simply already be canonical.
    issue.canonical_entity = issue.source_asset
    issue.entity_resolution_status = "NO_ALIAS_REQUIRED"

    issue.entity_resolution_reason = (
        "No global alias was found for this Source Asset. The original "
        "name is retained; this alone does not indicate an entity-resolution "
        "problem."
    )

    return issue


def enrich_issues_with_entity_resolution(
    issues: list[StandardIssue],
) -> list[StandardIssue]:
    """
    Attach entity-resolution evidence to standardized issues.
    """

    aliases = load_entity_aliases()

    return [
        enrich_issue_with_entity_resolution(
            issue,
            aliases,
        )
        for issue in issues
    ]