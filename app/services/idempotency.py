from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator

import redis.asyncio as redis

from app.config import get_settings

_client: redis.Redis | None = None


def client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


@contextlib.asynccontextmanager
async def upload_lock(sha256: str, ttl_seconds: int = 30) -> AsyncGenerator[bool, None]:
    """Return True when the caller owns the lock, False when another upload is in flight."""
    key = f"adq:lock:upload:{sha256}"
    try:
        acquired = bool(await client().set(key, "1", nx=True, ex=ttl_seconds))
    except redis.RedisError:
        acquired = True
    try:
        yield acquired
    finally:
        if acquired:
            with contextlib.suppress(redis.RedisError):
                await client().delete(key)
