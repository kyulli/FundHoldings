#!/usr/bin/env python3
"""Human review and promotion gate for entity resolution.

`resolve.py` proposes; this script is the only thing that writes aliases.
The separation is deliberate: a wrong merge silently adds two unrelated
companies' exposure together, and no downstream check would catch it.

Commands
--------
    python review.py show     --out ./output
        Print the review queue with the evidence needed to decide.

    python review.py mark     --out ./output --pair 3 --verdict merge
        Record a verdict on one queued pair.

    python review.py families --out ./output
        Group the numbered-series pairs (Fund II/III, SPV 6/7, ...) by their
        common stem, so 193 pairs read as ~80 decisions instead of 193.

    python review.py mark-family --out ./output --stem "kimberlite" --verdict reject
        Apply one verdict to every pair in a family at once.

    python review.py promote  --out ./output --reviewer "Kyulli"
        Write auto-merge clusters + human-approved merges into
        entity_aliases.json. Refuses to run if the queue has unreviewed
        pairs, unless --allow-unreviewed is passed.

    python review.py apply    --out ./output --data ../data/holdings.csv
        Add a canonical_company column to the holdings data and report what
        collapsed. Read-only with respect to the source file.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

QUEUE = "entity_review_queue.csv"
CLUSTERS = "entity_clusters.csv"


# --------------------------------------------------------------------------


def cmd_show(args: argparse.Namespace) -> int:
    q = _load_queue(args.out / QUEUE)
    if args.pending:
        q = q[q.reviewer_verdict == ""]

    if q.empty:
        print("Review queue is empty.")
        return 0

    print(f"{len(q)} pair(s) awaiting a decision. These are all genuinely")
    print("ambiguous spellings -- sequence-number pairs (Fund II/III, SPV 6/7)")
    print("are auto-rejected upstream and never reach this queue; see")
    print("'review.py families' for that audit trail.\n")
    print("For each: do these two names refer to the same company?\n")

    for i, r in q.iterrows():
        verdict = r.reviewer_verdict
        print(f"[{i}] score {r.score:.0f}   {'** ' + verdict + ' **' if verdict else ''}")
        print(f"     A: {r.name_a}")
        managers_a = str(r.managers_a) if pd.notna(r.managers_a) else "n/a"
        print(f"        funds={r.n_funds_a}  rows={r.rows_a}  managers={managers_a}  ids={r.asset_ids_a}")
        print(f"     B: {r.name_b}")
        managers_b = str(r.managers_b) if pd.notna(r.managers_b) else "n/a"
        print(f"        funds={r.n_funds_b}  rows={r.rows_b}  managers={managers_b}  ids={r.asset_ids_b}")
        print(f"     why queued: {r.rationale}")
        print(f"     mark it:    python review.py mark --pair {i} --verdict merge|reject")
        print()
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    path = args.out / QUEUE
    q = _load_queue(path)
    if args.pair not in q.index:
        print(f"No pair {args.pair}. Range is 0..{len(q)-1}.")
        return 1
    if args.verdict not in ("merge", "reject"):
        print("Verdict must be 'merge' or 'reject'.")
        return 1

    q.loc[args.pair, "reviewer_verdict"] = args.verdict
    if args.note:
        q.loc[args.pair, "reviewer_notes"] = args.note
    q.to_csv(path, index=False)

    r = q.loc[args.pair]
    print(f"[{args.pair}] {r.name_a}  <->  {r.name_b}   marked {args.verdict}")
    remaining = (q.reviewer_verdict == "").sum()
    print(f"{remaining} pair(s) still unreviewed.")
    return 0


def _load_queue(path: Path) -> pd.DataFrame:
    q = pd.read_csv(path, dtype={"reviewer_verdict": "string",
                                 "reviewer_notes": "string",})
    q["reviewer_verdict"] = q["reviewer_verdict"].fillna("")
    return q


SEQ_AUDIT = "sequence_conflicts_rejected.csv"
OVERRIDES = "manual_overrides.csv"


def cmd_families(args: argparse.Namespace) -> int:
    """Read-only audit view. resolve.py auto-rejects sequence-conflict pairs
    (Fund II vs III, SPV 6 vs 7) without putting them in the review queue --
    they are, without exception in this book, different vehicles, not spelling
    variants, so a human decision was pure overhead. This command exists so
    that decision stays inspectable: if you doubt a specific rejection, look
    it up here and use `override` if you disagree.
    """
    path = args.out / SEQ_AUDIT
    if not path.exists():
        print(f"No {SEQ_AUDIT} yet -- run resolve.py first.")
        return 0

    a = pd.read_csv(path)
    if a.empty:
        print("No sequence-conflict pairs were rejected.")
        return 0

    print(f"{len(a)} pair(s) auto-rejected for a differing vintage/series "
          f"number, across {a['name_a'].nunique() + a['name_b'].nunique()} names.")
    print("Not in the review queue -- no action needed. Listed for audit only.\n")
    print(a[["name_a", "name_b", "score"]].to_string(index=False))
    print(f"\nDisagree with one of these? "
          f"python review.py override --name-a '...' --name-b '...' --verdict merge")
    return 0


def cmd_override(args: argparse.Namespace) -> int:
    """Force a verdict on a specific pair that never entered the review queue
    -- either a sequence-conflict auto-reject, or any other pair a reviewer
    has an outside reason to weigh in on. Appends to manual_overrides.csv,
    which `promote` reads in addition to the queue.
    """
    valid_verdicts = ("merge", "reject", "defer", "parent_child", "alias", "variant")
    if args.verdict not in valid_verdicts:
        print(f"Verdict must be one of: {', '.join(valid_verdicts)}")
        return 1
    path = args.out / OVERRIDES
    row = pd.DataFrame([{
        "name_a": args.name_a, "name_b": args.name_b, "verdict": args.verdict,
        "reviewer": args.reviewer, "note": args.note,
        "recorded": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }])
    if path.exists():
        existing = pd.read_csv(path)
        existing = existing[
            ~((existing.name_a == args.name_a) & (existing.name_b == args.name_b))
        ]
        row = pd.concat([existing, row], ignore_index=True)
    row.to_csv(path, index=False)
    print(f"{args.name_a!r} <-> {args.name_b!r} overridden to {args.verdict!r} "
          f"by {args.reviewer}")
    return 0


# --------------------------------------------------------------------------


def cmd_promote(args: argparse.Namespace) -> int:
    clusters = pd.read_csv(args.out / CLUSTERS)
    q = _load_queue(args.out / QUEUE)

    unreviewed = q[q.reviewer_verdict == ""]
    if len(unreviewed) and not args.allow_unreviewed:
        print(f"{len(unreviewed)} of {len(q)} queued pairs have no verdict.")
        print("Promoting now would silently drop them. Either review them")
        print("  python review.py show --pending")
        print("or promote the auto-merge clusters only:")
        print("  python review.py promote --allow-unreviewed")
        return 1

    aliases_path = args.aliases
    existing = (
        json.loads(aliases_path.read_text(encoding="utf-8"))
        if aliases_path.exists()
        else {"global": {}, "by_fund": {}}
    )
    existing.setdefault("global", {})
    existing.setdefault("by_fund", {})
    before = len(existing["global"])

    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from resolve import normalize

    added_auto = 0
    for _, c in clusters.iterrows():
        canon = c.canonical_name
        for variant in str(c.variants).split(" | "):
            if variant == canon:
                continue
            key = normalize(variant)
            if key and existing["global"].get(key) != canon:
                existing["global"][key] = canon
                added_auto += 1

    added_human = 0
    approved = q[q.reviewer_verdict == "merge"]
    for _, r in approved.iterrows():
        # Longer name is the canonical one: it usually retains the legal
        # suffix and full spelling, which is what a report should show.
        a, b = str(r.name_a), str(r.name_b)
        canon, variant = (a, b) if len(a) >= len(b) else (b, a)
        key = normalize(variant)
        if key and existing["global"].get(key) != canon:
            existing["global"][key] = canon
            added_human += 1

    # Manual overrides: pairs that never entered the review queue at all
    # (auto-rejected sequence-conflict pairs, or anything else a reviewer has
    # an outside reason to force) but that `review.py override` recorded.
    added_override = 0
    overrides_path = args.out / OVERRIDES
    if overrides_path.exists():
        overrides = pd.read_csv(overrides_path)
        for _, r in overrides[overrides.verdict == "merge"].iterrows():
            a, b = str(r.name_a), str(r.name_b)
            canon, variant = (a, b) if len(a) >= len(b) else (b, a)
            key = normalize(variant)
            if key and existing["global"].get(key) != canon:
                existing["global"][key] = canon
                added_override += 1

    existing["_meta"] = {
        "last_promoted": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reviewer": args.reviewer,
        "auto_merge_aliases": added_auto,
        "human_approved_aliases": added_human,
        "manual_override_aliases": added_override,
        "rejected_pairs": int((q.reviewer_verdict == "reject").sum()),
        "unreviewed_pairs_at_promotion": int(len(unreviewed)),
    }

    aliases_path.parent.mkdir(parents=True, exist_ok=True)
    aliases_path.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"Promoted by {args.reviewer}")
    print(f"  from auto-merge clusters : {added_auto}")
    print(f"  from human approval      : {added_human}")
    print(f"  from manual overrides    : {added_override}")
    print(f"  global aliases {before} -> {len(existing['global'])}")
    print(f"  written to {aliases_path}")
    return 0


# --------------------------------------------------------------------------


def cmd_apply(args: argparse.Namespace) -> int:
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from resolve import normalize

    aliases = json.loads(args.aliases.read_text(encoding="utf-8"))
    amap = aliases.get("global", {})
    if not amap:
        print("No aliases yet. Run promote first.")
        return 1

    df = pd.read_csv(args.data, low_memory=False)
    col = "Source Asset"
    df["canonical_company"] = [
        amap.get(normalize(n), n) if isinstance(n, str) else n for n in df[col]
    ]

    before = df[col].nunique()
    after = df["canonical_company"].nunique()
    changed = int((df[col] != df["canonical_company"]).sum())

    out = args.out / args.output_name
    df.to_csv(out, index=False)

    print(f"distinct names  {before:,} -> {after:,}   ({before - after:,} collapsed)")
    print(f"rows relabelled {changed:,}")

    g = (
        df.groupby("canonical_company")
        .agg(
            variants=(col, "nunique"),
            funds=("Fund Allocator ID", "nunique"),
            managers=("Investment Manager Allocator ID", "nunique"),
            rows=(col, "size"),
        )
        .query("variants > 1")
        .sort_values(["funds", "variants"], ascending=False)
    )
    print(f"\n{len(g)} companies now span more than one source name.")
    print("Widest reach after resolution:\n")
    print(g.head(15).to_string())

    g.to_csv(args.out / "canonical_company_reach.csv")
    print(f"\nWrote {out}")
    print(f"Wrote {args.out / 'canonical_company_reach.csv'}")
    return 0


# --------------------------------------------------------------------------


def main() -> int:
    default_aliases = (
        Path(__file__).parent.parent
        / "pdf_validation" / "configs" / "entity_aliases.json"
    )

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", type=Path, default=Path("./output"))
    ap.add_argument("--aliases", type=Path, default=default_aliases)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("show", help="print the review queue")
    s.add_argument("--pending", action="store_true", help="only undecided pairs")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("mark", help="record a verdict on one pair")
    s.add_argument("--pair", type=int, required=True)
    s.add_argument("--verdict", required=True, choices=["merge", "reject"])
    s.add_argument("--note", default="")
    s.set_defaults(func=cmd_mark)

    s = sub.add_parser("families", help="audit view of auto-rejected sequence pairs")
    s.set_defaults(func=cmd_families)

    s = sub.add_parser("override", help="force a verdict on a specific pair "
                                        "(e.g. a sequence-conflict auto-reject "
                                        "you believe is wrong, or mark for review)")
    s.add_argument("--name-a", required=True)
    s.add_argument("--name-b", required=True)
    s.add_argument("--verdict", required=True,
                   choices=["merge", "reject", "defer", "parent_child", "alias", "variant"],
                   help="merge | reject | defer | parent_child | alias | variant")
    s.add_argument("--reviewer", required=True)
    s.add_argument("--note", default="")
    s.set_defaults(func=cmd_override)

    s = sub.add_parser("promote", help="write aliases (the gate)")
    s.add_argument("--reviewer", required=True)
    s.add_argument("--allow-unreviewed", action="store_true")
    s.set_defaults(func=cmd_promote)

    s = sub.add_parser("apply", help="add canonical_company to holdings")
    s.add_argument("--data", type=Path, required=True)
    s.add_argument("--output-name", default="holdings_with_canonical.csv",
                   help="filename to write inside --out (default: holdings_with_canonical.csv)")
    s.set_defaults(func=cmd_apply)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
