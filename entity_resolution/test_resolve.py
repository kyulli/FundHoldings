#!/usr/bin/env python3
"""Regression tests for entity resolution.

Every case below is a real name pair from the Brown holdings data. The
must-not-merge cases matter more than the must-merge ones: a missed merge
leaves a name fragmented, which is the status quo, but a wrong merge silently
adds two unrelated companies' exposure together and there is no downstream
check that would catch it.

Run:  python test_resolve.py
"""

from __future__ import annotations

import sys

from resolve import NameRecord, build_token_df, decide, normalize, score_pair

# Expected outcomes:
#   "auto_merge"  -- must be merged without asking a human
#   "no_merge"    -- must NOT be auto-merged; either review or auto_reject is
#                    acceptable. Which of the two a pair lands in depends on how
#                    generic its words are in the surrounding corpus, and that
#                    distinction carries no safety weight: both outcomes leave
#                    the names unmerged. Asserting the exact bucket would make
#                    the suite fail on corpus composition rather than on a real
#                    regression.
#
# (name_a, name_b, expected, why)
CASES: list[tuple[str, str, str, str]] = [
    # ---- must merge: punctuation / legal suffix only --------------------
    ("Anthropic", "Anthropic, Inc.", "auto_merge", "legal suffix"),
    ("Airbnb", "Airbnb Inc", "auto_merge", "legal suffix, no comma"),
    ("A24 Films LLC", "A24 Films, LLC", "auto_merge", "comma only"),
    ("Anthropic PBC", "Anthropic,PBC", "auto_merge", "missing space + suffix"),
    ("ASP-SEG Opportunities LP", "ASP-SEG Opportunities, LP", "auto_merge", "comma"),

    # ---- must merge: vendor embedded an alias in parentheses ------------
    ("10Beauty, Inc.", "10Beauty, Inc. (NailPro, Inc.)", "auto_merge", "parenthetical alias"),
    ("Anterior (fka Co:Helm)", "Anterior, Inc.", "auto_merge", "fka parenthetical"),
    ("ACA Group", 'ACA Group (fka "Foreside")', "auto_merge", "fka parenthetical"),

    # ---- must merge: whitespace-only difference -------------------------
    ("AcuityMD", "Acuity MD", "auto_merge", "word break"),
    ("BharatPe", "Bharat Pe", "auto_merge", "word break"),
    ("ManyPets", "Many Pets", "auto_merge", "word break"),
    ("SurgeTrading, Inc.", "Surge Trading, Inc.", "auto_merge", "word break"),
    ("Marqvision", "Marq Vision Inc.", "auto_merge", "word break + suffix"),

    # ---- must merge: OCR confusable characters --------------------------
    # Capital I and lowercase l are the same glyph in many PDF fonts.
    ("Harmonic AI", "Harmonic Al", "auto_merge", "I/l confusable"),
    ("Talisman AI", "Talisman Al", "auto_merge", "I/l confusable"),
    ("Decart.AI", "Decart.Al", "auto_merge", "I/l confusable"),
    ("Cranium AI, Inc", "Cranium Al, Inc", "auto_merge", "I/l confusable, known alias"),

    # ---- MUST NOT MERGE: sequence numbers are load-bearing ---------------
    ("V2 Healthcare", "V3 Healthcare", "no_merge", "different version"),
    ("LLeo SPV 6, SL", "LLeo SPV 7, SL", "no_merge", "different SPV"),
    ("Golden State Warriors", "Golden State Warriors 3.0", "no_merge", "different vehicle"),
    ("Tampa Bay Lightning", "Tampa Bay Lightning 2.0", "no_merge", "different vehicle"),
    ("Harris Blitzer Sports & Entertainment",
     "Harris Blitzer Sports & Entertainment 2.0", "no_merge", "different vehicle"),
    ("Shift", "Shift5", "no_merge", "different company"),
    ("Accel Atoms AIN", "Accel Atoms AIN7 (M)", "no_merge", "different vehicle"),

    # ---- MUST NOT MERGE: different companies, similar names -------------
    ("Delphi", "Delphix, Inc.", "no_merge", "different companies"),
    ("Square, Inc.", "SquareX", "no_merge", "different companies"),
    ("Thread", "Threads", "no_merge", "different companies"),
    ("Xiaomai", "Xiaomi", "no_merge", "different companies"),
    ("Sprout", "Sprouts, Inc.", "no_merge", "different companies"),
    ("Moment", "Momento, Inc.", "no_merge", "different companies"),
    ("Wonder Group, Inc.", "Wondery", "no_merge", "different companies"),

    # ---- MUST NOT MERGE: generic word overlap only ----------------------
    # These are the pairs that IDF weighting exists to kill.
    ("Slack Technologies, Inc.", "SiMa Technologies", "no_merge", "generic token only"),
    ("Kala Pharmaceuticals, Inc.", "Koye Pharmaceuticals", "no_merge", "generic token only"),
    ("Locus Technologies, Inc.", "Loom Technologies", "no_merge", "generic token only"),
    ("Druva Technologies", "Duet Technologies Inc.", "no_merge", "generic token only"),
    ("Shape Technologies", "SiMa Technologies", "no_merge", "generic token only"),
]

# A corpus that makes "technologies"/"pharmaceuticals" look generic, mirroring
# their real document frequency in the holdings data.
CORPUS_FILLER = (
    [f"Filler{i} Technologies" for i in range(40)]
    + [f"Filler{i} Pharmaceuticals" for i in range(40)]
    + [f"Filler{i} Systems" for i in range(20)]
    + [f"Filler{i} Group" for i in range(20)]
    + [f"Filler{i} Healthcare" for i in range(20)]
)


def make_records(names: list[str]) -> dict[str, NameRecord]:
    return {n: NameRecord(raw=n, norm=normalize(n)) for n in names}


def main() -> int:
    names = sorted({n for a, b, _, _ in CASES for n in (a, b)} | set(CORPUS_FILLER))
    recs = make_records(names)
    tok_df = build_token_df(recs)

    passed = failed = 0
    failures: list[str] = []

    for a_raw, b_raw, expected, why in CASES:
        a, b = recs[a_raw], recs[b_raw]
        score, ts, pr, ds = score_pair(a, b, tok_df)
        decision, rationale = decide(a, b, score, "test")
        # score_pair gates candidacy in the real pipeline; replicate that here.
        if score < 84.0 and not (a.asset_ids & b.asset_ids):
            decision, rationale = "auto_reject", f"score {score:.0f} below 84"

        ok = (decision == "auto_merge") if expected == "auto_merge" \
            else (decision != "auto_merge")
        if ok:
            passed += 1
        else:
            failed += 1
            failures.append(
                f"  {a_raw!r} vs {b_raw!r}\n"
                f"      expected {expected}, got {decision} ({rationale})\n"
                f"      score={score:.1f} token_sort={ts:.0f} partial={pr:.0f} distinctive={ds:.0f}\n"
                f"      norm: {a.norm!r} | {b.norm!r}\n"
                f"      reason for expectation: {why}"
            )

    print(f"{passed} passed, {failed} failed, {len(CASES)} total")
    if failures:
        print("\nFAILURES:\n" + "\n\n".join(failures))
        return 1
    print("\nAll entity resolution decisions match expectations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
