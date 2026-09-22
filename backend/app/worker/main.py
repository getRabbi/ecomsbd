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
from app.analytics.network import build_network_benchmarks
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.couriers.jobs import (
    poll_courier_statuses,
    recover_unknown_bookings,
    refresh_courier_credentials,
    sync_courier_payments,
    sync_courier_returns,
)
from app.db.session import dispose_engine, get_engine
from app.db.tenancy import install_tenancy_guards
from app.imports.jobs import commit_import_job, sweep_stuck_imports
from app.messaging.jobs import dispatch_messages
from app.notifications.jobs import scan_alerts, send_weekly_summaries
from app.public_api.webhooks import dispatch_webhooks
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
    if settings.push_transport == "fcm":
        from app.notifications.transport import FcmPushTransport

        ctx["push_transport"] = FcmPushTransport(settings)
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


class WorkerSettings:
    """ARQ configuration."""

    functions: ClassVar[list[Any]] = [
        dispatch_messages,
        dispatch_webhooks,
        build_network_benchmarks,
        dispatch_outbox,
        scan_alerts,
        send_weekly_summaries,
        reconcile_billing,
        expire_subscriptions,
        dispatch_notifications,
        expire_exports,
        run_account_deletions,
        poll_courier_statuses,
        recover_unknown_bookings,
        sync_courier_returns,
        sync_courier_payments,
        refresh_courier_credentials,
        # Imports large enough to outlast a request are committed here. The
        # job is enqueued by the request that marked the batch COMMITTING.
        commit_import_job,
        sweep_stuck_imports,
    ]

    cron_jobs: ClassVar[list[Any]] = [
        cron(dispatch_messages, second={5, 35}),
        cron(dispatch_webhooks, second={10, 40}),
        cron(build_network_benchmarks, day=8, hour=3, minute=45, run_at_startup=True),
        # Frequent, cheap and idempotent: the dispatcher claims work with
        # SKIP LOCKED, so overlapping runs contend for nothing.
        cron(dispatch_outbox, second={0, 15, 30, 45}, run_at_startup=True),
        # Smart alerts, twice a day: 09:30 Dhaka so they are waiting when the
        # seller opens the app, and 15:30 so a courier account that broke in
        # the morning is not left until tomorrow. ARQ crons fire on host local
        # time (the deployment sets TZ=UTC). Extra runs cannot duplicate
        # anything: each alert identity is raised once and then held by its
        # cooldown (app.notifications.rules).
        cron(scan_alerts, hour={3, 9}, minute=30),
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
        # Courier synchronisation (phase C). Steadfast documents no webhook, so
        # polling is the production path rather than a safety net — the
        # cadences below are what keeps a seller's parcel status true.
        #
        # Recovery runs most often and is the reason the whole set exists: every
        # minute an ambiguous booking stays unresolved is an order the seller
        # cannot book and a parcel that may or may not be moving. It is cheap
        # when idle, because the query finds nothing.
        cron(recover_unknown_bookings, minute=set(range(0, 60, 5))),
        # Status polling decides per parcel how often to ask (fresh, active,
        # stale), so running the sweep every ten minutes costs one query per
        # shop when nothing is due.
        cron(poll_courier_statuses, minute=set(range(0, 60, 10))),
        # Returns move on a human timescale at the courier's end.
        cron(sync_courier_returns, minute={7, 37}),
        # Payments settle daily at most. Hourly is generous and keeps the
        # Money screen close to the courier's own portal.
        cron(sync_courier_payments, minute={12}),
        # Credential health: slow on purpose. Its job is to notice a revoked
        # key before a seller hits it mid-booking, not to poll a working one.
        cron(refresh_courier_credentials, hour={5, 17}, minute=25),
        # A commit killed part-way leaves its batch in COMMITTING. This only
        # reports them: re-running is deliberate, because one that keeps dying
        # halfway is a bug to look at rather than a thing to retry forever.
        cron(sweep_stuck_imports, minute={23}),
    ]

    on_startup = startup
    on_shutdown = shutdown
    job_timeout = 120
    keep_result = 3600

    # ARQ reads vars(WorkerSettings), so descriptors are not evaluated.
    # This module is the worker entrypoint; validate its configuration at import.
    redis_settings = _redis_settings()
    max_jobs = get_settings().worker_max_jobs
    queue_name = get_settings().worker_queue_name
