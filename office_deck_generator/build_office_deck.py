from __future__ import annotations

from pathlib import Path

import pandas as pd
from pptx import Presentation
from pptx.util import Inches, Pt

from office_deck_generator.config import (
    OFFICE_TEMPLATE,
    DEFAULT_DECK_OUTPUT,
    OUTPUT_DIR,
)
from office_deck_generator.load_outputs import discover_outputs
from office_deck_generator.extract_tables import read_sheet_if_exists


def format_percent(value) -> str:
    """Format a decimal ratio as a percentage for display only."""
    if pd.isna(value):
        return ""

    return f"{float(value):.1%}"


def friendly_label(value: str) -> str:
    """Convert machine-readable enum labels into presentation text."""
    if not isinstance(value, str):
        return str(value)

    return value.replace("_", " ").title()


def clear_existing_slides(prs: Presentation) -> None:
    """
    Remove example/template slides while preserving layouts and masters.
    """

    slide_ids = list(prs.slides._sldIdLst)

    for slide_id in slide_ids:
        prs.part.drop_rel(slide_id.rId)
        prs.slides._sldIdLst.remove(slide_id)


def set_placeholder_text(
    slide,
    idx: int,
    text: str,
) -> None:
    """
    Set text on a placeholder by placeholder index.
    """

    for placeholder in slide.placeholders:
        if placeholder.placeholder_format.idx == idx:
            placeholder.text = text
            return


def add_title_slide(
    prs: Presentation,
    title: str,
    subtitle: str,
):
    slide = prs.slides.add_slide(
        prs.slide_layouts[5]
    )

    set_placeholder_text(
        slide,
        0,
        f"{title}\n{subtitle}",
    )

    return slide


def add_section_slide(
    prs: Presentation,
    title: str,
):
    slide = prs.slides.add_slide(
        prs.slide_layouts[4]
    )

    set_placeholder_text(
        slide,
        1,
        title,
    )

    return slide


def add_image_slide(
    prs: Presentation,
    title: str,
    image_path: Path,
    note: str = "",
):
    slide = prs.slides.add_slide(
        prs.slide_layouts[0]
    )

    set_placeholder_text(
        slide,
        0,
        title,
    )

    content_placeholder = None

    for placeholder in slide.placeholders:
        if placeholder.placeholder_format.idx == 1:
            content_placeholder = placeholder
            break

    if content_placeholder is None:
        raise RuntimeError(
            "Content placeholder idx=1 not found."
        )

    left = content_placeholder.left
    top = content_placeholder.top
    width = content_placeholder.width
    height = content_placeholder.height

    # Remove the unused content placeholder shape.
    sp = content_placeholder._element
    sp.getparent().remove(sp)

    slide.shapes.add_picture(
        str(image_path),
        left,
        top,
        width=width,
        height=height,
    )

    if note:
        set_placeholder_text(
            slide,
            13,
            note,
        )

    return slide


def dataframe_to_slide_table(
    slide,
    df: pd.DataFrame,
    max_rows: int = 12,
    percent_columns: list[str] | None = None,
    friendly_columns: list[str] | None = None,
):
    """
    Insert existing DataFrame values into the template content area.
    """

    percent_columns = percent_columns or []
    friendly_columns = friendly_columns or []

    df = df.head(max_rows).copy()

    content_placeholder = None

    for placeholder in slide.placeholders:
        if placeholder.placeholder_format.idx == 1:
            content_placeholder = placeholder
            break

    if content_placeholder is None:
        raise RuntimeError(
            "Content placeholder idx=1 not found."
        )

    left = content_placeholder.left
    top = content_placeholder.top
    width = content_placeholder.width
    height = content_placeholder.height

    sp = content_placeholder._element
    sp.getparent().remove(sp)

    rows = len(df) + 1
    cols = len(df.columns)

    table_shape = slide.shapes.add_table(
        rows,
        cols,
        left,
        top,
        width,
        height,
    )

    table = table_shape.table

    for col_idx, column in enumerate(df.columns):
        table.cell(0, col_idx).text = str(column)

    for row_idx, (_, row) in enumerate(
        df.iterrows(),
        start=1,
    ):
        for col_idx, value in enumerate(row):
            column_name = df.columns[col_idx]

            if pd.isna(value):
                text = ""

            elif column_name in percent_columns:
                text = format_percent(value)

            elif column_name in friendly_columns:
                text = friendly_label(value)

            elif isinstance(value, float):
                text = f"{value:.3f}".rstrip("0").rstrip(".")

            else:
                text = str(value)

            table.cell(
                row_idx,
                col_idx,
            ).text = text

    # Light formatting only; actual template styling stays dominant.
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(9)

    return table


def add_table_slide(
    prs: Presentation,
    title: str,
    df: pd.DataFrame,
    note: str = "",
    max_rows: int = 12,
    percent_columns: list[str] | None = None,
    friendly_columns: list[str] | None = None,
):
    slide = prs.slides.add_slide(
        prs.slide_layouts[0]
    )

    set_placeholder_text(
        slide,
        0,
        title,
    )

    dataframe_to_slide_table(
        slide,
        df,
        max_rows=max_rows,
        percent_columns=percent_columns,
        friendly_columns=friendly_columns,
    )

    if note:
        set_placeholder_text(
            slide,
            13,
            note,
        )

    return slide


def build_deck():
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    outputs = discover_outputs()

    prs = Presentation(
        OFFICE_TEMPLATE
    )

    clear_existing_slides(prs)

    # Title

    add_title_slide(
        prs,
        "Private Funds Data Quality & Validation",
        "August 2026",
    )

    # Data-State Analysis

    data_state_images = outputs.get(
        "data_state_images",
        [],
    )

    if data_state_images:
        add_section_slide(
            prs,
            "I. Data-State Analysis",
        )

    image_titles = {
        "completeness_by_dimension.png":
            "Data Completeness by Dimension",
        "fund_quality_distribution_outliers.png":
            "Fund Quality Distribution & Outliers",
        "manager_field_heatmap.png":
            "Manager-Level Field Completeness",
        "portfolio_concentration.png":
            "Portfolio Concentration",
        "quality_by_investment_type.png":
            "Data Quality by Investment Type",
    }

    for image_path in data_state_images:
        title = image_titles.get(
            image_path.name,
            image_path.stem.replace("_", " ").title(),
        )

        add_image_slide(
            prs,
            title,
            image_path,
            note=(
                "Source: existing Data-State Analysis exports. "
                "Figure inserted without recalculation."
            ),
        )

    # Cleaning Pipeline

    ops_report = outputs.get(
        "ops_report"
    )

    if ops_report:
        add_section_slide(
            prs,
            "II. Quarterly Cleaning Pipeline",
        )

        summary_df = read_sheet_if_exists(
            ops_report,
            "Summary",
        )

        if summary_df is not None:
            # Remove fully blank rows only.
            summary_df = summary_df.dropna(
                how="all"
            )

            add_table_slide(
                prs,
                "Quarterly Pipeline Summary",
                summary_df,
                note=(
                    "Source: current Ops report. "
                    "Values reproduced directly from the existing workbook."
                ),
                max_rows=23,
            )

    # PDF Validation

    # Intentionally skipped until a stable upstream output is available.
    # No placeholder results or synthetic numbers are generated.


    # Diagnosis & Reliability

    diagnosis_report = outputs.get(
        "diagnosis_report"
    )

    if diagnosis_report:
        add_section_slide(
            prs,
            "III. Diagnosis & Reliability",
        )

        diagnosis_overview = read_sheet_if_exists(
            diagnosis_report,
            "Diagnosis Overview",
        )

        if diagnosis_overview is not None:
            add_table_slide(
                prs,
                "Diagnosis Overview",
                diagnosis_overview[
                    [
                        "category",
                        "metric",
                        "value",
                    ]
                ],
                note=(
                    "Source: diagnosis/reliability framework output. "
                    "Only existing reported values are shown."
                ),
                max_rows=10,
            )

        reliability_df = read_sheet_if_exists(
            diagnosis_report,
            "Reliability Analysis",
        )

        if reliability_df is not None:
            add_table_slide(
                prs,
                "Evidence Reliability",
                reliability_df[
                    [
                        "category",
                        "metric",
                        "count",
                        "percent_of_issues",
                    ]
                ],
                note=(
                    "Evidence coverage reflects currently integrated "
                    "upstream sources; PDF validation evidence will be "
                    "added once available."
                ),
                max_rows=15,
                percent_columns=[
                    "percent_of_issues",
                ],
            )

        decision_queue = read_sheet_if_exists(
            diagnosis_report,
            "Decision Queue",
        )

        if decision_queue is not None:
            add_table_slide(
                prs,
                "Office Decision Queue",
                decision_queue[
                    [
                        "decision_state",
                        "severity",
                        "confidence",
                        "issue_count",
                        "affected_funds",
                        "affected_managers",
                    ]
                ],
                note=(
                    "Recommended review priorities are reproduced from "
                    "the diagnosis framework output."
                ),
                max_rows=10,
                friendly_columns=[
                    "decision_state",
                ],
            )

    prs.save(
        DEFAULT_DECK_OUTPUT
    )

    print(
        f"Deck written to: {DEFAULT_DECK_OUTPUT}"
    )


def add_workflow_slide(
    prs: Presentation,
):
    """
    Add a high-level workflow slide using presentation text only.
    """

    slide = prs.slides.add_slide(
        prs.slide_layouts[0]
    )

    set_placeholder_text(
        slide,
        0,
        "End-to-End Data Quality Workflow",
    )

    content_placeholder = None

    for placeholder in slide.placeholders:
        if placeholder.placeholder_format.idx == 1:
            content_placeholder = placeholder
            break

    if content_placeholder is None:
        raise RuntimeError(
            "Content placeholder idx=1 not found."
        )

    left = content_placeholder.left
    top = content_placeholder.top
    width = content_placeholder.width
    height = content_placeholder.height

    sp = content_placeholder._element
    sp.getparent().remove(sp)

    stages = [
        "Data-State Analysis",
        "Quarterly Cleaning Pipeline",
        "PDF Validation",
        "Diagnosis & Reliability",
        "Office Decision Support",
    ]

    stage_width = width / len(stages)

    for i, stage in enumerate(stages):
        box = slide.shapes.add_textbox(
            left + int(i * stage_width),
            top + int(height * 0.28),
            int(stage_width * 0.88),
            int(height * 0.25),
        )

        tf = box.text_frame
        tf.clear()

        p = tf.paragraphs[0]
        p.text = stage

        for run in p.runs:
            run.font.size = Pt(15)
            run.font.bold = True

        if i < len(stages) - 1:
            arrow = slide.shapes.add_textbox(
                left + int(
                    (i + 0.86) * stage_width
                ),
                top + int(height * 0.32),
                int(stage_width * 0.15),
                int(height * 0.15),
            )

            arrow.text = "→"

            for paragraph in arrow.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(20)

    set_placeholder_text(
        slide,
        13,
        (
            "Existing analytical outputs are progressively transformed "
            "into operational review guidance; no upstream results are "
            "recomputed in the reporting layer."
        ),
    )

    return slide


if __name__ == "__main__":
    build_deck()