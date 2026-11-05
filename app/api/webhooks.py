from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.db import get_session
from app.errors import APIError
from app.models import WebhookDelivery, WebhookEndpoint
from app.schemas import WebhookDeliveryOut, WebhookEndpointCreate, WebhookEndpointOut
from app.tasks.webhooks import deliver_webhook

router = APIRouter(tags=["webhooks"], dependencies=[Depends(require_api_key)])


@router.post("/webhooks/endpoints", response_model=WebhookEndpointOut, status_code=201)
async def create_endpoint(payload: WebhookEndpointCreate, session: AsyncSession = Depends(get_session)):
    endpoint = WebhookEndpoint(
        name=payload.name,
        url=payload.url,
        secret=payload.secret,
        events=payload.events,
    )
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)
    return WebhookEndpointOut.model_validate(endpoint)


@router.get("/webhooks/endpoints", response_model=list[WebhookEndpointOut])
async def list_endpoints(session: AsyncSession = Depends(get_session)):
    endpoints = await session.scalars(select(WebhookEndpoint).order_by(WebhookEndpoint.created_at.desc()))
    return [WebhookEndpointOut.model_validate(endpoint) for endpoint in endpoints]


@router.get("/webhooks/deliveries", response_model=list[WebhookDeliveryOut])
async def list_deliveries(
    status_filter: str | None = Query(default=None, alias="status"),
    document_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    filters = []
    if status_filter:
        filters.append(WebhookDelivery.status == status_filter)
    if document_id:
        filters.append(WebhookDelivery.document_id == document_id)
    deliveries = await session.scalars(
        select(WebhookDelivery).where(*filters).order_by(WebhookDelivery.created_at.desc()).limit(limit)
    )
    return [WebhookDeliveryOut.model_validate(delivery) for delivery in deliveries]


@router.post("/webhooks/deliveries/{delivery_id}/retry", response_model=WebhookDeliveryOut)
async def retry_delivery(delivery_id: str, session: AsyncSession = Depends(get_session)):
    delivery = await session.get(WebhookDelivery, delivery_id)
    if delivery is None:
        raise APIError(404, "not_found", "Webhook delivery not found")
    if delivery.status == "delivered":
        raise APIError(409, "already_delivered", "Webhook was already delivered")
    delivery.status = "pending"
    delivery.last_error = None
    await session.commit()
    await session.refresh(delivery)
    deliver_webhook.delay(delivery.id)
    return WebhookDeliveryOut.model_validate(delivery)
