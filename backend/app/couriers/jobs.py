"""Courier background jobs.

Brief sections 17, 47; master spec section 42.

Five jobs, all with the same shape and the same three guarantees:

*   **Cross-tenant, tenant-scoped inside.** Each runs on a system session
    because it spans shops, and installs each shop's tenant into the ambient
    context before touching its data — so a job's own queries are scoped exactly
    as a request's would be, and a bug cannot read across shops.

*   **Restartable and idempotent.** Running twice in the same minute — two
    workers, a restart, a manual trigger — must not double anything. Where that
    is not free, the underlying operation carries it: a booking attempt is
    recovered by a state check, a provider payment by a unique constraint, a
    poll by being a read.

*   **Bounded.** Every job takes a per-shop batch limit. One large shop must not
    be able to spend a whole run, and the provider must not be hammered.

A shop with no connected courier account is skipped entirely and costs nothing,
which is what keeps these cheap on a platform where manual courier mode is a
first-class path.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import sqlalchemy as sa

from app.common.provider_health import ProviderHealthService, ProviderKind
from app.consignments.service import ConsignmentService
from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.context import ActorType, RequestContext, clear_context, set_context
from app.core.logging import get_logger, log_duration
from app.core.security import CredentialVault
from app.couriers.accounts import CourierAccountService
from app.couriers.capabilities import Capability
from app.couriers.metrics import CourierMetric, record_metric
from app.couriers.models import CourierAccount, CourierAccountStatus
from app.couriers.recovery import BookingRecoveryService
from app.couriers.returns import CourierReturnService
from app.couriers.status_sync import StatusSyncService
from app.db.session import session_scope, system_session
from app.db.tenancy import allow_cross_tenant
from app.money.service import ReceivableService

__all__ = [
    "poll_courier_statuses",
    "recover_unknown_bookings",
    "refresh_courier_credentials",
    "sync_courier_payments",
    "sync_courier_returns",
]

log = get_logger("app.couriers.jobs")

PROVIDER = "steadfast"

#: Providers whose parcels the status job polls. Steadfast documents no
#: webhook, so polling is its whole sync path; RedX documents a status lookup,
#: and polling keeps its parcels current whether or not a shop has pasted the
#: callback URL into RedX. Pathao publishes no status lookup and is absent.
POLLED_PROVIDERS: tuple[str, ...] = (PROVIDER, "redx")


async def _connected_accounts(
    session: Any, provider: str = PROVIDER
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """``(tenant_id, account_id)`` for every shop with a usable courier account.

    Read under an explicit cross-tenant escape hatch, which logs its reason —
    this is the one query in the job that legitimately spans shops, and
    everything after it runs scoped to a single tenant.
    """
    with allow_cross_tenant("courier job: find connected accounts"):
        rows = await session.execute(
            sa.select(CourierAccount.tenant_id, CourierAccount.id).where(
                CourierAccount.provider == provider,
                CourierAccount.status == str(CourierAccountStatus.CONNECTED),
                CourierAccount.api_key_encrypted.is_not(None),
            )
        )
    return [(row[0], row[1]) for row in rows.all()]


async def _per_tenant(
    job_name: str,
    handler: Callable[[Any, uuid.UUID], Awaitable[dict[str, int]]],
    *,
    provider: str = PROVIDER,
) -> dict[str, int]:
    """Run ``handler`` once per shop with a connected account for ``provider``.

    One shop's failure never stops the others: the exception is logged with the
    shop it belongs to and the loop continues. A courier job that aborts on the
    first bad account would silently stop syncing every shop after it in the
    list.
    """
    totals: dict[str, int] = {"tenants": 0, "errors": 0}

    # Only the account listing spans shops. Each shop's work then gets its own
    # tenant-scoped session: the tenancy guards filter its reads to that shop
    # and stamp its inserts with it, and it commits or rolls back on its own.
    # A shared system session did neither: inserts reached the database with
    # no tenant, a per-shop lookup could see another shop's account, and one
    # shop's rollback undid the shops before it.
    async with system_session(f"worker: {job_name}") as listing:
        accounts = await _connected_accounts(listing, provider)

    for tenant_id, _account_id in accounts:
        token = set_context(
            RequestContext(
                trace_id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                actor_type=ActorType.SYSTEM,
                job_name=job_name,
            )
        )
        try:
            async with session_scope() as session:
                with log_duration(log, job_name, provider=provider):
                    result = await handler(session, tenant_id)
            for key, value in result.items():
                totals[key] = totals.get(key, 0) + value
            totals["tenants"] += 1
        except Exception as exc:
            totals["errors"] += 1
            log.error(
                "courier job failed for a shop",
                extra={
                    "provider": provider,
                    "job_name": job_name,
                    "error": type(exc).__name__,
                },
            )
        finally:
            clear_context(token)

    return totals


def _services(session: Any) -> tuple[CourierAccountService, ConsignmentService]:
    settings = get_settings()
    accounts = CourierAccountService(session, vault=CredentialVault(settings))
    consignments = ConsignmentService(session, receivables=ReceivableService(session))
    return accounts, consignments


# --------------------------------------------------------------- polling --


async def poll_courier_statuses(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Ask the provider about parcels whose status check is due.

    The production synchronisation path. Steadfast documents no webhook, so
    nothing else keeps a parcel's status current, and this job is written to be
    sufficient on its own rather than as a safety net under one. RedX parcels
    are polled the same way, one provider after the other, each only for shops
    with that provider connected.
    """
    settings = get_settings()

    def handler_for(provider: str) -> Callable[[Any, uuid.UUID], Awaitable[dict[str, int]]]:
        async def handle(session: Any, _tenant_id: uuid.UUID) -> dict[str, int]:
            accounts, consignments = _services(session)
            sync = StatusSyncService(
                session, accounts=accounts, consignments=consignments, settings=settings
            )
            report = await sync.poll_due(provider=provider)
            lag = await sync.sync_lag_seconds(provider=provider)
            if lag:
                record_metric(CourierMetric.STATUS_SYNC_LAG, value=lag, provider=provider)
            return {
                "checked": report.checked,
                "changed": report.changed,
                "settled": report.settled,
                "poll_errors": report.errors,
                "unknown_status": report.unknown_status,
                "needs_quantities": report.needs_quantities,
            }

        return handle

    totals: dict[str, int] = {}
    for provider in POLLED_PROVIDERS:
        result = await _per_tenant(
            "courier:poll_statuses", handler_for(provider), provider=provider
        )
        for key, value in result.items():
            totals[key] = totals.get(key, 0) + value
    return totals


# -------------------------------------------------------------- recovery --


async def recover_unknown_bookings(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Resolve bookings whose outcome was never confirmed.

    The most important job in this module. Every attempt it leaves unresolved
    is an order a seller cannot book and a parcel that may or may not be on its
    way, so it runs often and backs off per attempt rather than per run.
    """
    settings = get_settings()

    async def handle(session: Any, _tenant_id: uuid.UUID) -> dict[str, int]:
        accounts, consignments = _services(session)
        recovery = BookingRecoveryService(
            session, accounts=accounts, consignments=consignments, settings=settings
        )
        attempts = await recovery.due_attempts()
        counts = {"recovered": 0, "inconclusive": 0, "manual_review": 0}
        for attempt in attempts:
            result = await recovery.recover(attempt)
            key = {
                "RECOVERED": "recovered",
                "INCONCLUSIVE": "inconclusive",
                "MANUAL_REVIEW": "manual_review",
            }.get(str(result.outcome))
            if key:
                counts[key] += 1
        return counts

    return await _per_tenant("courier:recover_bookings", handle)


# --------------------------------------------------------------- returns --


async def sync_courier_returns(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Refresh return requests that have not finished."""

    async def handle(session: Any, _tenant_id: uuid.UUID) -> dict[str, int]:
        accounts, _ = _services(session)
        returns = CourierReturnService(session, accounts=accounts)
        changed = await returns.sync_open_requests(provider=PROVIDER)
        return {"returns_changed": changed}

    return await _per_tenant("courier:sync_returns", handle)


# -------------------------------------------------------------- payments --


async def sync_courier_payments(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Import provider payments and hand them to reconciliation."""
    from app.couriers.payments import PaymentSyncService

    settings = get_settings()

    async def handle(session: Any, _tenant_id: uuid.UUID) -> dict[str, int]:
        accounts, _ = _services(session)
        sync = PaymentSyncService(session, accounts=accounts, settings=settings)
        report = await sync.sync(provider=PROVIDER)
        return {
            "payments_seen": report.seen,
            "payments_imported": report.imported,
            "payments_changed": report.changed,
            "payment_errors": report.errors,
        }

    return await _per_tenant("courier:sync_payments", handle)


# ------------------------------------------------------------ credentials --


async def refresh_courier_credentials(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Re-check stored credentials on a slow cadence.

    Catches a key revoked at the provider before a seller discovers it
    mid-booking. Uses the safest documented read, and — because the check is
    not conclusive when the provider is merely unreachable — a failure here can
    only ever move an account to ``NEEDS_RECONNECT`` through the same
    three-strikes rule the interactive path uses.
    """

    async def handle(session: Any, _tenant_id: uuid.UUID) -> dict[str, int]:
        accounts, _ = _services(session)
        health = ProviderHealthService(session)
        if not await health.allows(PROVIDER, capability=str(Capability.CREDENTIAL_VALIDATION)):
            return {"credential_checks": 0}
        outcome = await accounts.test_connection(PROVIDER)
        record_metric(
            CourierMetric.CREDENTIAL_CONNECTED,
            provider=PROVIDER,
            result=str(outcome.result),
        )
        return {"credential_checks": 1}

    return await _per_tenant("courier:refresh_credentials", handle)


async def courier_health_snapshot() -> dict[str, Any]:
    """Provider health as the ops screen reads it.

    Not a job — a read, exposed here so the admin console and the smoke tool
    both use one implementation.
    """
    async with system_session("ops: courier health snapshot") as session:
        health = ProviderHealthService(session)
        views = await health.snapshot(tenant_id=None)
        return {
            "as_of": utc_now().isoformat(),
            "providers": [
                view.as_dict() for view in views if view.kind == str(ProviderKind.COURIER)
            ],
        }
