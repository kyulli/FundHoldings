"""Conservative character-level watermark filtering for native PDF text.

Removes only high-confidence watermark overlays (dedicated rotated fonts and
exact StartTNR…EndTNR tracking tokens). Fail-open: when fingerprints are weak,
original text is preserved and suspects are reported.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber

# Dedicated watermark font observed on Brown ILPA overlays.
_WATERMARK_FONT_MARKERS = ("gonotokurrent",)
_TNR_TOKEN_RE = re.compile(r"StartTNR\d+EndTNR", flags=re.I)
_ROTATION_THRESHOLD = 0.3
_LARGE_SIZE_THRESHOLD = 20.0


@dataclass
class WatermarkReport:
    page: int
    removed_char_count: int = 0
    signatures: list[str] = field(default_factory=list)
    reconstructed_watermarks: list[str] = field(default_factory=list)
    suspects: list[str] = field(default_factory=list)
    fail_open: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fontname(char: dict[str, Any]) -> str:
    return str(char.get("fontname") or "")


def _font_lower(char: dict[str, Any]) -> str:
    return _fontname(char).lower()


def _matrix(char: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
    raw = char.get("matrix") or (1, 0, 0, 1, 0, 0)
    try:
        return tuple(float(x) for x in raw[:6])  # type: ignore[return-value]
    except (TypeError, ValueError):
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _is_rotated(char: dict[str, Any]) -> bool:
    m = _matrix(char)
    return abs(m[1]) > _ROTATION_THRESHOLD or abs(m[2]) > _ROTATION_THRESHOLD


def _size(char: dict[str, Any]) -> float:
    try:
        return float(char.get("size") or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_dedicated_watermark_font(char: dict[str, Any]) -> bool:
    name = _font_lower(char)
    return any(marker in name for marker in _WATERMARK_FONT_MARKERS)


def is_watermark_char(char: dict[str, Any]) -> bool:
    """Strong fingerprint: dedicated watermark font.

    Rotation / large size are cross-checks only and never delete alone.
    """
    if not _is_dedicated_watermark_font(char):
        return False
    # Prefer rotated+large, but dedicated font alone is enough for this overlay family.
    return True


def _is_white(char: dict[str, Any]) -> bool:
    color = char.get("non_stroking_color")
    if color == 1 or color == 1.0:
        return True
    if isinstance(color, (list, tuple)) and len(color) >= 3:
        try:
            return all(abs(float(c) - 1.0) < 1e-6 for c in color[:3])
        except (TypeError, ValueError):
            return False
    return False


def _strip_tnr_tokens_from_chars(chars: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Remove exact StartTNR…EndTNR runs; keep neighboring white title text."""
    if not chars:
        return chars, 0, []

    # Build contiguous runs by reading order (top then x).
    ordered = sorted(
        enumerate(chars),
        key=lambda item: (round(float(item[1].get("top") or 0), 1), float(item[1].get("x0") or 0)),
    )
    keep_idx = {i for i, _ in ordered}
    removed = 0
    signatures: list[str] = []

    # Scan white characters for TNR token sequence.
    white_run: list[tuple[int, str]] = []
    for idx, char in ordered:
        if _is_white(char) and str(char.get("text") or ""):
            white_run.append((idx, str(char.get("text") or "")))
        else:
            if white_run:
                text = "".join(t for _, t in white_run)
                for match in _TNR_TOKEN_RE.finditer(text):
                    # Map match span back onto white_run char indices.
                    start, end = match.span()
                    cursor = 0
                    for char_idx, token in white_run:
                        token_len = len(token)
                        token_start = cursor
                        token_end = cursor + token_len
                        if token_end > start and token_start < end:
                            if char_idx in keep_idx:
                                keep_idx.remove(char_idx)
                                removed += 1
                        cursor = token_end
                    signatures.append("StartTNR")
            white_run = []

    if white_run:
        text = "".join(t for _, t in white_run)
        for match in _TNR_TOKEN_RE.finditer(text):
            start, end = match.span()
            cursor = 0
            for char_idx, token in white_run:
                token_len = len(token)
                token_start = cursor
                token_end = cursor + token_len
                if token_end > start and token_start < end:
                    if char_idx in keep_idx:
                        keep_idx.remove(char_idx)
                        removed += 1
                cursor = token_end
            signatures.append("StartTNR")

    kept = [chars[i] for i in range(len(chars)) if i in keep_idx]
    return kept, removed, signatures


def _strip_overlapping_spaces(chars: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Remove space glyphs that overlap a previous character (watermark intercalation).

    These spaces often sit inside digit runs and make amounts look like ``6 08,088,446``.
    Only overlapping / nested spaces are removed; normal word spaces are kept.
    """
    if not chars:
        return chars, 0

    ordered = sorted(
        enumerate(chars),
        key=lambda item: (round(float(item[1].get("top") or 0), 1), float(item[1].get("x0") or 0)),
    )
    keep_idx = {i for i, _ in ordered}
    removed = 0
    prev_idx: int | None = None
    prev_char: dict[str, Any] | None = None
    for idx, char in ordered:
        text = str(char.get("text") or "")
        if text == " " and prev_char is not None:
            try:
                gap = float(char.get("x0") or 0) - float(prev_char.get("x1") or 0)
            except (TypeError, ValueError):
                gap = 0.0
            prev_text = str(prev_char.get("text") or "")
            # Overlapping the previous glyph (common watermark artifact inside amounts).
            if gap < -0.5 and (prev_text.isdigit() or prev_text in {",", ".", "$", ")"}):
                if idx in keep_idx:
                    keep_idx.remove(idx)
                    removed += 1
                continue
        if idx in keep_idx:
            prev_idx = idx
            prev_char = char
    kept = [chars[i] for i in range(len(chars)) if i in keep_idx]
    return kept, removed


def filter_watermark_chars(chars: list[dict[str, Any]], *, page: int = 0) -> tuple[list[dict[str, Any]], WatermarkReport]:
    """Filter watermark chars with fail-open semantics."""
    report = WatermarkReport(page=page)
    if not chars:
        return chars, report

    dedicated = [c for c in chars if is_watermark_char(c)]
    suspects: list[str] = []

    # Weak fingerprints: large rotated text without dedicated font — report only.
    for c in chars:
        if is_watermark_char(c):
            continue
        if _is_rotated(c) and _size(c) >= _LARGE_SIZE_THRESHOLD and "cidfont" not in _font_lower(c):
            suspects.append(f"large_rotated:{_fontname(c)}:{_size(c):.1f}")

    if dedicated:
        font_sig = sorted({_fontname(c) for c in dedicated})
        report.signatures.extend(font_sig)
        # Rebuild watermark string in diagonal reading order.
        ordered = sorted(
            dedicated,
            key=lambda c: (float(c.get("x0") or 0) + float(c.get("top") or 0), float(c.get("x0") or 0)),
        )
        reconstructed = "".join(str(c.get("text") or "") for c in ordered)
        if reconstructed.strip():
            report.reconstructed_watermarks.append(reconstructed.strip())
        kept = [c for c in chars if not is_watermark_char(c)]
        report.removed_char_count += len(chars) - len(kept)
        chars = kept
    elif suspects:
        # Fail-open: do not delete on weak evidence alone.
        report.fail_open = True
        report.suspects.extend(sorted(set(suspects)))

    chars, tnr_removed, tnr_sigs = _strip_tnr_tokens_from_chars(chars)
    report.removed_char_count += tnr_removed
    for sig in tnr_sigs:
        if sig not in report.signatures:
            report.signatures.append(sig)

    chars, space_removed = _strip_overlapping_spaces(chars)
    if space_removed:
        report.removed_char_count += space_removed
        if "overlapping_space" not in report.signatures:
            report.signatures.append("overlapping_space")

    if suspects and not dedicated:
        report.suspects = sorted(set(suspects))

    return chars, report


def extract_clean_page_text(page: Any) -> tuple[str, list[dict[str, Any]], WatermarkReport]:
    """Filter page chars then rebuild text/words via pdfplumber FilteredPage."""
    page_num = int(getattr(page, "page_number", 0) or 0)
    raw_chars = list(page.chars or [])
    kept, report = filter_watermark_chars(raw_chars, page=page_num)

    if report.removed_char_count == 0 and not report.fail_open:
        text = page.extract_text() or ""
        words = page.extract_words() or []
        return text, words, report

    # Rebuild via filter so extract_text/extract_words stay consistent.
    kept_keys = {
        (
            str(c.get("text") or ""),
            round(float(c.get("x0") or 0), 3),
            round(float(c.get("top") or 0), 3),
            round(float(c.get("size") or 0), 3),
            str(c.get("fontname") or ""),
        )
        for c in kept
    }

    def _keep(obj: dict[str, Any]) -> bool:
        if obj.get("object_type") and obj.get("object_type") != "char":
            return True
        if "fontname" not in obj and "text" not in obj:
            return True
        key = (
            str(obj.get("text") or ""),
            round(float(obj.get("x0") or 0), 3),
            round(float(obj.get("top") or 0), 3),
            round(float(obj.get("size") or 0), 3),
            str(obj.get("fontname") or ""),
        )
        return key in kept_keys

    filtered = page.filter(_keep)
    text = filtered.extract_text() or ""
    words = filtered.extract_words() or []
    # Also strip any residual TNR token that survived as contiguous text.
    if _TNR_TOKEN_RE.search(text):
        text = _TNR_TOKEN_RE.sub("", text)
    return text, words, report


def read_clean_page_text(page: Any) -> tuple[str, list[dict[str, Any]], WatermarkReport]:
    """Canonical page-text reader for all native PDF text consumers.

    Prefer this over ``page.extract_text()`` so routing, layout, SOA, text
    fallback, and template-generator share one watermark-filtered text layer.
    """
    return extract_clean_page_text(page)


def read_clean_page_lines(page: Any) -> tuple[list[str], WatermarkReport]:
    """Return non-empty cleaned text lines plus the watermark report."""
    text, _, report = read_clean_page_text(page)
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines, report


def watermark_report_summary(reports: list[WatermarkReport] | list[dict[str, Any]]) -> dict[str, Any]:
    """Compact multi-page watermark summary for extraction evidence."""
    pages: list[dict[str, Any]] = []
    signatures: set[str] = set()
    removed = 0
    fail_open = False
    for item in reports:
        data = item.to_dict() if isinstance(item, WatermarkReport) else dict(item)
        pages.append(data)
        signatures.update(data.get("signatures") or [])
        removed += int(data.get("removed_char_count") or 0)
        fail_open = fail_open or bool(data.get("fail_open"))
    return {
        "pages": pages,
        "signatures": sorted(signatures),
        "removed_char_count": removed,
        "fail_open": fail_open,
    }


def normalize_statement_label(label: str) -> str:
    """Non-destructive statement-label normalize after watermark char filtering.

    Does not delete by broad heuristics (all single chars / large size / confidential).
    Collapses whitespace and strips short trailing residues commonly left by diagonal
    overlay extraction inside accounting labels (e.g. ``... payables B``, ``... affiliate r 6``).
    """
    text = re.sub(r"\s+", " ", str(label or "")).strip()
    if not text:
        return ""
    # letter + digit crumbs: "r 6", "o 2", "R 5", "s 8", "e 1", "A 0"
    text = re.sub(r"\s+[A-Za-z]\s+[-–—0-9]\s*$", "", text)
    # trailing isolated letter or punctuation residue
    text = re.sub(r"(?<=[A-Za-z)])\s+[A-Za-z@:]\s*$", "", text)
    return text.strip(" :-")


def detect_pdf_watermarks(pdf_path: Path | str, *, max_pages: int = 25) -> dict[str, Any]:
    """Diagnostic helper: per-page watermark reports without mutating the PDF."""
    pdf_path = Path(pdf_path)
    reports: list[dict[str, Any]] = []
    signatures: set[str] = set()
    reconstructed: list[str] = []
    with pdfplumber.open(str(pdf_path)) as doc:
        for page in doc.pages[:max_pages]:
            _, _, report = extract_clean_page_text(page)
            reports.append(report.to_dict())
            signatures.update(report.signatures)
            reconstructed.extend(report.reconstructed_watermarks)
    return {
        "pdf_path": str(pdf_path),
        "pages": reports,
        "signatures": sorted(signatures),
        "reconstructed_watermarks": reconstructed,
        "removed_char_count": sum(r.get("removed_char_count") or 0 for r in reports),
    }
