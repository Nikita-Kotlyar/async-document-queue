from __future__ import annotations

from celery import Celery
from kombu import Exchange, Queue

from app.config import get_settings

settings = get_settings()

dlx = Exchange("adq.dlx", type="direct", durable=True)
documents_exchange = Exchange("adq.documents", type="direct", durable=True)
ocr_exchange = Exchange("adq.ocr", type="direct", durable=True)
webhooks_exchange = Exchange("adq.webhooks", type="direct", durable=True)
maintenance_exchange = Exchange("adq.maintenance", type="direct", durable=True)

task_queues = (
    Queue(
        "documents",
        documents_exchange,
        routing_key="documents",
        durable=True,
        queue_arguments={"x-dead-letter-exchange": "adq.dlx", "x-dead-letter-routing-key": "dlq"},
    ),
    Queue(
        "ocr",
        ocr_exchange,
        routing_key="ocr",
        durable=True,
        queue_arguments={"x-dead-letter-exchange": "adq.dlx", "x-dead-letter-routing-key": "dlq"},
    ),
    Queue(
        "webhooks",
        webhooks_exchange,
        routing_key="webhooks",
        durable=True,
        queue_arguments={"x-dead-letter-exchange": "adq.dlx", "x-dead-letter-routing-key": "dlq"},
    ),
    Queue(
        "maintenance",
        maintenance_exchange,
        routing_key="maintenance",
        durable=True,
        queue_arguments={"x-dead-letter-exchange": "adq.dlx", "x-dead-letter-routing-key": "dlq"},
    ),
    Queue("dlq", dlx, routing_key="dlq", durable=True),
)

celery_app = Celery(
    "async_document_queue",
    broker=settings.rabbitmq_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_queues=task_queues,
    task_default_queue="documents",
    task_default_exchange="adq.documents",
    task_default_routing_key="documents",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_acks_on_failure_or_timeout=False,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_track_started=True,
    task_ignore_result=True,
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=settings.celery_task_always_eager,
    beat_schedule={
        "reap-stale-jobs": {
            "task": "app.tasks.maintenance.reap_stale_jobs",
            "schedule": 300.0,
        },
    },
)

celery_app.conf.imports = (
    "app.tasks.documents",
    "app.tasks.webhooks",
    "app.tasks.maintenance",
)
