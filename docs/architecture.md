# Architecture

## Components

| Layer | Responsibility |
|---|---|
| API | Upload, document queries, issue registry, dead-letter replay, webhook endpoint registration |
| Intake | Watches a mailbox directory and a folder drop for new documents |
| Queue | RabbitMQ exchanges and queues for documents, OCR, webhooks and maintenance |
| Worker | Celery tasks for extraction, OCR, validation and webhook delivery |
| Store | PostgreSQL for documents, extractions, jobs, attempts, issues, deliveries and audit events |
| Locks | Redis for upload locks, idempotency and controlled fault injection |
| Receiver | Reference webhook sink used by the integration suite |

## Processing flow

1. Intake or the API stores the file under a content-addressed path.
2. `documents.sha256` is unique; an existing hash returns the existing record.
3. A `process_document` task is published to the `documents` queue.
4. Text PDF, DOCX, XLSX and CSV files are parsed directly.
5. PNG and image-only PDF files are routed to the `ocr` queue.
6. Extracted fields are validated and persisted with issue records.
7. `document.processed`, `document.needs_review` or `document.failed` webhook
   deliveries are created.
8. Webhook delivery uses HMAC-SHA256, stable event ids and exponential retries.
9. Permanent failures and exhausted retries are recorded in `dead_letters` and
   published to the `dlq` queue.

## Status model

```text
queued → processing → validated | needs_review | failed | dead_letter
```

`validated` means no validation issues were found. `needs_review` means the
document was extracted but requires human attention. `dead_letter` means the
document cannot be processed without intervention.

## Delivery guarantees

- Celery uses `task_acks_late=True`, `task_reject_on_worker_lost=True` and
  `worker_prefetch_multiplier=1`.
- `task_acks_on_failure_or_timeout=False` allows exhausted tasks to reach the
  dead-letter exchange.
- Every document has one `process_document` job and, for OCR inputs, one
  additional `ocr_document` job.
- Webhook deliveries are tracked per endpoint and event type; replayed deliveries
  keep the same event id.

## Data model

| Table | Purpose |
|---|---|
| `documents` | Canonical document record and current status |
| `extractions` | Parsed fields, engine, confidence and warnings |
| `jobs` | Task-level execution records |
| `job_attempts` | Individual retry attempts |
| `document_issues` | Validation findings |
| `webhook_endpoints` | Registered receivers |
| `webhook_deliveries` | Delivery state, attempts and response codes |
| `dead_letters` | Permanent failures and exhausted retries |
| `idempotency_keys` | HTTP idempotency key cache |
| `audit_events` | Upload, reprocess and replay audit trail |

## Non-goals

The service does not provide JWT/RBAC, multi-tenancy, Kubernetes manifests or a
managed cloud deployment. Those concerns belong to the surrounding platform.
