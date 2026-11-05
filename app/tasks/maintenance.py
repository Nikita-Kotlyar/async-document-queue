from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db import SyncSessionLocal
from app.models import Document, Job
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()


@celery_app.task(name="app.tasks.maintenance.reap_stale_jobs")
def reap_stale_jobs() -> dict:
    threshold = datetime.now(timezone.utc) - timedelta(minutes=settings.stale_processing_minutes)
    with SyncSessionLocal() as session:
        stale = session.scalars(
            select(Document).where(Document.status == "processing", Document.updated_at < threshold)
        ).all()
        for document in stale:
            document.status = "failed"
            document.error_code = "stale_processing"
            document.error_message = "Document stayed in processing longer than the stale timeout"
            document.processed_at = datetime.now(timezone.utc)
            job = session.scalar(select(Job).where(Job.document_id == document.id).order_by(Job.created_at.desc()))
            if job:
                job.status = "failed"
                job.last_error = document.error_message
        session.commit()
        return {"reaped": len(stale)}


@celery_app.task(name="app.tasks.maintenance.consume_dead_letter")
def consume_dead_letter(payload: dict) -> dict:
    """Consumer for the DLQ when an operator intentionally starts a worker on it."""
    logger.warning("dead-letter message consumed: %s", payload)
    return {"consumed": True, "payload": payload}
