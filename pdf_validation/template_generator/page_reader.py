"""Read schedule pages out of a PDF as plain text lines.

Both the generator and the deterministic replay engine read the PDF through
this one function, so what the LLM was shown at generation time is exactly what
replay will see later. If these diverged, an approved template could quietly
stop matching.

Note this is line-oriented text, not table cells. That is deliberate: the
failure mode diagnosed in these statements is that geometric column-slicing
(Camelot) shatters wrapped company names and glues adjacent numbers together.
pdfplumber's text layer keeps a visual row on one line, which is what the
hand-written fallbacks in src/pdf_validation/text_fallback.py already rely on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber


@dataclass
class PageLines:
    page: int
    lines: list[str]


def read_pages(pdf_path: Path | str, pages: list[int]) -> list[PageLines]:
    """Return normalised text lines for the given 1-based page numbers."""
    out: list[PageLines] = []
    with pdfplumber.open(pdf_path) as doc:
        for page_num in pages:
            if page_num < 1 or page_num > len(doc.pages):
                continue
            raw = doc.pages[page_num - 1].extract_text() or ""
            lines = []
            for line in raw.splitlines():
                text = re.sub(r"[ \t]+", " ", line).strip()
                if text:
                    lines.append(text)
            out.append(PageLines(page=page_num, lines=lines))
    return out


def page_count(pdf_path: Path | str) -> int:
    with pdfplumber.open(pdf_path) as doc:
        return len(doc.pages)


def find_schedule_pages(
    pdf_path: Path | str,
    *,
    titles: tuple[str, ...] = (
        "schedule of investments",
        "schedule of portfolio investments",
        "combined schedule of portfolio investments",
        "condensed schedule of investments",
        "portfolio of investments",
        "statement of portfolio investments",
    ),
) -> list[int]:
    """Best-effort guess at which pages hold the holdings schedule.

    Used only to seed the generator when the router did not already supply
    schedule_pages. A wrong guess here is cheap: the reviewer sees the page
    numbers in the draft template and the captured evidence will be obviously
    empty or wrong.
    """
    hits: list[int] = []
    with pdfplumber.open(pdf_path) as doc:
        for idx, page in enumerate(doc.pages, start=1):
            text = (page.extract_text() or "").lower()
            if not text:
                continue
            head = "\n".join(text.splitlines()[:12])
            if any(t in head for t in titles):
                hits.append(idx)
                continue
            # Continuation pages usually carry no title but do carry the
            # money columns; treat a page as a continuation of a hit page.
            if hits and hits[-1] == idx - 1 and re.search(r"[\d,]{7,}", text):
                hits.append(idx)
    return hits


def sample_for_prompt(
    pdf_path: Path | str,
    pages: list[int],
    *,
    max_lines_per_page: int = 60,
    max_total_lines: int = 220,
) -> str:
    """Render the schedule pages as a compact, line-numbered block for the LLM.

    Truncated on purpose: a full audited statement can be thousands of lines,
    and the generator only needs enough of the schedule to infer its shape.
    """
    chunks: list[str] = []
    total = 0
    for pl in read_pages(pdf_path, pages):
        if total >= max_total_lines:
            break
        chunks.append(f"--- page {pl.page} ---")
        for line in pl.lines[:max_lines_per_page]:
            if total >= max_total_lines:
                break
            chunks.append(line)
            total += 1
    return "\n".join(chunks)
