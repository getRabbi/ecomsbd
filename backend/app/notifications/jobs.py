"""Scheduled alert jobs (master spec section 42).

Two crons, both of which run over every active shop:

*   :func:`scan_alerts` — the V2.2 smart-alert pass (payout overdue,
    discrepancies, stuck parcels, RTO, stock, margin, imports, courier
    accounts); see :mod:`app.notifications.smart`.
*   :func:`send_weekly_summaries` — the Friday 18:00 Asia/Dhaka summary.

The weekly job runs *hourly* and decides for itself whether the Dhaka clock has
reached Friday evening, rather than being pinned to one UTC hour. ARQ's cron
fires on the worker host's local time, and a summary that silently arrives on
Saturday morning because a container was deployed with the wrong ``TZ`` is the
kind of failure nobody notices until a seller mentions it months later. Timing
lives in the code, in the tenant's timezone, where it can be tested.

Sending twice is harmless: the summary deduplicates on its week-ending date, so
an hourly job that already sent this week's summary does nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa

from app.core.clock import FRIDAY, business_date, tenant_now, utc_now
from app.core.context import ActorType, RequestContext, clear_context, set_context
from app.core.logging import get_logger
from app.db.session import session_scope, system_session
from app.notifications.alerts import AlertService
from app.notifications.models import NotificationKind
from app.notifications.service import NotificationService
from app.tenants.models import Tenant, TenantStatus

log = get_logger("app.worker.alerts")

__all__ = [
    "SUMMARY_HOUR",
    "scan_alerts",
    "send_weekly_summaries",
]

#: 18:00 in the tenant's own timezone (master spec section 42).
SUMMARY_HOUR = 18


async def _active_tenants() -> list[tuple[uuid.UUID, str]]:
    """Every shop the jobs should run for, with its timezone.

    Suspended and pending-deletion shops are skipped: nobody is going to act
    on the alert, and writing to them keeps producing rows for an account that
    is on its way out.
    """
    async with system_session("worker: enumerate tenants for alerts") as session:
        rows = await session.execute(
            sa.select(Tenant.id, Tenant.timezone).where(Tenant.status == str(TenantStatus.ACTIVE))
        )
        return [(tenant_id, timezone) for tenant_id, timezone in rows]


def _context(tenant_id: uuid.UUID, job_name: str) -> RequestContext:
    return RequestContext(
        trace_id=uuid.uuid4().hex,
        tenant_id=tenant_id,
        actor_type=ActorType.SYSTEM,
        job_name=job_name,
    )


async def scan_alerts(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Raise today's alerts for every active shop.

    One shop failing does not stop the rest: each runs in its own session and
    its own transaction, so a bad row in one seller's data cannot cost every
    other seller their morning alerts.
    """
    created = failed = 0

    for tenant_id, _timezone in await _active_tenants():
        token = set_context(_context(tenant_id, "alerts:daily"))
        try:
            async with session_scope() as session:
                created += await AlertService(session).raise_daily_alerts()
        except Exception as exc:
            failed += 1
            log.exception(
                "daily alert scan failed for one shop",
                extra={"tenant_id": str(tenant_id), "error": f"{type(exc).__name__}: {exc}"},
            )
        finally:
            clear_context(token)

    return {"created": created, "failed": failed}


async def send_weekly_summaries(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Send the Friday summary to every shop whose local clock says it is time.

    ``sent`` counts shops the summary was newly written for. A shop that
    already has this week's summary is counted in ``skipped``, which is the
    normal result for every run after the first on a Friday evening — the
    summary's aggregate queries are not run again just to be discarded.
    """
    now = utc_now()
    sent = skipped = failed = 0

    for tenant_id, timezone in await _active_tenants():
        local = tenant_now(timezone, at=now)
        if local.weekday() != FRIDAY or local.hour < SUMMARY_HOUR:
            skipped += 1
            continue

        week_end = business_date(timezone, at=now)
        token = set_context(_context(tenant_id, "alerts:weekly-summary"))
        try:
            async with session_scope() as session:
                notifications = NotificationService(session)
                if await notifications.exists(
                    NotificationKind.WEEKLY_SUMMARY, f"weekly:{week_end.isoformat()}"
                ):
                    skipped += 1
                    continue
                alerts = AlertService(session, notifications=notifications)
                summary = await alerts.send_weekly_summary(week_end=week_end)
                sent += 1
                log.info(
                    "weekly summary sent",
                    extra={
                        "tenant_id": str(tenant_id),
                        "week_end": summary.week_end.isoformat(),
                        "contribution_profit_paisa": summary.contribution_profit_paisa,
                    },
                )
        except Exception as exc:
            failed += 1
            log.exception(
                "weekly summary failed for one shop",
                extra={"tenant_id": str(tenant_id), "error": f"{type(exc).__name__}: {exc}"},
            )
        finally:
            clear_context(token)

    return {"sent": sent, "skipped": skipped, "failed": failed}
