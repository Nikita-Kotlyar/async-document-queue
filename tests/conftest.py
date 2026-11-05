from __future__ import annotations

import os
import shutil
from pathlib import Path

os.environ.setdefault("ADQ_DATABASE_URL", "sqlite+aiosqlite:///./test_adq.db")
os.environ.setdefault("ADQ_SYNC_DATABASE_URL", "sqlite:///./test_adq.db")
# Intentionally unreachable Redis for unit tests.
os.environ.setdefault("ADQ_REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("ADQ_RABBITMQ_URL", "memory://")
os.environ.setdefault("ADQ_STORAGE_DIR", "./test_storage")
os.environ.setdefault("ADQ_STATE_DIR", "./test_state")
os.environ.setdefault("ADQ_CELERY_TASK_ALWAYS_EAGER", "true")
if os.name == "nt":
    candidate = Path(os.environ.get("PROGRAMFILES", "")) / "Tesseract-OCR" / "tesseract.exe"
    if candidate.exists():
        os.environ.setdefault("ADQ_TESSERACT_CMD", str(candidate))
os.environ.setdefault("ADQ_OCR_LANG", "rus+eng")
os.environ.setdefault("ADQ_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("ADQ_ENABLE_FAULT_INJECTION", "true")

import pytest
from fastapi.testclient import TestClient

from app.db import Base, sync_engine
from app.main import app
from app.tasks.documents import process_document
from app.tasks.webhooks import deliver_webhook

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
EXAMPLES = ROOT / "examples" / "inbox" / "sample-batch-01"


@pytest.fixture(scope="session", autouse=True)
def database():
    Base.metadata.drop_all(sync_engine)
    Base.metadata.create_all(sync_engine)
    yield
    Base.metadata.drop_all(sync_engine)
    for folder in (Path("test_storage"), Path("test_state")):
        shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture(autouse=True)
def disable_webhook_enqueue(monkeypatch):
    """Unit tests must not perform network delivery; live delivery is covered by the integration stack."""
    monkeypatch.setattr(deliver_webhook, "delay", lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def fast_redis(monkeypatch):
    """Keep unit tests off the real Redis socket; integration tests exercise the real service."""
    from app.api import documents as documents_api
    from app.services import faults, idempotency

    class _DummyLock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _DummyRedis:
        async def ping(self):
            return True

        async def aclose(self):
            return None

        async def set(self, *args, **kwargs):
            return True

        async def delete(self, *args, **kwargs):
            return 0

    monkeypatch.setattr(idempotency, "upload_lock", lambda *args, **kwargs: _DummyLock())
    monkeypatch.setattr(documents_api, "upload_lock", lambda *args, **kwargs: _DummyLock())
    monkeypatch.setattr(idempotency, "client", lambda: _DummyRedis())
    monkeypatch.setattr(faults, "client", lambda: None)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(process_document, "delay", lambda *args, **kwargs: None)
    monkeypatch.setattr(deliver_webhook, "delay", lambda *args, **kwargs: None)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sample_invoice_pdf() -> Path:
    return FIXTURES / "invoice.pdf"


@pytest.fixture
def sample_invoice_docx() -> Path:
    return FIXTURES / "invoice.docx"


@pytest.fixture
def sample_invoice_xlsx() -> Path:
    return FIXTURES / "invoice.xlsx"


@pytest.fixture
def sample_invoice_csv() -> Path:
    return FIXTURES / "invoice.csv"


@pytest.fixture
def sample_scan_png() -> Path:
    return FIXTURES / "invoice.png"


@pytest.fixture
def sample_quality_act() -> Path:
    return FIXTURES / "quality_act.pdf"


@pytest.fixture
def intake_dir() -> Path:
    return EXAMPLES
