'''Classify Source Asset strings into investment and non-investment categories.

The vendor extract puts every line of a GP schedule into one Source Asset column.
That column mixes three different things:

  1. Real portfolio holdings          -> score them, resolve them
  2. Balance sheet and journal entries -> never score them
  3. Unnamed aggregation buckets       -> score them, never resolve them

Category 2 is the one the original filter missed. It only matched strings
starting with 'Non-Investment Assets' or 'Non-Investment Liabilities', so
accounting entries that arrive without that prefix stayed in the population.
The two largest, 'Quarterly Unrealized Gain/Loss' and 'ASC 740-10 Accrual',
were the two most frequently reported 'companies' in the entire book.

Category 3 matters for a different reason. 'Other Investments' is a real
economic exposure that belongs in a completeness score, but it is not an
entity, so it can never be matched across funds. Treating it as a company
inflates the entity resolution work queue with rows that can never resolve.

Design rules
------------
Patterns are anchored and explicit. Substring matching on words like 'cash'
or 'tax' is not safe here, because the book contains real companies named
'Itz Cash (Itz Cash Card Limited)', 'Keeper Tax Inc.' and
'Gamma Labs, Inc. (dba Column Tax)'.

SPV and co-investment vehicles are investments, not artefacts. A row named
'Valar Co-Invest 13 LP (Qonto)' is exposure to Qonto and must be kept.

Names that look generic but may be real entities are not classified. They are
returned in a review queue for a human to decide, consistent with the project
rule that automatic matching may propose candidates but not accept them.
'''

import re

import pandas as pd

# --- Category 1: balance sheet lines carrying the vendor prefix -------------
# Kept from the original implementation. Covers the spelling variants observed
# in the extract, including 'Non-Investmentsa' and the singular 'Asset'.
BALANCE_SHEET_PREFIX = re.compile(
    r'(?i)^\s*(?:Net\s+)?Non[- ]?Investments?a?\s+'
    r'(?:Assets?|Liabilities)'
)

# --- Category 2: accounting and valuation entries --------------------------
# Anchored to the full string wherever the name is a fixed label, so that a
# real company containing the same word is not swept up.
ACCOUNTING_ENTRY = [
    # Valuation movements booked as their own line
    r'(?i)^\s*(?:quarterly|annual|cumulative)\s+.*gain\s*/?\s*\(?loss(?:es)?\)?\s*$',
    r'(?i)^\s*(?:quarterly|annual)\s+unrealized\s+gains?\s*/?\s*\(?losses?\)?\s*$',
    r'(?i)^\s*AFS\s+gain\s*/?\s*\(?loss\)?\s*$',
    r'(?i)^\s*annual\s+current\s+cost\s+gain\s*/?\s*\(?loss\)?\s*$',
    # Tax and accounting standard accruals
    r'(?i)^\s*ASC\s*\d+[-\d]*\s+accrual\s*$',
    r'(?i)^\s*tax\s+distributions?\s*$',
    # Currency translation
    r'(?i)^\s*(?:cumulative\s+)?(?:unrealized\s+)?(?:gain|loss)?\s*'
    r'.{0,20}foreign\s+currency\s+translation.*$',
    r'(?i)^\s*foreign\s+currency\s+translation\s*$',
    # Cash held outside the prefixed balance sheet lines
    r'(?i)^\s*(?:idle\s+)?cash(?:\s+and\s+cash\s+equivalents)?\.?\s*$',
    # Settlement and receivable items
    r'(?i)^\s*pending\s+escrow\s*$',
    r'(?i)^\s*.*transfer\s+receivable\s*$',
    r'(?i)^\s*realized\s+proceeds\s+applied\s+to\s+loan\s+repayment\s*$',
]

# --- Category 2b: subtotal and total rows ----------------------------------
# These double count holdings that are already listed individually above them.
SUBTOTAL_ROW = [
    r'(?i)^\s*(?:sub)?total\b.*$',
]

# --- Category 3: unnamed aggregation buckets -------------------------------
# Real exposure, not a resolvable entity.
UNNAMED_AGGREGATE = [
    r'(?i)^\s*other\b.*$',                      # Other, Other Investments, Other - US - Tech
    r'(?i)^\s*.*\bseed\s+(?:investments?|deals?)\s*$',
    r'(?i)^\s*seed\s+\d{4}[-\w]*\s*$',          # Seed 2023-P
    r'(?i)^\s*(?:active|realized)\s+seed\s+deals\s*$',
    r'(?i)^\s*diversified\s+credit\s*$',
    r'(?i)^\s*residential\s*$',
    r'(?i)^\s*commercial\s+real\s+estate\s+debt\s*&\s*equity\s+securities\s*$',
    r'(?i)^\s*(?:realized\s+)?fund\s+investments?\s*$',
    r'(?i)^\s*passive\s+investments?\s*$',
    r'(?i)^\s*passive\s*/\s*quickstart\s+and\s+seed\s+investments?\s*$',
    r'(?i)^\s*quickstart\s+and\s+seed\s+investments?\s*$',
    r'(?i)^\s*recycled\s+investments?\s*$',
    r'(?i)^\s*secondary\s+market\s+investments?\s*$',
]

# --- Review queue ----------------------------------------------------------
# Generic-sounding but plausibly real entity names. Never auto-classified.
REVIEW_QUEUE = [
    r'(?i)^\s*(?:edge|hero|thales|greylock\s+scout)\s+investments?\s*$',
]

CATEGORIES = ('balance_sheet', 'accounting_entry', 'subtotal',
              'unnamed_aggregate', 'holding')


def _any(patterns, text):
    return any(re.match(p, text) for p in patterns)


def classify_source_asset(value) -> str:
    '''Return one of CATEGORIES for a single Source Asset string.'''
    text = str(value) if pd.notna(value) else ''
    if not text.strip():
        return 'accounting_entry'
    if BALANCE_SHEET_PREFIX.match(text):
        return 'balance_sheet'
    if _any(SUBTOTAL_ROW, text):
        return 'subtotal'
    if _any(ACCOUNTING_ENTRY, text):
        return 'accounting_entry'
    if _any(UNNAMED_AGGREGATE, text):
        return 'unnamed_aggregate'
    return 'holding'


def needs_review(value) -> bool:
    '''True when a name is generic enough to warrant a human decision.'''
    return _any(REVIEW_QUEUE, str(value) if pd.notna(value) else '')


def annotate(df: pd.DataFrame, column: str = 'Source Asset') -> pd.DataFrame:
    '''Add source_asset_class, is_scorable and is_resolvable to a copy of df.

    is_scorable   rows that belong in a completeness score
    is_resolvable rows that can be matched to a named company across funds
    '''
    out = df.copy()
    out['source_asset_class'] = out[column].map(classify_source_asset)
    out['is_scorable'] = out['source_asset_class'].isin(['holding', 'unnamed_aggregate'])
    out['is_resolvable'] = out['source_asset_class'].eq('holding')
    out['needs_name_review'] = out[column].map(needs_review)
    return out


def summarise(df: pd.DataFrame, column: str = 'Source Asset') -> pd.DataFrame:
    '''Row and name counts per category, for reporting.'''
    annotated = annotate(df, column)
    summary = annotated.groupby('source_asset_class').agg(
        rows=(column, 'size'),
        distinct_names=(column, 'nunique'),
    )
    summary['share_of_rows'] = summary['rows'] / len(annotated)
    order = [c for c in CATEGORIES if c in summary.index]
    return summary.loc[order]
