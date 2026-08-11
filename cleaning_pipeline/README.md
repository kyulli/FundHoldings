# Quarterly Data Cleaning Pipeline

**What this is for:** every time a new vendor CSV extract arrives, run one command and get back one Excel file telling you what's new, what's still broken, and what got fixed — no need to open the notebook or write any code.

---

## How to run it (3 steps)

### 1. Replace the vendor CSV

Get the new extract from the vendor and save it over the existing file, keeping the exact same name:

```
FundHoldings/data/holdings_anonymized.csv
```

### 2. (Optional) Drop in any new fund PDFs

If a new PDF is available for a fund we already have an approved extraction template for, put it here:

```
FundHoldings/cleaning_pipeline/incoming_pdfs/A0d2a71/    <- SYN Ventures Fund II
FundHoldings/cleaning_pipeline/incoming_pdfs/A103ce5/    <- Castanea Partners Fund III (scanned/OCR)
```

If you don't have a new PDF for a fund this quarter, leave its folder empty — the pipeline just skips it, that's not an error.

(Only these two funds have an approved template right now. PDFs for other funds can't be auto-checked yet — see "What this doesn't do yet" below.)

### 3. Run the pipeline

Open Terminal:

```bash
cd FundHoldings/cleaning_pipeline
python run_quarterly_refresh.py
```

It takes a few minutes. When it's done, it prints the path to a file like:

```
cleaning_pipeline/runs/20260811T164028Z/ops_report.xlsx
```

Open that file. Done.

---

## How the Report is Generated

The pipeline runs in 5 stages, all from one command:

1. **Notebook execution** — re-runs `notebooks/phase1_holdings_data_state_analysis.ipynb` on the current vendor CSV, regenerating every tracked report under `FundHoldings/reports/`. This is the same analysis that has been reviewed and validated all along; the pipeline just runs it headless instead of interactively.

2. **Baseline loading** — reads the most recent snapshot from `cleaning_pipeline/snapshots/` to use as a comparison point. A snapshot is a frozen copy of the reports/ CSVs at a point in time, plus a manifest recording when it was taken and basic metadata (row count, max As At Date).

3. **Diffing** — compares the fresh reports/ against the baseline snapshot using row-level and metric-level diffs to answer three questions:
   - What completeness issues are **new** this quarter (rows in reports now that weren't in the snapshot)?
   - What issues have been **resolved** since last run (rows in the snapshot that are gone from reports)?
   - What issues are **persisting** (still present in both)?
   
   This same logic applies to Deal Status exceptions, consistency rule violation rates, and fund/manager completeness scores. The diffs are the core of the output — you only see what changed, not the entire backlog every time.

4. **PDF checks** (optional) — for any fund with an approved extraction template and a new PDF staged under `incoming_pipeline/incoming_pdfs/<fund_id>/`, extracts holdings data from the PDF and compares it against the vendor CSV for that fund and date. Mismatches are folded into the Excel report.

5. **Excel workbook generation** — exports a single `ops_report.xlsx` file combining all diffs, metadata, and PDF results into a human-readable workbook. The Summary sheet shows high-level counts with specific fund/manager IDs; remaining sheets show detail sorted by fund ID for easy scanning.

After step 5, if `--skip-snapshot` is not set, this run's reports/ are saved as the new baseline in `snapshots/`, so the next run diffs against today instead of yesterday.

---

## Data & Storage

**Input CSV** — the vendor extract, always at the same path:
```
FundHoldings/data/holdings_anonymized.csv
```
This file is where quarterly data arrives from the vendor. The pipeline reads it once per run and never modifies it.

**Phase 1 reports** — the scored/analyzed output, regenerated every run:
```
FundHoldings/reports/
  ├─ data_state_by_field.csv                 (completeness scores by field)
  ├─ data_state_by_fund_conditional.csv      (completeness by fund, adjusted for field applicability)
  ├─ data_state_by_manager_conditional.csv   (completeness by manager)
  ├─ flagged_missing_fields_conditional.csv  (all rows with missing required fields)
  ├─ deal_status_conformance_exceptions.csv  (rows where reported status conflicts with numbers)
  ├─ consistency_rule_results.csv            (16 internal consistency checks + violation rate)
  └─ reporting_gaps_by_series.csv            (funds missing expected quarterly/semi-annual reports)
```
These are the raw scoring outputs from the Phase 1 notebook. The pipeline tracks changes in these files across runs.

**Snapshots** — frozen baselines for diffing:
```
FundHoldings/cleaning_pipeline/snapshots/
  ├─ 20260811T173501Z_csv_holdings_anonymized_2425subset/
  │   ├─ data_state_by_fund_conditional.csv
  │   ├─ flagged_missing_fields_conditional.csv
  │   ├─ [other tracked reports...]
  │   └─ manifest.json                       (metadata: taken_at_utc, max_as_at_date, row count)
  ├─ 20260811T173705Z/
  │   ├─ [same structure]
  │   └─ manifest.json
  └─ ...
```
Each snapshot is a point-in-time copy of reports/ plus metadata. Snapshots are created automatically after every run (unless `--skip-snapshot` is set) and are never deleted. The pipeline always diffs against the most recent one.

**Run outputs** — the Excel workbook and any PDF extraction detail:
```
FundHoldings/cleaning_pipeline/runs/
  ├─ 20260811T173448Z/
  │   ├─ ops_report.xlsx                     (the file you open)
  │   └─ pdf_checks/                         (only if PDFs were staged)
  │       ├─ A0d2a71/
  │       │   └─ [extraction and comparison results for each PDF]
  │       └─ A103ce5/
  │           └─ [extraction and comparison results]
  └─ 20260811T173512Z/
      ├─ ops_report.xlsx
      └─ ...
```
Each run gets its own timestamped directory. The workbook is what you open; PDF results (if any) are stored as JSON and Excel files for debugging.

**Incoming PDFs** — where you stage new PDFs for checking:
```
FundHoldings/cleaning_pipeline/incoming_pdfs/
  ├─ A0d2a71/                                (SYN Ventures Fund II)
  │   ├─ SYN_Ventures_Q1_2026.pdf            (new this quarter)
  │   └─ _processed/                         (automatically archived after checking)
  │       ├─ SYN_Ventures_Q4_2025.pdf
  │       └─ ...
  ├─ A103ce5/                                (Castanea Partners Fund III)
  │   └─ _processed/
  │       ├─ Castanea_Q1_2026.pdf
  │       └─ ...
  └─ [empty folders for other funds with templates, waiting for new PDFs]
```
Drop a new PDF into `incoming_pdfs/<fund_id>/` (not `_processed`) before running the pipeline. After the check completes, it's automatically moved into `_processed` so it won't be re-checked next quarter. If there's nothing new, leave the folder empty or omit it entirely — the pipeline treats that as "no new PDF for this fund, skip the check."

---

## Reading the Excel File

The workbook has these tabs, in the order you should look at them:

**Summary** — one page. How many new problems this quarter, how many got fixed, how many are still open. Start here.

**New Issues This Quarter** — every row that's newly missing a required field (Capital Invested, Deal Status, Sector, etc.) that wasn't a problem last time. This is the list to act on first — it's new, so something changed.

**Escalation List** — funds whose overall data completeness is below 85%, worst first. If a fund's score got worse since last run, that's flagged in the `score_delta` column (negative = got worse).

**Deal Status Exceptions** — cases where the reported status ("Written Off," "Fully Exited," etc.) doesn't match what the underlying numbers say it should be. Each row is marked `NEW` or `persisting`.

**Consistency Rule Results** — all internal consistency checks (e.g., "a value can't go down over time" for cumulative fields), with `rate_delta` showing whether each rule got better or worse vs. last run.

**PDF Mismatches** — only appears if a PDF was checked this run and something didn't match the CSV. If everything matched, this tab is simply absent (nothing to show).

---

## What "new" and "resolved" mean

Every run is compared against the **last run's results** (not against the very first baseline forever — the comparison point moves forward each time). So "new this quarter" always means "different from the last time someone ran this," which is what you want when reviewing quarter over quarter.

If you ever want to see everything again from scratch without comparing to anything, that's not a supported flag today — ask the DSI team if you need it.

---

## What this doesn't do yet

- **Only 2 funds have an approved PDF template** (SYN Ventures, Castanea). Building a template for another fund is development work, not something this pipeline can do on its own. If a fund's PDFs are consistently showing gaps in the CSV and you want PDF-level spot-checking for it, flag it to the DSI team.
- **This finds problems, it doesn't fix the CSV.** No row is ever edited or deleted — this is a detection tool, not an auto-correction tool. Remediation (contacting a manager, correcting a value) is still a human decision.
- **Unusual PDFs get skipped, not guessed at.** If a fund's PDF doesn't match a known template, the extraction step declines to process it rather than risk pulling wrong numbers. That fund's completeness scores still come from the CSV as normal; it just won't get a PDF-level cross-check.

---

## For whoever runs this technically (light troubleshooting)

- **"Notebook execution failed"** — the pipeline stops and does not update `reports/` or move the baseline forward, so nothing is left in a half-updated state. Check the printed error, it comes straight from the notebook cell that failed. Common cause: the new CSV is missing a column the notebook expects.
- **Snapshots** live in `cleaning_pipeline/snapshots/`, one per run, oldest to newest by folder name. Each is a frozen copy of that run's `reports/` output plus a `manifest.json`. Nothing is ever deleted automatically.
- **Every run's full output** (workbook + any PDF extraction detail) is kept under `cleaning_pipeline/runs/<timestamp>/` — old runs are never overwritten.
- `python run_quarterly_refresh.py --skip-pdf-check` — skip the PDF step entirely (faster, CSV-only refresh).
- `python run_quarterly_refresh.py --skip-snapshot` — dry run; computes and shows the report but does not move the baseline forward, so running it again immediately re-compares against the same starting point. Useful for testing.
