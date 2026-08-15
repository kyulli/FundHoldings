"""
Locate existing project outputs for the Office reporting deck.

This module only discovers outputs produced by upstream workflows.
It does not recompute, modify, or fabricate analytical results.
"""

from __future__ import annotations

from pathlib import Path

from office_deck_generator.config import (
    CLEANING_RUNS_DIR,
    DIAGNOSIS_OUTPUT,
    DATA_STATE_IMAGES,
)

from office_deck_generator.config import (
    CURRENT_OPS_REPORT,
    DIAGNOSIS_OUTPUT,
    DATA_STATE_IMAGES,
)

def get_data_state_images() -> list[Path]:
    """
    Return existing Data-State Analysis image exports.
    """

    return [
        path
        for path in DATA_STATE_IMAGES
        if path.exists()
    ]


def get_current_ops_report() -> Path | None:
    """
    Return the explicitly configured reporting-period Ops workbook.
    """

    if CURRENT_OPS_REPORT.exists():
        return CURRENT_OPS_REPORT

    return None


def get_diagnosis_report() -> Path | None:
    """
    Return the diagnosis/reliability workbook if it exists.
    """

    if DIAGNOSIS_OUTPUT.exists():
        return DIAGNOSIS_OUTPUT

    return None


def discover_outputs() -> dict:
    """
    Return the currently available reporting inputs.
    """

    return {
        "data_state_images": get_data_state_images(),
        "ops_report": get_current_ops_report(),
        "diagnosis_report": get_diagnosis_report(),
        "pdf_validation": None,
    }
