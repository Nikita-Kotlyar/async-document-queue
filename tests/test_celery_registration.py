from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.tasks import documents as _documents  # noqa: F401
from app.tasks import maintenance as _maintenance  # noqa: F401
from app.tasks import webhooks as _webhooks  # noqa: F401
from app.tasks.celery_app import celery_app

EXPECTED_TASKS = {
    "app.tasks.documents.process_document",
    "app.tasks.documents.ocr_document",
    "app.tasks.webhooks.deliver_webhook",
    "app.tasks.maintenance.reap_stale_jobs",
    "app.tasks.maintenance.consume_dead_letter",
}


def test_imports_are_configured():
    assert set(celery_app.conf.imports) == {
        "app.tasks.documents",
        "app.tasks.webhooks",
        "app.tasks.maintenance",
    }


def test_all_tasks_are_registered():
    registered = {name for name in celery_app.tasks if name.startswith("app.tasks.")}
    assert registered == EXPECTED_TASKS


@pytest.mark.integration
def test_live_worker_has_all_tasks_registered():
    if os.getenv("ADQ_INTEGRATION") != "1":
        pytest.skip("set ADQ_INTEGRATION=1 and start docker compose")
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "worker",
            "celery",
            "-A",
            "app.tasks.celery_app:celery_app",
            "inspect",
            "registered",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    for task in EXPECTED_TASKS:
        assert task in result.stdout
