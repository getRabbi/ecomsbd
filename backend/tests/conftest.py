"""Test fixtures.

The suite runs against SQLite by default so it needs no database server, and
against PostgreSQL when ``TEST_DATABASE_URL`` points at one. The schema is
created by **running the real Alembic migrations**, not ``metadata.create_all``:
a migration that does not apply is a broken deploy, and a suite that never runs
one would not notice.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _test_database_url(tmp_path: Path) -> str:
    configured = os.environ.get("TEST_DATABASE_URL")
    if configured:
        return configured
    return f"sqlite+aiosqlite:///{tmp_path / 'ecomsbd_test.db'}"


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def settings(tmp_path_factory: pytest.TempPathFactory):
    """Session-wide settings pointing at a throwaway database."""
    tmp_path = tmp_path_factory.mktemp("ecomsbd")
    os.environ.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": _test_database_url(tmp_path),
            "LOG_JSON": "false",
            "LOG_LEVEL": "WARNING",
            "OTP_PROVIDER": "dev_console",
            "ALLOW_DEV_OTP": "true",
            "OTP_EXPOSE_DEBUG_CODE": "true",
            # Cooldown off: several tests request codes back to back, and the
            # cooldown itself is covered by a dedicated test that re-enables it.
            "OTP_RESEND_COOLDOWN_SECONDS": "0",
        }
    )
    os.environ.pop("REDIS_URL", None)

    from app.core.config import get_settings, reset_settings_cache

    reset_settings_cache()
    return get_settings()


@pytest.fixture(scope="session", autouse=True)
def migrated_database(settings) -> Iterator[None]:
    """Apply every migration once per session."""
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    yield


@pytest_asyncio.fixture(autouse=True)
async def _clean_state(settings) -> AsyncIterator[None]:
    """Reset shared process state between tests."""
    from app.api.deps import reset_singletons
    from app.common.cache import InMemoryBackend, set_cache_backend
    from app.core.context import RequestContext, set_context

    set_cache_backend(InMemoryBackend())
    reset_singletons()
    set_context(RequestContext(trace_id="test"))
    yield
    set_cache_backend(None)


@pytest_asyncio.fixture
async def db(settings) -> AsyncIterator[AsyncSession]:
    """A tenant-scoped session, rolled back after the test."""
    from app.db.session import get_sessionmaker
    from app.db.tenancy import install_tenancy_guards

    install_tenancy_guards()
    import app.models  # noqa: F401

    factory = get_sessionmaker(settings)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def system_db(settings) -> AsyncIterator[AsyncSession]:
    """An explicitly unscoped session for arranging cross-tenant fixtures."""
    from app.db.session import get_sessionmaker
    from app.db.tenancy import install_tenancy_guards, mark_session_system

    install_tenancy_guards()
    import app.models  # noqa: F401

    factory = get_sessionmaker(settings)
    async with factory() as session:
        mark_session_system(session.sync_session, "test fixture")
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(settings) -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to the real application, including middleware."""
    from app.main import create_app

    app = create_app(settings)
    # Exercising lifespan keeps startup wiring (mappers, tenancy guards) inside
    # the test's coverage rather than assuming it works.
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http_client,
        app.router.lifespan_context(app),
    ):
        yield http_client


@pytest.fixture
def unique_phone() -> str:
    """A distinct valid Bangladeshi mobile number per call."""
    suffix = uuid.uuid4().int % 100_000_000
    return f"017{suffix:08d}"
