"""Intake workers and outbox handlers (V4 WP1.3).

* ``scan_device_health`` (hourly): a linked phone or extension that has sent
  no heartbeat for 24 hours shows ``NEEDS_ATTENTION`` in the Integrations Hub,
  and the shop is told at most once per 24 hours per device. The connection
  stays ``CONNECTED`` so the device can report back; its next heartbeat clears
  the state (:func:`app.device_links.service.heartbeat`).
* ``intake.draft_ready`` handler: the fact that a draft is ready for review.
  Phase 4 (Autopilot) starts its recipe from it; until then it is recorded.

Codes and ids only: no message text, sender name or phone is logged.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.outbox import OutboxEvent, OutboxTopic
from app.core.clock import utc_now
from app.core.context import ActorType, RequestContext, clear_context, set_context
from app.core.logging import get_logger
from app.db.session import session_scope, system_session
from app.integrations.models import IntegrationConnection
from app.worker.jobs import register_handler

__all__ = ["NEEDS_ATTENTION", "STALE_AFTER", "check_device", "scan_device_health"]

log = get_logger(__name__)

NEEDS_ATTENTION = "NEEDS_ATTENTION"
HEARTBEAT_STALE = "HEARTBEAT_STALE"
#: No heartbeat for this long and the device needs the seller's attention.
STALE_AFTER = timedelta(hours=24)
#: At most one notification per device in this window.
NOTIFY_EVERY = timedelta(hours=24)


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def check_device(db: AsyncSession, conn: IntegrationConnection, now: datetime) -> str:
    """``OK``, ``STALE`` (already told) or ``NOTIFIED``; ``SKIPPED`` for the
    shop's own share/paste connection, which has no device behind it."""
    from app.intake.service import MANUAL_PLATFORM

    config = dict(conn.config or {})
    if config.get("platform") == MANUAL_PLATFORM:
        return "SKIPPED"
    last = _when(config.get("last_heartbeat_at")) or conn.created_at
    if now - last < STALE_AFTER:
        return "OK"
    outcome = "STALE"
    if config.get("health") != NEEDS_ATTENTION:
        config["health"] = NEEDS_ATTENTION
        conn.last_error_at, conn.last_error_code = now, HEARTBEAT_STALE
    told = _when(config.get("stale_notified_at"))
    if told is None or now - told >= NOTIFY_EVERY:
        await _notify(db, conn, now)
        config["stale_notified_at"] = now.isoformat()
        outcome = "NOTIFIED"
    conn.config = config  # a new dict, so the JSON change is tracked
    await db.flush()
    return outcome


async def _notify(db: AsyncSession, conn: IntegrationConnection, now: datetime) -> None:
    from app.notifications.models import NotificationKind, Severity
    from app.notifications.service import NotificationService
    from app.notifications.templates import render

    params = {"platform": str((conn.config or {}).get("platform") or "android")}
    title, body = render(NotificationKind.DEVICE_NEEDS_ATTENTION, params, "en") or ("", "")
    await NotificationService(db).notify(
        kind=NotificationKind.DEVICE_NEEDS_ATTENTION,
        severity=Severity.ACTION,
        title=title,
        body=body,
        dedupe_key=f"device-stale:{conn.id}:{now.isoformat()}",
        entity_type="integration_connection",
        entity_id=conn.id,
        payload={"params": params, "target": {"route": "integrations"}},
        occurred_at=now,
    )


def _enter(tenant_id: uuid.UUID) -> Any:
    return set_context(
        RequestContext(
            trace_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            actor_type=ActorType.SYSTEM,
            job_name="intake_device_health",
        )
    )


async def scan_device_health(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    from app.device_links.service import DEVICE_PROVIDER

    now = utc_now()
    async with system_session("intake: device health") as db:
        rows = (
            await db.execute(
                sa.select(IntegrationConnection.tenant_id, IntegrationConnection.id).where(
                    IntegrationConnection.provider == DEVICE_PROVIDER,
                    IntegrationConnection.state == "CONNECTED",
                )
            )
        ).all()
    counts = {"checked": 0, "stale": 0, "notified": 0, "failed": 0}
    for tenant_id, connection_id in rows:
        token = _enter(tenant_id)
        try:
            async with session_scope() as db:
                conn = await db.scalar(
                    sa.select(IntegrationConnection)
                    .where(IntegrationConnection.id == connection_id)
                    .with_for_update()
                )
                if conn is None or conn.state != "CONNECTED":
                    continue
                outcome = await check_device(db, conn, now)
            counts["checked"] += 1
            if outcome in ("STALE", "NOTIFIED"):
                counts["stale"] += 1
            if outcome == "NOTIFIED":
                counts["notified"] += 1
        except Exception as exc:
            counts["failed"] += 1
            log.warning(
                "device health check failed",
                extra={"connection_id": str(connection_id), "error": type(exc).__name__},
            )
        finally:
            clear_context(token)
    log.info("device health scan finished", extra=counts)
    return counts


@register_handler(OutboxTopic.INTAKE_DRAFT_READY)
async def _on_draft_ready(_session: AsyncSession, event: OutboxEvent) -> None:
    """Record that a draft is ready. Phase 4's Autopilot recipe starts here;
    nothing is converted, confirmed or booked from this handler."""
    payload = event.payload or {}
    log.info(
        "intake draft ready",
        extra={
            "draft_id": str(payload.get("draft_id") or ""),
            "capture_source": str(payload.get("capture_source") or ""),
            "tenant_id": str(event.tenant_id or ""),
        },
    )
