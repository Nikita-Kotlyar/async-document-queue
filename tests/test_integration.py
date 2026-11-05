from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration


def test_live_stack_health():
    if os.getenv("ADQ_INTEGRATION") != "1":
        pytest.skip("set ADQ_INTEGRATION=1 and start docker compose")
    base_url = os.getenv("ADQ_API_URL", "http://localhost:8081")  # live integration endpoint
    response = httpx.get(f"{base_url}/health", timeout=10)
    assert response.status_code == 200
    body = response.json()
    assert body["database"] == "ok"
    assert body["redis"] == "ok"
    assert body["queue"] == "ok"


def test_live_upload_reaches_terminal_status():
    if os.getenv("ADQ_INTEGRATION") != "1":
        pytest.skip("set ADQ_INTEGRATION=1 and start docker compose")
    base_url = os.getenv("ADQ_API_URL", "http://localhost:8081")  # live integration endpoint
    sample = Path(__file__).resolve().parent / "fixtures" / "invoice.pdf"
    with sample.open("rb") as handle:
        response = httpx.post(
            f"{base_url}/api/v1/documents",
            files={"file": (sample.name, handle, "application/pdf")},
            data={"source": "integration-test"},
            timeout=30,
        )
    assert response.status_code in (200, 202)
    document_id = response.json()["document"]["id"]
    deadline = time.time() + 60
    status = None
    while time.time() < deadline:
        detail = httpx.get(f"{base_url}/api/v1/documents/{document_id}", timeout=10).json()
        status = detail["status"]
        if status in {"validated", "needs_review", "failed", "dead_letter"}:
            break
        time.sleep(2)
    assert status in {"validated", "needs_review"}, status
