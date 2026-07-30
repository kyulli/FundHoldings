import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

def _find_syn_q3_pdf() -> Path:
    """Locate the SYN Ventures Q3 2025 PDF across known sample locations.

    The same document lives under two names depending on how it was staged:
    an underscore-normalized copy under samples/, and the original
    space-separated filename under sample_data/. Both are checked so the
    golden tests run regardless of which copy is present locally.
    """
    candidates = [
        ROOT / "samples" / "A0d2a71" / "SYN_Ventures_Fund_II_LP_Q3_2025_Financial_Statements.pdf",
        ROOT / "samples" / "SYN_Ventures_Fund_II_LP_Q3_2025_Financial_Statements.pdf",
        ROOT.parent / "sample_data" / "A0d2a71" / "SYN Ventures Fund II LP - Q3 2025 Financial Statements.pdf",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    # Return the preferred path so the test assertion reports a stable location.
    return candidates[0]


PDF = _find_syn_q3_pdf()
CONFIG = ROOT / "configs" / "syn_ventures_fund_ii_q3_2025.json"
OUTPUT = ROOT / "outputs" / "syn_q3_2025_test"
