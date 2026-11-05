from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

JsonType = JSON().with_variant(JSONB, "postgresql")


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(32), default="upload", index=True)
    source_ref: Mapped[str | None] = mapped_column(String(512), index=True)
    doc_type: Mapped[str | None] = mapped_column(String(32), index=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal", index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    business_key: Mapped[str | None] = mapped_column(String(128), index=True)
    storage_path: Mapped[str] = mapped_column(String(1024))
    meta: Mapped[dict] = mapped_column("metadata", JsonType, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    extraction: Mapped[Extraction | None] = relationship(
        back_populates="document", cascade="all, delete-orphan", uselist=False
    )
    jobs: Mapped[list[Job]] = relationship(back_populates="document", cascade="all, delete-orphan")
    issues: Mapped[list[DocumentIssue]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    deliveries: Mapped[list[WebhookDelivery]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Extraction(Base):
    __tablename__ = "extractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), unique=True, index=True
    )
    engine: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    fields: Mapped[dict] = mapped_column(JsonType, default=dict)
    warnings: Mapped[list] = mapped_column(JsonType, default=list)
    raw_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document] = relationship(back_populates="extraction")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    task_name: Mapped[str] = mapped_column(String(128))
    queue: Mapped[str] = mapped_column(String(32), default="documents")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    last_error: Mapped[str | None] = mapped_column(Text)
    celery_task_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    document: Mapped[Document] = relationship(back_populates="jobs")
    attempts_log: Mapped[list[JobAttempt]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class JobAttempt(Base):
    __tablename__ = "job_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job] = relationship(back_populates="attempts_log")


class DocumentIssue(Base):
    __tablename__ = "document_issues"
    __table_args__ = (UniqueConstraint("document_id", "code", "message", name="uq_document_issue"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    message: Mapped[str] = mapped_column(Text)
    field: Mapped[str | None] = mapped_column(String(64))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document] = relationship(back_populates="issues")


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(String(1024))
    secret: Mapped[str] = mapped_column(String(256))
    events: Mapped[list] = mapped_column(JsonType, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    endpoint_id: Mapped[str] = mapped_column(
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JsonType, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[Document] = relationship(back_populates="deliveries")
    endpoint: Mapped[WebhookEndpoint] = relationship()


class DeadLetter(Base):
    __tablename__ = "dead_letters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str | None] = mapped_column(String(36), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), index=True)
    delivery_id: Mapped[str | None] = mapped_column(String(36), index=True)
    queue: Mapped[str] = mapped_column(String(32), default="dlq")
    reason: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_sha256: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JsonType, default=dict)
    status_code: Mapped[int] = mapped_column(Integer, default=202)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IntakeState(Base):
    __tablename__ = "intake_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_ref: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="processed")
    error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(64), default="system")
    payload: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
