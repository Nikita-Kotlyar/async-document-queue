from __future__ import annotations

import shutil

import pytest

from app.services.extraction import extract_document, parse_date, parse_decimal


def test_parse_decimal_variants():
    assert str(parse_decimal("1 000,00")) == "1000.00"
    assert str(parse_decimal("1,234.56")) == "1234.56"
    assert str(parse_decimal("1000.0 руб.")) == "1000.0"


def test_parse_date_variants():
    assert parse_date("15.01.2026") == "2026-01-15"
    assert parse_date("2026-01-15") == "2026-01-15"
    assert parse_date("15/01/26") == "2026-01-15"


def test_extract_pdf_invoice(sample_invoice_pdf):
    result = extract_document(sample_invoice_pdf)
    assert result.doc_type == "invoice"
    assert result.fields["doc_number"] == "INV-2099-0042"
    assert result.fields["inn"] == "0000000000"
    assert result.fields["doc_date"] == "2026-01-15"
    assert result.fields["total_amount"] == "1000.00"
    assert len(result.fields["lines"]) == 1


def test_extract_docx_invoice(sample_invoice_docx):
    result = extract_document(sample_invoice_docx)
    assert result.doc_type == "invoice"
    assert result.fields["doc_number"] == "INV-2099-0042"
    assert result.fields["counterparty"] == "ACME Sample LLC"
    assert len(result.fields["lines"]) == 1


def test_extract_xlsx_invoice(sample_invoice_xlsx):
    result = extract_document(sample_invoice_xlsx)
    assert result.doc_type == "invoice"
    assert result.fields["doc_number"] == "INV-2099-0042"
    assert result.fields["doc_date"] == "2026-01-15"
    assert len(result.fields["lines"]) == 1


def test_extract_csv_invoice(sample_invoice_csv):
    result = extract_document(sample_invoice_csv)
    assert result.doc_type == "invoice"
    assert result.fields["doc_number"] == "INV-2099-0042"
    assert result.fields["inn"] == "0000000000"
    assert len(result.fields["lines"]) == 1


def test_extract_quality_act(sample_quality_act):
    result = extract_document(sample_quality_act)
    assert result.doc_type == "quality_act"
    assert result.fields["act_id"] == "QA-2099-0001"
    assert result.fields["sku"] == "CBL-SAMPLE-001"
    assert result.fields["qty_act"] == "95"
    assert result.fields["qty_erp"] == "100"


@pytest.mark.ocr
def test_extract_png_scan(sample_scan_png):
    if shutil.which("tesseract") is None:
        pytest.importorskip("pytesseract")
    result = extract_document(sample_scan_png)
    assert result.doc_type == "invoice"
    assert result.fields["doc_number"] == "INV-2099-0042"
    assert result.fields["total_amount"] == "1000.00"
    assert len(result.fields["lines"]) == 1
