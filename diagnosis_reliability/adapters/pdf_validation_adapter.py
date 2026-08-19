"""
Adapter for converting PDF validation outputs
into Office-facing ExceptionIssue objects.
"""

import json
from pathlib import Path
from ..models import ExceptionIssue
from ..config import ACTIONS


def load_jsonl(path: Path) -> list[dict]:
    """
    Load jsonl records.
    """

    if not path.exists():
        return []

    with path.open(encoding="utf-8") as f:
        return [
            json.loads(line)
            for line in f
            if line.strip()
        ]


# PDF comparison blocked issues

def build_pdf_block_issues(
    gates: list[dict],
    source: str | None = None,
    fund_id: str | None = None,
    as_at_date: str | None = None,
):
    """
    Convert failed comparability gates
    into PDF Comparison Blocked issues.
    """

    issues = []

    for gate in gates:

        if gate.get("status") == "PASS":
            continue

        issues.append(
            ExceptionIssue(
                issue_id=f"PDF_GATE_{len(issues)+1}",

                exception_type="PDF Comparison Blocked",

                fund_id=fund_id,

                as_at_date=as_at_date,

                description=(
                    "PDF cannot be used for automated comparison"
                ),

                issue_detail=(
                    f"{gate.get('gate')}: "
                    f"{gate.get('reason')}. "
                    f"Extraction mode: "
                    f"{gate.get('extraction_mode')}"
                ),

                evidence_available=(
                    "PDF validation gate result available"
                ),

                pdf_source=source,

                diagnosis=(
                    "Comparison blocked because "
                    "required PDF information was unavailable."
                ),

                recommended_action=(
                    ACTIONS["resolve_extraction"]
                ),
            )
        )

    return issues


# PDF / Vendor value mismatch issues

def build_pdf_amount_issues(
    comparisons: list[dict],
    source: str | None = None,
    fund_id: str | None = None,
    as_at_date: str | None = None,
):
    """
    Convert PDF/vendor amount mismatches
    into exceptions.
    """

    issues = []

    for row in comparisons:

        if row.get("status") != "mismatch":
            continue

        difference = None

        if row.get("difference") is not None:

            try:
                difference = float(
                    row["difference"]
                )

            except Exception:
                difference = None

        issues.append(
            ExceptionIssue(
                issue_id=f"PDF_AMOUNT_{len(issues)+1}",

                fund_id=fund_id,

                as_at_date=as_at_date,

                exception_type=(
                    "PDF / Vendor Value Mismatch"
                ),

                description=(
                    "Values extracted from PDF differ "
                    "from structured holdings data"
                ),

                issue_detail=(
                    f"{row.get('pdf_company_name')} - "
                    f"{row.get('logical_field')} mismatch"
                ),

                evidence_available=(
                    "PDF value and vendor value available"
                ),

                source_asset=(
                    row.get("vendor_source_asset")
                ),

                pdf_value=(
                    row.get("pdf_value")
                ),

                vendor_value=(
                    row.get("csv_value")
                ),

                difference=difference,

                pdf_source=source,

                diagnosis=(
                    "PDF reported value differs "
                    "from structured holdings data."
                ),

                recommended_action=(
                    ACTIONS["review_value"]
                ),
            )
        )

    return issues


# Entity mapping review issues

def build_entity_mapping_issues(
    candidates: list[dict],
    source: str | None = None,
    fund_id: str | None = None,
    as_at_date: str | None = None,
):
    """
    Convert unresolved entity mappings
    into review issues.

    Note:
    Entity resolution logic is owned by the
    entity resolution pipeline. This adapter only
    converts unresolved outputs into review items.
    """

    issues = []

    for row in candidates:

        similarity = row.get("similarity")

        if row.get("confirmed"):
            continue

        if similarity is not None and similarity >= 0.95:
            continue

        issues.append(
            ExceptionIssue(
                issue_id=f"ENTITY_MAPPING_{len(issues)+1}",

                fund_id=fund_id,

                as_at_date=as_at_date,

                exception_type=(
                    "Entity Mapping Review"
                ),

                description=(
                    "Entity mapping requires review"
                ),

                issue_detail=(
                    f"{row.get('pdf_company_name')} "
                    f"mapping candidate: "
                    f"{row.get('vendor_source_asset_candidate')}"
                ),

                evidence_available=(
                    "PDF company name and mapping candidate available"
                ),

                source_asset=(
                    row.get(
                        "vendor_source_asset_candidate"
                    )
                ),

                pdf_source=source,

                entity_resolution_status=(
                    row.get("status")
                ),

                diagnosis=(
                    "Entity identity could not be confirmed "
                    "by existing mapping results."
                ),

                recommended_action=(
                    ACTIONS["confirm_mapping"]
                ),
            )
        )

    return issues


# Non-comparable PDF reports

def build_comparison_block_issues(
    report: dict,
    source: str | None = None,
):
    """
    Convert non-comparable PDF validation reports
    into PDF Comparison Blocked issues.
    """

    issues = []

    if report.get("comparability_status") == "comparable":
        return issues

    issues.append(
        ExceptionIssue(
            issue_id="PDF_COMPARISON_BLOCKED",

            exception_type=(
                "PDF Comparison Blocked"
            ),

            description=(
                "PDF cannot be compared automatically"
            ),

            issue_detail=(
                report.get(
                    "reason",
                    "Unknown comparison issue"
                )
            ),

            evidence_available=(
                "PDF validation report available"
            ),

            pdf_source=source,

            diagnosis=(
                "Automated comparison could not be completed "
                "with available PDF information."
            ),

            recommended_action=(
                ACTIONS["resolve_extraction"]
            ),
        )
    )

    return issues


def load_pdf_validation_issues(
    vendor_dir: Path,
    fund_id: str | None = None,
    as_at_date: str | None = None,
):
    """
    Load PDF validation comparison outputs
    and convert them into ExceptionIssue objects.
    """

    issues = []

    comparison_report = (
        vendor_dir
        / "comparison_report.json"
    )

    if not comparison_report.exists():
        return issues

    with comparison_report.open(
        encoding="utf-8"
    ) as f:

        report = json.load(f)

    source = str(vendor_dir)


    # Blocked comparison

    gates = report.get(
        "gates",
        []
    )

    issues.extend(
        build_pdf_block_issues(
            gates,
            source=source,
            fund_id=fund_id,
            as_at_date=as_at_date,
        )
    )


    # Value comparison mismatch

    comparisons = report.get(
        "comparisons",
        []
    )

    issues.extend(
        build_pdf_amount_issues(
            comparisons,
            source=source,
            fund_id=fund_id,
            as_at_date=as_at_date,
        )
    )


    # Entity mapping review

    candidates = report.get(
        "entity_mapping_candidates",
        []
    )

    issues.extend(
        build_entity_mapping_issues(
            candidates,
            source=source,
            fund_id=fund_id,
            as_at_date=as_at_date,
        )
    )

    return issues


def build_batch_blocked_issues(
    results: list[dict],
):
    """
    Convert batch-level PDF validation failures
    into PDF Comparison Blocked issues.
    """

    issues = []

    for idx, row in enumerate(results):

        # Only keep blocked cases
        status = row.get("status")

        if status in [
            "PASS",
            "pass",
            "completed",
            "success",
        ]:
            continue

        issues.append(
            ExceptionIssue(
                issue_id=f"PDF_BATCH_{idx+1}",

                exception_type=(
                    "PDF Comparison Blocked"
                ),

                fund_id=(
                    row.get("fund_id")
                ),

                as_at_date=(
                    row.get("as_at_date")
                ),

                description=(
                    "PDF cannot be used for automated comparison"
                ),

                issue_detail=(
                    row.get(
                        "reason",
                        "Batch validation failed"
                    )
                ),

                evidence_available=(
                    "PDF validation batch result available"
                ),

                pdf_source=(
                    row.get("pdf_path")
                    or row.get("source")
                ),

                diagnosis=(
                    "Comparison blocked because "
                    "required PDF information was unavailable."
                ),

                recommended_action=(
                    ACTIONS["resolve_extraction"]
                ),
            )
        )

    return issues