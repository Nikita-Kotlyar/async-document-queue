from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import require_api_key
from app.config import get_settings
from app.errors import APIError
from app.schemas import FaultInjectionRequest
from app.services.faults import current_faults, set_fault

router = APIRouter(tags=["admin"], dependencies=[Depends(require_api_key)])
settings = get_settings()


@router.post("/admin/faults")
async def inject_fault(payload: FaultInjectionRequest) -> dict:
    if not settings.enable_fault_injection:
        raise APIError(403, "fault_injection_disabled", "Fault injection is disabled")
    if payload.name not in {"ocr", "webhook"}:
        raise APIError(400, "unknown_fault", "Supported faults: ocr, webhook")
    set_fault(payload.name, payload.count)
    return {"fault": payload.name, "count": payload.count, "active": current_faults()}


@router.get("/admin/faults")
async def get_faults() -> dict:
    return {"active": current_faults()}
