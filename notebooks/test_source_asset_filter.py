'''Regression tests for Source Asset classification.

Run from the notebooks directory:  python -m pytest test_source_asset_filter.py -q

The cases below are drawn from the actual extract. The false-positive cases
matter most: several real portfolio companies contain the words 'cash' and
'tax', and SPV or co-investment vehicles are genuine exposure, so a filter
that matches on substrings would silently delete real holdings.
'''

import pandas as pd
import pytest

from source_asset_filter import annotate, classify_source_asset, needs_review, summarise


# --- rows that must be excluded from scoring -------------------------------

@pytest.mark.parametrize('name', [
    'Non-Investment Assets - Cash',
    'Non-Investment Liabilities - Other Liabilities',
    'Non-Investmentsa Assets - Cash and cash equivalents',
    'Non-Investment Asset - Cash',                 # singular, missed by the old filter
    'Net Non-Investment Assets from Canyon Laurell II Master Fund',
])
def test_balance_sheet(name):
    assert classify_source_asset(name) == 'balance_sheet'


@pytest.mark.parametrize('name', [
    'Quarterly Unrealized Gain/Loss',
    'Quarterly Unrealized Gains/Losses',
    'Annual Current Cost Gain/Loss',
    'Annual Current Cost Gain/loss',
    'AFS Gain/Loss',
    'ASC 740-10 Accrual',
    'Tax Distributions',
    'Cumulative unrealized loss from foreign currency translation',
    'Foreign Currency Translation',
    'Cash',
    'Idle Cash',
    'Pending Escrow',
    'AO Football Transfer Receivable',
    'Realized Proceeds Applied to Loan Repayment',
])
def test_accounting_entry(name):
    assert classify_source_asset(name) == 'accounting_entry'


@pytest.mark.parametrize('name', [
    'Subtotal Private Investments',
    'Subtotal Crypto Assets',
    'Total Portfolio Investments',
])
def test_subtotal(name):
    assert classify_source_asset(name) == 'subtotal'


# --- rows that are real exposure but not entities --------------------------

@pytest.mark.parametrize('name', [
    'Other Investments',
    'Other',
    'Other - United States - Real Estate',
    'Other seed investments',
    'Seed Investments',
    'Active Seed Deals',
    'Realized Seed Deals',
    'Seed 2023-P',
    'Diversified Credit',
    'Residential',
    'Commercial Real Estate Debt & Equity Securities',
    'Fund Investments',
    'Passive Investments',
    'Recycled Investment',
    'Secondary Market Investment',
])
def test_unnamed_aggregate(name):
    assert classify_source_asset(name) == 'unnamed_aggregate'


# --- labels and placeholders surfaced by Phase 3 ---------------------------
# Every case below is a real Source Asset value that reached entity resolution
# classified as 'holding' and had to be excluded by hand there before fuzzy
# matching merged it into a fake company. They belong to the classifier, not
# to resolve.py, so both phases agree on what a company is.

@pytest.mark.parametrize('name', [
    # Sector/geography label in the company-name column. 43 rows, 34 of them
    # from one manager (A4a62c2) -- a mapping fault at a single source.
    'United States - Information Technology',
    'France - Consumer Staples',
    'United Kingdom - Real Estate',
    'South America - Real Estate',
    'United States - Materials',
    'United States - Technology',
    'United States - Insurance',
    'United States - Financial',
    'Information Technology - United States',        # reversed order
    'Technology - United States - Insurance',        # three segments

    # A GP's own redaction: region/sector plus a bare letter. 16 rows, all from
    # manager A99a662. 'company A' and 'company B' score 96% similar, so fuzzy
    # matching would otherwise fuse two deliberately distinct holdings.
    'Europe - Materials company A',
    'Europe - Utilities company B',
    'North America - Energy company A',
    'North America - Information technology company A',
    'North America - Information technology company B',
    'North America - Legal services mixed company C',
    'Global - Legal services mixed company B',
    'North America - Pharmaceuticals, biotechnology and life sciences company B',

    # The extract's anonymisation placeholder, reused for three unrelated
    # masked positions inside the same fund -- not a currency or vintage
    # variant of one company.
    'Source Code',
    'Source Code II',
    'Source Code RMB',

    # Aggregate placeholder for undisclosed holdings, same role as
    # 'Seed Investments'. 23 rows across 4 funds.
    'Various',
])
def test_label_placeholder(name):
    assert classify_source_asset(name) == 'unnamed_aggregate'


# --- false positives: these must survive as holdings -----------------------

@pytest.mark.parametrize('name', [
    'Stripe',
    'Instacart',
    'Itz Cash (Itz Cash Card Limited)',      # contains 'cash'
    'Keeper Tax Inc.',                       # contains 'tax'
    'Gamma Labs, Inc. (dba Column Tax)',     # contains 'tax'
    'Commercial Bakeries',                   # starts with 'commercial'
    'Valar Co-Invest 13 LP (Qonto)',         # co-investment vehicle
    'Elephant Partners 2023 SPV-A, L.P. (Fleet.io)',
    'WestCap NYDIG Co-Invest 2021, LLC',
    'Bitmain (Crimson Partners SPV)',
    'Axonius, Inc.',
    'Bitcoin (BTC)',
    # Guards on the label patterns above. The dash-separated rule only fires
    # when BOTH sides come from the region/sector vocabulary, so a real name
    # carrying a dash, a region word, or a sector word must stay a holding.
    'Bridge Point - Series A',
    'Aera Technology - Series D',
    'United Airlines',                       # region word, no dash
    'Energy Vault Holdings, Inc.',           # sector word, no dash
    'Real Estate Webmasters',                # sector word leading the name
    'Global Payments Inc.',                  # region word leading the name
    'Insurance Technologies Corporation',
    'Source Code Capital Fund IV',           # placeholder prefix, real fund
    'Company A Holdings Ltd',                # 'company' + letter, but no dash
])
def test_real_holdings_survive(name):
    assert classify_source_asset(name) == 'holding'


# --- review queue ----------------------------------------------------------

@pytest.mark.parametrize('name', [
    'Edge Investments',
    'Hero Investments',
    'Thales Investment',
    'Greylock Scout Investments',
])
def test_review_queue_flagged_not_excluded(name):
    '''Generic-sounding names are flagged for a human, never auto-excluded.'''
    assert needs_review(name) is True
    assert classify_source_asset(name) == 'holding'


def test_stripe_not_flagged():
    assert needs_review('Stripe') is False


# --- frame-level behaviour -------------------------------------------------

def test_annotate_flags():
    df = pd.DataFrame({'Source Asset': [
        'Stripe',                          # holding
        'Other Investments',               # unnamed_aggregate
        'ASC 740-10 Accrual',              # accounting_entry
        'Non-Investment Assets - Cash',    # balance_sheet
        'Subtotal Private Investments',    # subtotal
    ]})
    out = annotate(df)
    assert out['is_scorable'].tolist() == [True, True, False, False, False]
    assert out['is_resolvable'].tolist() == [True, False, False, False, False]


def test_blank_is_not_a_holding():
    assert classify_source_asset(None) == 'accounting_entry'
    assert classify_source_asset('   ') == 'accounting_entry'


def test_summarise_covers_every_row():
    df = pd.DataFrame({'Source Asset': [
        'Stripe', 'Other Investments', 'ASC 740-10 Accrual',
        'Non-Investment Assets - Cash', 'Total Portfolio Investments',
    ]})
    assert summarise(df)['rows'].sum() == len(df)
