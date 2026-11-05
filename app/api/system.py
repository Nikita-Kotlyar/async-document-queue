from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import DeadLetter, Document, WebhookDelivery
from app.schemas import HealthOut
from app.services import idempotency

router = APIRouter(tags=["system"])
settings = get_settings()


def _check_rabbit() -> bool:
    if settings.rabbitmq_url.startswith("memory://"):
        return True
    try:
        from kombu import Connection

        with Connection(settings.rabbitmq_url, connect_timeout=2) as connection:
            connection.ensure_connection(max_retries=0)
        return True
    except Exception:
        return False


async def _redis_ok() -> bool:
    try:
        return bool(await idempotency.client().ping())
    except Exception:
        return False


@router.get("/health", response_model=HealthOut)
async def health(session: AsyncSession = Depends(get_session)):
    database = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        database = "error"
    redis_status = "ok" if await _redis_ok() else "error"
    queue_status = "ok" if await asyncio.to_thread(_check_rabbit) else "error"
    return HealthOut(status="ok" if database == "ok" else "degraded", database=database, redis=redis_status, queue=queue_status)


@router.get("/ready")
async def ready(session: AsyncSession = Depends(get_session)):
    database = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        database = "error"
    redis_status = "ok" if await _redis_ok() else "error"
    queue_status = "ok" if await asyncio.to_thread(_check_rabbit) else "error"
    healthy = database == "ok" and redis_status == "ok" and queue_status == "ok"
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ready" if healthy else "not_ready", "database": database, "redis": redis_status, "queue": queue_status},
    )


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(session: AsyncSession = Depends(get_session)):
    by_status = await session.execute(select(Document.status, func.count()).group_by(Document.status))
    deliveries = await session.execute(
        select(WebhookDelivery.status, func.count()).group_by(WebhookDelivery.status)
    )
    dead_letters = await session.scalar(select(func.count()).select_from(DeadLetter)) or 0
    lines = [
        "# HELP adq_documents_total Documents by status",
        "# TYPE adq_documents_total gauge",
    ]
    for status, count in by_status.all():
        lines.append(f'adq_documents_total{{status="{status}"}} {count}')
    lines.extend(
        [
            "# HELP adq_webhook_deliveries_total Webhook deliveries by status",
            "# TYPE adq_webhook_deliveries_total gauge",
        ]
    )
    for status, count in deliveries.all():
        lines.append(f'adq_webhook_deliveries_total{{status="{status}"}} {count}')
    lines.append(f"adq_dead_letters_total {dead_letters}")
    lines.append(f'adq_build_info{{version="0.1.0",timestamp="{datetime.now(timezone.utc).isoformat()}"}} 1')
    return "\n".join(lines) + "\n"
