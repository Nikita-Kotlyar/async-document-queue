from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="1C/CRM webhook sink")

SECRET = os.getenv("ADQ_WEBHOOK_SECRET", "")
FAIL_FIRST_N = int(os.getenv("FAIL_FIRST_N", "0"))
FAIL_STATUS = int(os.getenv("FAIL_STATUS", "503"))

events: list[dict] = []
seen: set[str] = set()
fail_remaining = FAIL_FIRST_N


def _verify(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post("/hooks/documents")
async def receive(
    request: Request,
    x_event_id: str | None = Header(default=None, alias="X-Event-ID"),
    x_event_type: str | None = Header(default=None, alias="X-Event-Type"),
    x_signature_256: str | None = Header(default=None, alias="X-Signature-256"),
):
    global fail_remaining
    body = await request.body()
    if not _verify(SECRET, body, x_signature_256):
        return JSONResponse(status_code=401, content={"error": "bad_signature"})
    if x_event_id in seen:
        return {"status": "duplicate", "event_id": x_event_id}
    if fail_remaining > 0:
        fail_remaining -= 1
        return JSONResponse(status_code=FAIL_STATUS, content={"error": "simulated_failure", "remaining": fail_remaining})
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})
    seen.add(x_event_id or "")
    events.append(
        {
            "event_id": x_event_id,
            "event_type": x_event_type,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
    )
    return {"status": "accepted", "event_id": x_event_id}


@app.get("/events")
async def list_events() -> dict:
    return {"count": len(events), "events": events}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "events": len(events), "fail_remaining": fail_remaining}


@app.post("/admin/reset")
async def reset() -> dict:
    global fail_remaining
    events.clear()
    seen.clear()
    fail_remaining = FAIL_FIRST_N
    return {"status": "reset", "fail_remaining": fail_remaining}


@app.post("/admin/fail")
async def fail_next(count: int = 1, status_code: int = 503) -> dict:
    global fail_remaining, FAIL_STATUS
    fail_remaining = count
    FAIL_STATUS = status_code
    return {"fail_remaining": fail_remaining, "status_code": FAIL_STATUS}
