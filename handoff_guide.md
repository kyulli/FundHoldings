# Private Funds Data Quality & Validation Framework

## Executive Handoff Guide

Last Updated: August 2026

---

# 1. Overview

This project provides an automated data quality monitoring and validation workflow for Private Funds investment holdings data.

The framework transforms structured holdings data and source-report validation outputs into Office-facing review guidance through five stages:

1. Data-State Analysis
2. Quarterly Cleaning Pipeline
3. PDF Validation
4. Entity Resolution
5. Exception Diagnosis

The final output is an executive reporting deck summarizing:

- Current data quality status
- Validation outcomes
- Identified exceptions
- Recommended review actions

---

# 2. Workflow Architecture

Source Holdings Data
↓
Data-State Analysis
↓
Quarterly Cleaning Pipeline
↓
PDF Validation
↓
Entity Resolution
↓
Exception Diagnosis
↓
Executive Reporting Deck

Each stage consumes upstream outputs and produces structured artifacts used by downstream modules.

---

# 3. Key Output Locations

## Phase 1 Data-State Analysis

Location:
reports/

Important outputs:
- data_state_by_fund.csv
- data_state_by_manager.csv
- data_state_by_fund_conditional.csv
- data_state_by_manager_conditional.csv
- data_state_by_field.csv
- consistency_rule_results.csv
- deal_status_conformance_exceptions.csv
- excluded_non_investment_rows.csv

These outputs are consumed by:
office_deck_generator/phase1_summary.py

The reporting layer does not recompute Phase 1 metrics.
It only adapts existing analytical outputs into executive summaries.

---

## Quarterly Cleaning Pipeline

Location:
cleaning_pipeline/runs/

Main output:
*_ops_report.xlsx

Consumed by:
office_deck_generator/load_outputs.py

The pipeline tracks:
- Newly identified issues
- Resolved issues
- Still-open issues
- Quarterly changes against previous baselines

---

## PDF Validation

Source location:
test_output/

Expected output:
comparison_report.json

The reporting layer summarizes:
- Document comparability
- Validation status
- Matched values
- Value mismatches
- Review-required documents

---

## Entity Resolution

Location:
entity_resolution/

Purpose:
- Confirm relationships between PDF disclosures and structured holdings data
- Measure entity matching coverage
- Identify unresolved mappings requiring review

---

## Exception Diagnosis

Location:
diagnosis_reliability/

Main output:
diagnosis_reliability/outputs/diagnosis_report.xlsx

Important sheets:
- Exception Summary
- Action Summary
- Impact Summary
- Exception Detail

The diagnosis module converts validation findings into:
- Root cause classification
- Severity assessment
- Recommended actions
- Office review guidance

---

# 4. Generating the Executive Deck

Activate environment:

conda activate fund-project

Generate the latest deck:

python -m office_deck_generator.build_office_deck

Output:
office_deck_generator/outputs/office_report_deck.pptx

---

# 5. Quarterly Refresh Process

For a new reporting period:

## Step 1
Update the source holdings dataset.

## Step 2
Run the Phase 1 data quality analysis.

Refresh: reports/

## Step 3
Run the quarterly cleaning pipeline.

Generate: cleaning_pipeline/runs/

## Step 4
Run PDF validation. 

Refresh: test_output/

## Step 5
Run exception diagnosis. 

Generate: diagnosis_reliability/outputs/diagnosis_report.xlsx

## Step 6
Generate updated executive deck. 

Run: python -m office_deck_generator.build_office_deck

---

# 6. Reporting Design Principles

## No duplicated analytics

The deck generation layer does not perform analytical calculations.

It only:
- Discovers existing outputs
- Formats results
- Generates Office-facing summaries

Analytical logic remains inside upstream modules.

---

## Source of Truth

Generated pipeline outputs are authoritative.

If documentation and generated summaries differ, use:

reports/*.csv
diagnosis_reliability/outputs/*.xlsx

as the source of truth.
---

## Separation of Responsibilities

Analytical modules:
- Calculate metrics
- Detect validation issues
- Generate structured outputs

Reporting module:
- Summarizes findings
- Communicates impact
- Provides review guidance

---

# 7. Maintenance Guidelines

When adding new validation checks:
1. Add logic to the upstream validation module.
2. Export structured outputs.
3. Update loader adapters if required.
4. Update deck sections only when new executive insights are needed.

Avoid embedding business logic inside: office_deck_generator/

The reporting layer should remain lightweight, reproducible, and easy to refresh.

---

# 8. Current Executive Deliverable
The final executive handoff consists of:

## Code
Private_Funds_Project/

## Analytical Outputs

reports/

cleaning_pipeline/runs/test_output/

diagnosis_reliability/outputs/

## Executive Report

office_deck_generator/outputs/office_report_deck.pptx

## Documentation

HANDOFF_GUIDE.md

This structure enables future quarterly refreshes without redesigning the workflow.