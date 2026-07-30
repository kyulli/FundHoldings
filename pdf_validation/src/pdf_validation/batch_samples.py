#!/usr/bin/env python3
"""Compatibility wrapper: prefer `python -m pdf_validation batch`."""

from pdf_validation.batch_runner import run_batch

if __name__ == "__main__":
    run_batch()
