from __future__ import annotations

from fastapi import Header

from app.config import get_settings
from app.errors import APIError


async def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    settings = get_settings()
    if settings.api_key and x_api_key != settings.api_key:
        raise APIError(401, "unauthorized", "Invalid or missing API key")
