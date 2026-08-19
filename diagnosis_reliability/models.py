"""
Data models for Office-facing exception review.

The model stores:
- what happened
- available evidence
- diagnosis
- recommended action

It does not calculate validation results.
"""

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class ExceptionIssue:
    """
    A single exception generated from upstream validation outputs.
    """

    # Identification

    issue_id: str

    exception_type: str

    fund_id: str | None = None

    manager_id: str | None = None

    source_asset: str | None = None

    as_at_date: str | None = None


    # Office-facing explanation

    description: str | None = None

    issue_detail: str | None = None

    evidence_available: str | None = None

    diagnosis: str | None = None

    recommended_action: str | None = None

    recommended_guidance: str | None = None


    # Structured data evidence

    reported_value: Any = None

    derived_value: Any = None

    current_cost: float | None = None

    unrealized_value: float | None = None

    realized_proceeds: float | None = None


    # PDF validation evidence

    pdf_value: Any = None

    vendor_value: Any = None

    difference: float | None = None

    pdf_page: int | None = None

    pdf_source: str | None = None


    # Entity resolution evidence

    canonical_entity: str | None = None

    entity_resolution_status: str | None = None

    entity_resolution_reason: str | None = None


    # Review tracking

    review_required: bool = True

    def to_dict(self) -> dict:
        """
        Convert issue object into dictionary for export.
        """

        return asdict(self)