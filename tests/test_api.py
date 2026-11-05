from __future__ import annotations

import uuid


def _upload(client, name: str, payload: bytes, headers: dict | None = None):
    return client.post(
        "/api/v1/documents",
        files={"file": (name, payload, "application/octet-stream")},
        data={"source": "test"},
        headers=headers or {},
    )


def test_upload_and_duplicate(client):
    payload = f"invoice-{uuid.uuid4().hex}".encode()
    first = _upload(client, "one.pdf", payload)
    assert first.status_code == 202
    assert first.json()["duplicate"] is False
    assert first.json()["document"]["status"] == "queued"

    second = _upload(client, "two.pdf", payload)
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["document"]["id"] == first.json()["document"]["id"]


def test_idempotency_key(client):
    key = uuid.uuid4().hex
    payload = f"idem-{uuid.uuid4().hex}".encode()
    first = _upload(client, "a.pdf", payload, {"Idempotency-Key": key})
    assert first.status_code == 202
    second = _upload(client, "b.pdf", payload, {"Idempotency-Key": key})
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    conflict = _upload(client, "c.pdf", f"other-{uuid.uuid4().hex}".encode(), {"Idempotency-Key": key})
    assert conflict.status_code == 409


def test_list_detail_stats(client):
    payload = f"detail-{uuid.uuid4().hex}".encode()
    created = _upload(client, "detail.pdf", payload).json()["document"]
    listing = client.get("/api/v1/documents", params={"limit": 5})
    assert listing.status_code == 200
    assert listing.json()["total"] >= 1
    detail = client.get(f"/api/v1/documents/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == created["id"]
    stats = client.get("/api/v1/stats")
    assert stats.status_code == 200
    assert stats.json()["documents_total"] >= 1


def test_reprocess(client):
    created = _upload(client, "reprocess.pdf", f"reprocess-{uuid.uuid4().hex}".encode()).json()["document"]
    response = client.post(f"/api/v1/documents/{created['id']}/reprocess")
    assert response.status_code == 200
    assert response.json()["status"] == "queued"


def test_dead_letter_empty(client):
    response = client.get("/api/v1/dead-letter")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert "database" in response.json()


def test_upload_empty(client):
    response = client.post("/api/v1/documents", files={"file": ("empty.bin", b"", "application/octet-stream")})
    assert response.status_code == 400


def test_missing_document(client):
    assert client.get("/api/v1/documents/missing").status_code == 404
    assert client.post("/api/v1/dead-letter/missing/retry").status_code == 404


def test_admin_faults(client, monkeypatch):
    from app.api import admin as admin_api

    monkeypatch.setattr(admin_api, "set_fault", lambda name, count: count)
    monkeypatch.setattr(admin_api, "current_faults", lambda: {"ocr": 1})
    response = client.post("/api/v1/admin/faults", json={"name": "ocr", "count": 1})
    assert response.status_code == 200
    assert response.json()["fault"] == "ocr"
    assert client.get("/api/v1/admin/faults").json()["active"] == {"ocr": 1}


def test_metrics_and_ready(client):
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "adq_documents_total" in metrics.text
    ready = client.get("/ready")
    assert ready.status_code in (200, 503)


def test_webhook_endpoints_api(client):
    created = client.post(
        "/api/v1/webhooks/endpoints",
        json={"name": "sink", "url": "http://example.test/hook", "secret": "s", "events": ["document.processed"]},
    )
    assert created.status_code == 201
    endpoints = client.get("/api/v1/webhooks/endpoints")
    assert endpoints.status_code == 200
    assert len(endpoints.json()) >= 1
    deliveries = client.get("/api/v1/webhooks/deliveries")
    assert deliveries.status_code == 200
    assert client.post("/api/v1/webhooks/deliveries/missing/retry").status_code == 404
