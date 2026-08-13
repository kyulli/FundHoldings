#!/usr/bin/env python3
"""Cross-fund entity resolution for portfolio company names.

Problem
-------
The same portfolio company is written differently by different GPs, so it
fragments across funds:

    "Oomnitz"                  (A0d2a71)
    "Oomnitza, Inc."           (Aeedb32)
    "OOMNITZA INC"             (A2a9ea5)

Any cross-fund question -- total exposure to a company, how many managers hold
it, whether two funds mark it at different values -- is wrong until these are
collapsed to one entity.

Approach
--------
1. Normalise:  case, punctuation, legal suffixes (Inc/LLC/Ltd/LP/...), spacing.
2. Block:      only compare names that share a rare token or a sorted-prefix
               key. Full pairwise on 8.7k names is 38M comparisons; blocking
               cuts it to a few hundred thousand and loses nothing that a
               fuzzy threshold would have kept.
3. Score:      rapidfuzz token_sort_ratio + partial_ratio, combined.
4. Evidence:   attach the facts a reviewer needs -- which funds, which vendor
               Source Asset IDs, row counts, investment types.
5. Auto-decide only what is safe. Everything else goes to a review queue.

Nothing here writes to entity_aliases.json. Promotion is a separate, human
step (review.py), because a false merge silently corrupts every downstream
exposure number and is very hard to detect afterwards.

Usage
-----
    python resolve.py --data ../data/holdings_anonymized.csv --out ./output
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

# Phase 1's row classifier is the source of truth for "is this row a company
# holding". Importing it (instead of re-deriving the same distinction from
# Type of Investment) is what keeps Phase 3's population in sync with Phase 1's
# entity-resolution scope section. Divergence here previously produced 7,533
# names against Phase 1's 8,633 -- two different definitions of "company" that
# happened to share a name.
NOTEBOOKS_DIR = Path(__file__).resolve().parent.parent / "notebooks"
if str(NOTEBOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(NOTEBOOKS_DIR))
import source_asset_filter as saf  # noqa: E402

# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

# Ordered longest-first so "limited partnership" is stripped before "limited".
LEGAL_SUFFIXES = [
    "limited partnership", "limited liability company", "societas europaea",
    "public limited company", "incorporated", "corporation", "company",
    "holdings", "holding", "group", "partners", "partnership",
    "llc", "l.l.c", "lp", "l.p", "llp", "plc", "pbc", "inc", "corp", "co",
    "ltd", "limited", "gmbh", "ag", "sa", "s.a", "nv", "n.v", "bv", "b.v",
    "ab", "as", "a/s", "oy", "aps", "spa", "s.p.a", "srl", "s.r.l",
    "pty", "pte", "sarl", "kk", "k.k", "sas", "sasu", "se", "cv", "vof",
]

# Tokens that carry no identity signal on their own.
STOPWORDS = {"the", "and", "of", "for", "a", "an"}

# Series/class designations: "Waymo LLC Series C" and "Waymo LLC" are the same
# company at different instrument grain. Stripped for matching, but recorded --
# a reviewer needs to know the difference was a share class, not a typo.
SERIES_RE = re.compile(
    r"\b(series|class|tranche|round)\s+[a-z0-9]{1,3}\b|"
    r"\b(seed|preferred|common|ordinary)\s+(stock|shares|units)\b",
    re.I,
)

PAREN_RE = re.compile(r"\(([^)]*)\)")
NONALNUM_RE = re.compile(r"[^a-z0-9 ]+")
WS_RE = re.compile(r"\s+")

# "L.P." has to become "lp", not "l p". NONALNUM_RE below turns every period
# into a space, which is right for sentence-like text but wrong for a dotted
# abbreviation: "Fund II, L.P." and "Fund II LP" would normalise to
# "fund ii l p" and "fund ii lp" -- two words vs one -- and LEGAL_SUFFIXES can
# only strip a single trailing suffix, so it matches neither. Collapsing
# multi-dot abbreviations (L.P., L.L.C., S.A., N.V., ...) before that pass
# fixes the whole family at once, rather than special-casing each one.
#
# The trailing letter is optional dot ([a-z]\.?, not [a-z]\.) because vendor
# data is inconsistent about the final period: "Fund I, L.P" (no closing dot,
# usually because a comma or the string end follows) is at least as common as
# "Fund I, L.P.". Requiring the closing dot left "L.P" un-collapsed, which
# NONALNUM_RE then split into stray "l" and "p" tokens -- and a stray "l"
# reads as the roman numeral for 50 to `_sequence_conflict`, which is exactly
# how "Ocean Propeller Fund I, L.P" picked up a spurious vintage marker.
ABBREV_DOTS_RE = re.compile(r"\b[a-z]\.(?:[a-z]\.?)+", re.I)

# "Pvt" is the standard South Asian abbreviation for "Private" in company
# registrations (Pvt Ltd == Private Limited). Without this, "X Pvt Limited"
# and "X Private Limited" normalise to different strings and the identical
# entity fails to auto-merge purely on a regional spelling convention.
PVT_RE = re.compile(r"\bpvt\b", re.I)


def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


# OCR and font confusables. Capital I, lowercase l and digit 1 are visually
# identical in many PDF fonts, which is how "Cranium AI" became "Cranium Al" in
# the vendor feed. Same for O/0. Folding these to a single character makes the
# pair exactly equal instead of merely similar, so it can be decided
# mechanically rather than sent to a human.
CONFUSABLE = str.maketrans({"l": "i", "1": "i", "0": "o", "5": "s", "8": "b"})


def fold_confusables(s: str) -> str:
    return s.translate(CONFUSABLE)


def normalize(name: str) -> str:
    """Aggressive normalisation used for blocking and scoring."""
    if not isinstance(name, str):
        return ""
    s = strip_accents(name).lower().strip()
    s = s.replace("&", " and ")
    s = PAREN_RE.sub(" ", s)          # drop parentheticals: "House of Home AB (Nordic Knots)"
    s = SERIES_RE.sub(" ", s)         # drop share-class designations
    s = PVT_RE.sub("private", s)      # "Pvt" -> "Private" before suffix matching
    s = ABBREV_DOTS_RE.sub(lambda m: m.group(0).replace(".", ""), s)  # "L.P." -> "lp"
    s = NONALNUM_RE.sub(" ", s)
    s = WS_RE.sub(" ", s).strip()

    # Strip trailing legal suffixes repeatedly: "foo inc llc" -> "foo"
    changed = True
    while changed:
        changed = False
        for suf in LEGAL_SUFFIXES:
            if s.endswith(" " + suf):
                s = s[: -(len(suf) + 1)].strip()
                changed = True
                break
    return s


def tokens(norm: str) -> frozenset[str]:
    return frozenset(t for t in norm.split() if t and t not in STOPWORDS)


def parentheticals(name: str) -> list[str]:
    """Aliases the vendor already embedded in the name, e.g. '(Nordic Knots)'."""
    return [m.strip() for m in PAREN_RE.findall(name or "") if m.strip()]


def has_series(name: str) -> bool:
    return bool(SERIES_RE.search(name or ""))


# --------------------------------------------------------------------------
# Non-company names
# --------------------------------------------------------------------------
#
# Some values in the Source Asset field are not companies at all. Entity
# resolution surfaces them because their variants match each other, but they
# must not become aliases -- merging "United States - Information Technology"
# into a canonical "company" would launder a data-entry error into a clean
# looking entity. They are reported separately as a data-quality finding.

_SECTOR_WORDS = {
    "information technology", "health care", "healthcare", "financials",
    "industrials", "consumer discretionary", "consumer staples", "energy",
    "materials", "utilities", "real estate", "communication services",
    "telecommunications", "technology", "financial services",
}
_GEO_WORDS = {
    "united states", "europe", "asia", "north america", "south america",
    "china", "india", "canada", "japan", "global", "emea", "apac", "latam",
    "united kingdom", "germany", "france",
}

PLACEHOLDER_RE = re.compile(
    r"^(seed investments?|various|various investments?|other|others|misc\w*|"
    r"n/?a|none|unknown|tbd|undisclosed|not disclosed|confidential|"
    r"cash|cash equivalents?|escrow|escrow receivable|portfolio|"
    r"multiple companies|aggregate|unallocated)$",
    re.I,
)

# "Source Code" / "Source Code II" / "Source Code RMB" is not a currency
# variant of one company -- it is the literal anonymisation placeholder this
# extract used to mask real names, reused for several *different* underlying
# entities within the same fund (confirmed by Type of Investment: "Source
# Code" alone is a Private Company, "Source Code II" and "Source Code RMB" are
# both Fund vehicles -- three different things, not one company in three
# currencies). Matching on suffix similarity here would merge unrelated masked
# entities into a single fake "company".
ANONYMISATION_PLACEHOLDER_RE = re.compile(r"^source\s+code\b", re.I)

# "North America - Information technology company A" / "...company B": one GP
# (manager A99a662, Type of Investment "Other" on every row) redacts its own
# confidential holdings this way, grouping them by region/sector and standing
# in a bare letter for the real name. This is the GP's own redaction, not our
# anonymisation pass, but the effect is the same: "company A" is not a company
# name to match against anything, it is "an unnamed holding in this sector
# bucket." Matching two of these on string similarity would merge two
# admittedly-different, admittedly-unnamed companies into one.
REDACTED_LETTER_RE = re.compile(r"^.+\s-\s.+\scompany\s[a-z]$", re.I)


def classify_non_company(raw: str) -> str | None:
    """Return a data-quality reason if this name is not a company, else None."""
    s = (raw or "").strip()
    if not s:
        return None

    if PLACEHOLDER_RE.match(s):
        return "placeholder / aggregate label, not a named company"

    if ANONYMISATION_PLACEHOLDER_RE.match(s):
        return "anonymisation placeholder, reused across different masked entities"

    if REDACTED_LETTER_RE.match(s):
        return "GP-redacted confidential holding (region/sector + bare letter, no real name)"

    # "Information Technology - United States" and its reversal: a sector and a
    # geography joined by a dash, sitting in the company-name column.
    parts = [p.strip().lower() for p in re.split(r"\s*[-–|/]\s*", s)]
    if len(parts) == 2:
        has_sector = bool(set(parts) & _SECTOR_WORDS)
        has_geo = bool(set(parts) & _GEO_WORDS)
        if has_sector and has_geo:
            return "sector/geography label in the company-name field"
        if has_sector and len(parts[0].split()) <= 3 and len(parts[1].split()) <= 3:
            if all(p in _SECTOR_WORDS or p in _GEO_WORDS for p in parts):
                return "classification label in the company-name field"
    return None


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass
class NameRecord:
    """One distinct Source Asset string and everywhere it appears."""

    raw: str
    norm: str
    funds: set[str] = field(default_factory=set)
    managers: set[str] = field(default_factory=set)
    asset_ids: set[str] = field(default_factory=set)
    inv_types: set[str] = field(default_factory=set)
    rows: int = 0

    @property
    def toks(self) -> frozenset[str]:
        return tokens(self.norm)


@dataclass
class Pair:
    a: NameRecord
    b: NameRecord
    score: float
    token_sort: float
    partial: float
    distinctive: float     # similarity of the rare-token remainder
    reason: str            # what made this a candidate
    decision: str          # auto_merge | review | auto_reject
    rationale: str         # why that decision

    def to_row(self) -> dict:
        shared_ids = self.a.asset_ids & self.b.asset_ids
        return {
            "name_a": self.a.raw,
            "name_b": self.b.raw,
            "score": round(self.score, 2),
            "token_sort_ratio": round(self.token_sort, 2),
            "partial_ratio": round(self.partial, 2),
            "distinctive_ratio": round(self.distinctive, 2),
            "norm_a": self.a.norm,
            "norm_b": self.b.norm,
            "decision": self.decision,
            "rationale": self.rationale,
            "match_reason": self.reason,
            "same_vendor_id": bool(shared_ids),
            "cross_fund": not (self.a.funds & self.b.funds),
            "funds_a": " | ".join(sorted(self.a.funds)),
            "funds_b": " | ".join(sorted(self.b.funds)),
            "n_funds_a": len(self.a.funds),
            "n_funds_b": len(self.b.funds),
            "managers_a": " | ".join(sorted(self.a.managers)),
            "managers_b": " | ".join(sorted(self.b.managers)),
            "asset_ids_a": " | ".join(sorted(self.a.asset_ids)),
            "asset_ids_b": " | ".join(sorted(self.b.asset_ids)),
            "inv_type_a": " | ".join(sorted(self.a.inv_types)),
            "inv_type_b": " | ".join(sorted(self.b.inv_types)),
            "rows_a": self.a.rows,
            "rows_b": self.b.rows,
            "family": (
                family_stem(self.a.norm)
                if self.rationale.endswith("different entities")
                else ""
            ),
            "reviewer_verdict": "",   # human review: merge / reject
            "reviewer_notes": "",
        }


# --------------------------------------------------------------------------
# Build records
# --------------------------------------------------------------------------

COLS = {
    "manager": "Investment Manager Allocator ID",
    "fund": "Fund Allocator ID",
    "name": "Source Asset",
    "asset_id": "Source Asset ID",
    "inv_type": "Type of Investment",
}


def build_records(df: pd.DataFrame, resolvable_only: bool = True) -> dict[str, NameRecord]:
    """Build one NameRecord per distinct Source Asset string.

    Population is Phase 1's `is_resolvable` set by default: rows whose
    `source_asset_class` (from source_asset_filter.py) is `holding` --
    i.e. not a balance-sheet line, accounting entry, subtotal, or unnamed
    aggregation bucket ("Other Investments", "Seed Investments"). This is a
    text classification of the Source Asset string itself, deliberately
    independent of `Type of Investment`: the two fields disagree often enough
    (blank, "Other", or a wrong tag on a real company row) that filtering on
    `Type of Investment` silently drops resolvable companies and admits rows
    Phase 1 already excluded.
    """
    if resolvable_only:
        annotated = saf.annotate(df, column=COLS["name"])
        df = annotated[annotated["is_resolvable"]]

    recs: dict[str, NameRecord] = {}
    for raw, fund, mgr, aid, itype in zip(
        df[COLS["name"]], df[COLS["fund"]], df[COLS["manager"]],
        df[COLS["asset_id"]], df[COLS["inv_type"]],
    ):
        if not isinstance(raw, str) or not raw.strip():
            continue
        r = recs.get(raw)
        if r is None:
            r = recs[raw] = NameRecord(raw=raw, norm=normalize(raw))
        r.rows += 1
        if isinstance(fund, str):
            r.funds.add(fund)
        if isinstance(mgr, str):
            r.managers.add(mgr)
        if isinstance(aid, str):
            r.asset_ids.add(aid)
        if isinstance(itype, str):
            r.inv_types.add(itype)
    return recs


# --------------------------------------------------------------------------
# Blocking
# --------------------------------------------------------------------------


def build_blocks(recs: dict[str, NameRecord], max_block: int = 400) -> dict[str, set[str]]:
    """Group names that are worth comparing.

    Three block keys, unioned:
      - exact normalised form   (catches suffix-only differences)
      - first 4 chars of norm   (catches typos late in the string)
      - each token              (catches word-order and extra-word differences)

    Very common tokens ("capital", "technologies") produce huge blocks that are
    mostly noise, so blocks above `max_block` are dropped -- any real pair in
    them will still be caught by the prefix or exact-norm key.
    """
    blocks: dict[str, set[str]] = defaultdict(set)
    for raw, r in recs.items():
        if not r.norm:
            continue
        blocks["N:" + r.norm].add(raw)
        blocks["P:" + r.norm[:4]].add(raw)
        for t in r.toks:
            if len(t) >= 4:
                blocks["T:" + t].add(raw)

    return {
        k: v for k, v in blocks.items()
        if 2 <= len(v) <= max_block
    }


# --------------------------------------------------------------------------
# Scoring + decisions
# --------------------------------------------------------------------------

AUTO_MERGE_SCORE = 97.0
REVIEW_SCORE = 84.0

# A token appearing in more than this many distinct names carries no identity
# signal -- "technologies", "capital", "health". Raw fuzzy scoring is badly
# fooled by these: "Slack Technologies" vs "SiMa Technologies" scores 86 on
# token_sort_ratio purely because of the shared generic word.
COMMON_TOKEN_DF = 15


def build_token_df(recs: dict[str, NameRecord]) -> dict[str, int]:
    """Document frequency of each token across distinct names."""
    df: dict[str, int] = defaultdict(int)
    for r in recs.values():
        for t in r.toks:
            df[t] += 1
    return dict(df)


def distinctive(r: NameRecord, tok_df: dict[str, int]) -> str:
    """The name with generic high-frequency tokens removed.

    'Slack Technologies' -> 'slack';  'SiMa Technologies' -> 'sima'
    Falls back to the full normalised form when every token is generic, so
    names made entirely of common words are still comparable.
    """
    keep = [t for t in r.norm.split()
            if t not in STOPWORDS and tok_df.get(t, 0) <= COMMON_TOKEN_DF]
    return " ".join(keep) if keep else r.norm


def score_pair(a: NameRecord, b: NameRecord,
               tok_df: dict[str, int]) -> tuple[float, float, float, float]:
    """Return (combined, token_sort, partial, distinctive) scores.

    token_sort handles word reordering and is the primary signal; partial_ratio
    rescues containment ("oomnitz" in "oomnitza"). The distinctive score is
    computed on the rare-token remainder and acts as a gate, not a booster --
    it can only pull a pair down, never lift a weak one up.
    """
    ts = fuzz.token_sort_ratio(a.norm, b.norm)
    pr = fuzz.partial_ratio(a.norm, b.norm)
    ds = fuzz.token_sort_ratio(distinctive(a, tok_df), distinctive(b, tok_df))
    base = 0.75 * ts + 0.25 * pr
    # Cap the pair at its distinctive similarity: two names cannot be more
    # alike than their identity-bearing parts are.
    return min(base, ds), ts, pr, ds


def decide(a: NameRecord, b: NameRecord, score: float, reason: str) -> tuple[str, str]:
    """Return (decision, rationale).

    Conservative by construction: the cost of a wrong merge (silently wrong
    exposure numbers across funds) far exceeds the cost of a missed merge
    (a name stays fragmented, which is the status quo).
    """
    # The vendor already asserts these are one entity.
    if a.asset_ids & b.asset_ids:
        return "auto_merge", "shares a vendor Source Asset ID"

    # Identical after normalisation -- difference is punctuation/suffix only.
    if a.norm == b.norm and a.norm:
        return "auto_merge", "identical after normalisation"

    # Any numeric/sequence difference is disqualifying and is checked before
    # the mechanical rules below, because "V2 Healthcare" vs "V3 Healthcare"
    # and "LLeo SPV 6" vs "LLeo SPV 7" differ by exactly one character yet are
    # different entities. Sequence numbers are meaningful in fund vehicle names.
    if _sequence_conflict(a.norm, b.norm):
        return "auto_reject", "sequence number differs — different entities"

    # Whitespace-only difference: "AcuityMD" / "Acuity MD", "BharatPe" /
    # "Bharat Pe". Word-splitting is a transcription artefact, not identity.
    if a.norm.replace(" ", "") == b.norm.replace(" ", ""):
        return "auto_merge", "identical ignoring word breaks"

    # Confusable-character difference: "Harmonic AI" / "Harmonic Al".
    if fold_confusables(a.norm.replace(" ", "")) == \
       fold_confusables(b.norm.replace(" ", "")):
        return "auto_merge", "identical after folding OCR confusables (I/l/1, O/0)"

    if score >= AUTO_MERGE_SCORE:
        return "auto_merge", f"score {score:.0f} >= {AUTO_MERGE_SCORE:.0f}"

    if score >= REVIEW_SCORE:
        return "review", f"score {score:.0f} in review band"

    return "auto_reject", f"score {score:.0f} below {REVIEW_SCORE:.0f}"


# Standalone roman numerals (fund vintages: "Fund II" vs "Fund III").
# A full valid-numeral matcher, not just I-XII: "Glade Brook Private Investors
# XLIII" (43) vs "...XVIII" (18) both fell through the old i/ii/iii/iv/v..xii
# cap and landed in the score-based review band instead of being recognised as
# a vintage conflict.
#
# Restricted to the symbols i/v/x/l (values 1-49) -- deliberately dropping
# c/d/m (100/500/1000) from the alphabet, not just from the whitelist. A scan
# of every name in this book's population showed every *real* fund-vintage
# number uses only i/v/x/l (Fund I..XLIII), while every c/d/m token that
# validated as a "roman numeral" was actually a business abbreviation: "MD"
# (Acuity MD), "DC" (Poplar DC Holdings), "CM" (Ara CM Holdings), "CV" (CV
# Advisors), "CI" (CI Financial), "CC"/"CCC"/"CCI" (fund and vendor initials).
# No fund in this book is named for its 100th, 500th or 1000th vintage, so
# there is no real case c/d/m would catch and a long list of English/business
# abbreviations it would falsely catch instead.
#
# `_CANDIDATE_TOKEN_RE` finds whole all-i/v/x/l tokens; `_ROMAN_FULLMATCH_RE`
# (used with fullmatch, not search) validates each one so a token like "mix"
# -- not reachable now that m is excluded -- or "livid" -- not a full-token
# match -- is never accepted, and so a fully-optional pattern can never
# produce the classic zero-width match at every word start.
#
# Two more collisions surfaced by the same corpus scan, both bare/short even
# after dropping c/d/m: solitary "l" (from "L&O Apartments", an initial pair
# split on "&", not the 50th vintage of anything) and "li" (from "Li Auto
# Inc.", a surname, valued at 51). Neither has a real use in this book's
# fund-vintage names -- the smallest real "l"-containing vintage is "xl" (40)
# -- so they are excluded outright rather than left to coincidentally match.
_ROMAN_FALSE_FRIENDS = frozenset({"l", "li"})
_CANDIDATE_TOKEN_RE = re.compile(r"\b[ivxl]+\b")
_ROMAN_FULLMATCH_RE = re.compile(r"(?:xl|l?x{0,3})(?:ix|iv|v?i{0,3})")


class _RomanMatcher:
    """Drop-in for re.compile(...).findall() with whole-token validation."""

    def findall(self, s: str) -> list[str]:
        out = []
        for tok in _CANDIDATE_TOKEN_RE.findall(s):
            if tok in _ROMAN_FALSE_FRIENDS:
                continue
            m = _ROMAN_FULLMATCH_RE.fullmatch(tok)
            if m and m.group(0):
                out.append(tok)
        return out


ROMAN_RE = _RomanMatcher()
# Any digit run, including ones fused to a word: "v2", "shift5", "spv 6", "3 0".
DIGIT_RE = re.compile(r"\d+")

# A trailing sequence token: "kimberlite ii" -> "kimberlite", "jadeite iv" ->
# "jadeite", "kf134" -> "kf", "glade brook xliii" -> "glade brook". Used only
# to GROUP review/audit pairs that were held for the same reason (same series,
# different vintage number) so a reviewer can dispatch a whole family in one
# glance instead of reading every pairwise combination. It is never used to
# decide whether to merge; that stays with `_sequence_conflict`, deliberately
# conservative than string-stripping alone.
TRAILING_SEQ_RE = re.compile(
    r"\s+(?:[ivxl]+|\d+[a-z]?)$|(?<=[a-z])\d+[a-z]?$"
)


def family_stem(norm: str) -> str:
    """Collapse a numbered-series name to its series root for grouping."""
    return TRAILING_SEQ_RE.sub("", norm).strip() or norm


def _sequence_conflict(a: str, b: str) -> bool:
    """True if the names carry different sequence/vintage/version numbers.

    Sequence numbers are load-bearing in this dataset. Fund vehicles ("Fund II"
    vs "Fund III"), SPV series ("LLeo SPV 6" vs "LLeo SPV 7"), club-deal
    vintages ("Tampa Bay Lightning" vs "Tampa Bay Lightning 2.0") and product
    versions ("V2 Healthcare" vs "V3 Healthcare") are separate entities that
    differ by one character, so string similarity is actively misleading here.

    Deliberately asymmetric-safe: a number present on one side and absent on the
    other also counts as a conflict, because "Warriors" and "Warriors 3.0" are
    different investment vehicles.
    """
    if set(DIGIT_RE.findall(a)) != set(DIGIT_RE.findall(b)):
        return True
    return set(ROMAN_RE.findall(a)) != set(ROMAN_RE.findall(b))


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


def split_non_companies(
    recs: dict[str, NameRecord],
) -> tuple[dict[str, NameRecord], list[tuple[NameRecord, str]]]:
    """Separate real company names from data-quality artefacts."""
    good: dict[str, NameRecord] = {}
    bad: list[tuple[NameRecord, str]] = []
    for raw, r in recs.items():
        reason = classify_non_company(raw)
        if reason:
            bad.append((r, reason))
        else:
            good[raw] = r
    return good, bad


def resolve(
    recs: dict[str, NameRecord], max_block: int = 400
) -> tuple[list[Pair], list[Pair]]:
    """Return (kept_pairs, sequence_audit_pairs).

    `kept_pairs` feeds clustering and the review queue as before.

    `sequence_audit_pairs` is the sequence-conflict auto-rejects on their own.
    They are not actionable -- `decide()` has already determined these are
    different vehicles, not candidates for a human to weigh in on -- but many
    score 90-99%, high enough that "why wasn't this merged" is a fair question
    for an auditor to ask later. Silently dropping them (the previous
    behaviour) left no record to answer that question with. Low-score
    auto-rejects (< REVIEW_SCORE, no shared vendor ID) are not collected here:
    they were never close enough to warrant an explanation.
    """
    blocks = build_blocks(recs, max_block)
    tok_df = build_token_df(recs)

    seen: set[tuple[str, str]] = set()
    pairs: list[Pair] = []
    sequence_audit: list[Pair] = []

    for key, members in blocks.items():
        kind = key[0]
        for x, y in combinations(sorted(members), 2):
            if (x, y) in seen:
                continue
            seen.add((x, y))
            a, b = recs[x], recs[y]
            score, ts, pr, ds = score_pair(a, b, tok_df)
            if score < REVIEW_SCORE and not (a.asset_ids & b.asset_ids):
                continue
            reason = {"N": "same normalised form", "P": "shared prefix", "T": "shared token"}[kind]
            decision, rationale = decide(a, b, score, reason)
            pair = Pair(a, b, score, ts, pr, ds, reason, decision, rationale)
            if decision == "auto_reject":
                if rationale.endswith("different entities"):
                    sequence_audit.append(pair)
                continue
            pairs.append(pair)

    pairs.sort(key=lambda p: (-p.score, p.a.raw))
    sequence_audit.sort(key=lambda p: (-p.score, p.a.raw))
    return pairs, sequence_audit


def build_clusters(pairs: list[Pair]) -> list[list[str]]:
    """Union-find over auto_merge pairs only -- review pairs must not merge."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for p in pairs:
        if p.decision == "auto_merge":
            union(p.a.raw, p.b.raw)

    groups: dict[str, list[str]] = defaultdict(list)
    for name in parent:
        groups[find(name)].append(name)
    return [sorted(v) for v in groups.values() if len(v) > 1]


SUFFIX_HINT_RE = re.compile(
    r"[,.]?\s+(inc|llc|ltd|corp|co|plc|pbc|lp|llp|limited|corporation|"
    r"incorporated|gmbh|ab|sa|nv|bv|ag|se)\.?$",
    re.I,
)


def pick_canonical(names: list[str], recs: dict[str, NameRecord]) -> str:
    """Choose the name an analyst should see in a report.

    Plain "longest wins" is wrong here: the longest variant is usually the one
    carrying a fund-specific annotation, so it picks
    "Airbnb (Proprietary Fund BI)" over "Airbnb, Inc.". Preference order:

      1. no parenthetical   -- annotations like "(from Flipagram)",
                               "(Proprietary Fund BI)", "(f/k/a ...)" are one
                               GP's bookkeeping, not the company's name
      2. has a legal suffix -- "Airbnb, Inc." over bare "Airbnb"
      3. longer             -- retains fuller spelling
      4. more rows          -- the spelling most of the book already uses
    """
    def key(n: str) -> tuple:
        return (
            0 if parentheticals(n) else 1,
            1 if SUFFIX_HINT_RE.search(n) else 0,
            len(n),
            recs[n].rows,
            n,
        )

    return max(names, key=key)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, required=True, help="holdings CSV")
    ap.add_argument("--out", type=Path, default=Path("./output"))
    ap.add_argument("--all-rows", action="store_true",
                    help="skip Phase 1's is_resolvable filter; include balance "
                         "sheet lines, accounting entries, subtotals and "
                         "aggregate buckets. For diagnostics only.")
    ap.add_argument("--max-block", type=int, default=400)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    print(f"Reading {args.data} ...")
    df = pd.read_csv(args.data, low_memory=False, usecols=list(COLS.values()))
    print(f"  {len(df):,} rows")

    recs = build_records(df, resolvable_only=not args.all_rows)
    print(f"  {len(recs):,} distinct company names"
          f"{'' if args.all_rows else ' (is_resolvable, matches Phase 1 scope)'}")

    recs, non_companies = split_non_companies(recs)
    if non_companies:
        rows = sum(r.rows for r, _ in non_companies)
        print(f"  {len(non_companies)} name(s) held out as non-company "
              f"({rows:,} rows) -> data_quality_non_company.csv")
        pd.DataFrame([
            {
                "source_asset": r.raw,
                "reason": why,
                "rows": r.rows,
                "n_funds": len(r.funds),
                "funds": "|".join(sorted(r.funds)),
                "managers": "|".join(sorted(r.managers)),
            }
            for r, why in sorted(non_companies, key=lambda t: -t[0].rows)
        ]).to_csv(args.out / "data_quality_non_company.csv", index=False)

    print("Blocking and scoring ...")
    pairs, sequence_audit = resolve(recs, args.max_block)

    auto = [p for p in pairs if p.decision == "auto_merge"]
    rev = [p for p in pairs if p.decision == "review"]
    print(f"  {len(auto):,} auto-merge, {len(rev):,} need review")
    if sequence_audit:
        n_fam = len({family_stem(p.a.norm) for p in sequence_audit})
        print(f"  {len(sequence_audit):,} sequence-conflict pair(s) auto-rejected "
              f"without review ({n_fam} families) -> sequence_conflicts_rejected.csv")
        pd.DataFrame([p.to_row() for p in sequence_audit]).to_csv(
            args.out / "sequence_conflicts_rejected.csv", index=False)

    clusters = build_clusters(pairs)
    print(f"  {len(clusters):,} clusters from auto-merge pairs")

    # --- pair-level outputs -------------------------------------------------
    pd.DataFrame([p.to_row() for p in pairs]).to_csv(
        args.out / "entity_pairs_all.csv", index=False)
    pd.DataFrame([p.to_row() for p in rev]).to_csv(
        args.out / "entity_review_queue.csv", index=False)

    # --- cluster-level output ----------------------------------------------
    crows = []
    for i, names in enumerate(clusters, 1):
        canon = pick_canonical(names, recs)
        funds: set[str] = set()
        mgrs: set[str] = set()
        aids: set[str] = set()
        rows = 0
        for n in names:
            r = recs[n]
            funds |= r.funds
            mgrs |= r.managers
            aids |= r.asset_ids
            rows += r.rows
        crows.append({
            "cluster_id": f"EC{i:04d}",
            "canonical_name": canon,
            "n_variants": len(names),
            "variants": " | ".join(names),
            "n_funds": len(funds),
            "n_managers": len(mgrs),
            "n_vendor_asset_ids": len(aids),
            "total_rows": rows,
            "funds": " | ".join(sorted(funds)),
            "vendor_asset_ids": " | ".join(sorted(aids)),
            # A cluster spanning >1 vendor ID is a vendor-side duplicate:
            # the vendor is carrying the same company under two IDs.
            "vendor_id_fragmentation": len(aids) > 1,
            "cross_fund": len(funds) > 1,
        })
    cdf = pd.DataFrame(crows).sort_values(
        ["n_funds", "n_variants"], ascending=False) if crows else pd.DataFrame()
    cdf.to_csv(args.out / "entity_clusters.csv", index=False)

    # --- proposed alias map (NOT promoted; review.py does that) -------------
    proposed = {}
    for names in clusters:
        canon = pick_canonical(names, recs)
        for n in names:
            if n != canon:
                proposed[normalize(n)] = canon
    (args.out / "proposed_aliases.json").write_text(
        json.dumps({"global": proposed, "by_fund": {}}, indent=2, ensure_ascii=False),
        encoding="utf-8")

    # --- summary ------------------------------------------------------------
    n_frag = int(cdf["vendor_id_fragmentation"].sum()) if len(cdf) else 0
    n_xfund = int(cdf["cross_fund"].sum()) if len(cdf) else 0
    summary = {
        "distinct_names_in": len(recs),
        "candidate_pairs": len(pairs),
        "auto_merge_pairs": len(auto),
        "review_pairs": len(rev),
        "clusters": len(clusters),
        "names_absorbed": sum(len(c) - 1 for c in clusters),
        "clusters_spanning_multiple_funds": n_xfund,
        "clusters_with_multiple_vendor_ids": n_frag,
        "distinct_names_after_merge": len(recs) - sum(len(c) - 1 for c in clusters),
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\nSummary")
    for k, v in summary.items():
        print(f"  {k:38s} {v:,}")
    print(f"\nWrote to {args.out}/:")
    optional = (["data_quality_non_company.csv"] if non_companies else []) + \
               (["sequence_conflicts_rejected.csv"] if sequence_audit else [])
    for f in ["entity_clusters.csv", "entity_review_queue.csv",
              "entity_pairs_all.csv", "proposed_aliases.json", "summary.json",
              *optional]:
        print(f"  {f}")
    print("\nNothing was promoted to entity_aliases.json. Run review.py next.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
