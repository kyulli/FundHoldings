"""Lookup versioned vendor-mapping configs by fund id (no fund names in call sites)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _pkg_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_vendor_mapping_registry(path: Path | None = None) -> dict[str, Any]:
    path = path or (_pkg_root() / "configs" / "vendor_mapping_registry.json")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resolve_mapping_path_for_fund(
    fund_id: str,
    *,
    registry: dict[str, Any] | None = None,
    pkg_root: Path | None = None,
) -> Path | None:
    """Return absolute path to approved mapping JSON for fund_id, or None."""
    pkg_root = pkg_root or _pkg_root()
    registry = registry or load_vendor_mapping_registry()
    entry = (registry.get("by_fund_id") or {}).get(str(fund_id))
    if not entry:
        return None
    rel = entry.get("mapping_config")
    if not rel:
        return None
    path = Path(rel)
    if not path.is_absolute():
        path = pkg_root / rel
    return path if path.exists() else None


def load_mapping_for_fund(
    fund_id: str,
    *,
    registry: dict[str, Any] | None = None,
    pkg_root: Path | None = None,
    allow_default_native_template: bool = False,
) -> tuple[dict[str, Any] | None, Path | None, dict[str, Any]]:
    """
    Load fund mapping.

    Returns (mapping_dict, path, registry_entry).
    If no fund entry and allow_default_native_template, clones the default native template
    with vendor_fund_id rewritten (entity_mappings cleared).
    """
    pkg_root = pkg_root or _pkg_root()
    registry = registry or load_vendor_mapping_registry()
    entry = dict((registry.get("by_fund_id") or {}).get(str(fund_id)) or {})
    path = resolve_mapping_path_for_fund(fund_id, registry=registry, pkg_root=pkg_root)
    if path is not None:
        mapping = json.loads(path.read_text(encoding="utf-8"))
        return mapping, path, entry

    if not allow_default_native_template:
        return None, None, entry

    rel = registry.get("default_native_mapping_template")
    if not rel:
        return None, None, entry
    template_path = pkg_root / rel if not Path(rel).is_absolute() else Path(rel)
    if not template_path.exists():
        return None, None, entry
    mapping = json.loads(template_path.read_text(encoding="utf-8"))
    mapping["comparability"]["fund_identity"]["vendor_fund_id"] = fund_id
    mapping["entity_mappings"] = []
    return mapping, template_path, entry
