#!/usr/bin/env python3
"""Entity resolution CLI for Phase 3.

Usage:
    python -m pdf_validation.entity_resolution_cli collect \
        --root /path/to/pdf_validation --threshold 0.85

    python -m pdf_validation.entity_resolution_cli review \
        --root /path/to/pdf_validation --threshold 0.85 --interactive

    python -m pdf_validation.entity_resolution_cli export \
        --root /path/to/pdf_validation --out /path/to/entity_aliases.json
"""

import argparse
import json
import sys
from pathlib import Path

from pdf_validation.entity_resolver import EntityResolver


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="entity_resolution",
        description="Fuzzy match and approve company name entity mappings",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).parent.parent.parent,
        help="Path to pdf_validation directory",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # collect: just list candidates
    collect_parser = subparsers.add_parser("collect", help="Collect and rank candidates")
    collect_parser.add_argument(
        "--threshold",
        type=float,
        default=0.85,
        help="Minimum similarity to include (0.0-1.0)",
    )
    collect_parser.add_argument(
        "--show-all",
        action="store_true",
        help="Show all candidates (even low similarity)",
    )

    # review: interactive approval
    review_parser = subparsers.add_parser("review", help="Interactively review and approve")
    review_parser.add_argument(
        "--threshold",
        type=float,
        default=0.85,
        help="Minimum similarity to review",
    )
    review_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only review first N candidates",
    )

    # export: save approved mappings
    export_parser = subparsers.add_parser("export", help="Export approved mappings to aliases")
    export_parser.add_argument(
        "--threshold",
        type=float,
        default=0.85,
        help="Auto-approve candidates above this threshold",
    )
    export_parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output path (default: configs/entity_aliases.json)",
    )
    export_parser.add_argument(
        "--mode",
        choices=["append", "replace"],
        default="append",
        help="Merge with existing or replace entirely",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    resolver = EntityResolver(args.root)
    print(f"Loading from {args.root}\n")

    if args.command == "collect":
        resolver.collect_candidates(min_similarity=0.0 if args.show_all else args.threshold)
        print(resolver.summary())

        # Show top candidates
        threshold = 0.0 if args.show_all else args.threshold
        candidates = resolver.rank_candidates(threshold)
        print(f"\nTop candidates (threshold={threshold}):")
        for cand in candidates[:20]:
            print(
                f"  {cand.pdf_company_name:40s} → {cand.vendor_source_asset_candidate:40s} "
                f"({cand.similarity:.2%})"
            )
        if len(candidates) > 20:
            print(f"  ... and {len(candidates) - 20} more")

    elif args.command == "review":
        resolver.collect_candidates()
        candidates = resolver.rank_candidates(args.threshold)

        if args.limit:
            candidates = candidates[: args.limit]

        print(f"Reviewing {len(candidates)} candidates (threshold={args.threshold})\n")
        results = resolver.confirm_batch(candidates, interactive=True)

        approved_count = sum(1 for r in results if r.approved)
        print(f"\n\nApproved {approved_count} / {len(results)} candidates")
        resolver.approved = results

    elif args.command == "export":
        resolver.collect_candidates()
        candidates = resolver.rank_candidates(args.threshold)

        print(f"Auto-approving {len(candidates)} candidates above {args.threshold}")
        results = resolver.confirm_batch(candidates, interactive=False)
        resolver.save_aliases(results, path=args.out, mode=args.mode)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
