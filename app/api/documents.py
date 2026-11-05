from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import get_session
from app.errors import APIError
from app.models import AuditEvent, DeadLetter, Document, IdempotencyKey, Job, WebhookDelivery
from app.schemas import (
    DeadLetterOut,
    DocumentDetail,
    DocumentOut,
    DocumentPage,
    StatsOut,
    UploadResult,
)
from app.services.idempotency import upload_lock
from app.services.storage import save_bytes, sha256_bytes
from app.tasks.documents import process_document
from app.tasks.webhooks import deliver_webhook

router = APIRouter(tags=["documents"])
settings = get_settings()

TERMINAL = {"validated", "needs_review", "failed", "dead_letter"}


async def _read_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > settings.max_upload_bytes:
            raise APIError(413, "file_too_large", f"File exceeds {settings.max_upload_mb} MB")
        chunks.append(chunk)
    if total == 0:
        raise APIError(400, "empty_file", "Uploaded file is empty")
    return b"".join(chunks)


async def _existing_by_hash(session: AsyncSession, digest: str) -> Document | None:
    return await session.scalar(select(Document).where(Document.sha256 == digest))


@router.post("/documents", response_model=UploadResult, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    response: Response,
    file: UploadFile = File(...),
    source: str = Form("upload"),
    source_ref: str | None = Form(None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
):
    data = await _read_upload(file)
    digest = sha256_bytes(data)

    if idempotency_key:
        record = await session.get(IdempotencyKey, idempotency_key)
        if record:
            if record.request_sha256 != digest:
                raise APIError(409, "idempotency_conflict", "Idempotency-Key was used with a different payload")
            document = await session.get(Document, record.response.get("document_id"))
            if document:
                response.status_code = status.HTTP_200_OK
                return UploadResult(document=DocumentOut.model_validate(document), duplicate=True)

    async with upload_lock(digest) as acquired:
        if not acquired:
            await asyncio.sleep(0.2)
        existing = await _existing_by_hash(session, digest)
        if existing:
            if idempotency_key:
                session.add(
                    IdempotencyKey(
                        key=idempotency_key,
                        request_sha256=digest,
                        response={"document_id": existing.id},
                        status_code=200,
                    )
                )
                await session.commit()
            response.status_code = status.HTTP_200_OK
            return UploadResult(document=DocumentOut.model_validate(existing), duplicate=True)

        path = save_bytes(data, file.filename or "document.bin", digest)
        document = Document(
            sha256=digest,
            filename=file.filename or path.name,
            content_type=file.content_type,
            size_bytes=len(data),
            source=source,
            source_ref=source_ref,
            status="queued",
            storage_path=str(path),
        )
        session.add(document)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            existing = await _existing_by_hash(session, digest)
            if existing is None:
                raise APIError(409, "duplicate_race", "Concurrent upload lost the race")
            response.status_code = status.HTTP_200_OK
            return UploadResult(document=DocumentOut.model_validate(existing), duplicate=True)

        if idempotency_key:
            session.add(
                IdempotencyKey(
                    key=idempotency_key,
                    request_sha256=digest,
                    response={"document_id": document.id},
                    status_code=202,
                )
            )
        session.add(
            AuditEvent(
                entity="document",
                entity_id=document.id,
                action="uploaded",
                actor=source,
                payload={"filename": document.filename, "sha256": digest},
            )
        )
        await session.commit()
        await session.refresh(document)

    try:
        process_document.delay(document.id)
    except Exception as exc:
        document.error_code = "enqueue_failed"
        document.error_message = str(exc)
        await session.commit()
        raise APIError(503, "queue_unavailable", f"Document stored but queue is unavailable: {exc}") from exc

    return UploadResult(document=DocumentOut.model_validate(document), duplicate=False)


@router.get("/documents", response_model=DocumentPage)
async def list_documents(
    status_filter: str | None = Query(default=None, alias="status"),
    doc_type: str | None = None,
    priority: str | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    filters = []
    if status_filter:
        filters.append(Document.status == status_filter)
    if doc_type:
        filters.append(Document.doc_type == doc_type)
    if priority:
        filters.append(Document.priority == priority)
    if source:
        filters.append(Document.source == source)
    if q:
        filters.append(Document.filename.ilike(f"%{q}%") | Document.business_key.ilike(f"%{q}%"))

    total = await session.scalar(select(func.count()).select_from(Document).where(*filters)) or 0
    stmt = (
        select(Document)
        .where(*filters)
        .order_by(Document.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = (await session.scalars(stmt)).all()
    return DocumentPage(
        items=[DocumentOut.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/documents/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: str, session: AsyncSession = Depends(get_session)):
    stmt = (
        select(Document)
        .options(
            selectinload(Document.extraction),
            selectinload(Document.issues),
            selectinload(Document.jobs).selectinload(Job.attempts_log),
            selectinload(Document.deliveries),
        )
        .where(Document.id == document_id)
    )
    document = await session.scalar(stmt)
    if document is None:
        raise APIError(404, "not_found", "Document not found")
    return DocumentDetail.model_validate(document)


@router.post("/documents/{document_id}/reprocess", response_model=DocumentOut)
async def reprocess_document(document_id: str, session: AsyncSession = Depends(get_session)):
    document = await session.get(Document, document_id)
    if document is None:
        raise APIError(404, "not_found", "Document not found")
    if document.status == "processing":
        raise APIError(409, "already_processing", "Document is already being processed")
    previous_status = document.status
    document.status = "queued"
    document.error_code = None
    document.error_message = None
    document.processed_at = None
    session.add(
        AuditEvent(
            entity="document",
            entity_id=document.id,
            action="reprocess_requested",
            actor="api",
            payload={"previous_status": previous_status},
        )
    )
    await session.commit()
    await session.refresh(document)
    process_document.delay(document.id)
    return DocumentOut.model_validate(document)


@router.get("/dead-letter", response_model=list[DeadLetterOut])
async def list_dead_letter(
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    entries = await session.scalars(select(DeadLetter).order_by(DeadLetter.created_at.desc()).limit(limit))
    return [DeadLetterOut.model_validate(entry) for entry in entries]


@router.post("/dead-letter/{entry_id}/retry", response_model=DeadLetterOut)
async def retry_dead_letter(entry_id: str, session: AsyncSession = Depends(get_session)):
    entry = await session.get(DeadLetter, entry_id)
    if entry is None:
        raise APIError(404, "not_found", "Dead-letter entry not found")
    if entry.replayed_at:
        raise APIError(409, "already_replayed", "Dead-letter entry was already replayed")

    if entry.document_id:
        document = await session.get(Document, entry.document_id)
        if document:
            document.status = "queued"
            document.error_code = None
            document.error_message = None
            document.processed_at = None
            await session.commit()
            process_document.delay(document.id)
    elif entry.delivery_id:
        from app.models import WebhookDelivery

        delivery = await session.get(WebhookDelivery, entry.delivery_id)
        if delivery:
            delivery.status = "pending"
            delivery.last_error = None
            await session.commit()
            deliver_webhook.delay(delivery.id)

    entry.replayed_at = datetime.now(timezone.utc)
    session.add(
        AuditEvent(
            entity="dead_letter",
            entity_id=entry.id,
            action="replayed",
            actor="api",
            payload={"document_id": entry.document_id, "delivery_id": entry.delivery_id},
        )
    )
    await session.commit()
    await session.refresh(entry)
    return DeadLetterOut.model_validate(entry)


@router.get("/stats", response_model=StatsOut)
async def stats(session: AsyncSession = Depends(get_session)):
    by_status_rows = await session.execute(select(Document.status, func.count()).group_by(Document.status))
    by_status = {status: count for status, count in by_status_rows.all()}
    by_type_rows = await session.execute(select(Document.doc_type, func.count()).group_by(Document.doc_type))
    by_type = {(doc_type or "unknown"): count for doc_type, count in by_type_rows.all()}
    delivery_rows = await session.execute(
        select(WebhookDelivery.status, func.count()).group_by(WebhookDelivery.status)
    )
    deliveries = {status: count for status, count in delivery_rows.all()}
    dead_letters = await session.scalar(select(func.count()).select_from(DeadLetter)) or 0

    processed = await session.execute(
        select(Document.created_at, Document.processed_at).where(Document.processed_at.is_not(None))
    )
    durations = [
        (processed_at - created_at).total_seconds()
        for created_at, processed_at in processed.all()
        if processed_at and created_at
    ]
    avg = round(sum(durations) / len(durations), 3) if durations else None

    return StatsOut(
        documents_total=sum(by_status.values()),
        by_status=by_status,
        by_type=by_type,
        webhook_deliveries=deliveries,
        dead_letters=dead_letters,
        avg_processing_seconds=avg,
    )
