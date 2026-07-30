# PDF Validation References

This file defines the authoritative references for the PDF extraction and validation implementation.

## 1. Source-of-truth hierarchy

When sources disagree, use the following priority order:

1. The original PDF and its visible text, tables, headers, footnotes, and coordinates.
2. The approved implementation plan and output schemas in this repository.
3. The version-controlled template configuration under `configs/`.
4. Golden test expectations derived from manually verified PDF values.
5. Official documentation matching the installed dependency version.
6. Existing repository notebooks, only for vendor CSV field semantics and existing cleaning conventions.

Do not treat parser output, inferred values, third-party examples, blog posts, or model-generated interpretations as ground truth.

---

## 2. Local project references

### Sample PDF

Path:

`/Users/tonylyu/Downloads/SYN Ventures Fund II LP - Q3 2025 Financial Statements.pdf`

Verified document structure:

* Page 1: Cover and report identity
* Page 2: Statement of Assets, Liabilities, and Partners' Capital
* Pages 3–5: Schedule of Investments
* Page 6: Statement of Operations
* Page 7: Statement of Changes in Partners' Capital
* Page 8: Schedule of Realized Gain/(Loss)

Manually verified acceptance totals:

#### Schedule of Investments

* Cost: `177,475,405`
* Fair Value: `207,461,318`
* Unrealized Gain/(Loss): `29,985,913`

#### Schedule of Realized Gain/(Loss)

* Cost: `21,000,000`
* Cash Proceeds: `18,989,970`
* Realized Gain/(Loss): `-2,010,029`

Special structural case:

* Miggo Security Ltd. begins near the end of page 3.
* Its company subtotal continues at the top of page 4.
* This must be handled through explicit cross-page state rather than unconditional forward filling.

### Existing notebooks

`holdings_pdf_sample_selection.ipynb`

Use only for:

* Sample selection logic
* Fund eligibility criteria
* Latest reporting-date rules
* Exclusion of non-investment rows

Do not add PDF extraction logic to this notebook.

`holdings_anonymized.ipynb`

Use only for:

* Vendor CSV field meanings
* Existing column naming
* Existing cleaning conventions
* Understanding the vendor output grain

Do not treat its completeness score as PDF extraction ground truth.

---

## 3. Camelot

### Main documentation

https://camelot-py.readthedocs.io/en/stable/

Use for:

* General Camelot concepts
* Table objects
* Parsing reports
* Export behavior

### How Stream works

https://camelot-py.readthedocs.io/en/stable/user/how-it-works.html

Use for:

* Understanding whitespace-based table detection
* Understanding why Stream is appropriate for borderless financial tables
* Understanding the limitations of automatically inferred columns

Do not treat Camelot parsing accuracy as financial-data accuracy.

### Advanced usage

https://camelot-py.readthedocs.io/en/stable/user/advanced.html

Use for:

* `table_areas`
* `columns`
* `split_text`
* Text and column separator tuning
* Per-template extraction parameters

All table areas and column separators used for SYN must be stored in the version-controlled JSON template configuration, not embedded as unexplained constants in Python modules.

### API reference

https://camelot-py.readthedocs.io/en/stable/api.html

Use for:

* Exact `read_pdf()` arguments
* Stream parser parameters
* Table and Cell properties
* Cell and table geometry
* Parsing report fields

Before using an API, confirm that it exists in the exact Camelot version pinned in `requirements.txt`.

Do not copy API calls from a different Camelot version without checking compatibility.

---

## 4. pdfplumber

### Official repository and documentation

https://github.com/jsvine/pdfplumber

Use for:

* Accessing words, characters, lines, and their bounding boxes
* Cropping a page to a configured table area
* `extract_words()`
* `find_tables()`
* `extract_table()`
* Explicit and text-based table strategies
* Visual table debugging
* Reconstructing columns when Camelot fails structural validation

Relevant capabilities include:

* `Page.crop(bbox)`
* `Page.extract_words()`
* `Page.find_tables(table_settings)`
* `Page.extract_table(table_settings)`
* `Page.to_image()`
* `PageImage.debug_tablefinder(table_settings)`

The fallback must use template-configured bounding boxes and column rules.

Do not silently replace Camelot output. Preserve both parser outputs and record the selection decision and reason.

Before using an API, confirm that it exists in the exact pdfplumber version pinned in `requirements.txt`.

---

## 5. pandas Excel export

### ExcelWriter

https://pandas.pydata.org/docs/reference/api/pandas.ExcelWriter.html

### DataFrame.to_excel

https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.to_excel.html

Use for:

* Writing multiple DataFrames to one workbook
* Producing the fixed workbook sheets
* Preserving tabular machine results for human review

Required workbook sheets:

* README
* run_manifest
* metadata
* parser_decisions
* raw_cells
* investment_lots
* company_summary
* realized_lots
* reconciliation
* validation_issues

Do not use Excel as the only machine-readable output.

Do not place nested provenance objects directly into cells without an explicit serialized representation.

---

## 6. openpyxl

### Official documentation

https://openpyxl.readthedocs.io/en/stable/

### Tutorial

https://openpyxl.readthedocs.io/en/stable/tutorial.html

Use after pandas export for:

* Freeze panes
* Autofilters
* Column widths
* Number formats
* Sheet ordering
* Header formatting
* Conditional formatting for PASS, FAIL, and review statuses
* Workbook metadata and usability improvements

Do not use merged cells in machine-data sheets.

Formatting must not alter the underlying normalized values.

---

## 7. pytest

### Main documentation

https://docs.pytest.org/en/stable/

### Running tests

https://docs.pytest.org/en/stable/how-to/usage.html

### Parameterized tests

https://docs.pytest.org/en/stable/how-to/parametrize.html

Use for:

* Unit tests for normalization
* Row-classification tests
* Reconciliation tests
* Golden acceptance tests
* Regression testing across templates

Golden values must be stored as explicit expected fixtures or constants and must not be recalculated from the same extraction output being tested.

---

## 8. Python standard library

Use official Python documentation for:

* `argparse`
* `hashlib`
* `json`
* `decimal`
* `datetime`
* `pathlib`
* `subprocess`

https://docs.python.org/3/library/

Use `decimal.Decimal` rather than binary floating-point arithmetic for reported financial values and reconciliation differences.

SHA-256 values must be calculated from the actual PDF and configuration file bytes.

---

## 9. Deal Status inference

Deal Status is not a field reported in the PDF. It must be inferred by cross-referencing
the Schedule of Investments and the Schedule of Realized Gain/(Loss).

### Authoritative Deal Status vocabulary

The following are the only valid inferred values, aligned with the vendor CSV:

- `Current`
- `Written Down`
- `Written Off`
- `Partially Exited`
- `Partially Exited, Remainder Written Down`
- `Fully Exited`

Do not use non-standard terms such as "invested/unrealized", "partially realized",
or "fully realized". These do not match the vendor CSV and will cause comparison failures.

### Inference source of truth

The inference rules are derived from the structure of standard private fund financial
statements, not from any external documentation:

- **Schedule of Investments** (typically pages 3-5) lists currently held positions.
- **Schedule of Realized Gain/(Loss)** (typically the final schedule) lists positions
  that have been fully or partially sold.

A company appearing in both schedules must have its names normalized via
`entity_aliases.json` before cross-referencing. Spelling variants across schedules
(e.g. "Oomnitz" in Schedule of Realized vs "Oomnitza, Inc." in Schedule of Investments)
are a known data quality issue in source PDFs and must be handled by explicit aliases,
not fuzzy matching.

### Written Down threshold

The threshold for Written Down is FMV/Cost < 0.25. This is stored in
`deal_status.py` as `WRITTEN_DOWN_RATIO` and may be adjusted after consultation
with the Investment Office. Do not hard-code the threshold in tests.

---

## 10. Implementation constraints

The implementation must follow these rules even when parser documentation offers a more automatic alternative:

1. Raw evidence is immutable.
2. Reported, normalized, derived, and inferred values remain separate.
3. No unconditional company-name forward filling.
4. No inferred values may overwrite reported values.
5. A passing grand total does not prove row-level correctness.
6. Parser selection requires structural and financial validation.
7. Automatic fuzzy entity matching may only produce candidates.
8. Unknown currency, unit, identity, or reporting date must block vendor comparison.
9. Every normalized value must retain a path back to PDF page and bounding box.
10. Every dependency version used during extraction must be recorded in the run manifest.

---

## 10. Version discipline

Before implementation:

1. Check the active Python version.
2. Install compatible package versions.
3. Pin exact tested versions in `requirements.txt`.
4. Record those versions in `run_manifest`.
5. Use documentation matching those pinned versions.

If current official documentation differs from the installed API:

* Do not guess.
* Inspect the installed function signature.
* Select the documentation version matching the installed package.
* Record the compatibility decision in README or code comments.

Do not upgrade parser dependencies during an extraction run without regenerating and reviewing golden-test output.
