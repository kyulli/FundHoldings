# Entity Resolution

Collapses the different names GPs use for the same portfolio company into one
canonical entity, so cross-fund questions can be answered at all.

**The one thing this tool decides is: are these two `Source Asset` strings
different spellings of the same real entity, or two different entities?**
Only "yes" merges. Everything else — different fund vintages sharing a GP
name, different currencies inside a fund's own vehicle-naming convention,
anonymisation placeholders, sector labels sitting in the name column — is a
"no," and is either auto-rejected with a logged reason or held for a human.
See "How it decides" below and the two worked examples (`I`/`II` suffixes,
`Source Code RMB`) in the sections that follow.

## The problem

`Source Asset` is free text written by each GP. The same company arrives spelled
several ways:

```
Oomnitz  |  Oomnitza, Inc.
```

Until these collapse, every cross-fund number is wrong. "How much do we hold in
Oomnitz?" returns a partial answer depending on which spelling you happen to
filter on.

The vendor does not solve this. All 425 merged clusters span **more than one
`Source Asset ID`** — the vendor is carrying the same company under multiple
IDs, so its own identifiers cannot be used as ground truth.

## Population: reconciled with Phase 1

Phase 3 must resolve the same set of names Phase 1 scores, or the two
notebooks disagree about what "a company" is. The first version of this tool
did not do that — it filtered `Type of Investment` in `{Private Company,
Public Company}` directly off the raw CSV, giving 7,533 names, while Phase 1's
own **Section 8 (Entity resolution scope)** reports 8,633. Two different
definitions of "resolvable" happened to share a name.

The fix: import `notebooks/source_asset_filter.py` — the same row classifier
Phase 1 uses — and take its `is_resolvable` population directly, rather than
re-deriving a similar-looking filter from a different column. `is_resolvable`
classifies the *text* of `Source Asset` (`holding` vs `balance_sheet` vs
`accounting_entry` vs `subtotal` vs `unnamed_aggregate`) independently of
`Type of Investment`, which is why it does not match a `Type of Investment`
filter: a name can be a real, resolvable holding while `Type of Investment` is
blank, `Other`, or simply wrong on that row.

```
python resolve.py --data ../data/holdings_anonymized.csv --out ./output
  8,633 distinct company names (is_resolvable, matches Phase 1 scope)
```

This now matches `reports/source_asset_classification.csv` exactly
(`holding: 51,770 rows, 8,633 distinct_names`). If Phase 1's classifier
changes, re-running `resolve.py` picks up the change automatically — there is
one classifier, not two.

`--all-rows` skips this filter (includes balance sheet lines, accounting
entries, subtotals, aggregate buckets) for diagnostics only; it is not the
default and should not be used to build aliases.

## Result on the current book

| | |
|---|---|
| Distinct company names in (is_resolvable) | 8,633 |
| Held out as not-a-company (see below) | 21 |
| Auto-merged pairs | 536 |
| Clusters formed | 427 |
| Names absorbed | 475 |
| Distinct names after merge | 8,137 |
| Auto-rejected for a differing vintage/series number (audit trail, no review) | 192 |
| Pairs needing a human | 120 |

Companies with the widest reach after resolution: Stripe (18 funds),
ByteDance (17), Bitcoin (11), Ethereum (10), Meituan (10).

Bitcoin and Ethereum appear here because `is_resolvable` classifies on the
*text* of `Source Asset`, not on `Type of Investment` — a cryptocurrency
ticker written as a plain name is, correctly, in scope for the same spelling
cleanup as a company name.

## How it decides

Four stages, cheapest first.

**1. Normalise.** Case, accents, punctuation, parentheticals, share-class
designations, and ~40 legal suffixes (`Inc`, `LLC`, `PBC`, `GmbH`, `Pvt Ltd`…).
`Anthropic, Inc.` and `Anthropic` both become `anthropic`.

**2. Block.** Comparing 8,633 names pairwise is 37M comparisons. Names are
grouped by exact normalised form, 4-character prefix, and shared token; only
names sharing a block are compared. Runs in about a second.

**3. Score, with IDF weighting.** This is the part that matters. Plain fuzzy
matching is badly fooled by generic words:

```
"Slack Technologies, Inc."  vs  "SiMa Technologies"      token_sort = 86
"Kala Pharmaceuticals"      vs  "Koye Pharmaceuticals"   token_sort = 86
```

Both score high purely because of the shared industry word. So tokens appearing
in more than 15 distinct names are treated as carrying no identity signal, and
each pair is capped at the similarity of its *remaining* rare tokens — `slack`
vs `sima` scores 40, and the pair is dropped.

Measured on the corrected population: without this cap, 2,476 pairs would
need a human decision. With it, **312** (120 review + 192 sequence-conflict
audit entries). Same recall on the true matches used in `test_resolve.py`.

**4. Decide.** In order:

| Rule | Outcome | Example |
|---|---|---|
| Shares a vendor `Source Asset ID` | merge | vendor already asserts identity |
| Identical after normalisation | merge | `Airbnb` / `Airbnb, Inc.` |
| **Sequence number differs** | **auto-reject, logged** | `Scout Fund I` / `Scout Fund II` |
| Identical ignoring word breaks | merge | `AcuityMD` / `Acuity MD` |
| Identical folding OCR confusables | merge | `Harmonic AI` / `Harmonic Al` |
| Score ≥ 97 | merge | |
| Score ≥ 84 | review | |
| Otherwise | reject | |

The sequence-number check runs *before* the mechanical merge rules and before
the score threshold, because a shared vintage or series suffix drives the
score very high while marking a *different* entity — `Greylock Discovery Fund
II` / `III` scores 99, `Y Combinator Fund I` / `II` scores 98, `Oak Hill
Capital Partners V` / `VI` scores 98.

These pairs are **auto-rejected, not sent to review**: in this book, a name
differing only by a sequence number is, without exception in a manual audit of
the full set, a different investment vehicle — different closing date,
different committed capital, sometimes an entirely different underlying deal.
Putting a human in that loop was pure overhead for a decision the data already
answers. They are still logged, not silently dropped — see
`sequence_conflicts_rejected.csv` and `review.py families` below — and
`review.py override` exists for the rare case one is wrong.

### The OCR rule

`Cranium AI` vs `Cranium Al` was already hand-mapped in `entity_aliases.json`.
Capital `I` and lowercase `l` are the same glyph in most PDF fonts, so this
recurs constantly. Folding `l/1→i` and `O/0` catches the whole family
mechanically — `Harmonic`, `Talisman`, `RowSpace`, `Decart` all resolved
automatically instead of going to a human.

## Safety

The failure modes are asymmetric. A **missed merge** leaves a name fragmented,
which is the status quo. A **wrong merge** silently adds two unrelated
companies' exposure together, and nothing downstream would catch it. The system
is tuned accordingly:

- Merging and promoting are separate programs. `resolve.py` never writes aliases.
- `promote` refuses to run while the review queue has undecided pairs.
- Clusters are built from auto-merge pairs only; a reviewed pair never
  transitively pulls in a third name.
- `test_resolve.py` — 36 real pairs from this book, weighted toward the ones
  that must *not* merge (`Delphi`/`Delphix`, `Square`/`SquareX`,
  `Thread`/`Threads`, `Xiaomai`/`Xiaomi`, `Shift`/`Shift5`).

## Usage

### Quick workflow (basic mark/approve)

```bash
cd FundHoldings/entity_resolution
pip install rapidfuzz pandas

# 1. propose
python resolve.py --data ../data/holdings_anonymized.csv --out ./output

python review.py families

# 2. review the 120 genuinely ambiguous pairs (basic method)
python review.py show --pending
python review.py mark --pair 12 --verdict merge --note "typo in GP statement"
python review.py mark --pair 15 --verdict reject --note "different companies"
# ... mark all 120

# 3. promote (writes entity_aliases.json)
python review.py promote --reviewer "XXX"

# 4. apply to holdings data
python review.py apply --data ../data/holdings_anonymized.csv

# regression suite
python test_resolve.py
```

To promote only auto-merged clusters and leave the queue for later:
`python review.py promote --reviewer "XXX" --allow-unreviewed`.

### Detailed workflow (structured audit trail)

For richer metadata — verdict types, reasoning codes, confidence levels, followup notes:

```bash
# Method A: Record by queue index (simple but fragile if queue is edited)
python record_review.py record \
  --pair 12 \
  --verdict MERGE \
  --reason-code TYPO_VARIANT \
  --evidence "Same company, GP typo in Fund statement" \
  --confidence HIGH \
  --reviewed-by XXX

# Method B: Record by company names (recommended, robust to queue edits)
python record_review.py record \
  --name-a "Proprietary Fund BI" \
  --name-b "Proprietary Fund BII" \
  --verdict MERGE \
  --reason-code TYPO_VARIANT \
  --evidence "Same fund, different naming convention" \
  --confidence MEDIUM \
  --reviewed-by XXX

python record_review.py record \
  --name-a "Delphi" \
  --name-b "Delphix, Inc." \
  --verdict REJECT \
  --reason-code DIFF_COMPANY \
  --evidence "Delphi is payment processor; Delphix is data virtualization" \
  --confidence HIGH \
  --reviewed-by XXX

python record_review.py record \
  --name-a "Fika Ventures - A, L.P." \
  --name-b "Fika Ventures, L.P." \
  --verdict DEFER \
  --reason-code NEEDS_GP_CONFIRM \
  --evidence "Same MG, same records. Confirm if same fund with multiple classes or distinct entities." \
  --confidence MEDIUM \
  --followup "Contact GP for clarification" \
  --reviewed-by XXX

# View all recorded reviews
python record_review.py show-records

# Then promote as before
python review.py promote --reviewer "XXX"
```

Verdict types (for both `record_review.py record` and `review.py override`):
- `MERGE` — same company, merge the names
- `REJECT` — confirmed different entities
- `DEFER` — cannot determine without more info; use with --followup
- `ALIAS` — formerly-known-as or alternate name
- `VARIANT` — spelling/naming variant

Reason codes (optional, for structured categorization):
- `DIFF_MANAGER` — different investment managers
- `DIFF_FUND_VINTAGE` — different fund vintages (I/II/III)
- `DIFF_COMPANY` — confirmed different companies
- `GENERIC_WORDS_ONLY` — only generic word overlap
- `TYPO_VARIANT` — spelling/transcription variant
- `NEEDS_GP_CONFIRM` — needs GP confirmation
- `AMBIGUOUS` — cannot determine from name alone
- `OTHER` — other reason

### Auditing sequence-conflict pairs (optional)

```bash
# View the 192 auto-rejected pairs (different fund vintages, etc.)
python review.py families

# Force a specific verdict (any type: merge, reject, defer, etc.)
python review.py override \
  --name-a "Kimberlite I, LP" \
  --name-b "Kimberlite, LP" \
  --verdict merge \
  --reviewer "XXX" \
  --note "GP confirmed same vehicle, inconsistent naming"

# Or mark for deferred review
python review.py override \
  --name-a "816 Congress" \
  --name-b "ROF V 816 Congress, LLC" \
  --verdict defer \
  --reviewer "XXX" \
  --note "..."
  
# Or record in the structured audit trail:
python record_review.py record \
  --name-a "Kimberlite I, LP" \
  --name-b "Kimberlite, LP" \
  --verdict MERGE \
  --reason-code TYPO_VARIANT \
  --evidence "GP confirmed same vehicle" \
  --confidence HIGH \
  --reviewed-by XXX
```

### Why 192 pairs never reach the review queue

An earlier version held every sequence-differing pair (`Kimberlite II` /
`III`, `Northern Light Strategic Fund II` / `III`, `Freddie Mac 2022-KF134` /
`KF145`, `Glade Brook Private Investors XLIII` / `XVIII`) for a human — 192
pairs across 83 series in the current population.
Auditing that set showed
they were, without exception, different investment vehicles — different
closing dates, different committed capital, sometimes a wholly different
underlying deal (`Confidential Direct 14416` vs `14436` are two separate
transactions, not one mis-typed name). Merging any of them would sum two
distinct commitments into one "company" and corrupt Capital Committed / NAV.
Since the outcome was the same every time, `decide()` now auto-rejects these
directly — `resolve.py` prints `N sequence-conflict pair(s) auto-rejected
without review`, and they never enter `entity_review_queue.csv`.

This is not a silent drop: `sequence_conflicts_rejected.csv` (~191 rows) and
`review.py families` keep every one of these decisions inspectable, and
`review.py override --name-a ... --name-b ... --verdict merge` gives a human a
way to force a specific pair back in — the auto-reject is a default, not a
dead end. Several of these pairs score 97–99, comfortably above the merge bar;
lowering `AUTO_MERGE_SCORE` would not have changed the outcome, since they are
held by the sequence-conflict rule specifically, which runs before the score
check.

### The 120 that remain need a human, by construction

These are genuinely ambiguous spelling, with no clean separator between match
and non-match:

```
score 92.3   Mitiga Security Inc.  /  Mitigia Security Inc.   <- same company (typo)
score 92.3   Delphi                /  Delphix, Inc.           <- different companies
score 92.5   Parsable, Inc.        /  Parseable                <- unclear without lookup
```

There is no threshold that keeps the first pair and drops the second — they
score within 0.1 of each other. What *was* fixable mechanically has been: the
normaliser previously failed to match `"X, L.P."` against `"X LP"` (the period
inside the abbreviation split it into two tokens before suffix-stripping ran)
and did not treat `Pvt` as a synonym for `Private`. Both are fixed now, moving
about 10 pairs from "review" into "identical after normalisation" — a
zero-risk mechanical merge, not a threshold change.

## Outputs

| File | Contents |
|---|---|
| `entity_clusters.csv` | 427 merged groups, canonical name, funds, vendor IDs |
| `entity_review_queue.csv` | 120 pairs needing a human, with evidence columns |
| `sequence_conflicts_rejected.csv` | 192 auto-rejected vintage/series pairs, audit trail only |
| `entity_pairs_all.csv` | Every scored candidate that reached review or merge, and why |
| `data_quality_non_company.csv` | Names that are not companies (see below) |
| `review_records.jsonl` | Complete audit trail of human verdicts (from `record_review.py`); one JSON object per line with verdict type, reason code, confidence, evidence, etc. |
| `manual_overrides.csv` | Verdicts recorded by `review.py override` or overrides to sequence-conflict pairs; read by `promote` |
| `proposed_aliases.json` | Preview of the alias map; not yet promoted |
| `summary.json` | Run statistics |
| `holdings_with_canonical.csv` | Holdings plus a `canonical_company` column |
| `canonical_company_reach.csv` | Per-company fund and manager reach |

Canonical name selection prefers, in order: no parenthetical annotation, has a
legal suffix, longer, more rows. Without the first rule the longest variant wins
and you get `Airbnb (Proprietary Fund BI)` instead of `Airbnb, Inc.`

## Detailed Review Records

`record_review.py` produces a structured audit trail in `review_records.jsonl`. Each line is one JSON object:

```json
{
  "pair_id": 12,
  "reviewed_by": "XXX",
  "reviewed_at": "2026-08-13T10:44:10.010542",
  "score": 92.5,
  "id_a": "483301",
  "id_b": "61833 | 62387",
  "name_a": "Oomnitz, Inc.",
  "name_b": "Oomnitza",
  "verdict": "MERGE",
  "reason_code": "TYPO_VARIANT",
  "evidence": "Same company, GP typo in vendor statement",
  "confidence": "HIGH",
  "needs_followup": false,
  "followup_notes": ""
}
```

**Verdict types:** `MERGE`, `REJECT`, `DEFER`, `PARENT_CHILD`, `ALIAS`, `VARIANT`

**Reason codes:** `DIFF_MANAGER`, `DIFF_FUND_VINTAGE`, `DIFF_COMPANY`, `GENERIC_WORDS_ONLY`, `TYPO_VARIANT`, `NEEDS_GP_CONFIRM`, `AMBIGUOUS`, `OTHER`

**Confidence:** `HIGH`, `MEDIUM`, `LOW`

This structured log is optional — you can use the simpler `review.py mark` workflow instead. But when you need to justify a decision later (e.g., "why did we merge these?"), the record trail is there. `promote` reads both `entity_review_queue.csv` (from `review.py mark`) and `review_records.jsonl` (from `record_review.py record`), so mixing the two methods is fine.

## Data-quality findings

Twenty-one `Source Asset` values are not companies. They are held out of
matching rather than merged, because turning a data-entry error into a
clean-looking canonical entity would hide it. All twenty-one fall outside
Phase 1's existing classifier — `source_asset_filter.py` currently returns
`holding` (in scope) for these, so this is new signal for that classifier, not
a duplicate of its existing categories:

- **Eight sector/geography labels sitting in the company-name column** —
  `United States - Information Technology`, `France - Consumer Staples`,
  `United States - Consumer Staples`, `United Kingdom - Real Estate`,
  `South America - Real Estate`, `United States - Technology`, `United States
  - Materials`, `United States - Real Estate`. 43 rows total, and **34 of them
  come from a single manager, `A4a62c2`** — a systematic mapping fault at one
  source, not scattered typos. Worth raising with that manager directly, and a
  candidate pattern to add to `UNNAMED_AGGREGATE` in `source_asset_filter.py`.
- **`Various`** — 23 rows across 4 funds, an aggregate placeholder standing in
  for individually undisclosed holdings. Same treatment as `Seed Investments`,
  which Phase 1's classifier already catches; `Various` is not yet in that
  pattern list.
- **`Source Code` / `Source Code II` / `Source Code RMB`** — 36 rows in two
  funds (`A07e679`, `A159f57`). Not a currency variant of one company: it is
  the literal anonymisation placeholder this extract uses to mask real names,
  reused for *three different underlying entities* within the same fund —
  `Type of Investment` confirms `Source Code` is a Private Company while
  `Source Code II` and `Source Code RMB` are both Fund vehicles. Fuzzy matching
  would have merged three unrelated masked positions into one fake "company"
  on suffix similarity alone; excluded by name pattern instead.
- **Eight `"{Region} - {Sector} company {Letter}"` names** — e.g. `North
  America - Information technology company A` / `...company B`, `Europe -
  Materials company A`. 16 rows, all from one manager (`A99a662`), all `Type
  of Investment = Other`. This is that GP's own redaction of confidential
  holdings, not our anonymisation — it groups undisclosed positions by
  region/sector and stands in a bare letter for the real name. `company A` and
  `company B` scored 96% similar and would otherwise have been merged into one
  fake "company," when the source data explicitly marks them as two different,
  deliberately unnamed, holdings.

(`Seed Investments` / `Seed investments`, flagged in an earlier draft of this
tool, is already excluded upstream by Phase 1's `UNNAMED_AGGREGATE` pattern
and does not need separate handling here.)

## Known limits

- **Acquisitions collapse into the acquirer.** `Block (from Crew)` and
  `Block (from Weebly)` both merge into `Block`. Correct for company identity,
  but the acquisition provenance is dropped. If entry-basis analysis needs it,
  keep the original `Source Asset` alongside `canonical_company` — `apply` does
  not overwrite it.
- **Population is Phase 1's `is_resolvable` set**, which includes fund vehicles,
  SPVs, and crypto tickers whenever their `Source Asset` text is not classified
  as balance-sheet/accounting/subtotal/aggregate — it is not limited to `Type
  of Investment` in `{Private Company, Public Company}`. Use `--all-rows` only
  for diagnostics; it is not a stricter or looser company filter, it is no
  filter.
- **Genuinely different companies with near-identical names go to review, not
  reject.** `Delphi` / `Delphix` cannot be separated by string comparison alone;
  a human has to look.
- **No transitive merging through reviewed pairs**, by design. If A–B is
  auto-merged and B–C is human-approved, C joins the cluster only at promotion
  time, not during clustering.

## Superseded files

An earlier attempt in `pdf_validation/` targeted the wrong dataset — it read
`entity_candidates.jsonl` (PDF↔vendor matches *within* one fund, 33 pairs)
rather than cross-fund company names. These files are dead and safe to delete:

```
pdf_validation/src/pdf_validation/entity_resolver.py
pdf_validation/src/pdf_validation/entity_resolution_cli.py
pdf_validation/test_entity_resolution.py
pdf_validation/ENTITY_RESOLUTION.md
pdf_validation/PHASE3_INTEGRATION_GUIDE.md
```

`pdf_validation/src/pdf_validation/entity_mapping.py` is **not** superseded —
it is the consumer of `entity_aliases.json` and still in use.
