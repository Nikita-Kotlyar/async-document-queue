# Runbook

## Проверки здоровья

```bash
curl "http://<host>:${ADQ_API_PORT:-8081}/health"
curl "http://<host>:${ADQ_API_PORT:-8081}/ready"
curl "http://<host>:${ADQ_API_PORT:-8081}/metrics"
```

`/ready` проверяет PostgreSQL, Redis и AMQP-брокер.

## Логи

```bash
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f intake
docker compose logs -f webhook-sink
```

## Очереди

RabbitMQ Management доступен на порту `ADQ_RABBITMQ_MANAGEMENT_PORT` (по
умолчанию `15672`). Альтернатива через CLI:

```bash
docker compose exec -T rabbitmq rabbitmqctl list_queues name messages consumers
```

Очереди `documents`, `ocr`, `webhooks` и `maintenance` в норме должны иметь ноль
готовых сообщений. `dlq` содержит сообщения, требующие внимания оператора.

## Повтор dead-letter

```bash
curl "http://<host>:${ADQ_API_PORT:-8081}/api/v1/dead-letter"
curl -X POST "http://<host>:${ADQ_API_PORT:-8081}/api/v1/dead-letter/{id}/retry"
```

Повтор повреждённого или неподдерживаемого документа обычно снова вернёт его в
dead-letter; перед повтором изучи причину.

## Миграции

```bash
docker compose exec -T api alembic current
docker compose exec -T api alembic upgrade head
docker compose exec -T api alembic check
```

## Backup и restore

```bash
docker compose exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > backup.sql
cat backup.sql | docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

## Масштабирование

- Увеличивай concurrency `worker` для CPU-затратного OCR.
- Добавляй отдельный worker с `-Q ocr`, чтобы медленные сканы не блокировали
  текстовые документы.
- Сохраняй `worker_prefetch_multiplier=1`, чтобы упавший worker не держал
  большое количество неподтверждённых сообщений.

## Типовые сбои

| Симптом | Что проверить | Действие |
|---|---|---|
| Документы остаются в `queued` | Логи `worker`, глубину очередей RabbitMQ | Проверить регистрацию задач и доступность брокера |
| Webhook остаётся в `retrying` | `webhook_deliveries.last_error` | Проверить получателя и секрет HMAC |
| `dlq` растёт | `dead_letters.reason` | Исправить исходный документ или воспроизвести после исправления |
| OCR выполняется слишком долго | Глубину очереди `ocr` | Добавить OCR-воркеры или уменьшить лимит страниц/DPI |
