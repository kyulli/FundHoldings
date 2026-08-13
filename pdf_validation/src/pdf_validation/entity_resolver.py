"""Entity resolution — fuzzy matching and human approval for company name aliases.

This module implements Phase 3 entity resolution:
  - Collect candidate pairs from all entity_candidates.jsonl files
  - Filter by similarity threshold
  - Interactive human confirmation (CLI)
  - Update entity_aliases.json with approved mappings
  - Integration hook for cleaning pipeline

Workflow:
  resolver = EntityResolver(pdf_validation_root)
  candidates = resolver.collect_candidates()  # All unconfirmed fuzzy matches

  # CLI: review and approve
  python -m entity_resolver approve --threshold 0.8 --out aliases.json

  # Or programmatic:
  approved = resolver.confirm_batch(candidates, interactive=True)
  resolver.save_aliases(approved)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


@dataclass
class EntityCandidate:
    """A single (pdf_name → vendor_name) mapping candidate."""

    pdf_company_name: str
    vendor_source_asset_candidate: str
    similarity: float  # 0.0 to 1.0, from rapidfuzz
    fund_id: str | None = None
    status: Literal["candidate_only", "confirmed_explicit", "user_confirmed"] = "candidate_only"
    confirmed: bool = False
    source_file: str | None = None  # Which entity_candidates.jsonl it came from
    notes: str | None = None

    def score_key(self) -> tuple[float, str]:
        """Sort key: (similarity desc, pdf_name asc)."""
        return (-self.similarity, self.pdf_company_name)


@dataclass
class ResolutionResult:
    """Result of resolving a candidate pair."""

    pdf_name: str
    vendor_name: str
    similarity: float
    fund_id: str | None = None
    approved: bool = True
    reviewer_notes: str = ""
    confirmation_method: Literal["fuzzy", "exact", "user_manual"] = "fuzzy"


class EntityResolver:
    """Collect, rank, and approve entity mappings across all funds."""

    def __init__(self, pdf_validation_root: Path):
        self.root = Path(pdf_validation_root)
        self.candidates: list[EntityCandidate] = []
        self.approved: list[ResolutionResult] = []

    def collect_candidates(self, min_similarity: float = 0.0) -> list[EntityCandidate]:
        """Collect all entity_candidates.jsonl files and deduplicate.

        Args:
            min_similarity: Only include candidates with similarity >= this threshold

        Returns:
            Deduplicated list sorted by similarity (high to low)
        """
        candidate_files = list(self.root.glob("**/entity_candidates.jsonl"))
        print(f"Found {len(candidate_files)} entity_candidates.jsonl files")

        seen: dict[tuple[str, str], EntityCandidate] = {}

        for fpath in candidate_files:
            fund_id = self._extract_fund_id(fpath)
            try:
                with fpath.open(encoding="utf-8") as f:
                    for line_no, line in enumerate(f, 1):
                        if not line.strip():
                            continue
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError as e:
                            print(f"  WARNING {fpath}:{line_no} JSON error: {e}")
                            continue

                        # Only collect unconfirmed fuzzy candidates
                        if item.get("status") != "candidate_only":
                            continue
                        if item.get("confirmed"):
                            continue

                        pdf_name = item.get("pdf_company_name")
                        vendor_name = item.get("vendor_source_asset_candidate")
                        similarity = float(item.get("similarity", 0.0))

                        if not pdf_name or not vendor_name:
                            continue
                        if similarity < min_similarity:
                            continue

                        # Deduplicate by (pdf_name, vendor_name) across funds
                        # Keep the highest similarity
                        key = (pdf_name, vendor_name)
                        if key in seen:
                            if similarity > seen[key].similarity:
                                seen[key].similarity = similarity
                        else:
                            seen[key] = EntityCandidate(
                                pdf_company_name=pdf_name,
                                vendor_source_asset_candidate=vendor_name,
                                similarity=similarity,
                                fund_id=fund_id,
                                source_file=str(fpath),
                                notes=item.get("note"),
                            )
            except Exception as e:
                print(f"  ERROR reading {fpath}: {e}")

        self.candidates = sorted(seen.values(), key=EntityCandidate.score_key)
        print(f"Collected {len(self.candidates)} unique candidates (after dedup)")
        return self.candidates

    def _extract_fund_id(self, fpath: Path) -> str | None:
        """Extract fund ID from path like A103ce5_..._Castanea_.../entity_candidates.jsonl."""
        for part in fpath.parts[::-1]:  # Walk up from filename
            if part.startswith("A") and len(part) == 7:
                return part
            # Also try underscore-delimited prefix
            if part.startswith("A") and "_" in part:
                fund_id = part.split("_")[0]
                if len(fund_id) == 7:
                    return fund_id
        return None

    def rank_candidates(self, similarity_threshold: float = 0.85) -> list[EntityCandidate]:
        """Filter candidates by similarity threshold and return ranked list.

        Args:
            similarity_threshold: Minimum similarity to include (0.0 to 1.0)

        Returns:
            Candidates sorted by similarity descending
        """
        ranked = [c for c in self.candidates if c.similarity >= similarity_threshold]
        print(f"Ranked {len(ranked)} candidates above threshold {similarity_threshold}")
        return sorted(ranked, key=EntityCandidate.score_key)

    def confirm_batch(
        self,
        candidates: list[EntityCandidate] | None = None,
        interactive: bool = False,
    ) -> list[ResolutionResult]:
        """Confirm a batch of candidates (interactive or batch mode).

        Args:
            candidates: Which candidates to confirm. If None, use self.candidates
            interactive: If True, prompt user for each candidate

        Returns:
            List of approved ResolutionResult records
        """
        if candidates is None:
            candidates = self.candidates

        results = []

        for i, cand in enumerate(candidates, 1):
            if interactive:
                print(f"\n[{i}/{len(candidates)}] {cand.pdf_company_name} → {cand.vendor_source_asset_candidate}")
                print(f"    Similarity: {cand.similarity:.1%}")
                if cand.fund_id:
                    print(f"    Fund: {cand.fund_id}")

                while True:
                    resp = input("  [A]ccept / [S]kip / [R]eject? ").strip().lower()
                    if resp in ("a", "s", "r"):
                        break
                    print("  Please enter A, S, or R")

                if resp == "s":
                    continue

                approved = resp == "a"
                notes = ""
                if approved:
                    notes = input("  Notes (optional)? ").strip()
            else:
                # Batch mode: auto-approve anything above threshold
                approved = cand.similarity >= 0.85
                notes = ""

            if approved or not interactive:  # Record even skipped in non-interactive
                results.append(ResolutionResult(
                    pdf_name=cand.pdf_company_name,
                    vendor_name=cand.vendor_source_asset_candidate,
                    similarity=cand.similarity,
                    fund_id=cand.fund_id,
                    approved=approved if interactive else True,
                    reviewer_notes=notes,
                    confirmation_method="user_manual" if interactive else "fuzzy",
                ))

        self.approved = results
        return results

    def load_aliases(self, path: Path | None = None) -> dict[str, Any]:
        """Load current entity_aliases.json."""
        if path is None:
            path = self.root.parent / "configs" / "entity_aliases.json"
        if not path.exists():
            return {"global": {}, "by_fund": {}}
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    def save_aliases(
        self,
        results: list[ResolutionResult] | None = None,
        path: Path | None = None,
        mode: Literal["append", "replace"] = "append",
    ) -> None:
        """Save approved mappings back to entity_aliases.json.

        Args:
            results: ResolutionResult records to save. If None, use self.approved
            path: Where to save. Defaults to configs/entity_aliases.json
            mode: "append" to merge with existing; "replace" to overwrite
        """
        if results is None:
            results = self.approved
        if path is None:
            path = self.root.parent / "configs" / "entity_aliases.json"

        approved_only = [r for r in results if r.approved]

        # Load existing
        aliases = self.load_aliases(path) if mode == "append" else {"global": {}, "by_fund": {}}

        # Normalize and add new mappings
        for r in approved_only:
            norm_pdf_name = r.pdf_name.lower().strip()

            # Decide: fund-specific or global?
            # If it appears in only one fund, use fund-specific
            # Otherwise, global
            if r.fund_id:
                if r.fund_id not in aliases["by_fund"]:
                    aliases["by_fund"][r.fund_id] = {}
                aliases["by_fund"][r.fund_id][norm_pdf_name] = r.vendor_name
            else:
                aliases["global"][norm_pdf_name] = r.vendor_name

        # Write back
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(aliases, f, indent=2, ensure_ascii=False)

        print(f"Saved {len(approved_only)} approved mappings to {path}")

    def summary(self) -> str:
        """Return human-readable summary of collected candidates."""
        lines = [
            f"Entity Resolution Summary",
            f"=" * 50,
            f"Total candidates collected: {len(self.candidates)}",
        ]

        # Group by similarity range
        ranges = {
            "perfect (1.0)": [c for c in self.candidates if c.similarity == 1.0],
            "high (0.95–0.99)": [c for c in self.candidates if 0.95 <= c.similarity < 1.0],
            "good (0.85–0.94)": [c for c in self.candidates if 0.85 <= c.similarity < 0.95],
            "moderate (0.75–0.84)": [c for c in self.candidates if 0.75 <= c.similarity < 0.85],
            "low (<0.75)": [c for c in self.candidates if c.similarity < 0.75],
        }

        for label, group in ranges.items():
            if group:
                lines.append(f"  {label}: {len(group)} candidates")

        if self.approved:
            approved_count = sum(1 for r in self.approved if r.approved)
            lines.append(f"\nApproved: {approved_count} / {len(self.approved)} reviewed")

        return "\n".join(lines)
