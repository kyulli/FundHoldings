# Phase 3 Integration Guide

## Overview

Phase 3 entity resolution produces canonical company name mappings via fuzzy matching + human review. This guide shows how to integrate those mappings into:

1. **Phase 2 cleaning pipeline** — use aliases in PDF spot-checking
2. **Phase 1 deal status inference** — use canonical names to cross-reference schedules
3. **Analytics exports** — enable cross-fund portfolio comparisons

---

## Part 1: Entity Resolution Setup (One-Time)

### A. Collect All Candidates

```bash
cd FundHoldings/pdf_validation
python -m src.pdf_validation.entity_resolution_cli collect --threshold 0.85
```

Expected output:
```
Found 26 entity_candidates.jsonl files
Collected 425 unique candidates (after dedup)

Entity Resolution Summary
==================================================
Total candidates collected: 425
  perfect (1.0): 312 candidates
  high (0.95–0.99): 48 candidates
  good (0.85–0.94): 65 candidates
  ...
```

### B. Interactive Review (Approx 30–60 minutes)

Start with high confidence (≥95%):

```bash
python -m src.pdf_validation.entity_resolution_cli review --threshold 0.95
```

Then lower threshold:

```bash
python -m src.pdf_validation.entity_resolution_cli review --threshold 0.85
```

For each candidate, decide:
- **Accept [A]** — these are the same company
- **Skip [S]** — come back to this later
- **Reject [R]** — these are different companies

Example interaction:
```
[47/312] Oomnitz → Oomnitza, Inc.
    Similarity: 93%
    Fund: A0d2a71
  [A]ccept / [S]kip / [R]eject? a
  Notes (optional)? Confirmed in Phase 1 exception list
```

### C. Export Approved Mappings

Once you've reviewed candidates, export:

```bash
python -m src.pdf_validation.entity_resolution_cli export \
    --threshold 0.90 \
    --mode append
```

This auto-approves anything ≥90% and saves to `configs/entity_aliases.json`.

Check the result:

```python
import json
with open('FundHoldings/pdf_validation/configs/entity_aliases.json') as f:
    aliases = json.load(f)
    print(f"Global mappings: {len(aliases['global'])}")
    print(f"By-fund overrides: {len(aliases['by_fund'])} funds")
    # Sample some:
    for k, v in list(aliases['global'].items())[:5]:
        print(f"  {k} → {v}")
```

---

## Part 2: Phase 2 Cleaning Pipeline Integration

### Currently Implemented

The `entity_mapping.py` module already has:

```python
def resolve_alias(pdf_name: str, *, fund_id: str | None = None, aliases: dict[str, Any] | None = None) -> str | None
```

This function:
1. Normalizes the PDF company name
2. Checks fund-specific overrides first (if `fund_id` is provided)
3. Falls back to global aliases
4. Returns canonical vendor name or None if not found

### Wiring into PDF Spot-Check

In `cleaning_pipeline/pdf_check.py`:

```python
from pdf_validation.entity_mapping import resolve_alias, load_aliases

def spot_check_pdfs(fund_id, pdf_path, csv_rows):
    """Spot-check extracted PDFs against vendor data."""
    aliases = load_aliases()
    
    # Extract company names from PDF (via Camelot/pdfplumber/template)
    extracted_companies = extract_companies_from_pdf(pdf_path)
    
    # Resolve to canonical names
    canonical_companies = []
    for name in extracted_companies:
        canonical = resolve_alias(name, fund_id=fund_id, aliases=aliases)
        if canonical:
            canonical_companies.append(canonical)
        else:
            canonical_companies.append(name)  # No alias, use original
    
    # Now compare canonical names to CSV
    for row in csv_rows:
        csv_name = row['Company Name']
        csv_canonical = resolve_alias(csv_name, fund_id=fund_id, aliases=aliases)
        
        # Match logic:
        if csv_canonical in canonical_companies:
            print(f"✓ {csv_name} found in PDF as {csv_canonical}")
        else:
            print(f"✗ {csv_name} NOT FOUND in PDF")
```

### Wiring into Deal Status Inference

In `Phase1/phase1_holdings_data_state_analysis.ipynb`, cell ~60 (deal status derivation):

```python
from pdf_validation.entity_mapping import resolve_alias, load_aliases

aliases = load_aliases()

def infer_deal_status(company_name, fund_id, schedule_investments, schedule_realized):
    """Infer deal status by matching company across schedules."""
    
    # Canonicalize
    canonical = resolve_alias(company_name, fund_id=fund_id, aliases=aliases)
    if not canonical:
        canonical = company_name.lower().strip()
    
    # Check both schedules
    in_investments = any(
        resolve_alias(c, fund_id=fund_id, aliases=aliases) == canonical
        for c in schedule_investments.get('company_name', [])
    )
    in_realized = any(
        resolve_alias(c, fund_id=fund_id, aliases=aliases) == canonical
        for c in schedule_realized.get('company_name', [])
    )
    
    # Derive status
    if in_investments and not in_realized:
        return 'Current'
    elif in_investments and in_realized:
        return 'Partially Exited'
    elif in_realized and not in_investments:
        return 'Fully Exited'
    else:
        return 'Unknown'
```

---

## Part 3: Quarterly Refresh with Entity Resolution

### Updated Quarterly Workflow

When running `cleaning_pipeline/run_quarterly_refresh.py`:

```bash
# Phase 1: Update Phase 1 analysis (if needed)
# python FundHoldings/notebooks/phase1_holdings_data_state_analysis.ipynb --refresh

# Phase 2: Run cleaning pipeline
cd FundHoldings
python cleaning_pipeline/run_quarterly_refresh.py \
    --baseline-date 2025-12-31 \
    --snapshot-date 2026-03-31 \
    --use-entity-resolution
```

The `--use-entity-resolution` flag:
1. Loads `configs/entity_aliases.json`
2. Canonicalizes all company names before comparison
3. Improves PDF ↔ CSV matching accuracy
4. Reduces false "mismatch" flags in ops_report.xlsx

### Testing Entity Resolution Quality

After exporting aliases, re-run Phase 1 analysis:

```bash
# Regenerate Phase 1 completeness scores with aliases
python -c "
from pathlib import Path
import sys
sys.path.insert(0, 'FundHoldings/notebooks')

# Re-run deal status inference with updated aliases
# Check if exception list shrinks (fewer ambiguous cases)
"
```

---

## Part 4: Cross-Fund Analytics (Future)

Once entity resolution is solid, you can:

### A. Build Company-Centric Portfolio View

```python
from pdf_validation.entity_mapping import resolve_alias, load_aliases

aliases = load_aliases()
company_portfolio = {}

for fund_csv in all_fund_csvs:
    fund_id = fund_csv.name.split('_')[0]
    for row in read_csv(fund_csv):
        canonical = resolve_alias(row['Company Name'], fund_id=fund_id, aliases=aliases)
        if canonical not in company_portfolio:
            company_portfolio[canonical] = []
        company_portfolio[canonical].append({
            'fund': fund_id,
            'holdings': row['Shares'] * row['Price'],
            'cost': row['Cost'],
        })

# Now you can ask:
# - Which funds own "Apple, Inc."?
# - Total portfolio exposure to each company?
# - Which companies are held by multiple funds?
```

### B. Consistency Checks

```python
# Flag: same company appears as different deal statuses across funds
for company, holdings in company_portfolio.items():
    statuses = {h['fund']: infer_deal_status(...) for h in holdings}
    if len(set(statuses.values())) > 1:
        print(f"⚠ {company} has conflicting status: {statuses}")
```

### C. Export for Dashboards

```python
company_summary = []
for company, holdings in company_portfolio.items():
    company_summary.append({
        'canonical_name': company,
        'num_funds': len(set(h['fund'] for h in holdings)),
        'total_exposure': sum(h['holdings'] for h in holdings),
        'funds': ', '.join(set(h['fund'] for h in holdings)),
    })

pd.DataFrame(company_summary).to_csv('cross_fund_company_summary.csv', index=False)
```

---

## Quick Reference

### Approve High-Confidence Matches Only

```bash
# Approve 100% matches and very high confidence (95%+)
python -m src.pdf_validation.entity_resolution_cli export --threshold 0.95
```

### Review Manually (Recommended First Pass)

```bash
# Interactive: approve one at a time
python -m src.pdf_validation.entity_resolution_cli review --threshold 0.95 --limit 50
# Then export
python -m src.pdf_validation.entity_resolution_cli export --threshold 0.85 --mode append
```

### Load Aliases Programmatically

```python
from pdf_validation.entity_mapping import load_aliases, resolve_alias

aliases = load_aliases()
canonical = resolve_alias("Oomnitz", fund_id="A0d2a71", aliases=aliases)
# Returns: "Oomnitza, Inc."
```

### Check Current Aliases

```bash
python -c "
from pathlib import Path
import json
aliases_path = Path('FundHoldings/pdf_validation/configs/entity_aliases.json')
with aliases_path.open() as f:
    aliases = json.load(f)
    print(f'Global: {len(aliases[\"global\"])} mappings')
    print(f'By-fund: {len(aliases[\"by_fund\"])} fund-specific sets')
"
```

---

## Troubleshooting

### "Some candidates rejected in review still show up in export"

They shouldn't. If they do, it's a bug. In interactive mode, rejected candidates are **not** saved to results. Only accepted [A] candidates are added to results.

### "Similarity scores don't match my intuition"

Scores are algorithmic (Levenshtein distance). Review candidates interactively and use [R]eject if you disagree.

### "I want to remove a mapping from entity_aliases.json"

Edit the file directly:
```json
{
  "global": {
    // Delete the line you don't want
  }
}
```

Or regenerate from scratch with `--mode replace` (caution: overwrites).

---

## Files Modified/Created

| File | Role | Status |
|------|------|--------|
| `entity_resolver.py` | Core entity resolution logic | ✅ Created |
| `entity_resolution_cli.py` | CLI for collect/review/export | ✅ Created |
| `entity_mapping.py` | Alias loading and resolution (existing) | ✅ Already integrated |
| `configs/entity_aliases.json` | Approved mappings (updated) | ⏳ To be updated |
| `ENTITY_RESOLUTION.md` | User documentation | ✅ Created |
| `Phase 2 cleaning_pipeline` | Will use aliases automatically | ⏳ No changes needed |
| `Phase 1 deal_status.py` | Will use aliases for cross-schedule matching | ⏳ Ready to integrate |

---

## Timeline Estimate

- **Collect + Review**: 1–2 hours
- **Export + Validation**: 30 min
- **Phase 2 re-test**: 30 min
- **Phase 1 re-analysis**: 15 min

**Total**: ~3 hours to operationalize entity resolution.
