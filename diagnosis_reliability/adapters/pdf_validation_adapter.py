"""
Adapter for converting PDF validation outputs
into Office-facing ExceptionIssue objects.
"""

import json
from pathlib import Path

from ..models import ExceptionIssue


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
                    f"{gate.get('reason')}"
                ),

                evidence_available=(
                    "PDF validation gate result available"
                ),

                pdf_source=source,

                extraction_quality=(
                    gate.get("extraction_mode")
                ),

                comparability_status=(
                    "blocked"
                ),

                diagnosis=(
                    "Comparison blocked due to "
                    "PDF validation failure."
                ),

                recommended_action=(
                    "Resolve Extraction — "
                    "Review PDF/source report"
                ),
            )
        )

    return issues


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

        issues.append(
            ExceptionIssue(
                issue_id=f"PDF_AMOUNT_{len(issues)+1}",

                fund_id=fund_id,
                as_at_date=as_at_date,

                exception_type=(
                    "PDF / Vendor Value Difference"
                ),

                description=(
                    "Values extracted from PDF differ "
                    "from vendor dataset"
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

                difference=(
                    float(row["difference"])
                    if row.get("difference")
                    else None
                ),

                mapping_status=(
                    row.get("entity_mapping_status")
                ),

                pdf_source=source,

                diagnosis=(
                    "PDF reported value differs "
                    "from structured holdings data."
                ),

                recommended_action=(
                    "Review Value — "
                    "Verify PDF/source report"
                ),
            )
        )

    return issues


def build_entity_mapping_issues(
    candidates: list[dict],
    source: str | None = None,
    fund_id: str | None = None,
    as_at_date: str | None = None,
):
    """
    Convert unresolved entity mappings
    into review issues.
    """

    issues = []

    for row in candidates:

        similarity = row.get("similarity")

        if row.get("confirmed"):
            continue

        # Ignore exact fuzzy candidates
        # unless similarity is low

        if similarity is not None and similarity >= 0.95:
            continue

        issues.append(
            ExceptionIssue(
                issue_id=f"PDF_ENTITY_{len(issues)+1}",

                fund_id=fund_id,
                as_at_date=as_at_date,

                exception_type=(
                    "Entity Mapping Review"
                ),

                description=(
                    "PDF entity mapping requires review"
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

                mapping_status=(
                    row.get("status")
                ),

                pdf_source=source,

                diagnosis=(
                    "Entity mapping is not confirmed "
                    "by approved mapping rules."
                ),

                recommended_action=(
                    "Confirm Mapping — "
                    "Review entity resolution"
                ),
            )
        )

    return issues


def build_comparison_block_issues(
    report: dict,
    source: str | None = None,
):
    """
    Convert non-comparable PDF validation reports
    into PDF Comparison Blocked issues.
    """

    issues = []

    status = report.get(
        "comparability_status"
    )

    if status in {
        "comparable",
        "aggregate_only_comparable",
    }:
        return issues

    summary = report.get(
        "summary",
        {}
    )

    reason_codes = summary.get(
        "reason_codes",
        []
    )

    reason = (
        ", ".join(reason_codes)
        if reason_codes
        else "Unknown comparability issue"
    )

    issues.append(
        ExceptionIssue(
            issue_id=(
                f"PDF_BLOCK_{len(issues)+1}"
            ),

            exception_type=(
                "PDF Comparison Blocked"
            ),

            description=(
                "PDF cannot be automatically "
                "compared against structured holdings data"
            ),

            issue_detail=(
                f"Comparison blocked: {reason}"
            ),

            evidence_available=(
                "PDF comparison report available"
            ),

            pdf_source=source,

            comparability_status=(
                status
            ),

            diagnosis=(
                "PDF extraction completed, but "
                "automated comparison could not "
                "be completed due to validation "
                "constraints."
            ),

            recommended_action=(
                "Resolve Extraction — "
                "Review PDF/source report"
            ),
        )
    )

    return issues


def load_pdf_validation_issues(
    vendor_comparison_dir: Path,
    fund_id: str | None = None,
    as_at_date: str | None = None,
):
    """
    Main entry point.
    """

    issues = []

    gates = load_jsonl(
        vendor_comparison_dir
        / "comparability_gates.jsonl"
    )

    amounts = load_jsonl(
        vendor_comparison_dir
        / "amount_comparisons.jsonl"
    )

    entities = load_jsonl(
        vendor_comparison_dir
        / "entity_candidates.jsonl"
    )

    source = str(vendor_comparison_dir)

    issues.extend(
        build_pdf_block_issues(
            gates,
            source,
            fund_id,
            as_at_date,
        )
    )

    issues.extend(
        build_pdf_amount_issues(
            amounts,
            source,
            fund_id,
            as_at_date,
        )
    )

    issues.extend(
        build_entity_mapping_issues(
            entities,
            source,
            fund_id,
            as_at_date,
        )
    )

    report_path = (
        vendor_comparison_dir
        / "comparison_report.json"
    )

    if report_path.exists():
        with report_path.open(
            encoding="utf-8"
        ) as f:
            report = json.load(f)

    issues.extend(
        build_comparison_block_issues(
            report,
            source
        )
    )

    return issues


def build_batch_blocked_issues(
    results: list[dict],
):

    issues = []

    for row in results:

        if not row.get("blocked_reason"):
            continue

        issues.append(
            ExceptionIssue(
                issue_id=(
                    f"PDF_BLOCK_{len(issues)+1}"
                ),

                exception_type=(
                    "PDF Comparison Blocked"
                ),

                fund_id=row.get(
                    "fund_id"
                ),

                description=(
                    "PDF cannot be automatically "
                    "compared against holdings data"
                ),

                issue_detail=(
                    row.get(
                        "blocked_reason"
                    )
                ),

                evidence_available=(
                    "Batch validation result available"
                ),

                diagnosis=(
                    "Automated comparison was blocked "
                    "because required validation "
                    "conditions were not satisfied."
                ),

                recommended_action=(
                    "Resolve Extraction — "
                    "Review PDF/source report"
                ),
            )
        )

    return issues