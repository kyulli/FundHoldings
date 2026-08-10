"""Unified page text access for native and scanned PDFs.

OCR is an evidence source only. It does not invent business field semantics.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import pdfplumber
import pypdfium2 as pdfium

TextSource = Literal["native", "ocr", "mixed"]


@dataclass
class OcrWord:
    text: str
    conf: float
    left: float
    top: float
    width: float
    height: float
    page: int
    line_num: int | None = None
    block_num: int | None = None

    @property
    def bbox(self) -> list[float]:
        return [self.left, self.top, self.left + self.width, self.top + self.height]


@dataclass
class PageContent:
    page: int
    source: TextSource
    text: str
    words: list[OcrWord]
    native_char_count: int
    image_path: str | None = None
    engine: str | None = None
    engine_version: str | None = None
    dpi: int | None = None
    elapsed_s: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["words"] = [asdict(w) for w in self.words]
        return payload


def _native_page_stats(pdf_path: Path, max_pages: int = 25) -> list[dict[str, Any]]:
    stats: list[dict[str, Any]] = []
    with pdfplumber.open(str(pdf_path)) as doc:
        for idx, page in enumerate(doc.pages[:max_pages]):
            text = page.extract_text() or ""
            words = page.extract_words() or []
            stats.append(
                {
                    "page": idx + 1,
                    "char_count": len(text.strip()),
                    "word_count": len(words),
                    "text": text,
                    "words": words,
                }
            )
    return stats


def detect_pdf_text_source(pdf_path: Path | str, *, max_pages: int = 25, min_chars: int = 40) -> dict[str, Any]:
    """Classify a PDF as native / scanned / mixed from embedded text coverage."""
    pdf_path = Path(pdf_path)
    stats = _native_page_stats(pdf_path, max_pages=max_pages)
    page_flags = []
    for row in stats:
        is_native = row["char_count"] >= min_chars
        page_flags.append({"page": row["page"], "char_count": row["char_count"], "is_native": is_native})
    native_pages = sum(1 for p in page_flags if p["is_native"])
    scanned_pages = len(page_flags) - native_pages
    if native_pages == 0:
        source: TextSource = "scanned"
    elif scanned_pages == 0:
        source = "native"
    else:
        source = "mixed"
    return {
        "pdf_path": str(pdf_path),
        "source": source,
        "native_pages": native_pages,
        "scanned_pages": scanned_pages,
        "pages_inspected": len(page_flags),
        "page_flags": page_flags,
    }


def render_page_png(pdf_path: Path | str, page_number: int, out_path: Path, *, dpi: int = 300) -> Path:
    """Render one 1-indexed PDF page to PNG."""
    pdf_path = Path(pdf_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf_path))
    if page_number < 1 or page_number > len(doc):
        raise ValueError(f"page {page_number} out of range for {pdf_path}")
    page = doc[page_number - 1]
    scale = dpi / 72.0
    pil = page.render(scale=scale).to_pil()
    pil.save(out_path)
    return out_path


def ocr_png_tesseract(image_path: Path | str, *, page_number: int, psm: int = 6) -> dict[str, Any]:
    """OCR a rendered page with Tesseract and return text + word boxes/confidence."""
    import time

    import pytesseract
    from PIL import Image

    image_path = Path(image_path)
    img = Image.open(image_path)
    t0 = time.time()
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, config=f"--psm {psm}")
    text = pytesseract.image_to_string(img, config=f"--psm {psm}")
    elapsed = time.time() - t0
    words: list[OcrWord] = []
    for i, raw in enumerate(data["text"]):
        txt = (raw or "").strip()
        if not txt:
            continue
        conf = float(data["conf"][i])
        if conf < 0:
            continue
        words.append(
            OcrWord(
                text=txt,
                conf=conf,
                left=float(data["left"][i]),
                top=float(data["top"][i]),
                width=float(data["width"][i]),
                height=float(data["height"][i]),
                page=page_number,
                line_num=int(data["line_num"][i]),
                block_num=int(data["block_num"][i]),
            )
        )
    try:
        engine_version = str(pytesseract.get_tesseract_version())
    except Exception:  # noqa: BLE001
        engine_version = "unknown"
    return {
        "engine": "tesseract",
        "engine_version": engine_version,
        "elapsed_s": elapsed,
        "text": text,
        "words": words,
        "image_size": list(img.size),
    }


def get_page_content(
    pdf_path: Path | str,
    page_number: int,
    *,
    prefer: Literal["auto", "native", "ocr"] = "auto",
    render_dir: Path | None = None,
    dpi: int = 300,
    min_native_chars: int = 40,
) -> PageContent:
    """Return page text from native layer or OCR."""
    pdf_path = Path(pdf_path)
    native_stats = _native_page_stats(pdf_path, max_pages=max(page_number, 1))
    native = next((r for r in native_stats if r["page"] == page_number), None)
    native_chars = int(native["char_count"]) if native else 0
    use_ocr = prefer == "ocr" or (prefer == "auto" and native_chars < min_native_chars)

    if not use_ocr and native is not None:
        words = [
            OcrWord(
                text=str(w.get("text") or ""),
                conf=100.0,
                left=float(w["x0"]),
                top=float(w["top"]),
                width=float(w["x1"]) - float(w["x0"]),
                height=float(w["bottom"]) - float(w["top"]),
                page=page_number,
            )
            for w in native.get("words") or []
            if str(w.get("text") or "").strip()
        ]
        return PageContent(
            page=page_number,
            source="native",
            text=native.get("text") or "",
            words=words,
            native_char_count=native_chars,
            engine="pdfplumber",
            engine_version=None,
            dpi=None,
        )

    if render_dir is None:
        digest = hashlib.sha256(str(pdf_path.resolve()).encode()).hexdigest()[:12]
        render_dir = Path(__file__).resolve().parents[2] / "outputs" / "ocr_pages" / digest
    image_path = Path(render_dir) / f"page-{page_number:02d}-{dpi}dpi.png"
    if not image_path.exists():
        render_page_png(pdf_path, page_number, image_path, dpi=dpi)
    ocr = ocr_png_tesseract(image_path, page_number=page_number)
    return PageContent(
        page=page_number,
        source="ocr",
        text=ocr["text"],
        words=ocr["words"],
        native_char_count=native_chars,
        image_path=str(image_path),
        engine=ocr["engine"],
        engine_version=ocr["engine_version"],
        dpi=dpi,
        elapsed_s=ocr["elapsed_s"],
    )


def get_document_pages(
    pdf_path: Path | str,
    pages: list[int],
    *,
    prefer: Literal["auto", "native", "ocr"] = "auto",
    render_dir: Path | None = None,
    dpi: int = 300,
) -> list[PageContent]:
    return [
        get_page_content(pdf_path, page, prefer=prefer, render_dir=render_dir, dpi=dpi) for page in pages
    ]
