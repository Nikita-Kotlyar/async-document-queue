from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps

from app.config import get_settings
from app.services.errors import CorruptDocumentError, OcrUnavailableError


@dataclass
class OcrResult:
    text: str
    confidence: float
    engine: str


def _configure() -> None:
    settings = get_settings()
    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd


def _ocr_image(image: Image.Image) -> OcrResult:
    _configure()
    settings = get_settings()
    prepared = ImageOps.grayscale(image)
    prepared = ImageOps.autocontrast(prepared)
    try:
        data = pytesseract.image_to_data(
            prepared,
            lang=settings.ocr_lang,
            output_type=pytesseract.Output.DICT,
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrUnavailableError("tesseract_not_found") from exc
    except pytesseract.TesseractError as exc:
        raise OcrUnavailableError(f"tesseract_error:{exc}") from exc

    words: list[str] = []
    confidences: list[float] = []
    for text, conf in zip(data.get("text", []), data.get("conf", [])):
        text = (text or "").strip()
        if not text:
            continue
        words.append(text)
        try:
            value = float(conf)
        except (TypeError, ValueError):
            continue
        if value >= 0:
            confidences.append(value)
    confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0.0
    return OcrResult(text="\n".join(words), confidence=confidence, engine="tesseract")


def ocr_image_file(path: Path) -> OcrResult:
    try:
        with Image.open(path) as image:
            return _ocr_image(image)
    except OcrUnavailableError:
        raise
    except OSError as exc:
        raise OcrUnavailableError(f"image_open_failed:{exc}") from exc


def ocr_pdf(path: Path, max_pages: int = 10, dpi: int = 200) -> OcrResult:
    _configure()
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - dependency is required
        raise OcrUnavailableError("pymupdf_not_installed") from exc

    try:
        document = pymupdf.open(path)
    except Exception as exc:
        raise CorruptDocumentError(f"pdf_open_failed:{exc}") from exc

    texts: list[str] = []
    confidences: list[float] = []
    try:
        for page_number in range(min(len(document), max_pages)):
            try:
                page = document.load_page(page_number)
                pixmap = page.get_pixmap(dpi=dpi)
                image = Image.open(io.BytesIO(pixmap.tobytes("png")))
                result = _ocr_image(image)
            except CorruptDocumentError:
                raise
            except Exception as exc:
                raise CorruptDocumentError(f"pdf_page_failed:{exc}") from exc
            if result.text.strip():
                texts.append(result.text)
                confidences.append(result.confidence)
    finally:
        document.close()

    confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0.0
    return OcrResult(text="\n".join(texts), confidence=confidence, engine="tesseract+pdf")
