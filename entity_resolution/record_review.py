#!/usr/bin/env python3
"""Record detailed review verdicts with structured metadata.

Usage examples:

    # Method 1: By queue index (use --pair) — simplest but breaks if queue is edited
    python record_review.py record \
        --pair 3 \
        --verdict REJECT \
        --reason-code DIFF_COMPANY \
        --evidence "Delphi is a payment processor; Delphix is a data platform" \
        --reviewed-by Kyulli

    # Method 2: By name (use --name-a/--name-b) — more robust, insensitive to queue edits
    python record_review.py record \
        --name-a "Proprietary Fund BI" \
        --name-b "Proprietary Fund BII" \
        --verdict MERGE \
        --reason-code TYPO_VARIANT \
        --evidence "Same fund, different naming in two statements" \
        --confidence MEDIUM \
        --reviewed-by Kyulli

    # Other verdict types
    python record_review.py record \
        --name-a "816 Congress" \
        --name-b "ROF V 816 Congress, LLC" \
        --verdict DEFER \
        --reason-code NEEDS_GP_CONFIRM \
        --evidence "Could be parent/child relationship" \
        --confidence MEDIUM \
        --followup "Confirm with Julien" \
        --reviewed-by "Qiuli Lai"

    # Alias relationship
    python record_review.py record \
        --name-a "Apple Inc." \
        --name-b "Apple Computer Inc." \
        --verdict ALIAS \
        --evidence "Both names refer to same company" \
        --confidence HIGH \
        --reviewed-by Kyulli

    # View all recorded reviews
    python record_review.py show-records
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from review_schema import ReviewRecord, Verdict, ReasonCode, Confidence, create_review_record

RECORDS_FILE = "review_records.jsonl"


def cmd_record(args: argparse.Namespace) -> int:
    """Record a detailed review decision."""

    # Load the queue to get pair details
    queue_path = args.out / "entity_review_queue.csv"
    if not queue_path.exists():
        print("entity_review_queue.csv not found. Run resolve.py first.")
        return 1

    q = pd.read_csv(queue_path)

    # Find the pair either by index or by names
    row_index = None  # Will be set below

    if args.pair >= 0:
        # Use index (legacy method)
        if args.pair >= len(q):
            print(f"Pair index {args.pair} out of range [0, {len(q)-1}]")
            return 1
        row = q.iloc[args.pair]
        row_index = args.pair
    elif args.name_a and args.name_b:
        # Use names (preferred method - robust to queue edits)
        hits = q[((q.name_a == args.name_a) & (q.name_b == args.name_b)) |
                 ((q.name_a == args.name_b) & (q.name_b == args.name_a))]
        if len(hits) == 0:
            print(f"No pair found matching {args.name_a!r} <-> {args.name_b!r}")
            return 1
        if len(hits) > 1:
            print(f"Multiple pairs found; please be more specific")
            return 1
        row = hits.iloc[0]
        row_index = hits.index[0]
    else:
        print("Specify either --pair INDEX or both --name-a and --name-b")
        return 1

    # Validate verdict
    try:
        verdict = Verdict(args.verdict)
    except ValueError:
        print(f"Invalid verdict: {args.verdict}")
        print(f"Valid values: {', '.join(v.value for v in Verdict)}")
        return 1

    # Validate reason_code (optional)
    reason_code = None
    if args.reason_code:
        try:
            reason_code = ReasonCode(args.reason_code)
        except ValueError:
            print(f"Invalid reason_code: {args.reason_code}")
            print(f"Valid values: {', '.join(r.value for r in ReasonCode)}")
            return 1

    # Validate confidence
    try:
        confidence = Confidence(args.confidence)
    except ValueError:
        print(f"Invalid confidence: {args.confidence}")
        print(f"Valid values: {', '.join(c.value for c in Confidence)}")
        return 1

    # Create record (convert row_index to Python int for JSON serialization)
    record = create_review_record(
        pair_id=int(row_index),
        reviewed_by=args.reviewed_by,
        score=float(row.score),
        id_a=str(row.asset_ids_a),
        id_b=str(row.asset_ids_b),
        name_a=str(row.name_a),
        name_b=str(row.name_b),
        verdict=verdict,
        reason_code=reason_code,
        evidence=args.evidence or "",
        confidence=confidence,
        needs_followup=bool(args.followup),
        followup_notes=args.followup or "",
    )

    # Append to records file
    records_path = args.out / RECORDS_FILE
    with open(records_path, "a", encoding="utf-8") as f:
        f.write(record.to_json() + "\n")

    # Also update the queue's reviewer_verdict for compatibility
    q = q.astype({"reviewer_verdict": "string", "reviewer_notes": "string"})
    q.loc[row_index, "reviewer_verdict"] = args.verdict.lower()
    if args.evidence:
        q.loc[row_index, "reviewer_notes"] = f"[{verdict.value}] {args.evidence}"
    q.to_csv(queue_path, index=False)

    print(f"[{row_index}] {row.name_a!r} <-> {row.name_b!r}")
    print(f"    Verdict: {verdict.value}")
    if reason_code:
        print(f"    Reason: {reason_code.value}")
    print(f"    Confidence: {confidence.value}")
    if record.needs_followup:
        print(f"    Followup: {record.followup_notes}")
    print(f"    Recorded in {records_path}")

    return 0


def cmd_show_records(args: argparse.Namespace) -> int:
    """Show all recorded reviews."""
    records_path = args.out / RECORDS_FILE
    if not records_path.exists():
        print("No records yet.")
        return 0

    records = []
    with open(records_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    print(f"{len(records)} review(s) recorded:\n")
    for i, rec in enumerate(records, 1):
        print(f"[{i}] {rec['name_a']!r} <-> {rec['name_b']!r}")
        print(f"    Verdict: {rec['verdict']}")
        if rec['reason_code']:
            print(f"    Reason: {rec['reason_code']}")
        print(f"    Evidence: {rec['evidence']}")
        print(f"    Confidence: {rec['confidence']}")
        if rec['needs_followup']:
            print(f"    Followup: {rec['followup_notes']}")
        print(f"    By {rec['reviewed_by']} at {rec['reviewed_at']}\n")

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--out", type=Path, default=Path("./output"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    # record subcommand
    s = sub.add_parser("record", help="record a detailed review decision")
    s.add_argument("--pair", type=int, default=-1, help="queue index (optional if using --name-a/--name-b)")
    s.add_argument("--name-a", default="", help="entity A name (alternative to --pair)")
    s.add_argument("--name-b", default="", help="entity B name (alternative to --pair)")
    s.add_argument("--verdict", required=True,
                   choices=[v.value for v in Verdict],
                   help="MERGE | REJECT | DEFER | PARENT_CHILD | ALIAS | VARIANT")
    s.add_argument("--reason-code", default="",
                   help="structured reason (optional)")
    s.add_argument("--evidence", default="",
                   help="supporting evidence/explanation")
    s.add_argument("--confidence", default="HIGH",
                   choices=[c.value for c in Confidence],
                   help="HIGH | MEDIUM | LOW")
    s.add_argument("--reviewed-by", required=True,
                   help="reviewer name (e.g., Kyulli)")
    s.add_argument("--followup", default="",
                   help="notes on followup needed (sets needs_followup=true)")
    s.set_defaults(func=cmd_record)

    # show-records subcommand
    s = sub.add_parser("show-records", help="display all recorded reviews")
    s.set_defaults(func=cmd_show_records)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
