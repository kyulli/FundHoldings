"""
Core data models for the diagnosis and reliability framework.

All upstream sources should be converted into StandardIssue objects before
they enter diagnosis, severity scoring, reliability assessment, or export.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class StandardIssue:
    """
    Canonical representation of one data-quality issue.
    """

    # Identity
    issue_id: str
    source_system: str
    issue_type: str

    fund_id: str | None = None
    manager_id: str | None = None
    source_asset: str | None = None
    as_at_date: str | None = None
    field: str | None = None

    issue_detail: str | None = None
    source_report: str | None = None

    # Structured values / evidence
    reported_value: Any = None
    derived_value: Any = None

    pdf_value: Any = None
    vendor_value: Any = None
    difference: float | None = None
    difference_pct: float | None = None

    current_cost: float | None = None
    unrealized_value: float | None = None
    realized_proceeds: float | None = None

    # PDF / extraction evidence
    pdf_page: int | None = None
    pdf_source: str | None = None

    extraction_mode: str | None = None
    extraction_quality: str | None = None
    parser: str | None = None

    # Mapping / comparability evidence
    mapping_status: str | None = None
    mapping_confidence: float | None = None

    comparison_status: str | None = None
    comparability_status: str | None = None

    # Diagnosis outputs
    #
    # These are intentionally empty when the issue is first built.
    # Downstream modules populate them.
    root_cause: str | None = None
    severity: str | None = None
    severity_score: float | None = None

    confidence: str | None = None
    evidence_strength: str | None = None

    decision_state: str | None = None
    recommended_action: str | None = None
    review_required: bool | None = None

    # Explainability
    diagnosis_reason: str | None = None
    reliability_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert this issue into a dictionary for DataFrame/export use."""
        return asdict(self)