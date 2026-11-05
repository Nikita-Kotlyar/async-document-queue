from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SyncSessionLocal
from app.models import DeadLetter, Document, DocumentIssue
from app.services.storage import save_bytes, sha256_bytes
from app.tasks.documents import ocr_document, process_document


def create_document(path: Path, filename: str | None = None, content_type: str = "application/octet-stream") -> str:
    data = path.read_bytes()
    digest = sha256_bytes(data)
    stored = save_bytes(data, filename or path.name, digest)
    with SyncSessionLocal() as session:
        existing = session.scalar(select(Document).where(Document.sha256 == digest))
        if existing:
            return existing.id
        document = Document(
            sha256=digest,
            filename=filename or path.name,
            content_type=content_type,
            size_bytes=len(data),
            source="test",
            status="queued",
            storage_path=str(stored),
        )
        session.add(document)
        session.commit()
        session.refresh(document)
        return document.id


def get_document(document_id: str) -> Document:
    with SyncSessionLocal() as session:
        return session.scalar(
            select(Document).options(selectinload(Document.extraction)).where(Document.id == document_id)
        )


def issues_for(document_id: str) -> list[DocumentIssue]:
    with SyncSessionLocal() as session:
        return list(session.scalars(select(DocumentIssue).where(DocumentIssue.document_id == document_id)))


def test_process_pdf_invoice(sample_invoice_pdf):
    document_id = create_document(sample_invoice_pdf)
    result = process_document.run(document_id)
    assert result["status"] == "validated"
    document = get_document(document_id)
    assert document.status == "validated"
    assert document.doc_type == "invoice"
    assert document.business_key == "INV-2099-0042"
    assert document.extraction is not None
    assert document.extraction.fields["total_amount"] == "1000.00"


def test_process_is_idempotent(sample_invoice_xlsx):
    document_id = create_document(sample_invoice_xlsx)
    process_document.run(document_id)
    second = process_document.run(document_id)
    assert second["status"] == "skipped"


def test_broken_pdf_goes_to_dead_letter(intake_dir):
    document_id = create_document(intake_dir / "drop" / "broken_sample.pdf")
    result = process_document.run(document_id)
    assert result["status"] == "dead_letter"
    document = get_document(document_id)
    assert document.status == "dead_letter"
    with SyncSessionLocal() as session:
        entry = session.scalar(select(DeadLetter).where(DeadLetter.document_id == document_id))
        assert entry is not None


def test_empty_xlsx_goes_to_dead_letter(intake_dir):
    document_id = create_document(intake_dir / "drop" / "empty_sample.xlsx")
    result = process_document.run(document_id)
    assert result["status"] == "dead_letter"


def test_unsupported_zip_goes_to_dead_letter(intake_dir):
    document_id = create_document(intake_dir / "drop" / "unsupported_sample.zip")
    result = process_document.run(document_id)
    assert result["status"] == "dead_letter"


def test_negative_qty_needs_review(intake_dir):
    document_id = create_document(intake_dir / "drop" / "invoice_negative_qty.csv")
    result = process_document.run(document_id)
    assert result["status"] == "needs_review"
    assert any(issue.code == "negative_qty" for issue in issues_for(document_id))


def test_quality_act_scan_needs_review(intake_dir):
    document_id = create_document(intake_dir / "drop" / "quality_act_sample.png", content_type="image/png")
    result = ocr_document.run(document_id)
    assert result["status"] == "needs_review"
    document = get_document(document_id)
    assert document.priority == "high"
    codes = {issue.code for issue in issues_for(document_id)}
    assert "qty_mismatch" in codes
    assert "quality_rejected" in codes


def test_duplicate_business_key(sample_invoice_docx, tmp_path):
    first_id = create_document(sample_invoice_docx)
    process_document.run(first_id)

    import docx

    duplicate_path = tmp_path / "duplicate.docx"
    document = docx.Document()
    document.add_paragraph("Счёт INV-2099-0042")
    document.add_paragraph("Поставщик: ACME Sample LLC, ИНН: 0000000000")
    document.add_paragraph("Дата: 15.01.2026")
    table = document.add_table(rows=2, cols=6)
    for index, value in enumerate(["SKU", "Наименование", "Кол-во", "Ед.", "Цена", "Сумма"]):
        table.rows[0].cells[index].text = value
    for index, value in enumerate(["CBL-SAMPLE-001", "Sample cable", "100", "м", "9.99", "999.00"]):
        table.rows[1].cells[index].text = value
    document.add_paragraph("Итого: 999.00 руб.")
    document.save(duplicate_path)

    second_id = create_document(duplicate_path)
    result = process_document.run(second_id)
    assert result["status"] == "needs_review"
    assert any(issue.code == "duplicate_business_key" for issue in issues_for(second_id))


def test_maintenance_tasks():
    from app.tasks.maintenance import consume_dead_letter, reap_stale_jobs

    assert reap_stale_jobs.run()["reaped"] >= 0
    assert consume_dead_letter.run({"x": 1})["consumed"] is True
