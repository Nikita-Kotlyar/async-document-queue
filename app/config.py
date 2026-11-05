from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ADQ_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    database_url: str = "sqlite+aiosqlite:///./adq.db"
    sync_database_url: str | None = None
    redis_url: str = "redis://localhost:6380/0"
    rabbitmq_url: str = "amqp://localhost:5672//"

    storage_dir: Path = Path("storage")
    intake_dir: Path = Path("examples/inbox/sample-batch-01")
    state_dir: Path = Path("state")
    api_url: str = "http://localhost:8081"
    api_key: str = ""

    tesseract_cmd: str = ""
    ocr_lang: str = "rus+eng"
    low_ocr_confidence: float = 45.0

    webhook_secret: str = ""
    webhook_max_attempts: int = 4
    task_max_retries: int = 3
    max_upload_mb: int = 25
    enable_fault_injection: bool = True
    celery_task_always_eager: bool = False
    stale_processing_minutes: int = 15

    @property
    def sqlalchemy_sync_url(self) -> str:
        if self.sync_database_url:
            return self.sync_database_url
        url = self.database_url
        return url.replace("+asyncpg", "+psycopg").replace("+aiosqlite", "")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
