from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

PDF_MAGIC = b"%PDF-"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
ZIP_MAGIC = b"PK\x03\x04"

KIND_BY_EXTENSION = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".csv": "csv",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".zip": "zip",
    ".doc": "legacy_doc",
    ".xls": "legacy_xls",
}

MIME_BY_KIND = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "zip": "application/zip",
    "unknown": "application/octet-stream",
}


@dataclass
class Detection:
    kind: str
    mime: str
    extension: str
    warnings: list[str] = field(default_factory=list)


def _zip_kind(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
        if "word/document.xml" in names:
            return "docx"
        if "xl/workbook.xml" in names:
            return "xlsx"
    except zipfile.BadZipFile:
        return "zip"
    return "zip"


def detect(path: Path, filename: str | None = None) -> Detection:
    filename = filename or path.name
    extension = Path(filename).suffix.lower()
    head = path.read_bytes()[:8] if path.exists() else b""
    warnings: list[str] = []

    if head.startswith(PDF_MAGIC):
        kind = "pdf"
    elif head.startswith(PNG_MAGIC):
        kind = "png"
    elif head.startswith(JPEG_MAGIC):
        kind = "jpeg"
    elif head.startswith(ZIP_MAGIC):
        kind = _zip_kind(path)
    else:
        kind = KIND_BY_EXTENSION.get(extension, "unknown")

    by_extension = KIND_BY_EXTENSION.get(extension)
    if by_extension and kind != by_extension and not (kind == "zip" and by_extension in {"docx", "xlsx"}):
        warnings.append(f"extension_mismatch:{extension}->{kind}")
    if kind == "unknown":
        warnings.append("unknown_format")

    return Detection(kind=kind, mime=MIME_BY_KIND.get(kind, MIME_BY_KIND["unknown"]), extension=extension, warnings=warnings)


def classify_document(kind: str, text: str, filename: str) -> str:
    lowered = text.lower()
    name = filename.lower()
    if "акт" in lowered and "качеств" in lowered:
        return "quality_act"
    if name.startswith("qa-") or "акт качества" in lowered:
        return "quality_act"
    if "счёт" in lowered or "счет" in lowered or "invoice" in lowered or name.startswith("inv-"):
        return "invoice"
    if kind in {"png", "jpeg"}:
        return "scan"
    if "акт" in lowered:
        return "quality_act"
    return "unknown"


def priority_for(doc_type: str, text: str) -> str:
    lowered = text.lower()
    if doc_type == "quality_act" or "претенз" in lowered or "срочн" in lowered:
        return "high"
    return "normal"
