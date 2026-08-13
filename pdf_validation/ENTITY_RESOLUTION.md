# Entity Resolution — Phase 3

## Problem

Companies appear under different names across funds:
- "Oomnitz" (one fund's schedule) vs "Oomnitza, Inc." (another fund's schedule)
- "Alphabet CLA" vs "Alphabet, Inc."
- "Waymo LLC Series C" vs "Waymo LLC"

This breaks cross-fund analysis. Deal Status inference (Phase 1) and portfolio analytics both need a canonical company identity.

## Solution

Three-step workflow:

1. **Collect** — gather all fuzzy match candidates from the PDF validation pipeline
2. **Review** — human approves high-confidence matches
3. **Export** — save approved mappings to `configs/entity_aliases.json`

Then in Phase 2 cleaning pipeline, these aliases are used to resolve company identities before vendor comparison and deal status inference.

---

## Data Flow

```
entity_candidates.jsonl (from each fund's PDF validation)
         ↓
    EntityResolver.collect_candidates()
         ↓
    [Deduplicated, ranked by similarity]
         ↓
    EntityResolver.confirm_batch(interactive=True)
         ↓
    entity_aliases.json ← approved mappings
         ↓
    resolve_alias() lookup in cleaning pipeline
```

---

## Workflow

### Step 1: Collect Candidates

```bash
cd FundHoldings/pdf_validation
python -m src.pdf_validation.entity_resolution_cli collect --threshold 0.85
```

Output shows:
- Total candidates by similarity band
- Top 20 examples
- Funds involved

### Step 2: Review and Approve

```bash
python -m src.pdf_validation.entity_resolution_cli review --threshold 0.85 --interactive
```

For each candidate:
```
[1/425] Apollo Information Systems Corp. → Apollo Information Systems Corp.
    Similarity: 100%
    Fund: A103ce5

  [A]ccept / [S]kip / [R]eject? a
  Notes (optional)? Exact match after normalization
```

Press:
- **A** — approve this mapping (will add to aliases.json)
- **S** — skip (don't decide now)
- **R** — reject (don't map these)

### Step 3: Export Approved Mappings

```bash
python -m src.pdf_validation.entity_resolution_cli export \
    --threshold 0.90 \
    --out configs/entity_aliases.json \
    --mode append
```

- `--threshold 0.90` — auto-approve anything ≥90% similar (no human review)
- `--mode append` — merge with existing mappings (default)
- `--mode replace` — overwrite entire file (use with caution)

---

## Understanding Similarity Scores

| Score | Meaning | Example |
|-------|---------|---------|
| 1.0 | Identical after normalization | "Oomnitza, Inc." → "Oomnitza, Inc." |
| 0.95–0.99 | Minor punctuation/case diff | "Oomnitz" → "Oomnitza, Inc." |
| 0.85–0.94 | Abbreviation or slight typo | "Cranium AI" → "Cranium Al" |
| <0.85 | Risky — possible false positive | Often rejected |

### How Similarity is Calculated

Using `rapidfuzz.fuzz.ratio()`:
- Converts both names to lowercase
- Strips punctuation
- Computes string edit distance
- Returns similarity as 0.0–1.0

---

## Safety Guardrails

1. **No auto-confirmation.** Candidates must be human-reviewed before aliases.json is updated.
2. **Deduplication.** Same (pdf_name, vendor_name) pair across multiple funds counted once.
3. **Fund-specific overrides.** If a mapping is fund-specific (appears in only one fund), store it under `by_fund[fund_id]` instead of global.
4. **Audit trail.** Each approved mapping records:
   - `pdf_company_name`
   - `vendor_source_asset_candidate`
   - `similarity` score
   - `reviewer_notes` (if added during interactive review)
   - `confirmation_method` (fuzzy, exact, user_manual)

---

## Integration with Phase 2 Cleaning Pipeline

In `cleaning_pipeline/run_quarterly_refresh.py`:

```python
from pdf_validation.entity_mapping import resolve_alias

aliases = load_aliases()
for row in company_rows:
    canonical_name = resolve_alias(row['company_name'], aliases=aliases)
    # Use canonical_name for downstream comparisons
```

This is already wired into `entity_mapping.py`. Once aliases.json is updated, it's automatic.

---

## Example Session

```bash
$ cd FundHoldings/pdf_validation
$ python -m src.pdf_validation.entity_resolution_cli collect --threshold 0.85

Found 26 entity_candidates.jsonl files
Collected 425 unique candidates (after dedup)

Entity Resolution Summary
==================================================
Total candidates collected: 425
  perfect (1.0): 312 candidates
  high (0.95–0.99): 48 candidates
  good (0.85–0.94): 65 candidates

Top candidates (threshold=0.85):
  Oomnitz                                  → Oomnitza, Inc.                          (93.33%)
  Cranium AI, Inc                          → Cranium Al, Inc                         (94.12%)
  Alphabet CLA                             → Alphabet, Inc.                          (81.25%)
  Waymo LLC Series C                       → Waymo LLC                               (96.77%)
  ... and 8 more
```

Then review:

```bash
$ python -m src.pdf_validation.entity_resolution_cli review --threshold 0.90

Reviewing 360 candidates (threshold=0.90)

[1/360] Oomnitz → Oomnitza, Inc.
    Similarity: 93%
    Fund: A0d2a71
  [A]ccept / [S]kip / [R]eject? a
  Notes (optional)? Confirmed in Phase 1 analysis

[2/360] Waymo LLC Series C → Waymo LLC
    Similarity: 97%
    Fund: A729d8b
  [A]ccept / [S]kip / [R]eject? a
  Notes (optional)? Series designation removed in canonical name

...

Approved 358 / 360 candidates
```

Finally export:

```bash
$ python -m src.pdf_validation.entity_resolution_cli export --threshold 0.85 --mode append

Auto-approving 412 candidates above 0.85
Saved 412 approved mappings to /path/to/configs/entity_aliases.json
```

---

## Troubleshooting

### "Entity resolution requires rapidfuzz"

Install it:
```bash
pip install rapidfuzz
```

### Similarity score seems wrong

Scores are computed by `rapidfuzz.fuzz.ratio()`, which:
- Ignores case
- Strips punctuation
- Runs Levenshtein distance

If you disagree with a score, approve/reject it manually in interactive mode.

### I want to re-run entity resolution

Just run the review again. Interactive mode will ask you to re-confirm anything that wasn't skipped before. Rejected candidates are not re-asked.

### Can I undo approved mappings?

Edit `configs/entity_aliases.json` directly, or export with `--mode replace` (which overwrites the entire file). Make sure you understand the consequences.

---

## Status

- ✅ Candidate collection from PDFs
- ✅ Deduplication and ranking
- ✅ Interactive approval CLI
- ✅ Alias export and storage
- ✅ Integration with entity_mapping.resolve_alias()
- ⏳ Integration into Phase 2 cleaning pipeline (ready, not yet wired)
- ⏳ Cross-fund analytics using canonical names (Phase 3 extension)

---

## Next Steps

1. **Run collect** to see the full candidate set
2. **Run review with high threshold** (0.95+) to approve obvious matches first
3. **Lower threshold gradually** (0.90, 0.85) to pick up more fuzzy matches
4. **Export approved mappings** to entity_aliases.json
5. **Re-run Phase 1 analysis** with updated aliases to verify deal status inference improves
