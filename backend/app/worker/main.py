"""ARQ worker entry point.

Run with::

    arq app.worker.main.WorkerSettings

Master spec section 42 lists the full job schedule. Phase A registers the one
job whose machinery is real — the outbox dispatcher — and the cron entries for
later jobs arrive with the features that need them, so the schedule always
reflects what actually runs.
"""

from __future__ import annotations

from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings

from app import __version__
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine, get_engine
from app.db.tenancy import install_tenancy_guards
from app.worker.jobs import dispatch_outbox

log = get_logger("app.worker.main")

__all__ = ["WorkerSettings"]


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    import app.models  # noqa: F401  (registers every mapper)

    install_tenancy_guards()
    get_engine(settings)
    log.info("ecomsbd worker started", extra={"version": __version__})


async def shutdown(ctx: dict[str, Any]) -> None:
    from app.common.cache import close_cache

    await close_cache()
    await dispose_engine()
    log.info("ecomsbd worker stopped")


def _redis_settings() -> RedisSettings:
    settings = get_settings()
    if not settings.redis_url:
        raise RuntimeError(
            "REDIS_URL is required to run the worker. The in-process cache "
            "fallback exists for the API in local development only; a worker "
            "without Redis would silently process nothing."
        )
    return RedisSettings.from_dsn(settings.redis_url)


class _Lazy:
    """Descriptor resolving a setting on class attribute access.

    ARQ reads these as plain class attributes. Resolving them lazily keeps
    importing this module free of side effects, so the test suite and the API
    process can import it without a Redis connection string.
    """

    def __init__(self, resolve: Any) -> None:
        self._resolve = resolve

    def __get__(self, instance: object, owner: type | None = None) -> Any:
        return self._resolve()


class WorkerSettings:
    """ARQ configuration."""

    functions: ClassVar[list[Any]] = [dispatch_outbox]

    cron_jobs: ClassVar[list[Any]] = [
        # Frequent, cheap and idempotent: the dispatcher claims work with
        # SKIP LOCKED, so overlapping runs contend for nothing.
        cron(dispatch_outbox, second={0, 15, 30, 45}, run_at_startup=True),
    ]

    on_startup = startup
    on_shutdown = shutdown
    job_timeout = 120
    keep_result = 3600

    redis_settings = _Lazy(_redis_settings)
    max_jobs = _Lazy(lambda: get_settings().worker_max_jobs)
