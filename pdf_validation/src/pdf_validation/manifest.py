"""Run manifest helpers."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pdf_validation import OUTPUT_SCHEMA_VERSION, PARSER_VERSION


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def package_versions() -> dict[str, str]:
    versions: dict[str, str] = {"python": sys.version.split()[0]}
    for name in ("camelot", "pdfplumber", "pandas", "openpyxl", "pytest"):
        try:
            module = __import__(name if name != "camelot" else "camelot")
            versions[name] = getattr(module, "__version__", "unknown")
        except Exception as exc:  # noqa: BLE001 - record import failure explicitly
            versions[name] = f"unavailable:{exc.__class__.__name__}"
    return versions


def build_run_manifest(
    *,
    pdf_path: Path,
    config_path: Path,
    config: dict[str, Any],
    cli_args: dict[str, Any],
    started_at: datetime,
    finished_at: datetime,
    repo_root: Path,
) -> dict[str, Any]:
    return {
        "run_id": finished_at.strftime("%Y%m%dT%H%M%SZ"),
        "started_at_utc": started_at.astimezone(timezone.utc).isoformat(),
        "finished_at_utc": finished_at.astimezone(timezone.utc).isoformat(),
        "duration_seconds": (finished_at - started_at).total_seconds(),
        "pdf_path": str(pdf_path),
        "pdf_sha256": file_sha256(pdf_path),
        "config_path": str(config_path),
        "config_sha256": file_sha256(config_path),
        "template_id": config.get("template_id"),
        "template_version": config.get("template_version"),
        "parser_version": config.get("parser_version", PARSER_VERSION),
        "output_schema_version": config.get("output_schema_version", OUTPUT_SCHEMA_VERSION),
        "git_commit": git_commit(repo_root),
        "package_versions": package_versions(),
        "cli_args": cli_args,
        "config_snapshot": json.loads(json.dumps(config)),
    }
