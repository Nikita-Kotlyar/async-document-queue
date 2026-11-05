from __future__ import annotations

import hashlib
from pathlib import Path

from app.config import get_settings


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_bytes(data: bytes, filename: str, digest: str | None = None) -> Path:
    settings = get_settings()
    digest = digest or sha256_bytes(data)
    suffix = Path(filename).suffix.lower()
    root = Path(settings.storage_dir)
    target = (root / digest[:2] / f"{digest}{suffix}").resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(data)
    return target


def resolve_path(storage_path: str) -> Path:
    path = Path(storage_path)
    if path.is_absolute():
        return path
    return Path(get_settings().storage_dir) / path
