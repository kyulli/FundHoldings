from __future__ import annotations

from pathlib import Path

import pandas as pd
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

from office_deck_generator.config import (
    OFFICE_TEMPLATE,
    DEFAULT_DECK_OUTPUT,
    OUTPUT_DIR,
    DIAGNOSIS_OVERVIEW_METRICS,
)
from office_deck_generator.load_outputs import discover_outputs
from office_deck_generator.extract_tables import read_sheet_if_exists

from office_deck_generator.config import (
    OFFICE_TEMPLATE,
    DEFAULT_DECK_OUTPUT,
    OUTPUT_DIR,
    PIPELINE_SUMMARY_METRICS,
)


# Office-style presentation colors
COLOR_DARK_BROWN = RGBColor(92, 51, 45)
COLOR_DEEP_RED = RGBColor(128, 38, 38)

COLOR_WHITE = RGBColor(255, 255, 255)
COLOR_WARM_WHITE = RGBColor(250, 247, 244)
COLOR_LIGHT_TAN = RGBColor(239, 229, 220)

COLOR_TEXT = RGBColor(55, 45, 42)
COLOR_BORDER = RGBColor(190, 170, 155)


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


def style_table(
    table,
) -> None:
    """
    Apply a red / white / brown financial-reporting style.
    """

    for row_idx, row in enumerate(table.rows):
        for cell in row.cells:
            cell.margin_left = Pt(7)
            cell.margin_right = Pt(7)
            cell.margin_top = Pt(5)
            cell.margin_bottom = Pt(5 )

            # Header row
            if row_idx == 0:
                cell.fill.solid()
                cell.fill.fore_color.rgb = COLOR_DARK_BROWN

                for paragraph in cell.text_frame.paragraphs:
                    for run in paragraph.runs:
                        run.font.color.rgb = COLOR_WHITE
                        run.font.bold = True
                        run.font.size = Pt(11)

            # Alternating body rows
            else:
                cell.fill.solid()

                if row_idx % 2 == 0:
                    cell.fill.fore_color.rgb = COLOR_WARM_WHITE
                else:
                    cell.fill.fore_color.rgb = COLOR_LIGHT_TAN

                for paragraph in cell.text_frame.paragraphs:
                    for run in paragraph.runs:
                        run.font.color.rgb = COLOR_TEXT
                        run.font.size = Pt(10.5)


def highlight_decision_queue(
    table,
    df: pd.DataFrame,
) -> None:
    """
    Highlight high-severity rows without changing underlying values.
    """

    if "severity" not in df.columns:
        return

    severity_col = list(df.columns).index(
        "severity"
    )

    for df_row_idx, severity in enumerate(
        df["severity"],
        start=1,
    ):
        if str(severity).upper() == "HIGH":
            cell = table.cell(
                df_row_idx,
                severity_col,
            )

            cell.fill.solid()
            cell.fill.fore_color.rgb = COLOR_DEEP_RED

            for paragraph in cell.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.color.rgb = COLOR_WHITE
                    run.font.bold = True


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

    # Formatting
    style_table(table)

    return table


def add_table_slide(
    prs: Presentation,
    title: str,
    df: pd.DataFrame,
    note: str = "",
    max_rows: int = 12,
    percent_columns: list[str] | None = None,
    friendly_columns: list[str] | None = None,
    highlight_severity: bool = False,
):
    slide = prs.slides.add_slide(
        prs.slide_layouts[0]
    )

    set_placeholder_text(
        slide,
        0,
        title,
    )

    table = dataframe_to_slide_table(
        slide,
        df,
        max_rows=max_rows,
        percent_columns=percent_columns,
        friendly_columns=friendly_columns,
    )

    if highlight_severity:
        highlight_decision_queue(
            table,
            df.head(max_rows),
        )

    if note:
        set_placeholder_text(
            slide,
            13,
            note,
        )

    return slide, table


def add_decision_queue_slide(
    prs: Presentation,
    decision_queue: pd.DataFrame,
):
    """Create an executive Office Decision Queue slide.

    All actions are reproduced from existing office_guidance fields.
    No new analytical conclusions are generated.
    """

    slide = prs.slides.add_slide(
        prs.slide_layouts[0]
    )

    set_placeholder_text(
        slide,
        0,
        "Office Decision Queue",
    )

    # Find and remove default content placeholder.
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


    # Top: compact decision table
    display_df = decision_queue[
        [
            "decision_state",
            "severity",
            "confidence",
            "issue_count",
            "affected_funds",
            "affected_managers",
        ]
    ].copy()

    display_df["decision_state"] = (
        display_df["decision_state"]
        .apply(friendly_label)
    )

    table_height = int(height * 0.36)

    rows = len(display_df) + 1
    cols = len(display_df.columns)

    table_shape = slide.shapes.add_table(
        rows,
        cols,
        left,
        top,
        width,
        table_height,
    )

    table = table_shape.table

    # Header
    for col_idx, column in enumerate(
        display_df.columns
    ):
        table.cell(
            0,
            col_idx,
        ).text = friendly_label(column)

    # Body
    for row_idx, (_, row) in enumerate(
        display_df.iterrows(),
        start=1,
    ):
        for col_idx, value in enumerate(row):
            if pd.isna(value):
                text = ""
            else:
                text = str(value)

            table.cell(
                row_idx,
                col_idx,
            ).text = text

    style_table(table)

    highlight_decision_queue(
        table,
        display_df,
    )


    # Bottom section title
    guidance_top = (
        top
        + table_height
        + int(height * 0.08)
    )

    label_box = slide.shapes.add_textbox(
        left,
        guidance_top,
        width,
        int(height * 0.06),
    )

    p = label_box.text_frame.paragraphs[0]
    p.text = "Office Review Guidance"

    run = p.runs[0]
    run.font.size = Pt(15)
    run.font.bold = True
    run.font.color.rgb = COLOR_DARK_BROWN


    # Use existing guidance only
    # Deduplicate repeated guidance text.
    guidance_df = (
        decision_queue[
            [
                "decision_state",
                "office_guidance",
            ]
        ]
        .drop_duplicates()
        .reset_index(drop=True)
    )

    guidance_box_top = (
        guidance_top
        + int(height * 0.075)
    )

    guidance_height = int(
        height * 0.28
    )

    number_guidance = len(
        guidance_df
    )

    if number_guidance:
        gap = int(width * 0.018)

        box_width = int(
            (
                width
                - gap * (
                    number_guidance - 1
                )
            )
            / number_guidance
        )

        for i, row in guidance_df.iterrows():
            box_left = (
                left
                + i * (
                    box_width + gap
                )
            )

            action_box = slide.shapes.add_shape(
                MSO_SHAPE.ROUNDED_RECTANGLE,
                box_left,
                guidance_box_top,
                box_width,
                guidance_height,
            )

            action_box.fill.solid()
            action_box.fill.fore_color.rgb = COLOR_WARM_WHITE
            action_box.line.color.rgb = COLOR_BORDER
            action_box.line.width = Pt(1)

            # Inner layout: center title + body as one content block
            inner_margin_x = Pt(18)
            inner_margin_top = Pt(14)
            inner_margin_bottom = Pt(14)
            title_body_gap = Pt(8)

            inner_left = box_left + inner_margin_x
            inner_width = box_width - 2 * inner_margin_x

            title_height = int(guidance_height * 0.20)
            body_height = int(guidance_height * 0.34)

            content_block_height = (
                title_height
                + title_body_gap
                + body_height
            )

            content_top = int(
                guidance_box_top
                + (guidance_height - content_block_height) / 2
            )

            # Title box
            title_box = slide.shapes.add_textbox(
                inner_left,
                content_top,
                inner_width,
                title_height,
            )

            title_tf = title_box.text_frame
            title_tf.clear()
            title_tf.word_wrap = True
            title_tf.vertical_anchor = MSO_ANCHOR.MIDDLE
            title_tf.margin_left = 0
            title_tf.margin_right = 0
            title_tf.margin_top = 0
            title_tf.margin_bottom = 0

            p = title_tf.paragraphs[0]
            p.text = friendly_label(
                row["decision_state"]
            )
            p.alignment = PP_ALIGN.CENTER

            run = p.runs[0]
            run.font.size = Pt(16)
            run.font.bold = True
            run.font.color.rgb = COLOR_DEEP_RED

            # Body box
            body_box = slide.shapes.add_textbox(
                inner_left,
                content_top + title_height + title_body_gap,
                inner_width,
                body_height,
            )

            body_tf = body_box.text_frame
            body_tf.clear()
            body_tf.word_wrap = True
            body_tf.vertical_anchor = MSO_ANCHOR.MIDDLE
            body_tf.margin_left = 0
            body_tf.margin_right = 0
            body_tf.margin_top = 0
            body_tf.margin_bottom = 0

            p = body_tf.paragraphs[0]
            p.text = str(
                row["office_guidance"]
            )
            p.alignment = PP_ALIGN.LEFT

            run = p.runs[0]
            run.font.size = Pt(13)
            run.font.color.rgb = COLOR_TEXT

    set_placeholder_text(
        slide,
        13,
        (
            "Decision states, issue counts, and review guidance "
            "are reproduced directly from the diagnosis framework output."
        ),
    )

    return slide


def add_workflow_slide(
    prs: Presentation,
):
    """
    Add a readable Office-style end-to-end workflow slide.
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
        {
            "number": "01",
            "title": "Data-State Analysis",
            "description": (
                "Assess completeness, consistency, and "
                "fund- and manager-level data quality."
            ),
        },
        {
            "number": "02",
            "title": "Quarterly Cleaning Pipeline",
            "description": (
                "Re-run quality checks and compare results "
                "with the prior reporting baseline."
            ),
        },
        {
            "number": "03",
            "title": "PDF Validation",
            "description": (
                "Compare structured holdings with source-report "
                "evidence from validated PDF outputs."
            ),
        },
        {
            "number": "04",
            "title": "Diagnosis & Reliability",
            "description": (
                "Assess root cause, severity, evidence strength, "
                "and diagnosis confidence."
            ),
        },
        {
            "number": "05",
            "title": "Office Decision Support",
            "description": (
                "Translate exceptions into structured review "
                "actions and escalation guidance."
            ),
        },
    ]


    # Layout: 3 cards on top, 2 centered below
    card_width = int(width * 0.285)
    card_height = int(height * 0.34)

    horizontal_gap = int(width * 0.045)
    vertical_gap = int(height * 0.09)

    row1_top = top + int(height * 0.06)
    row2_top = row1_top + card_height + vertical_gap

    row1_total = (
        card_width * 3
        + horizontal_gap * 2
    )

    row1_left = (
        left
        + int((width - row1_total) / 2)
    )

    row2_total = (
        card_width * 2
        + horizontal_gap
    )

    row2_left = (
        left
        + int((width - row2_total) / 2)
    )

    positions = [
        (
            row1_left,
            row1_top,
        ),
        (
            row1_left
            + card_width
            + horizontal_gap,
            row1_top,
        ),
        (
            row1_left
            + 2 * (
                card_width
                + horizontal_gap
            ),
            row1_top,
        ),
        (
            row2_left,
            row2_top,
        ),
        (
            row2_left
            + card_width
            + horizontal_gap,
            row2_top,
        ),
    ]

    for i, stage in enumerate(stages):
        card_left, card_top = positions[i]


        # Card
        card = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            card_left,
            card_top,
            card_width,
            card_height,
        )

        card.fill.solid()
        card.fill.fore_color.rgb = COLOR_WHITE

        card.line.color.rgb = COLOR_BORDER
        card.line.width = Pt(1.25)


        # Number tag
        tag_width = int(card_width * 0.22)
        tag_height = int(card_height * 0.17)

        tag = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            card_left + int(card_width * 0.07),
            card_top + int(card_height * 0.07),
            tag_width,
            tag_height,
        )

        tag.fill.solid()
        tag.fill.fore_color.rgb = COLOR_DEEP_RED
        tag.line.fill.background()

        tag_tf = tag.text_frame
        tag_tf.clear()
        tag_tf.vertical_anchor = MSO_ANCHOR.MIDDLE

        p = tag_tf.paragraphs[0]
        p.text = stage["number"]
        p.alignment = PP_ALIGN.CENTER

        run = p.runs[0]
        run.font.size = Pt(11)
        run.font.bold = True
        run.font.color.rgb = COLOR_WHITE


        # Title — larger and given more vertical space
        title_box = slide.shapes.add_textbox(
            card_left + int(card_width * 0.07),
            card_top + int(card_height * 0.29),
            int(card_width * 0.86),
            int(card_height * 0.28),
        )

        title_tf = title_box.text_frame
        title_tf.clear()
        title_tf.word_wrap = True
        title_tf.margin_left = 0
        title_tf.margin_right = 0
        title_tf.margin_top = 0
        title_tf.margin_bottom = 0

        p = title_tf.paragraphs[0]
        p.text = stage["title"]

        run = p.runs[0]
        run.font.size = Pt(16)
        run.font.bold = True
        run.font.color.rgb = COLOR_DARK_BROWN


        # Description — moved lower to prevent overlap
        desc_box = slide.shapes.add_textbox(
            card_left + int(card_width * 0.07),
            card_top + int(card_height * 0.61),
            int(card_width * 0.86),
            int(card_height * 0.30),
        )

        desc_tf = desc_box.text_frame
        desc_tf.clear()
        desc_tf.word_wrap = True
        desc_tf.margin_left = 0
        desc_tf.margin_right = 0
        desc_tf.margin_top = 0
        desc_tf.margin_bottom = 0

        p = desc_tf.paragraphs[0]
        p.text = stage["description"]

        run = p.runs[0]
        run.font.size = Pt(11)
        run.font.color.rgb = COLOR_TEXT


    # Horizontal arrows: 1 → 2 → 3
    for i in [0, 1]:
        x = (
            positions[i][0]
            + card_width
        )

        y = (
            row1_top
            + int(card_height * 0.45)
        )

        arrow = slide.shapes.add_textbox(
            x,
            y,
            horizontal_gap,
            int(card_height * 0.15),
        )

        arrow_tf = arrow.text_frame
        arrow_tf.clear()

        p = arrow_tf.paragraphs[0]
        p.text = "→"
        p.alignment = PP_ALIGN.CENTER

        run = p.runs[0]
        run.font.size = Pt(20)
        run.font.bold = True
        run.font.color.rgb = COLOR_DARK_BROWN


    # Transition 3 ↓ 4
    down_arrow = slide.shapes.add_textbox(
        left + int(width * 0.485),
        row1_top + card_height,
        int(width * 0.06),
        vertical_gap,
    )

    p = down_arrow.text_frame.paragraphs[0]
    p.text = "↓"
    p.alignment = PP_ALIGN.CENTER

    run = p.runs[0]
    run.font.size = Pt(20)
    run.font.bold = True
    run.font.color.rgb = COLOR_DARK_BROWN


    # Horizontal arrow: 4 → 5
    arrow = slide.shapes.add_textbox(
        row2_left + card_width,
        row2_top + int(card_height * 0.45),
        horizontal_gap,
        int(card_height * 0.15),
    )

    p = arrow.text_frame.paragraphs[0]
    p.text = "→"
    p.alignment = PP_ALIGN.CENTER

    run = p.runs[0]
    run.font.size = Pt(20)
    run.font.bold = True
    run.font.color.rgb = COLOR_DARK_BROWN

    set_placeholder_text(
        slide,
        13,
        (
            "Each stage consumes existing upstream outputs and adds "
            "a progressively more operational layer of review."
        ),
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


    # Workflow
    add_workflow_slide(
        prs
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
            summary_df = summary_df.dropna(
                how="all"
            )

            summary_df = summary_df[
                summary_df["Metric"].isin(
                    PIPELINE_SUMMARY_METRICS
                )
            ].copy()

            add_table_slide(
                prs,
                "Quarterly Pipeline Summary",
                summary_df,
                note=(
                    "Source: current Ops report. "
                    "Selected summary metrics are reproduced directly "
                    "from the existing workbook; full issue-level detail "
                    "remains available in the Ops report."
                ),
                max_rows=10,
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
            diagnosis_exec = diagnosis_overview[
                diagnosis_overview["metric"].isin(
                    DIAGNOSIS_OVERVIEW_METRICS
                )
            ].copy()
            
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
                max_rows=8,
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
            add_decision_queue_slide(
                prs,
                decision_queue,
            )

    prs.save(
        DEFAULT_DECK_OUTPUT
    )

    print(
        f"Deck written to: {DEFAULT_DECK_OUTPUT}"
    )


if __name__ == "__main__":
    build_deck()