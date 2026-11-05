from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.services.detection import classify_document, detect, priority_for
from app.services.errors import CorruptDocumentError, UnsupportedDocumentError
from app.services.ocr import ocr_image_file, ocr_pdf


@dataclass
class ExtractionResult:
    doc_type: str
    fields: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0
    engine: str = ""
    raw_text: str = ""
    priority: str = "normal"


def parse_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"[-+]?\d[\d\s.,]*", text)
    if not match:
        return None
    text = match.group(0).strip().replace(" ", "").replace(" ", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        if re.search(r",\d{3}$", text) and text.count(",") == 1:
            text = text.replace(",", "")
        else:
            text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def decimal_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value.quantize(Decimal('0.01'))}"


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except (ValueError, TypeError):
            continue
    try:
        from dateutil import parser as date_parser

        return date_parser.parse(value, dayfirst=True).date().isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").replace("\xad", "-")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _read_pdf(path: Path) -> tuple[str, float, str, list[str]]:
    warnings: list[str] = []
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        text = ""
    text = _clean_text(text)
    if len(text) >= 40:
        return text, 0.95, "pypdf", warnings
    warnings.append("pdf_without_text_layer")
    result = ocr_pdf(path)
    return result.text, result.confidence / 100, result.engine, warnings


def _read_docx(path: Path) -> tuple[str, float, str, list[list[str]], list[str]]:
    try:
        import docx

        document = docx.Document(str(path))
    except Exception as exc:
        raise CorruptDocumentError(f"docx_open_failed:{exc}") from exc
    paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    rows: list[list[str]] = []
    for table in document.tables:
        for row in table.rows:
            rows.append([cell.text.strip() for cell in row.cells])
    text = "\n".join(paragraphs + [" | ".join(row) for row in rows])
    return _clean_text(text), 0.95, "python-docx", rows, []


def _read_xlsx(path: Path) -> tuple[str, float, str, list[list[str]], list[str]]:
    warnings: list[str] = []
    try:
        import openpyxl

        workbook = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    except Exception as exc:
        raise CorruptDocumentError(f"xlsx_open_failed:{exc}") from exc
    rows: list[list[str]] = []
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(values_only=True):
            values = ["" if value is None else str(value) for value in row]
            if any(value.strip() for value in values):
                rows.append(values)
    if not rows:
        warnings.append("xlsx_without_cached_values")
    text = "\n".join(" | ".join(row) for row in rows)
    return _clean_text(text), 0.9, "openpyxl", rows, warnings


def _read_csv(path: Path) -> tuple[str, float, str, list[list[str]], list[str]]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1251", "utf-8"):
        try:
            decoded = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise CorruptDocumentError("csv_encoding_unknown")
    sample = decoded[:2000]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    try:
        rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(decoded), delimiter=delimiter)]
    except csv.Error as exc:
        raise CorruptDocumentError(f"csv_parse_failed:{exc}") from exc
    text = "\n".join(" | ".join(row) for row in rows)
    return _clean_text(text), 0.9, "csv", rows, []


def _rows_to_lines(rows: list[list[str]]) -> list[dict]:
    lines: list[dict] = []
    header: list[str] | None = None
    header_index: dict[str, int] = {}
    for row in rows:
        lowered = [cell.lower() for cell in row]
        if "sku" in lowered and ("количество" in " ".join(lowered) or "кол-во" in " ".join(lowered) or "qty" in lowered):
            header = lowered
            header_index = {name: idx for idx, name in enumerate(lowered)}
            continue
        if header is None:
            continue
        sku = row[header_index.get("sku", 0)] if row else ""
        if not re.match(r"^[A-Z]{2,}-\S+", sku.strip()):
            continue
        def cell(*names: str) -> str:
            for name in names:
                idx = header_index.get(name)
                if idx is not None and idx < len(row):
                    return row[idx]
            return ""

        qty_value = parse_decimal(cell("кол-во", "количество", "qty"))
        price = parse_decimal(cell("цена", "price", "price_rub"))
        amount = parse_decimal(cell("сумма", "amount", "amount_rub"))
        unit = _normalize_unit(cell("ед.", "ед", "единица", "unit"))
        name = cell("наименование", "name")
        if amount is None and qty_value is not None and price is not None:
            amount = qty_value * price
        lines.append(
            {
                "sku": sku.strip(),
                "name": name.strip(),
                "qty": str(qty_value) if qty_value is not None else None,
                "unit": unit.strip() or None,
                "price": decimal_str(price),
                "amount": decimal_str(amount),
            }
        )
    return lines


def _text_blocks(text: str) -> list[list[str]]:
    """Group line-broken OCR/table output into one block per SKU."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^[A-Z]{2,}-\S+", line):
            if current:
                blocks.append(current)
            current = [line]
            continue
        if current and re.match(r"^(Итого|Поставщик|Счёт|Счет|Дата|ИНН)\b", line, re.IGNORECASE):
            blocks.append(current)
            current = []
            continue
        if current:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    normalized = unit.strip()
    aliases = {"m": "м", "M": "м", "km": "км", "KM": "км"}
    return aliases.get(normalized, normalized)


def _parse_line_parts(parts: list[str]) -> dict | None:
    if len(parts) < 4:
        return None
    sku, name = parts[0], parts[1]
    qty_match = re.match(r"([\d.,]+)\s*(.*)", parts[2])
    if not qty_match:
        return None
    qty_value = parse_decimal(qty_match.group(1))
    unit = qty_match.group(2).strip() or None
    unit = _normalize_unit(unit)
    price: Decimal | None = None
    amount: Decimal | None = None
    if len(parts) >= 6 and not unit:
        unit = parts[3]
        price = parse_decimal(parts[4])
        amount = parse_decimal(parts[5])
    elif len(parts) >= 5:
        price = parse_decimal(parts[3])
        amount = parse_decimal(parts[4])
    elif len(parts) >= 4:
        price = parse_decimal(parts[3])
    if amount is None and qty_value is not None and price is not None:
        amount = qty_value * price
    return {
        "sku": sku,
        "name": name,
        "qty": str(qty_value) if qty_value is not None else None,
        "unit": unit,
        "price": decimal_str(price),
        "amount": decimal_str(amount),
    }


def _lines_from_text(text: str) -> list[dict]:
    lines: list[dict] = []
    for block in _text_blocks(text):
        joined = " ".join(block)
        parts = [part.strip() for part in joined.split("|") if part.strip()]
        parsed = _parse_line_parts(parts)
        if parsed:
            lines.append(parsed)
            continue
        match = re.search(
            r"(?P<sku>[A-Z]{2,}-\S+)\s+(?P<name>.+?)\s+(?P<qty>[\d.,]+)\s*(?P<unit>м|км|шт|барабан|упаковка|палетоместо|рулон)\s+(?P<price>[\d.,]+)\s+(?P<amount>[\d.,]+)",
            joined,
        )
        if match:
            qty_value = parse_decimal(match.group("qty"))
            price = parse_decimal(match.group("price"))
            amount = parse_decimal(match.group("amount"))
            lines.append(
                {
                    "sku": match.group("sku"),
                    "name": match.group("name"),
                    "qty": str(qty_value) if qty_value is not None else None,
                    "unit": match.group("unit"),
                    "price": decimal_str(price),
                    "amount": decimal_str(amount),
                }
            )
    return lines


def _extract_invoice(text: str, rows: list[list[str]]) -> dict:
    fields: dict = {}
    flat = " ".join(text.splitlines())
    number = re.search(r"\bINV-\d{4}-\d+\b", flat, re.IGNORECASE)
    if number:
        fields["doc_number"] = number.group(0).upper()
    supplier = re.search(r"Поставщик[:\s|]+(.+?)(?:,\s*ИНН|\||\(|$)", flat, re.IGNORECASE)
    if supplier:
        fields["counterparty"] = supplier.group(1).strip().strip("|").strip()
    inn = re.search(r"ИНН[:\s|]+(\d{10}|\d{12})", flat, re.IGNORECASE)
    if inn:
        fields["inn"] = inn.group(1)
    date_match = re.search(
        r"Дата[:\s|]+(\d{4}-\d{2}-\d{2}|\d{2}[./-]\d{2}[./-]\d{2,4})",
        flat,
        re.IGNORECASE,
    )
    if date_match:
        fields["doc_date"] = parse_date(date_match.group(1))
    total = re.search(r"Итого[:\s|]+([\d\s.,]+)", flat, re.IGNORECASE)
    if total:
        fields["total_amount"] = decimal_str(parse_decimal(total.group(1)))
    lines = _rows_to_lines(rows) or _lines_from_text(text)
    if lines:
        fields["lines"] = lines
        if not fields.get("total_amount"):
            total_value = sum((parse_decimal(line.get("amount")) or Decimal("0") for line in lines), Decimal("0"))
            fields["total_amount"] = decimal_str(total_value)
    fields["currency"] = "RUB"
    return fields


def _extract_quality_act(text: str) -> dict:
    fields: dict = {}
    flat = " ".join(text.splitlines())
    act = re.search(r"\bQA-\d{4}-\d+\b", flat, re.IGNORECASE)
    if act:
        fields["act_id"] = act.group(0).upper()
        fields["doc_number"] = fields["act_id"]
    order = re.search(r"\bORD-\d{4}-\d+\b", flat, re.IGNORECASE)
    if order:
        fields["order_id"] = order.group(0).upper()
    sku = re.search(r"\bCBL-[A-Z0-9,\-]+\b", flat, re.IGNORECASE)
    if sku:
        fields["sku"] = sku.group(0)
    for key, pattern in (
        ("qty_act", r"(?:Количество по акту|qty_act)[:\s|]+([\d.,]+)"),
        ("qty_erp", r"(?:Количество по ERP|qty_erp)[:\s|]+([\d.,]+)"),
    ):
        match = re.search(pattern, flat, re.IGNORECASE)
        if match:
            value = parse_decimal(match.group(1))
            fields[key] = str(value) if value is not None else None
    verdict = re.search(r"Заключение[:\s|]+([А-Яа-яЁё]+)", flat, re.IGNORECASE)
    if verdict:
        fields["verdict"] = verdict.group(1).strip()
    return fields


def extract_document(path: Path, filename: str | None = None) -> ExtractionResult:
    filename = filename or path.name
    detection = detect(path, filename)
    warnings = list(detection.warnings)
    rows: list[list[str]] = []
    text = ""
    engine = detection.kind
    confidence = 0.5

    try:
        if detection.kind == "pdf":
            text, confidence, engine, pdf_warnings = _read_pdf(path)
            warnings.extend(pdf_warnings)
        elif detection.kind == "docx":
            text, confidence, engine, rows, docx_warnings = _read_docx(path)
            warnings.extend(docx_warnings)
        elif detection.kind == "xlsx":
            text, confidence, engine, rows, xlsx_warnings = _read_xlsx(path)
            warnings.extend(xlsx_warnings)
        elif detection.kind == "csv":
            text, confidence, engine, rows, csv_warnings = _read_csv(path)
            warnings.extend(csv_warnings)
        elif detection.kind in {"png", "jpeg"}:
            ocr_result = ocr_image_file(path)
            text, confidence, engine = ocr_result.text, ocr_result.confidence / 100, ocr_result.engine
        elif detection.kind in {"legacy_doc", "legacy_xls"}:
            raise UnsupportedDocumentError(f"legacy_format:{detection.kind}")
        else:
            raise UnsupportedDocumentError(f"unsupported_format:{detection.kind}")
    except (CorruptDocumentError, UnsupportedDocumentError):
        raise
    except Exception as exc:
        raise CorruptDocumentError(f"extraction_failed:{exc}") from exc

    if not text.strip():
        raise CorruptDocumentError("empty_document_text")

    doc_type = classify_document(detection.kind, text, filename)
    if doc_type == "unknown":
        warnings.append("unknown_document_type")
    if doc_type == "quality_act":
        fields = _extract_quality_act(text)
    else:
        fields = _extract_invoice(text, rows)
    if detection.kind in {"png", "jpeg"} and not fields.get("doc_number"):
        field_number = re.search(r"\bINV-\d{4}-\d+\b", text, re.IGNORECASE)
        if field_number:
            fields["doc_number"] = field_number.group(0).upper()
    if confidence < 0.5:
        warnings.append("low_ocr_confidence")

    return ExtractionResult(
        doc_type=doc_type,
        fields=fields,
        warnings=warnings,
        confidence=round(confidence, 3),
        engine=engine,
        raw_text=text[:20000],
        priority=priority_for(doc_type, text),
    )
