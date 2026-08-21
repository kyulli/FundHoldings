# FundHoldings

**A production-style data quality, entity resolution, and validation pipeline for private fund holdings data — built for the Brown University Investment Office.**

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen)](#testing)
[![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

> Brown University DSI Capstone · Advised by Julien Angel

52,048 scorable private-fund holdings, across 385 funds and 101 managers, quantified, cleaned, cross-checked against source PDFs, and turned into an operational review queue — with every number traceable back to source.

---

## Why this exists

The Investment Office receives 400+ private fund reports every quarter as unstandardized PDFs. A third-party vendor flattens these into one CSV extract — but nobody had ever systematically measured how trustworthy that CSV actually is before it drives portfolio analytics. This repo is that measurement, end to end: from raw extract, to a scored, reconciled, and entity-resolved dataset, to an operational exception queue Operations can act on directly.

## Headline results

| | |
|---|---|
| **Scorable positions** | 52,048 (filtered from 58,160 raw rows) |
| **Completeness** | 94.6% unconditional → 97.2% conditional (removes 59,990 not-applicable cells) |
| **Entities resolved** | 8,706 distinct names → 8,220 canonical entities |
| **Deal Status integrity** | 99.9% agreement between reported and independently re-derived status (51,113 rows) |
| **Consistency rules** | 16 internal checks; 11 pass at ≥99% |
| **PDF extraction accuracy** | 6/6 golden totals match exactly (0 difference); 128/128 reconciliation checks pass |
| **Exceptions classified** | 10,040, routed to a prioritized Office review queue |

Full findings with citations back to source: [`docs/Phase1_Data_State_Analysis_Glossary.pdf`](docs/Phase1_Data_State_Analysis_Glossary.pdf).

## Pipeline

```
                         ┌─────────────────────────┐
                         │   Vendor CSV Extract     │
                         │  (58,160 raw rows)       │
                         └────────────┬─────────────┘
                                      │
                    ┌─────────────────┴─────────────────┐
                    ▼                                     ▼
      ┌───────────────────────────┐          ┌───────────────────────────┐
      │  I. Data-State Analysis   │          │   III. PDF Validation      │
      │  notebooks/               │          │   pdf_validation/          │
      │  Field/fund/manager       │          │   Camelot + pdfplumber     │
      │  completeness, 16         │          │   extraction, golden-test  │
      │  consistency rules,       │          │   benchmarked, reconciled  │
      │  Deal Status derivation   │          │   against Statement totals │
      └──────────────┬─────────────┘          └──────────────┬──────────────┘
                      │                                        │
                      ▼                                        ▼
      ┌───────────────────────────┐          ┌───────────────────────────┐
      │  II. Quarterly Cleaning   │          │  IV. Entity Resolution /   │
      │  Pipeline                 │          │  PDF-to-Vendor Mapping     │
      │  cleaning_pipeline/       │          │  entity_resolution/        │
      │  Re-run vs. prior         │          │  Normalize → block → score │
      │  baseline each quarter    │          │  → decide → promote        │
      └──────────────┬─────────────┘          └──────────────┬──────────────┘
                      │                                        │
                      └───────────────────┬────────────────────┘
                                           ▼
                         ┌─────────────────────────────┐
                         │   V. Exception Diagnosis     │
                         │   diagnosis_reliability/     │
                         │   Classify → score severity  │
                         │   → prioritized decision      │
                         │   queue for Operations        │
                         └───────────────┬───────────────┘
                                         ▼
                         ┌─────────────────────────────┐
                         │   Office Report Generator    │
                         │   office_deck_generator/      │
                         │   Populates the IO's own      │
                         │   .pptx template from the     │
                         │   outputs above — no manual   │
                         │   copy-paste                  │
                         └─────────────────────────────┘
```

## What's in each module

| Module | What it does |
|---|---|
| [`notebooks/`](notebooks) | Phase 1 data-state analysis: row classification, field/fund/manager completeness (unconditional + conditional), 16 consistency rules, Deal Status re-derivation, reporting-cadence gap detection |
| [`entity_resolution/`](entity_resolution) | Collapses free-text company name variants into canonical entities — normalize → block → IDF-weighted score → decide → promote, with an asymmetric-risk design and a regression suite weighted toward near-miss non-matches |
| [`pdf_validation/`](pdf_validation) | Extracts holdings tables directly from source PDFs (dual-parser: Camelot + pdfplumber fallback), reconciles against Statement of Assets, and cross-references two schedules to infer Deal Status — benchmarked against a manually-verified golden sample |
| [`cleaning_pipeline/`](cleaning_pipeline) | Re-runs the data-state checks each quarter against the prior baseline, tracking new/resolved/still-open issues over time |
| [`diagnosis_reliability/`](diagnosis_reliability) | Turns raw validation output into classified exceptions with severity, confidence, and root cause — the actionable review queue |
| [`office_deck_generator/`](office_deck_generator) | Builds the Investment Office's own `.pptx` report directly from the outputs of every module above |

## Design principles

- **Every number is traceable.** Each finding in the report cites the exact notebook section, CSV, or script that produced it — see the [glossary](docs/Phase1_Data_State_Analysis_Glossary.pdf).
- **Conditional ≠ unconditional.** A blank cell isn't a defect if the field doesn't apply to that row (e.g. `Realized Proceeds` on a still-active deal). Every completeness metric is reported both ways.
- **Asymmetric risk in entity resolution.** A missed merge just leaves a name fragmented — recoverable. A wrong merge silently sums two unrelated companies' exposure — not recoverable. The matching pipeline is deliberately conservative as a result.
- **Golden-test everything that touches money.** The PDF extraction pipeline is benchmarked against a hand-verified sample before being trusted on unseen documents.

## Testing

```bash
pytest pdf_validation/tests/
pytest entity_resolution/
```

## Tech stack

Python, pandas, python-pptx, Camelot, pdfplumber, pytest, Jupyter.

## Author

**Qiuli Lai** — Brown University, MS Data Science Capstone
Advised by Julien Angel, Brown University Investment Office
