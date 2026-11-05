from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.db import SyncSessionLocal
from app.models import Document, WebhookDelivery, WebhookEndpoint
from app.services.webhooks import ensure_deliveries, event_payload, signature
from webhook_sink import main as sink


def test_signature_is_stable():
    body = b'{"a":1}'
    expected = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert signature("secret", body) == expected


def test_event_payload_shape():
    document = Document(
        id="doc-1",
        sha256="a" * 64,
        filename="invoice.pdf",
        content_type="application/pdf",
        size_bytes=10,
        source="test",
        priority="normal",
        status="validated",
        storage_path="/tmp/x",
    )
    payload = event_payload(document, None, "document.processed")
    assert payload["event_type"] == "document.processed"
    assert payload["document"]["id"] == "doc-1"
    assert payload["extraction"] is None


def test_ensure_deliveries_is_idempotent():
    with SyncSessionLocal() as session:
        endpoint = WebhookEndpoint(
            name="test",
            url="http://example.test/hook",
            secret="test-webhook-secret",
            events=["document.test_event"],
        )
        document = Document(
            sha256="b" * 64,
            filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=10,
            source="test",
            priority="normal",
            status="validated",
            storage_path="/tmp/x",
        )
        session.add_all([endpoint, document])
        session.commit()
        session.refresh(endpoint)
        session.refresh(document)

        first = ensure_deliveries(session, document, "document.test_event")
        second = ensure_deliveries(session, document, "document.test_event")
        assert len(first) == 1
        assert second == []
        assert session.query(WebhookDelivery).filter_by(document_id=document.id).count() == 1


def test_webhook_sink_accepts_valid_signature():
    sink.SECRET = "test-secret"
    sink.fail_remaining = 0
    sink.events.clear()
    sink.seen.clear()
    body = json.dumps({"event_type": "document.processed"}).encode()
    digest = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    client = TestClient(sink.app)
    response = client.post(
        "/hooks/documents",
        content=body,
        headers={"X-Event-ID": "evt-1", "X-Event-Type": "document.processed", "X-Signature-256": digest},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_webhook_sink_rejects_bad_signature():
    sink.SECRET = "test-secret"
    client = TestClient(sink.app)
    response = client.post(
        "/hooks/documents",
        content=b"{}",
        headers={"X-Event-ID": "evt-2", "X-Signature-256": "sha256=bad"},
    )
    assert response.status_code == 401


def test_webhook_sink_simulated_failure_then_success():
    sink.SECRET = "test-secret"
    sink.fail_remaining = 1
    sink.events.clear()
    sink.seen.clear()
    body = json.dumps({"event_type": "document.processed"}).encode()
    digest = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    client = TestClient(sink.app)
    headers = {"X-Event-ID": "evt-3", "X-Event-Type": "document.processed", "X-Signature-256": digest}
    first = client.post("/hooks/documents", content=body, headers=headers)
    assert first.status_code == 503
    second = client.post("/hooks/documents", content=body, headers=headers)
    assert second.status_code == 200


def _create_delivery() -> str:
    import uuid

    with SyncSessionLocal() as session:
        endpoint = WebhookEndpoint(
            name="task-sink",
            url="http://example.test/hook",
            secret="test-webhook-secret",
            events=["document.processed"],
        )
        document = Document(
            sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=10,
            source="test",
            priority="normal",
            status="validated",
            storage_path="/tmp/x",
        )
        session.add_all([endpoint, document])
        session.commit()
        session.refresh(endpoint)
        session.refresh(document)
        delivery = WebhookDelivery(
            document_id=document.id,
            endpoint_id=endpoint.id,
            event_type="document.processed",
            payload={"event_type": "document.processed"},
            status="pending",
            max_attempts=4,
        )
        session.add(delivery)
        session.commit()
        session.refresh(delivery)
        return delivery.id


def test_deliver_webhook_success(monkeypatch):
    from app.tasks import webhooks as webhook_tasks

    async def fake_post(**kwargs):
        return 200, "ok"

    monkeypatch.setattr(webhook_tasks, "post_webhook", fake_post)
    delivery_id = _create_delivery()
    result = webhook_tasks.deliver_webhook.run(delivery_id)
    assert result["status"] == "delivered"
    with SyncSessionLocal() as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        assert delivery.status == "delivered"
        assert delivery.attempts == 1


def test_deliver_webhook_retry_then_success(monkeypatch):
    from app.services.errors import WebhookTransientError
    from app.tasks import webhooks as webhook_tasks

    async def fake_500(**kwargs):
        return 503, "down"

    monkeypatch.setattr(webhook_tasks, "post_webhook", fake_500)
    delivery_id = _create_delivery()
    try:
        webhook_tasks.deliver_webhook.run(delivery_id)
        raise AssertionError("expected WebhookTransientError")
    except WebhookTransientError:
        pass
    with SyncSessionLocal() as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        assert delivery.status == "retrying"
        assert delivery.attempts == 1

    async def fake_200(**kwargs):
        return 200, "ok"

    monkeypatch.setattr(webhook_tasks, "post_webhook", fake_200)
    result = webhook_tasks.deliver_webhook.run(delivery_id)
    assert result["status"] == "delivered"
    with SyncSessionLocal() as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        assert delivery.attempts == 2


def test_deliver_webhook_permanent_failure(monkeypatch):
    from app.tasks import webhooks as webhook_tasks

    async def fake_400(**kwargs):
        return 400, "bad request"

    monkeypatch.setattr(webhook_tasks, "post_webhook", fake_400)
    delivery_id = _create_delivery()
    result = webhook_tasks.deliver_webhook.run(delivery_id)
    assert result["status"] == "dead_letter"
    with SyncSessionLocal() as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        assert delivery.status == "dead_letter"
