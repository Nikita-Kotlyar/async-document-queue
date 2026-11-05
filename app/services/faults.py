from __future__ import annotations

import redis

from app.config import get_settings

_client: redis.Redis | None = None


def client() -> redis.Redis | None:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


def set_fault(name: str, count: int, ttl_seconds: int = 600) -> int:
    key = f"adq:fault:{name}"
    connection = client()
    if connection is None:
        return count
    connection.set(key, count, ex=ttl_seconds)
    return count


def consume_fault(name: str) -> bool:
    connection = client()
    if connection is None:
        return False
    try:
        key = f"adq:fault:{name}"
        value = connection.get(key)
        if value is None:
            return False
        count = int(value)
        if count <= 0:
            return False
        connection.decr(key)
        return True
    except (redis.RedisError, ValueError):
        return False


def current_faults() -> dict[str, int]:
    result: dict[str, int] = {}
    connection = client()
    if connection is None:
        return result
    try:
        for key in connection.scan_iter("adq:fault:*"):
            value = connection.get(key)
            result[key.rsplit(":", 1)[-1]] = int(value or 0)
    except redis.RedisError:
        pass
    return result
