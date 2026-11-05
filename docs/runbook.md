# Runbook

## Health checks

```bash
curl "http://<host>:${ADQ_API_PORT:-8081}/health"
curl "http://<host>:${ADQ_API_PORT:-8081}/ready"
curl "http://<host>:${ADQ_API_PORT:-8081}/metrics"
```

`/ready` verifies PostgreSQL, Redis and the AMQP broker.

## Logs

```bash
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f intake
docker compose logs -f webhook-sink
```

## Queue inspection

RabbitMQ Management is available on `ADQ_RABBITMQ_MANAGEMENT_PORT` (default
`15672`). CLI alternative:

```bash
docker compose exec -T rabbitmq rabbitmqctl list_queues name messages consumers
```

`documents`, `ocr`, `webhooks` and `maintenance` should normally have zero ready
messages. `dlq` holds messages that require operator attention.

## Dead-letter replay

```bash
curl "http://<host>:${ADQ_API_PORT:-8081}/api/v1/dead-letter"
curl -X POST "http://<host>:${ADQ_API_PORT:-8081}/api/v1/dead-letter/{id}/retry"
```

Replaying a corrupt or unsupported document will usually return it to the
dead-letter queue; inspect the reason before replaying.

## Migrations

```bash
docker compose exec -T api alembic current
docker compose exec -T api alembic upgrade head
docker compose exec -T api alembic check
```

## Backup and restore

```bash
docker compose exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > backup.sql
cat backup.sql | docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

## Scaling

- Increase `worker` concurrency for CPU-bound OCR.
- Add a second worker with `-Q ocr` to isolate slow scans from text documents.
- Keep `worker_prefetch_multiplier=1` so a crashed worker does not hold a large
  batch of unacknowledged messages.

## Failure modes

| Symptom | Check | Action |
|---|---|---|
| Documents stay `queued` | `worker` logs, RabbitMQ queue depth | Verify task registration and broker connectivity |
| Webhook deliveries stay `retrying` | `webhook_deliveries.last_error` | Check receiver availability and HMAC secret |
| `dlq` grows | `dead_letters.reason` | Fix the source document or replay after correction |
| OCR takes too long | `ocr` queue depth | Add OCR workers or lower page/DPI limits |
