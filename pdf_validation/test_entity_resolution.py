#!/usr/bin/env python3
"""Quick test of entity resolution workflow."""

from pathlib import Path
from src.pdf_validation.entity_resolver import EntityResolver

# Test: collect candidates
root = Path(__file__).parent / "src" / "pdf_validation"
resolver = EntityResolver(root)

print("1. Collecting candidates...")
candidates = resolver.collect_candidates(min_similarity=0.85)
print(resolver.summary())

print("\n2. Ranking by similarity...")
ranked = resolver.rank_candidates(0.85)
print(f"   Found {len(ranked)} candidates >= 0.85 similarity")

# Show some examples
print("\n3. Example candidates:")
for cand in ranked[:10]:
    print(f"   {cand.pdf_company_name:40s} → {cand.vendor_source_asset_candidate:40s} ({cand.similarity:.2%})")

# Test batch approval (non-interactive)
print("\n4. Batch auto-approve (threshold 0.90)...")
high_conf = resolver.rank_candidates(0.90)[:5]  # Just first 5 for test
print(f"   Auto-approving {len(high_conf)} high-confidence candidates")

results = resolver.confirm_batch(high_conf, interactive=False)
print(f"   Generated {len(results)} ResolutionResult records")

# Test alias export (don't actually write, just preview)
print("\n5. Preview aliases structure:")
aliases = resolver.load_aliases()
print(f"   Global mappings: {len(aliases['global'])}")
print(f"   By-fund overrides: {len(aliases['by_fund'])} funds")

print("\nTest complete. To run interactively:")
print("  cd FundHoldings/pdf_validation")
print("  python -m src.pdf_validation.entity_resolution_cli collect --threshold 0.85")
print("  python -m src.pdf_validation.entity_resolution_cli review --threshold 0.85 --interactive")
print("  python -m src.pdf_validation.entity_resolution_cli export --threshold 0.90")
