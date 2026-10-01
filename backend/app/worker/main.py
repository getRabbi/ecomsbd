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
from app.automation.jobs import dispatch_automation, scan_segment_entries
from app.chat_orders.jobs import chat_order_housekeeping, process_chat_messages
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
from app.forecasting.jobs import snapshot_demand_forecasts
from app.imports.jobs import commit_import_job, sweep_stuck_imports
from app.integrations.jobs import (
    process_integration_events,
    run_integration_syncs,
    schedule_integrations,
    sync_inventories,
)
from app.messaging.campaign_jobs import run_campaigns
from app.messaging.jobs import dispatch_messages
from app.notifications.jobs import scan_alerts, send_weekly_summaries
from app.public_api.webhooks import dispatch_webhooks
from app.risk_providers.service import prune_external_risk_lookups
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
    # The process-wide engine is built here, first, so it takes the worker's
    # smaller pool; every job session in this process shares it.
    get_engine(
        settings.model_copy(update={"database_pool_size": settings.worker_database_pool_size})
    )
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
        run_campaigns,
        dispatch_webhooks,
        build_network_benchmarks,
        dispatch_automation,
        scan_segment_entries,
        process_integration_events,
        run_integration_syncs,
        schedule_integrations,
        sync_inventories,
        process_chat_messages,
        chat_order_housekeeping,
        dispatch_outbox,
        snapshot_demand_forecasts,
        prune_external_risk_lookups,
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
        # Campaigns: start scheduled sends, enrol flows and queue one minute's
        # worth of recipients; the dispatcher above does the sending (V3.3).
        cron(run_campaigns, second={20}),
        cron(dispatch_webhooks, second={10, 40}),
        cron(build_network_benchmarks, day=8, hour=3, minute=45, run_at_startup=True),
        # Workflow runs: new, retrying, woken by an event or at the end of a
        # delay. Waits are rows, so this one cron serves every workflow (V3.4).
        cron(dispatch_automation, second={12, 42}),
        # Customers who became inactive since yesterday (the one time-based
        # workflow trigger). 04:10 Dhaka.
        cron(scan_segment_entries, hour={22}, minute=10),
        # Integrations: queued webhook events every 15s, sync pages twice a
        # minute within a time budget, catch-up syncs and health every 5 min.
        cron(process_integration_events, second={3, 18, 33, 48}),
        # Chat-to-order: received Messenger/WhatsApp messages into drafts
        # every 15s; expiry and raw-message retention hourly.
        cron(process_chat_messages, second={8, 23, 38, 53}),
        cron(chat_order_housekeeping, minute={41}),
        cron(run_integration_syncs, second={25, 55}),
        cron(schedule_integrations, minute=set(range(0, 60, 5))),
        # Two-way stock: compare mapped items every 5 minutes (V3.2).
        cron(sync_inventories, minute=set(range(2, 60, 5))),
        # Frequent, cheap and idempotent: the dispatcher claims work with
        # SKIP LOCKED, so overlapping runs contend for nothing.
        cron(dispatch_outbox, second={0, 15, 30, 45}, run_at_startup=True),
        # Smart alerts, twice a day: 09:30 Dhaka so they are waiting when the
        # seller opens the app, and 15:30 so a courier account that broke in
        # the morning is not left until tomorrow. ARQ crons fire on host local
        # time (the deployment sets TZ=UTC). Extra runs cannot duplicate
        # anything: each alert identity is raised once and then held by its
        # cooldown (app.notifications.rules).
        # Demand forecasts, once a day just before the morning alert scan (08:50
        # Dhaka), so a predicted stock-out is on the snapshot the alert reads.
        # Idempotent per shop and day (V3.6).
        cron(snapshot_demand_forecasts, hour={2}, minute=50),
        # External risk lookups past retention (V3.7). Idempotent.
        cron(prune_external_risk_lookups, hour={4}, minute=20),
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
