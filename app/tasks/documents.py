from __future__ import annotations

import logging
from datetime import datetime, timezone

from celery import Task
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import SyncSessionLocal
from app.models import DeadLetter, Document, DocumentIssue, Extraction, Job, JobAttempt
from app.services.detection import classify_document, detect, priority_for
from app.services.errors import (
    CorruptDocumentError,
    PermanentProcessingError,
    TransientProcessingError,
    UnsupportedDocumentError,
)
from app.services.extraction import ExtractionResult, extract_document
from app.services.faults import consume_fault
from app.services.storage import resolve_path
from app.services.validation import IssueData, status_for_issues, validate_fields
from app.services.webhooks import ensure_deliveries
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()

TERMINAL_STATUSES = {"validated", "needs_review", "failed", "dead_letter"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_job(session, document_id: str, task_name: str, queue: str) -> Job:
    job = session.scalar(
        select(Job).where(Job.document_id == document_id, Job.task_name == task_name).order_by(Job.created_at.desc())
    )
    if job is None:
        job = Job(
            document_id=document_id,
            task_name=task_name,
            queue=queue,
            status="running",
            max_attempts=settings.task_max_retries + 1,
        )
        session.add(job)
        session.flush()
    return job


def _start_attempt(session, job: Job, task_id: str | None = None) -> JobAttempt:
    job.status = "running"
    job.attempts += 1
    job.celery_task_id = task_id or job.celery_task_id
    attempt = JobAttempt(job_id=job.id, attempt_no=job.attempts, status="running")
    session.add(attempt)
    session.flush()
    return attempt


def _finish_attempt(session, attempt: JobAttempt, status: str, error: str | None = None) -> None:
    attempt.status = status
    attempt.error = error
    attempt.finished_at = _now()


def _publish_dead_letter(payload: dict) -> None:
    if settings.celery_task_always_eager or settings.rabbitmq_url.startswith("memory://"):
        logger.info("dead-letter recorded without broker publish in eager/memory mode")
        return
    try:
        celery_app.send_task(
            "app.tasks.maintenance.consume_dead_letter",
            args=[payload],
            queue="dlq",
            routing_key="dlq",
            retry=False,
        )
    except Exception:
        logger.exception("failed to publish dead-letter message")


def _record_dead_letter(
    session,
    document: Document | None,
    reason: str,
    queue: str = "dlq",
    job: Job | None = None,
    delivery_id: str | None = None,
) -> DeadLetter:
    existing = session.scalar(
        select(DeadLetter).where(
            DeadLetter.document_id == (document.id if document else None),
            DeadLetter.delivery_id == delivery_id,
            DeadLetter.reason == reason,
        )
    )
    if existing:
        return existing
    entry = DeadLetter(
        document_id=document.id if document else None,
        job_id=job.id if job else None,
        delivery_id=delivery_id,
        queue=queue,
        reason=reason,
        payload={
            "document_id": document.id if document else None,
            "filename": document.filename if document else None,
            "sha256": document.sha256 if document else None,
            "reason": reason,
        },
    )
    session.add(entry)
    session.flush()
    _publish_dead_letter({"dead_letter_id": entry.id, "reason": reason, "document_id": entry.document_id})
    return entry


def _permanent_failure(session, document: Document, job: Job, exc: Exception, attempt: JobAttempt) -> None:
    message = str(exc)
    code = getattr(exc, "code", "permanent_error")
    document.status = "dead_letter"
    document.error_code = code
    document.error_message = message
    document.processed_at = _now()
    job.status = "dead_letter"
    job.last_error = message
    _finish_attempt(session, attempt, "failed", message)
    _record_dead_letter(session, document, f"{code}: {message}", job=job)
    deliveries = ensure_deliveries(session, document, "document.failed")
    session.commit()
    for delivery in deliveries:
        deliver_webhook.delay(delivery.id)


def _finalize(
    session,
    document: Document,
    job: Job,
    attempt: JobAttempt,
    result: ExtractionResult,
) -> None:
    document.doc_type = result.doc_type
    document.priority = result.priority
    document.business_key = result.fields.get("doc_number") or result.fields.get("act_id")

    extraction = document.extraction or Extraction(document_id=document.id)
    extraction.engine = result.engine
    extraction.confidence = result.confidence
    extraction.fields = result.fields
    extraction.warnings = result.warnings
    extraction.raw_text = result.raw_text
    document.extraction = extraction

    session.execute(delete(DocumentIssue).where(DocumentIssue.document_id == document.id))
    issues = validate_fields(result.doc_type, result.fields, result.confidence, result.warnings)

    if document.business_key:
        duplicate = session.scalar(
            select(Document).where(
                Document.id != document.id,
                Document.doc_type == result.doc_type,
                Document.business_key == document.business_key,
                Document.status.notin_(("failed", "dead_letter")),
            )
        )
        if duplicate:
            issues.append(
                IssueData(
                    code="duplicate_business_key",
                    severity="warning",
                    message=f"Business key {document.business_key} also exists in document {duplicate.id}",
                    field="business_key",
                )
            )

    for issue in issues:
        session.add(
            DocumentIssue(
                document_id=document.id,
                code=issue.code,
                severity=issue.severity,
                message=issue.message,
                field=issue.field,
            )
        )

    document.status = status_for_issues(issues)
    document.error_code = None
    document.error_message = None
    document.processed_at = _now()
    job.status = "succeeded"
    job.last_error = None
    _finish_attempt(session, attempt, "succeeded")

    event_type = "document.needs_review" if issues else "document.processed"
    deliveries = ensure_deliveries(session, document, event_type)
    session.commit()
    for delivery in deliveries:
        deliver_webhook.delay(delivery.id)


def _needs_ocr(path, kind: str) -> bool:
    if kind in {"png", "jpeg"}:
        return True
    if kind != "pdf":
        return False
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        return len(text.strip()) < 40
    except Exception:
        return False


class DocumentTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        document_id = args[0] if args else kwargs.get("document_id")
        if not document_id:
            return
        with SyncSessionLocal() as session:
            document = session.get(Document, document_id)
            if document is None or document.status in TERMINAL_STATUSES:
                return
            document.status = "dead_letter"
            document.error_code = getattr(exc, "code", "retry_exhausted")
            document.error_message = str(exc)
            document.processed_at = _now()
            job = session.scalar(
                select(Job).where(Job.document_id == document_id).order_by(Job.created_at.desc())
            )
            if job:
                job.status = "dead_letter"
                job.last_error = str(exc)
            _record_dead_letter(session, document, f"retry_exhausted: {exc}", job=job)
            deliveries = ensure_deliveries(session, document, "document.failed")
            session.commit()
            for delivery in deliveries:
                deliver_webhook.delay(delivery.id)


@celery_app.task(
    bind=True,
    base=DocumentTask,
    name="app.tasks.documents.process_document",
    autoretry_for=(TransientProcessingError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=settings.task_max_retries,
)
def process_document(self, document_id: str) -> dict:
    with SyncSessionLocal() as session:
        document = session.scalar(
            select(Document).options(selectinload(Document.extraction)).where(Document.id == document_id)
        )
        if document is None:
            return {"status": "missing", "document_id": document_id}
        if document.status in TERMINAL_STATUSES:
            return {"status": "skipped", "document_id": document_id, "reason": document.status}

        job = _get_job(session, document_id, "process_document", "documents")
        attempt = _start_attempt(session, job, self.request.id)
        document.status = "processing"
        session.commit()

        path = resolve_path(document.storage_path)
        try:
            detection = detect(path, document.filename)
            document.doc_type = classify_document(detection.kind, "", document.filename)
            document.priority = priority_for(document.doc_type, "")
            if detection.kind in {"legacy_doc", "legacy_xls"}:
                raise UnsupportedDocumentError(f"legacy_format:{detection.kind}")
            if detection.kind == "unknown":
                raise UnsupportedDocumentError("unsupported_format:unknown")
            if _needs_ocr(path, detection.kind):
                job.status = "succeeded"
                _finish_attempt(session, attempt, "succeeded")
                session.commit()
                ocr_document.apply_async(args=[document_id], queue="ocr", routing_key="ocr")
                return {"status": "routed_to_ocr", "document_id": document_id}
            result = extract_document(path, document.filename)
        except PermanentProcessingError as exc:
            _permanent_failure(session, document, job, exc, attempt)
            return {"status": "dead_letter", "document_id": document_id, "reason": str(exc)}
        except TransientProcessingError:
            session.rollback()
            raise
        except Exception as exc:
            session.rollback()
            raise TransientProcessingError(f"unexpected_process_error:{exc}") from exc

        _finalize(session, document, job, attempt, result)
        return {"status": document.status, "document_id": document_id}


@celery_app.task(
    bind=True,
    base=DocumentTask,
    name="app.tasks.documents.ocr_document",
    autoretry_for=(TransientProcessingError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=settings.task_max_retries,
)
def ocr_document(self, document_id: str) -> dict:
    with SyncSessionLocal() as session:
        document = session.scalar(
            select(Document).options(selectinload(Document.extraction)).where(Document.id == document_id)
        )
        if document is None:
            return {"status": "missing", "document_id": document_id}
        if document.status in TERMINAL_STATUSES:
            return {"status": "skipped", "document_id": document_id, "reason": document.status}

        job = _get_job(session, document_id, "ocr_document", "ocr")
        attempt = _start_attempt(session, job, self.request.id)
        document.status = "processing"
        session.commit()

        if consume_fault("ocr"):
            session.rollback()
            raise TransientProcessingError("injected_ocr_failure")

        path = resolve_path(document.storage_path)
        try:
            result = extract_document(path, document.filename)
        except PermanentProcessingError as exc:
            _permanent_failure(session, document, job, exc, attempt)
            return {"status": "dead_letter", "document_id": document_id, "reason": str(exc)}
        except TransientProcessingError:
            session.rollback()
            raise
        except Exception as exc:
            session.rollback()
            raise TransientProcessingError(f"unexpected_ocr_error:{exc}") from exc

        _finalize(session, document, job, attempt, result)
        return {"status": document.status, "document_id": document_id}


# Imported late to avoid a circular import at module load time.
from app.tasks.webhooks import deliver_webhook  # noqa: E402
