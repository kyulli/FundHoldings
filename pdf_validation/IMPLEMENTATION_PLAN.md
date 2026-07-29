# PDF Validation Implementation Plan

## Scope (this phase)

Implement and verify the PDF extraction pipeline for the SYN Ventures Fund II Q3 2025 sample:

1. Package structure and schemas
2. Raw evidence extraction (Camelot Stream + pdfplumber fallback)
3. Row classification and cross-page state
4. Normalized reported values
5. Reconciliation
6. JSONL + Excel export
7. Golden tests

Vendor comparison is deferred until PDF extraction golden tests pass.

## Source of truth

Treat, in order:

1. Original PDF text/tables/coordinates
2. This plan and schemas
3. Versioned template JSON under `configs/`
4. Manually verified golden values
5. Official docs matching installed package versions

Do not invent bounding boxes, currency/units, company identity mappings, missing metadata, Deal Status, or PDF↔CSV field equivalence.

## Package layout

```text
pdf_validation/
├── README.md
├── REFERENCES.md
├── IMPLEMENTATION_PLAN.md
├── requirements.txt
├── configs/syn_ventures_fund_ii_q3_2025.json
├── samples/
├── outputs/
├── src/pdf_validation/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── schemas.py
│   ├── metadata.py
│   ├── camelot_extractor.py
│   ├── pdfplumber_fallback.py
│   ├── parser_decisions.py
│   ├── normalization.py
│   ├── row_classification.py
│   ├── reconciliation.py
│   ├── export.py
│   ├── manifest.py
│   ├── pipeline.py
│   └── vendor_comparison.py   # stub until extraction passes
└── tests/test_syn_ventures_golden.py
```

CLI: `python -m pdf_validation extract --pdf ... --config ... --out ...`

## Template configuration

All SYN-specific values live in `configs/syn_ventures_fund_ii_q3_2025.json`:

- page ranges
- table areas
- column separators
- header/section patterns
- cross-page rules
- Camelot / pdfplumber parameters
- field-level dash semantics
- display precision / tolerance

## Three-layer result model

1. **raw evidence**: PDF text, page, bbox, parser, original table/row/column ids
2. **normalized reported data**: type/format normalization of explicitly disclosed values only
3. **derived/inferred data**: calculated totals, differences, optional inferences; never overwrite reported fields

Every numeric field retains:

- `<field>_raw`
- `<field>_normalized`
- `<field>_parse_status`
- `<field>_source_page`
- `<field>_source_bbox`

Parse statuses distinguish: `ok`, `blank`, `dash`, `zero`, `not_disclosed`, `not_applicable`, `parse_error`.

Dash meaning is field-specific from config.

## Row state machine

Supported row types:

- `repeated_header`
- `investment_lot`
- `company_subtotal`
- `grand_total`
- `page_continuation`
- `blank_or_noise`

Company names use explicit `active_company` state. No unconditional `ffill()`. Close active company on subtotal, grand total, or new table section.

## Parser selection

Preserve both Camelot and pdfplumber raw outputs when both run. Record selected parser and reason in `parser_decisions`.

Selection checks (all required; grand total alone is insufficient):

- header coverage
- column count
- key-field parse rate
- row structure
- subtotal recognition
- cross-page continuation
- reconciliation

## Reconciliation checks

Each check emits: `reported`, `calculated`, `difference`, `tolerance`, `tolerance_reason`, `status`.

Required:

1. Fair Value − Cost = Unrealized Gain
2. lot sum = company subtotal
3. company sum = schedule grand total
4. Schedule totals = Statement of Assets Cost / Fair Value
5. Realized Schedule totals = Statement Net Realized Gain/(Loss)

Tolerance may reflect PDF display precision; difference need not be mechanically zero.

## Deal Status schema

Always include:

- `deal_status_reported`
- `deal_status_inferred`
- `inference_rule`
- `inference_evidence`
- `inference_confidence`

This phase does not populate inferred status.

## Outputs

- JSONL: machine-normalized records
- Excel: human review with sheets README, run_manifest, metadata, parser_decisions, raw_cells, investment_lots, company_summary, realized_lots, reconciliation, validation_issues

`run_manifest` records PDF SHA-256, config SHA-256, git commit, parser versions, runtime, schema version.

## Golden acceptance (SYN)

- Schedule of Investments: pages 3–5
- Schedule of Realized Gain/(Loss): page 8
- Miggo Security Ltd. crosses pages 3→4 with subtotal continuation
- Investment totals: Cost `177475405`, Fair Value `207461318`, Unrealized Gain `29985913`
- Realized totals: Cost `21000000`, Cash Proceeds `18989970`, Realized Gain/(Loss) `-2010029`

## Dependency pinning

1. Inspect active Python environment
2. Install compatible packages
3. Run SYN extraction and golden tests
4. Pin exact successfully tested versions in `requirements.txt`

Do not invent version pins before a successful SYN run.

## Incremental execution order

1. Create package structure and schemas
2. Implement raw evidence extraction
3. Implement row classification and cross-page state
4. Implement normalized reported values
5. Implement reconciliation
6. Add JSONL and Excel export
7. Add golden tests
8. Only then implement vendor comparison

After each stage, run relevant tests. Do not generate all modules without executing the SYN sample.
