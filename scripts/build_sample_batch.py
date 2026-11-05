"""Generate the synthetic sample batch and test fixtures.

All names, identifiers, addresses and amounts are fictional placeholders.
Run from the repository root:

    .venv/Scripts/python.exe scripts/build_sample_batch.py
"""

from __future__ import annotations

import email
import io
import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import docx
import openpyxl
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "inbox" / "sample-batch-01"
FIXTURES = ROOT / "tests" / "fixtures"
MAIL = EXAMPLES / "mail"
DROP = EXAMPLES / "drop"

INVOICE_NUMBER = "INV-2099-0042"
SUPPLIER = "ACME Sample LLC"
INN = "0000000000"
INVOICE_DATE = "15.01.2026"
TOTAL = "1000.00"


def find_font() -> Path:
    candidates = [
        Path(os.environ.get("WINDIR", "")) / "Fonts" / "Arial.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError("A Cyrillic-capable TTF font is required to build the sample batch")


def invoice_lines() -> list[str]:
    return [
        f"Счёт {INVOICE_NUMBER}",
        f"Поставщик: {SUPPLIER}, ИНН: {INN}",
        "Адрес: 123 Example Street",
        f"Дата: {INVOICE_DATE}",
        "CBL-SAMPLE-001 | Sample cable 3x2.5 | 100 м | 10.00 | 1000.00",
        f"Итого: {TOTAL}",
    ]


def quality_act_lines() -> list[str]:
    return [
        "Акт качества QA-2099-0001",
        "Заказ: ORD-2099-0001",
        "SKU: CBL-SAMPLE-001",
        "Количество по акту: 95",
        "Количество по ERP: 100",
        "Заключение: брак",
    ]


def write_pdf(path: Path, lines: list[str]) -> None:
    import pymupdf

    document = pymupdf.open()
    page = document.new_page()
    font_path = find_font()
    page.insert_font(fontname="sample", fontfile=str(font_path))
    page.insert_text((72, 72), "\n".join(lines), fontsize=11, fontname="sample")
    document.save(path)
    document.close()


def write_docx(path: Path) -> None:
    document = docx.Document()
    document.add_heading(f"Счёт {INVOICE_NUMBER}", level=1)
    document.add_paragraph(f"Поставщик: {SUPPLIER}, ИНН: {INN}")
    document.add_paragraph("Адрес: 123 Example Street")
    document.add_paragraph(f"Дата: {INVOICE_DATE}")
    table = document.add_table(rows=2, cols=6)
    for index, value in enumerate(["SKU", "Наименование", "Кол-во", "Ед.", "Цена", "Сумма"]):
        table.rows[0].cells[index].text = value
    for index, value in enumerate(["CBL-SAMPLE-001", "Sample cable 3x2.5", "100", "м", "10.00", "1000.00"]):
        table.rows[1].cells[index].text = value
    document.add_paragraph(f"Итого: {TOTAL}")
    document.save(path)


def write_xlsx(path: Path) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Invoice"
    sheet.append(["Поставщик", SUPPLIER, "ИНН", INN])
    sheet.append(["Счёт", INVOICE_NUMBER, "Дата", "2026-01-15"])
    sheet.append([])
    sheet.append(["SKU", "Наименование", "Кол-во", "Ед.", "Цена", "Сумма"])
    sheet.append(["CBL-SAMPLE-001", "Sample cable 3x2.5", 100, "м", 10.00, 1000.00])
    sheet.append([None, None, None, None, "Итого", 1000.00])
    workbook.save(path)


def write_csv(path: Path) -> None:
    path.write_text(
        "sku;name;qty;unit;price_rub;amount_rub\n"
        f"Поставщик;{SUPPLIER};ИНН;{INN}\n"
        f"Счёт;{INVOICE_NUMBER};Дата;2026-01-15\n"
        "CBL-SAMPLE-001;Sample cable 3x2.5;100;м;10.00;1000.00\n",
        encoding="utf-8-sig",
    )


def write_image(path: Path, lines: list[str], size: tuple[int, int] = (1400, 1900)) -> None:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(find_font()), 36)
    y = 80
    for line in lines:
        draw.text((80, y), line, fill="black", font=font)
        y += 64
    image.save(path)


def write_eml(path: Path, subject: str, attachments: list[tuple[str, bytes, str, str]], index: int) -> None:
    message = EmailMessage()
    message["From"] = "sender@example.com"
    message["To"] = "recipient@example.com"
    message["Subject"] = subject
    message["Date"] = datetime(2026, 1, 15, 9, index, tzinfo=timezone.utc)
    message["Message-ID"] = f"<sample-{index:03d}@example.com>"
    message.set_content("Synthetic sample message for the document intake pipeline.")
    for filename, payload, maintype, subtype in attachments:
        message.add_attachment(payload, maintype=maintype, subtype=subtype, filename=filename)
    path.write_bytes(message.as_bytes())


def build() -> None:
    for directory in (EXAMPLES, MAIL, DROP, FIXTURES):
        directory.mkdir(parents=True, exist_ok=True)
    for pattern in ("mail/*.eml", "drop/*", "manifest.json", "README.md"):
        for path in EXAMPLES.glob(pattern):
            path.unlink()
    for path in FIXTURES.glob("*"):
        if path.is_file():
            path.unlink()

    invoice_pdf = DROP / "invoice_sample_001.pdf"
    write_pdf(invoice_pdf, invoice_lines())
    invoice_docx = DROP / "invoice_sample_002.docx"
    write_docx(invoice_docx)
    write_xlsx(DROP / "invoice_sample_003.xlsx")
    write_csv(DROP / "invoice_sample_004.csv")
    write_image(DROP / "invoice_sample_005.png", invoice_lines())
    write_pdf(DROP / "quality_act_sample.pdf", quality_act_lines())
    write_image(DROP / "quality_act_sample.png", quality_act_lines())

    broken = invoice_pdf.read_bytes()[:300]
    (DROP / "broken_sample.pdf").write_bytes(broken)
    (DROP / "empty_sample.xlsx").write_bytes(b"")
    with zipfile.ZipFile(DROP / "unsupported_sample.zip", "w") as archive:
        archive.writestr("readme.txt", "Synthetic archive used to exercise unsupported-format handling")

    (DROP / "invoice_negative_qty.csv").write_text(
        "sku;name;qty;unit;price_rub;amount_rub\n"
        f"Поставщик;{SUPPLIER};ИНН;{INN}\n"
        "Счёт;INV-2099-0043;Дата;2026-01-16\n"
        "CBL-SAMPLE-001;Sample cable 3x2.5;-10;м;10.00;-100.00\n",
        encoding="utf-8-sig",
    )
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Поставщик", SUPPLIER, "ИНН", ""])
    sheet.append(["Счёт", "INV-2099-0044", "Дата", "2026-01-17"])
    sheet.append([])
    sheet.append(["SKU", "Наименование", "Кол-во", "Ед.", "Цена", "Сумма"])
    sheet.append(["CBL-SAMPLE-001", "Sample cable 3x2.5", 2, "км", 1000, 2000])
    sheet.append(["CBL-SAMPLE-002", "Sample cable 3x4", 1, "барабан", 5000, 5000])
    sheet.append([None, None, None, None, "Итого", 9999])
    workbook.save(DROP / "invoice_wrong_units.xlsx")

    write_eml(
        MAIL / "01_invoice_pdf.eml",
        "Invoice INV-2099-0042",
        [("invoice_sample_001.pdf", invoice_pdf.read_bytes(), "application", "pdf")],
        1,
    )
    write_eml(
        MAIL / "02_invoice_docx.eml",
        "Invoice INV-2099-0042 (DOCX)",
        [("invoice_sample_002.docx", invoice_docx.read_bytes(), "application", "vnd.openxmlformats-officedocument.wordprocessingml.document")],
        2,
    )
    write_eml(
        MAIL / "03_invoice_scan.eml",
        "Invoice scan",
        [("scan_sample_003.png", (DROP / "invoice_sample_005.png").read_bytes(), "image", "png")],
        3,
    )
    write_eml(
        MAIL / "04_duplicate.eml",
        "Invoice duplicate",
        [("invoice_sample_001_copy.pdf", invoice_pdf.read_bytes(), "application", "pdf")],
        4,
    )
    write_eml(MAIL / "05_no_attachment.eml", "Information request", [], 5)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("scan.txt", "Synthetic archive attachment")
    write_eml(
        MAIL / "06_unsupported_zip.eml",
        "Document archive",
        [("unsupported_sample.zip", buffer.getvalue(), "application", "zip")],
        6,
    )

    manifest = {
        "description": "Синтетическая пачка с валидными, дублирующимися, ошибочными и неподдерживаемыми документами",
        "entries": [
            {"file": "mail/01_invoice_pdf.eml", "expected": "processed", "note": "Счёт в PDF"},
            {"file": "mail/02_invoice_docx.eml", "expected": "processed", "note": "Счёт в DOCX"},
            {"file": "mail/03_invoice_scan.eml", "expected": "processed", "note": "PNG-скан, OCR"},
            {"file": "mail/04_duplicate.eml", "expected": "duplicate", "note": "те же байты, что в 01"},
            {"file": "mail/05_no_attachment.eml", "expected": "ignored", "note": "без вложений"},
            {"file": "mail/06_unsupported_zip.eml", "expected": "dead_letter", "note": "ZIP-вложение"},
            {"file": "drop/invoice_sample_001.pdf", "expected": "processed", "note": "Счёт в PDF"},
            {"file": "drop/invoice_sample_002.docx", "expected": "processed", "note": "Счёт в DOCX"},
            {"file": "drop/invoice_sample_003.xlsx", "expected": "processed", "note": "Счёт в XLSX"},
            {"file": "drop/invoice_sample_004.csv", "expected": "processed", "note": "Счёт в CSV"},
            {"file": "drop/invoice_sample_005.png", "expected": "processed", "note": "PNG-скан, OCR"},
            {"file": "drop/quality_act_sample.pdf", "expected": "needs_review", "note": "акт качества с расхождением"},
            {"file": "drop/quality_act_sample.png", "expected": "needs_review", "note": "скан акта качества, OCR"},
            {"file": "drop/broken_sample.pdf", "expected": "dead_letter", "note": "обрезанный PDF"},
            {"file": "drop/empty_sample.xlsx", "expected": "rejected", "note": "пустая загрузка отклоняется"},
            {"file": "drop/unsupported_sample.zip", "expected": "dead_letter", "note": "неподдерживаемый архив"},
            {"file": "drop/invoice_negative_qty.csv", "expected": "needs_review", "note": "отрицательное количество"},
            {"file": "drop/invoice_wrong_units.xlsx", "expected": "needs_review", "note": "неверные единицы и расхождение суммы"},
        ],
    }
    (EXAMPLES / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (EXAMPLES / "README.md").write_text(
        "# Synthetic sample batch\n\n"
        "All documents, names, identifiers, addresses and amounts in this directory are fictional.\n"
        "The batch exercises valid invoices, OCR scans, duplicates, validation issues and dead-letter paths.\n"
        "Regenerate with `python scripts/build_sample_batch.py`.\n",
        encoding="utf-8",
    )

    shutil.copyfile(invoice_pdf, FIXTURES / "invoice.pdf")
    shutil.copyfile(invoice_docx, FIXTURES / "invoice.docx")
    shutil.copyfile(DROP / "invoice_sample_003.xlsx", FIXTURES / "invoice.xlsx")
    shutil.copyfile(DROP / "invoice_sample_004.csv", FIXTURES / "invoice.csv")
    shutil.copyfile(DROP / "invoice_sample_005.png", FIXTURES / "invoice.png")
    shutil.copyfile(DROP / "quality_act_sample.pdf", FIXTURES / "quality_act.pdf")
    print(f"built synthetic sample batch: {EXAMPLES}")
    print(f"built test fixtures: {FIXTURES}")


if __name__ == "__main__":
    build()
