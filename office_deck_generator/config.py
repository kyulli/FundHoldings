from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

OFFICE_TEMPLATE = (
    REPO_ROOT
    / "office_templates"
    / "office_report_template.pptx"
)

REPORTS_DIR = REPO_ROOT / "reports"

CLEANING_PIPELINE_DIR = (
    REPO_ROOT / "cleaning_pipeline"
)

CLEANING_RUNS_DIR = (
    CLEANING_PIPELINE_DIR / "runs"
)

PDF_VALIDATION_DIR = (
    REPO_ROOT / "pdf_validation"
)

DIAGNOSIS_DIR = (
    REPO_ROOT / "diagnosis_reliability"
)

DIAGNOSIS_OUTPUT = (
    DIAGNOSIS_DIR
    / "outputs"
    / "diagnosis_reliability_report.xlsx"
)

OUTPUT_DIR = (
    REPO_ROOT
    / "office_deck_generator"
    / "outputs"
)

DEFAULT_DECK_OUTPUT = (
    OUTPUT_DIR
    / "office_report_deck.pptx"
)

DATA_STATE_IMAGES = [
    REPORTS_DIR / "completeness_by_dimension.png",
    REPORTS_DIR / "fund_quality_distribution_outliers.png",
    REPORTS_DIR / "manager_field_heatmap.png",
    REPORTS_DIR / "portfolio_concentration.png",
    REPORTS_DIR / "quality_by_investment_type.png",
]

CURRENT_OPS_REPORT = (
    CLEANING_RUNS_DIR
    / "2426q1_new_report"
    / "2426q1_ops_report.xlsx"
)
