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
from app.notifications.jobs import scan_alerts, send_weekly_summaries
from app.worker.jobs import dispatch_outbox
from app.worker.maintenance import (
    dispatch_notifications,
    expire_exports,
    expire_subscriptions,
    reconcile_billing,
    run_account_deletions,
)

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

    functions: ClassVar[list[Any]] = [
        dispatch_outbox,
        scan_alerts,
        send_weekly_summaries,
        reconcile_billing,
        expire_subscriptions,
        dispatch_notifications,
        expire_exports,
        run_account_deletions,
    ]

    cron_jobs: ClassVar[list[Any]] = [
        # Frequent, cheap and idempotent: the dispatcher claims work with
        # SKIP LOCKED, so overlapping runs contend for nothing.
        cron(dispatch_outbox, second={0, 15, 30, 45}, run_at_startup=True),
        # Once a day, early: alerts should be waiting when the seller opens
        # the app, not arrive mid-afternoon. ARQ crons fire on host local
        # time, so this is 09:30 Dhaka only when the worker runs with
        # TZ=UTC, which the deployment sets. Getting that wrong shifts the
        # hour; it cannot produce a duplicate, because the notifications
        # deduplicate on the Dhaka business date.
        cron(scan_alerts, hour=3, minute=30),
        # Hourly, because the job decides for itself whether the *tenant's*
        # clock has reached Friday 18:00 (master spec section 42). ARQ crons
        # fire on host local time, and pinning the weekly summary to one UTC
        # hour would make it depend on how the container was deployed.
        cron(send_weekly_summaries, minute=5),
        # Billing (master spec section 90). Reconciliation first, expiry
        # second and an hour later: provider truth must have had its chance to
        # arrive before local expiry acts on a period that looks lapsed.
        cron(reconcile_billing, hour={2, 14}, minute=10),
        cron(expire_subscriptions, hour={3, 15}, minute=10),
        # Notification delivery. Every two minutes: a money alert that arrives
        # an hour late is worse than useless, and the job is idle when there is
        # nothing undelivered.
        cron(dispatch_notifications, minute=set(range(0, 60, 2))),
        # Housekeeping. Exports hold a copy of a shop's data behind a token, so
        # expiry runs often; deletions run once a day because the cooling-off
        # window is measured in days.
        cron(expire_exports, minute={0, 30}),
        cron(run_account_deletions, hour=4, minute=0),
    ]

    on_startup = startup
    on_shutdown = shutdown
    job_timeout = 120
    keep_result = 3600

    redis_settings = _Lazy(_redis_settings)
    max_jobs = _Lazy(lambda: get_settings().worker_max_jobs)
