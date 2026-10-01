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
            # Existing paid-mode tests explicitly exercise the retained architecture.
            "FREE_LAUNCH_MODE": "false",
            "BILLING_ENABLED": "true",
            # Production defers SMS sign-in (no gateway chosen), so
            # PHONE_OTP_LOGIN_ENABLED defaults to false and every OTP endpoint
            # answers FEATURE_DISABLED. The suite signs in over OTP, so without
            # this the shared sign-in fixture 403s and several hundred tests
            # fail for a reason that has nothing to do with what they assert.
            # Turned on here only; the production default is untouched.
            "PHONE_OTP_LOGIN_ENABLED": "true",
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


# --------------------------------------------------------------------------- #
# Courier providers
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def courier_flags_on(settings) -> AsyncIterator[None]:
    """Turn every courier's kill switch on, globally, for one test.

    The flags default to off, and connecting or booking with a courier whose
    flag is off is refused. A test about how a courier behaves once a shop may
    use it opts in with ``pytest.mark.usefixtures("courier_flags_on")``.

    The rows are committed, because routes read them from their own session,
    and removed afterwards: the test database lives for the whole session, and
    a leftover global row would turn couriers on for every later test.
    """
    import sqlalchemy as sa

    from app.common.feature_flags import COURIER_PROVIDER_FLAGS, FeatureFlag
    from app.db.session import get_sessionmaker
    from app.db.tenancy import install_tenancy_guards, mark_session_system

    install_tenancy_guards()
    import app.models  # noqa: F401

    keys = [str(flag) for flag in COURIER_PROVIDER_FLAGS.values()]
    factory = get_sessionmaker(settings)

    async def _remove() -> None:
        async with factory() as session:
            mark_session_system(session.sync_session, "test fixture")
            await session.execute(
                sa.delete(FeatureFlag).where(
                    FeatureFlag.key.in_(keys), FeatureFlag.tenant_id.is_(None)
                )
            )
            await session.commit()

    await _remove()
    async with factory() as session:
        mark_session_system(session.sync_session, "test fixture")
        for key in keys:
            session.add(FeatureFlag(key=key, enabled=True, rollout_percentage=100))
        await session.commit()
    try:
        yield
    finally:
        await _remove()


@pytest_asyncio.fixture(autouse=True)
async def _no_live_courier() -> AsyncIterator[None]:
    """Refuse a real courier HTTP call from anywhere in the suite.

    Installed for *every* test, not only the courier ones. Brief section 43: CI
    must never depend on Steadfast being up — and the way that rule usually
    breaks is a test that does not think it touches a provider reaching one
    through three layers of service wiring. A registry whose factory raises
    turns that into an immediate, obvious failure rather than a flaky build and
    a real parcel.
    """
    from app.couriers.metrics import reset_metrics
    from app.couriers.registry import CourierAdapterRegistry, set_courier_registry

    def _refuse():  # type: ignore[no-untyped-def]
        raise AssertionError(
            "A test tried to build a live courier adapter. Use the "
            "`steadfast_transport` fixture, or install a registry explicitly."
        )

    set_courier_registry(CourierAdapterRegistry({"steadfast": _refuse}))
    reset_metrics()
    yield
    set_courier_registry(None)


@pytest.fixture
def steadfast_transport():  # type: ignore[no-untyped-def]
    """A scripted Steadfast transport, installed into the adapter registry.

    Returns the transport so a test can enqueue responses and then assert what
    was actually sent — which is how "a create is sent exactly once" and "a bulk
    timeout resends nothing" are proved.
    """
    from app.couriers.registry import CourierAdapterRegistry, set_courier_registry
    from app.couriers.steadfast.adapter import SteadfastAdapter
    from app.couriers.steadfast.client import SteadfastClient, SteadfastConfig
    from app.couriers.steadfast.transport import FakeSteadfastTransport

    transport = FakeSteadfastTransport()

    async def _no_sleep(_seconds: float) -> None:
        return None

    def _factory():  # type: ignore[no-untyped-def]
        return SteadfastAdapter(
            SteadfastClient(
                transport,
                config=SteadfastConfig(bulk_chunk_size=3, max_read_retries=0),
                sleep=_no_sleep,
            )
        )

    set_courier_registry(CourierAdapterRegistry({"steadfast": _factory}))
    return transport
