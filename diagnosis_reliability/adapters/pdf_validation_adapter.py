"""
Adapter for PDF validation outputs.

The PDF validation pipeline is still evolving, so this adapter intentionally
defines a stable interface without hard-coding the current upstream schema.
"""

from __future__ import annotations

from pathlib import Path

from diagnosis_reliability.models import StandardIssue


def load_pdf_validation_issues(
    output_dir: Path,
) -> list[StandardIssue]:
    """
    Convert one PDF validation run into canonical StandardIssue objects.

    This function will be implemented once the upstream PDF validation output
    schema is finalized. Keeping the interface here allows the diagnosis layer
    to remain independent of upstream implementation details.
    """
    raise NotImplementedError(
        "PDF validation adapter is pending the finalized upstream output schema."
    )