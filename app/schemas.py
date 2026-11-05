from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ExtractionOut(ORMModel):
    engine: str
    confidence: float
    fields: dict[str, Any] = Field(default_factory=dict)
    warnings: list[Any] = Field(default_factory=list)
    created_at: datetime


class DocumentIssueOut(ORMModel):
    id: int
    code: str
    severity: str
    message: str
    field: str | None = None
    resolved: bool = False
    created_at: datetime


class JobAttemptOut(ORMModel):
    id: int
    attempt_no: int
    status: str
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class JobOut(ORMModel):
    id: str
    task_name: str
    queue: str
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None = None
    celery_task_id: str | None = None
    created_at: datetime
    updated_at: datetime
    attempts_log: list[JobAttemptOut] = Field(default_factory=list)


class WebhookDeliveryOut(ORMModel):
    id: str
    event_id: str
    event_type: str
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None = None
    last_status_code: int | None = None
    next_attempt_at: datetime | None = None
    created_at: datetime
    delivered_at: datetime | None = None


class DocumentOut(ORMModel):
    id: str
    sha256: str
    filename: str
    content_type: str | None = None
    size_bytes: int
    source: str
    source_ref: str | None = None
    doc_type: str | None = None
    priority: str
    status: str
    business_key: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None = None


class DocumentDetail(DocumentOut):
    extraction: ExtractionOut | None = None
    issues: list[DocumentIssueOut] = Field(default_factory=list)
    jobs: list[JobOut] = Field(default_factory=list)
    deliveries: list[WebhookDeliveryOut] = Field(default_factory=list)


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    total: int
    limit: int
    offset: int


class UploadResult(BaseModel):
    document: DocumentOut
    duplicate: bool = False


class WebhookEndpointCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=1024)
    secret: str = Field(min_length=1, max_length=256)
    events: list[str] = Field(default_factory=lambda: ["document.processed", "document.needs_review"])


class WebhookEndpointOut(ORMModel):
    id: str
    name: str
    url: str
    events: list[Any]
    active: bool
    created_at: datetime


class DeadLetterOut(ORMModel):
    id: str
    document_id: str | None = None
    job_id: str | None = None
    delivery_id: str | None = None
    queue: str
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    replayed_at: datetime | None = None


class StatsOut(BaseModel):
    documents_total: int
    by_status: dict[str, int]
    by_type: dict[str, int]
    webhook_deliveries: dict[str, int]
    dead_letters: int
    avg_processing_seconds: float | None = None


class FaultInjectionRequest(BaseModel):
    name: str = Field(description="ocr or webhook")
    count: int = Field(ge=0, le=50, default=1)


class HealthOut(BaseModel):
    status: str
    database: str
    redis: str
    queue: str
