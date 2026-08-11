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

## Reading the Excel file

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
