from __future__ import annotations

from collections.abc import AsyncGenerator, Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()


class Base(DeclarativeBase):
    pass


async_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)

sync_engine = create_engine(settings.sqlalchemy_sync_url, pool_pre_ping=True)
SyncSessionLocal = sessionmaker(bind=sync_engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


def get_sync_session() -> Generator[Session, None, None]:
    with SyncSessionLocal() as session:
        yield session
