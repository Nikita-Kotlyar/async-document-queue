from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import admin, documents, system, webhooks
from app.config import get_settings
from app.db import async_engine
from app.errors import APIError
from app.services import faults, idempotency

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    yield
    await async_engine.dispose()
    try:
        await idempotency.client().aclose()
    except Exception:
        pass
    try:
        faults.client().close()
    except Exception:
        pass


app = FastAPI(
    title="Async Document Queue",
    description="Reliable document intake: FastAPI, Celery, RabbitMQ, Redis, PostgreSQL, webhooks.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed",
                "details": exc.errors(),
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )


app.include_router(system.router)
app.include_router(documents.router, prefix="/api/v1")
app.include_router(webhooks.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")


@app.get("/")
async def root() -> dict:
    return {
        "service": "async-document-queue",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
        "api": "/api/v1",
    }
