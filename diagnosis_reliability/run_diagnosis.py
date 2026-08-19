"""
Main pipeline for Office-facing Exception Review Queue.
"""

from .loaders import load_all_validation_outputs
from .issue_builder import build_all_issues
from .diagnosis import diagnose_all
from .recommendations import recommend_all
from .export import export_diagnosis_report


def run():

    print("=" * 60)
    print("Starting Exception Diagnosis Pipeline")
    print("=" * 60)


    # 1. Load validation outputs

    print("\n[1/5] Loading validation outputs...")

    validation_outputs = (
        load_all_validation_outputs()
    )

    for name, data in validation_outputs.items():

        if hasattr(data, "shape"):

            print(
                f"{name}: {data.shape}"
            )

        elif isinstance(data, list):

            print(
                f"{name}: {len(data)} issues"
            )

        elif isinstance(data, dict):

            print(
                f"{name}: {len(data)} records"
            )

        else:

            print(
                f"{name}: loaded"
            )


    # 2. Build issues

    print(
        "\n[2/5] Building exception issues..."
    )

    issues = build_all_issues(
        validation_outputs
    )

    print(
        f"Total exceptions generated: {len(issues)}"
    )


    # Exception breakdown

    breakdown = {}

    for issue in issues:

        breakdown.setdefault(
            issue.exception_type,
            0
        )

        breakdown[
            issue.exception_type
        ] += 1

    for name, count in breakdown.items():

        print(
            f"  - {name}: {count}"
        )


    # 3. Diagnosis

    print(
        "\n[3/5] Generating diagnosis..."
    )

    issues = diagnose_all(
        issues
    )


    # 4. Recommendations

    print(
        "\n[4/5] Generating recommendations..."
    )

    issues = recommend_all(
        issues
    )


    # 5. Export

    print(
        "\n[5/5] Exporting report..."
    )

    output = export_diagnosis_report(
        issues
    )

    print("\nCompleted.")

    print(
        f"Output: {output}"
    )


if __name__ == "__main__":

    run()