import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

PDF = ROOT / "samples" / "SYN_Ventures_Fund_II_LP_Q3_2025_Financial_Statements.pdf"
CONFIG = ROOT / "configs" / "syn_ventures_fund_ii_q3_2025.json"
OUTPUT = ROOT / "outputs" / "syn_q3_2025_test"
