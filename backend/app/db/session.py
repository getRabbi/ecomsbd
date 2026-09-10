"""Async engine and session management.

Two session kinds exist, and the difference is deliberate:

*   :func:`session_scope` — the normal, tenant-scoped session. Every ORM read is
    filtered to the ambient tenant and every write is checked against it.
*   :func:`system_session` — an explicitly unscoped session for workers, admin
    repair tooling and migrations. It requires a written reason, which is logged.

There is no third option, so "I forgot to filter by tenant" is not reachable
from application code.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import AppEnv, Settings, get_settings
from app.core.logging import get_logger
from app.db.tenancy import install_tenancy_guards, mark_session_system

__all__ = [
    "create_engine",
    "dispose_engine",
    "get_engine",
    "get_sessionmaker",
    "session_scope",
    "system_session",
]

log = get_logger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _engine_kwargs(settings: Settings) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "echo": settings.database_echo,
        "pool_pre_ping": True,
        "future": True,
    }
    if settings.is_sqlite or settings.app_env is AppEnv.TEST:
        # SQLite has no meaningful server-side pool; NullPool keeps the
        # file-locking behaviour predictable across the test suite.
        #
        # The test environment gets NullPool on *any* backend, and that is not
        # cosmetic. pytest-asyncio runs each test in its own event loop, while
        # the engine is a process-wide singleton — so a pooled asyncpg
        # connection created in one test is handed to the next one on a loop
        # that has since closed, and fails with "Event loop is closed" a long
        # way from the cause. NullPool opens and closes per session, so no
        # connection ever outlives the loop that created it.
        kwargs["poolclass"] = NullPool
    else:
        kwargs.update(
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            pool_recycle=1800,
        )
    return kwargs


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    """Build a fresh engine. Prefer :func:`get_engine` in application code."""
    settings = settings or get_settings()
    install_tenancy_guards()
    return create_async_engine(settings.database_url, **_engine_kwargs(settings))


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Process-wide engine, created on first use."""
    global _engine
    if _engine is None:
        _engine = create_engine(settings)
    return _engine


def get_sessionmaker(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    """Process-wide session factory."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(settings),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def dispose_engine() -> None:
    """Close pooled connections. Called on application shutdown and in tests."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Tenant-scoped session with commit-on-success, rollback-on-error.

    The tenant comes from the ambient request context, so callers never pass it
    explicitly and cannot pass the wrong one.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


@asynccontextmanager
async def system_session(reason: str) -> AsyncIterator[AsyncSession]:
    """Deliberately unscoped session for workers, admin tooling and migrations.

    ``reason`` is mandatory and logged: an unscoped session is the one place
    where cross-tenant data can be reached, so each use is auditable.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        mark_session_system(session.sync_session, reason)
        log.info("system session opened", extra={"system_session_reason": reason})
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
