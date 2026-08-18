"""
Data models for Office-facing Exception Review Queue.
"""

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class ExceptionIssue:
    """
    A single exception record produced from validation outputs.
    """

    # Identification
    issue_id: str

    exception_type: str

    fund_id: str | None = None

    manager_id: str | None = None

    source_asset: str | None = None

    as_at_date: str | None = None


    # Issue Description
    description: str | None = None

    issue_detail: str | None = None


    # Evidence
    evidence_available: str | None = None


    # Original values
    reported_value: Any = None

    derived_value: Any = None


    # Financial evidence
    current_cost: float | None = None

    unrealized_value: float | None = None

    realized_proceeds: float | None = None


    # PDF / Vendor comparison
    pdf_value: Any = None

    vendor_value: Any = None

    difference: float | None = None


    # PDF evidence
    pdf_page: int | None = None

    pdf_source: str | None = None

    extraction_quality: str | None = None

    mapping_status: str | None = None

    comparability_status: str | None = None


    # Diagnosis Output
    diagnosis: str | None = None

    recommended_action: str | None = None


    # Review Metadata
    review_required: bool = True


    def to_dict(self) -> dict:
        """
        Convert issue to dictionary for dataframe/export.
        """

        return asdict(self)