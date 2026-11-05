from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, Extraction, WebhookDelivery, WebhookEndpoint


def signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def event_payload(document: Document, extraction: Extraction | None, event_type: str) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "document": {
            "id": document.id,
            "sha256": document.sha256,
            "filename": document.filename,
            "doc_type": document.doc_type,
            "priority": document.priority,
            "status": document.status,
            "business_key": document.business_key,
            "source": document.source,
            "source_ref": document.source_ref,
        },
        "extraction": {
            "engine": extraction.engine,
            "confidence": extraction.confidence,
            "fields": extraction.fields,
            "warnings": extraction.warnings,
        }
        if extraction
        else None,
    }


def ensure_deliveries(session: Session, document: Document, event_type: str) -> list[WebhookDelivery]:
    endpoints = session.scalars(select(WebhookEndpoint).where(WebhookEndpoint.active.is_(True))).all()
    created: list[WebhookDelivery] = []
    extraction = document.extraction
    payload = event_payload(document, extraction, event_type)
    for endpoint in endpoints:
        events = endpoint.events or []
        if event_type not in events:
            continue
        existing = session.scalar(
            select(WebhookDelivery).where(
                WebhookDelivery.document_id == document.id,
                WebhookDelivery.endpoint_id == endpoint.id,
                WebhookDelivery.event_type == event_type,
            )
        )
        if existing:
            continue
        delivery = WebhookDelivery(
            document_id=document.id,
            endpoint_id=endpoint.id,
            event_type=event_type,
            payload=payload,
            status="pending",
            max_attempts=4,
        )
        session.add(delivery)
        created.append(delivery)
    session.commit()
    for delivery in created:
        session.refresh(delivery)
    return created


async def post_webhook(
    url: str,
    secret: str,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    attempt: int,
) -> tuple[int, str]:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Event-ID": event_id,
        "X-Event-Type": event_type,
        "X-Attempt": str(attempt),
        "X-Signature-256": signature(secret, body),
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, content=body, headers=headers)
        return response.status_code, response.text[:500]
