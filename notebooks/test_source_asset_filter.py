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
