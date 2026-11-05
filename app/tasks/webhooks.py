from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from celery import Task
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import SyncSessionLocal
from app.models import DeadLetter, WebhookDelivery
from app.services.errors import WebhookTransientError
from app.services.faults import consume_fault
from app.services.webhooks import post_webhook
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dead_letter_delivery(session, delivery: WebhookDelivery, reason: str) -> None:
    delivery.status = "dead_letter"
    delivery.last_error = reason
    existing = session.scalar(select(DeadLetter).where(DeadLetter.delivery_id == delivery.id))
    if existing is None:
        entry = DeadLetter(
            document_id=delivery.document_id,
            delivery_id=delivery.id,
            queue="dlq",
            reason=reason,
            payload={"event_id": delivery.event_id, "event_type": delivery.event_type, "endpoint_id": delivery.endpoint_id},
        )
        session.add(entry)
        session.flush()
        if settings.celery_task_always_eager or settings.rabbitmq_url.startswith("memory://"):
            logger.info("webhook dead-letter recorded without broker publish in eager/memory mode")
        else:
            try:
                celery_app.send_task(
                    "app.tasks.maintenance.consume_dead_letter",
                    args=[{"dead_letter_id": entry.id, "delivery_id": delivery.id, "reason": reason}],
                    queue="dlq",
                    routing_key="dlq",
                    retry=False,
                )
            except Exception:
                logger.exception("failed to publish webhook dead-letter")


class WebhookTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        delivery_id = args[0] if args else kwargs.get("delivery_id")
        if not delivery_id:
            return
        with SyncSessionLocal() as session:
            delivery = session.scalar(
                select(WebhookDelivery).options(selectinload(WebhookDelivery.endpoint)).where(WebhookDelivery.id == delivery_id)
            )
            if delivery is None or delivery.status == "delivered":
                return
            _dead_letter_delivery(session, delivery, f"retry_exhausted: {exc}")
            session.commit()


@celery_app.task(
    bind=True,
    base=WebhookTask,
    name="app.tasks.webhooks.deliver_webhook",
    autoretry_for=(WebhookTransientError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=settings.webhook_max_attempts - 1,
)
def deliver_webhook(self, delivery_id: str) -> dict:
    with SyncSessionLocal() as session:
        delivery = session.scalar(
            select(WebhookDelivery).options(selectinload(WebhookDelivery.endpoint)).where(WebhookDelivery.id == delivery_id)
        )
        if delivery is None:
            return {"status": "missing", "delivery_id": delivery_id}
        if delivery.status in {"delivered", "dead_letter"}:
            return {"status": "skipped", "delivery_id": delivery_id, "reason": delivery.status}

        delivery.attempts += 1
        delivery.status = "delivering"
        session.commit()

        if consume_fault("webhook"):
            delivery.last_error = "injected_webhook_failure"
            delivery.last_status_code = 503
            delivery.status = "retrying"
            session.commit()
            raise WebhookTransientError("injected_webhook_failure")

        try:
            status_code, body = asyncio.run(
                post_webhook(
                    url=delivery.endpoint.url,
                    secret=delivery.endpoint.secret,
                    event_id=delivery.event_id,
                    event_type=delivery.event_type,
                    payload=delivery.payload,
                    attempt=delivery.attempts,
                )
            )
        except Exception as exc:
            delivery.last_error = f"transport_error:{exc}"
            delivery.status = "retrying"
            session.commit()
            raise WebhookTransientError(f"transport_error:{exc}") from exc

        delivery.last_status_code = status_code
        if 200 <= status_code < 300:
            delivery.status = "delivered"
            delivery.delivered_at = _now()
            delivery.last_error = None
            session.commit()
            return {"status": "delivered", "delivery_id": delivery_id, "status_code": status_code}

        delivery.last_error = f"http_{status_code}:{body}"
        if status_code >= 500 or status_code == 429:
            delivery.status = "retrying"
            session.commit()
            raise WebhookTransientError(f"http_{status_code}")

        _dead_letter_delivery(session, delivery, f"http_{status_code}:{body}")
        session.commit()
        return {"status": "dead_letter", "delivery_id": delivery_id, "status_code": status_code}
