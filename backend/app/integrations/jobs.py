"""Integration workers.

Each unit of work is split the same way: read what is needed in one short
transaction, talk to the provider with no transaction open, then write the
result in a second transaction that first checks nobody else already did.
Orders are written only through ``service.import_order``, so a retried event,
a resumed sync page and a webhook for an order the sync already imported all
end at the same V2 dedupe.
"""

from __future__ import annotations

import time
import uuid
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult

from app.core.clock import utc_now
from app.core.context import ActorType, RequestContext, clear_context, set_context
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.db.session import session_scope, system_session
from app.integrations import service
from app.integrations.http import ProviderError
from app.integrations.models import IntegrationConnection, IntegrationEvent, IntegrationSyncRun
from app.order_sources.models import ExternalOrder

log = get_logger(__name__)

BACKOFF = (60, 300, 900, 3600)
INCREMENTAL_EVERY = timedelta(minutes=15)
INCREMENTAL_OVERLAP = timedelta(minutes=10)
HEALTH_EVERY = timedelta(hours=1)
KEEP = timedelta(days=30)
SYNC_BUDGET_SECONDS = 20.0


def _backoff(attempts: int) -> datetime:
    return utc_now() + timedelta(seconds=BACKOFF[min(attempts, len(BACKOFF)) - 1])


def _enter(tenant_id: uuid.UUID) -> Any:
    return set_context(
        RequestContext(
            trace_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            actor_type=ActorType.SYSTEM,
            job_name="integrations",
        )
    )


# --------------------------------------------------------------- events ---


async def process_event(tenant_id: uuid.UUID, event_id: uuid.UUID) -> None:
    token = _enter(tenant_id)
    try:
        async with session_scope() as db:
            event = await db.scalar(
                sa.select(IntegrationEvent).where(IntegrationEvent.id == event_id).with_for_update()
            )
            if (
                event is None
                or event.status != "QUEUED"
                or (event.next_attempt_at and event.next_attempt_at > utc_now())
            ):
                return
            conn = await service.get(db, event.connection_id)
            if conn.state != "CONNECTED" or not event.external_ref:
                event.attempts += 1
                if conn.state == "AUTH_EXPIRED" and event.external_ref:
                    # Kept retryable: after a reconnect the seller retries it.
                    await service.record_issue(
                        db,
                        conn,
                        kind=event.kind,
                        topic=event.topic,
                        code="CONNECTION_NOT_ACTIVE",
                        external_ref=event.external_ref,
                        event=event,
                    )
                else:
                    event.status, event.code = "IGNORED", "CONNECTION_NOT_ACTIVE"
                return
            existing = await db.scalar(
                sa.select(ExternalOrder.order_id).where(
                    ExternalOrder.source_id == conn.source_id,
                    ExternalOrder.external_order_id == event.external_ref,
                )
            )
            if existing is not None and event.hint != "cancelled":
                # Nothing a provider read could change: no call is spent on it.
                event.attempts += 1
                event.status, event.code, event.order_id = "IGNORED", "DUPLICATE_IGNORED", existing
                return
            attempts, ref, connection_id = event.attempts, event.external_ref, conn.id
        failure: ProviderError | None = None
        node: dict[str, Any] | None = None
        try:
            link, credentials = await service.fresh_credentials(connection_id)
            node = await service.fetch_order(link, credentials, ref)
        except ProviderError as exc:
            failure = exc
        async with session_scope() as db:
            event = await db.scalar(
                sa.select(IntegrationEvent).where(IntegrationEvent.id == event_id).with_for_update()
            )
            if event is None or event.status != "QUEUED" or event.attempts != attempts:
                return
            conn = await service.get(db, connection_id)
            event.attempts += 1
            if failure is not None:
                service.mark_error(conn, failure.code)
                if failure.transient and event.attempts < service.AUTO_RETRY_LIMIT:
                    event.code, event.next_attempt_at = failure.code, _backoff(event.attempts)
                    return
                await service.record_issue(
                    db,
                    conn,
                    kind=event.kind,
                    topic=event.topic,
                    code=failure.code,
                    external_ref=ref,
                    event=event,
                )
                return
            outcome = await service.import_order(db, conn, node or {}, kind=event.kind, event=event)
            if outcome.status != "FAILED":
                event.status, event.code, event.order_id = (
                    outcome.status,
                    outcome.code,
                    outcome.order_id,
                )
            service.mark_ok(conn)
    finally:
        clear_context(token)


async def process_integration_events(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    async with system_session("integrations: due events") as db:
        rows = (
            await db.execute(
                sa.select(IntegrationEvent.tenant_id, IntegrationEvent.id)
                .where(
                    IntegrationEvent.status == "QUEUED",
                    IntegrationEvent.next_attempt_at <= utc_now(),
                )
                .order_by(IntegrationEvent.next_attempt_at)
                .limit(50)
            )
        ).all()
    for tenant_id, event_id in rows:
        try:
            await process_event(tenant_id, event_id)
        except Exception:
            log.exception("integration event failed", extra={"event_id": str(event_id)})
    return {"processed": len(rows)}


# ---------------------------------------------------------------- syncs ---


async def sync_step(tenant_id: uuid.UUID, run_id: uuid.UUID) -> bool:
    """Import one provider page. True when another page is ready now."""
    token = _enter(tenant_id)
    try:
        async with session_scope() as db:
            run = await db.scalar(
                sa.select(IntegrationSyncRun)
                .where(IntegrationSyncRun.id == run_id)
                .with_for_update()
            )
            if (
                run is None
                or run.status not in {"QUEUED", "RUNNING"}
                or run.next_run_at > utc_now()
            ):
                return False
            conn = await service.get(db, run.connection_id)
            if conn.state != "CONNECTED":
                run.status, run.last_error_code = "FAILED", "CONNECTION_NOT_ACTIVE"
                run.finished_at = utc_now()
                return False
            run.status = "RUNNING"
            run.started_at = run.started_at or utc_now()
            conn.sync_state = "RUNNING"
            cursor, pages, connection_id = run.cursor, run.pages, conn.id
        failure: ProviderError | None = None
        found: tuple[list[dict[str, Any]], str | None, int | None] = ([], None, None)
        try:
            link, credentials = await service.fresh_credentials(connection_id)
            found = await service.fetch_page(link, credentials, run)
        except ProviderError as exc:
            failure = exc
        async with session_scope() as db:
            run = await db.scalar(
                sa.select(IntegrationSyncRun)
                .where(IntegrationSyncRun.id == run_id)
                .with_for_update()
            )
            if run is None or run.status != "RUNNING" or run.cursor != cursor or run.pages != pages:
                return False
            conn = await service.get(db, connection_id)
            if failure is not None:
                run.attempts += 1
                run.last_error_code = failure.code
                service.mark_error(conn, failure.code)
                if failure.transient and run.attempts < service.AUTO_RETRY_LIMIT:
                    run.next_run_at = _backoff(run.attempts)
                    return False
                run.status, run.finished_at = "FAILED", utc_now()
                conn.sync_state = "FAILING"
                if failure.code in service.RECONNECT:
                    await service.record_issue(
                        db, conn, kind="AUTH", topic="sync.auth", code=failure.code
                    )
                return False
            nodes, next_cursor, total = found
            for node in nodes:
                try:
                    outcome = await service.import_order(db, conn, node, kind="SYNC")
                except ProviderError:
                    run.skipped += 1
                    continue
                if outcome.status == "PROCESSED":
                    run.imported += 1
                elif outcome.code == "DUPLICATE_IGNORED":
                    run.duplicates += 1
                elif outcome.status == "IGNORED":
                    run.skipped += 1
                else:
                    run.failed += 1
            # Committed with the page's orders: an interrupted run resumes here.
            run.cursor, run.pages, run.attempts = next_cursor, run.pages + 1, 0
            run.total = total if total is not None else run.total
            run.last_error_code = None
            service.mark_ok(conn)
            if next_cursor is not None:
                return True
            run.status, run.finished_at = "COMPLETED", utc_now()
            conn.sync_state = "IDLE"
            conn.last_sync_at = utc_now()
            if run.kind == "INCREMENTAL":
                conn.config = {**conn.config, "high_water": run.until.isoformat()}
            return False
    finally:
        clear_context(token)


async def run_integration_syncs(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    async with system_session("integrations: due syncs") as db:
        rows = (
            await db.execute(
                sa.select(IntegrationSyncRun.tenant_id, IntegrationSyncRun.id)
                .where(
                    IntegrationSyncRun.status.in_(["QUEUED", "RUNNING"]),
                    IntegrationSyncRun.next_run_at <= utc_now(),
                )
                .order_by(IntegrationSyncRun.next_run_at)
                .limit(10)
            )
        ).all()
    deadline, steps = time.monotonic() + SYNC_BUDGET_SECONDS, 0
    for tenant_id, run_id in rows:
        try:
            while await sync_step(tenant_id, run_id):
                steps += 1
                if time.monotonic() > deadline:
                    break
        except Exception:
            log.exception("integration sync step failed", extra={"run_id": str(run_id)})
        steps += 1
        if time.monotonic() > deadline:
            break
    return {"steps": steps}


# ----------------------------------------------------------- scheduling ---


async def maintain(tenant_id: uuid.UUID, connection_id: uuid.UUID) -> None:
    """Queue the catch-up sync and re-check health when each is due."""
    token = _enter(tenant_id)
    try:
        async with session_scope() as db:
            conn = await service.get(db, connection_id, lock=True)
            if conn.state != "CONNECTED":
                return
            now = utc_now()
            config = conn.config
            if conn.provider in service.ORDER_PROVIDERS:
                last = config.get("incremental_at")
                if not last or now - datetime.fromisoformat(last) >= INCREMENTAL_EVERY:
                    mark = config.get("high_water") or config.get("import_from") or now.isoformat()
                    # An import already running covers this window too.
                    with suppress(ConflictError):
                        await service.start_sync(
                            db,
                            conn,
                            kind="INCREMENTAL",
                            since=datetime.fromisoformat(mark) - INCREMENTAL_OVERLAP,
                            until=now,
                            actor_id=None,
                        )
                    conn.config = {**conn.config, "incremental_at": now.isoformat()}
            checked = conn.config.get("checked_at")
            if conn.provider != "CUSTOM_WEBSITE" and (
                not checked or now - datetime.fromisoformat(checked) >= HEALTH_EVERY
            ):
                await service.health_check(db, conn)
    finally:
        clear_context(token)


async def schedule_integrations(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    async with system_session("integrations: schedule") as db:
        rows = (
            await db.execute(
                sa.select(IntegrationConnection.tenant_id, IntegrationConnection.id)
                .where(
                    IntegrationConnection.state == "CONNECTED",
                    IntegrationConnection.provider != "CUSTOM_WEBSITE",
                )
                .order_by(IntegrationConnection.updated_at)
                .limit(500)
            )
        ).all()
        stale = (
            sa.select(IntegrationEvent.id)
            .where(
                IntegrationEvent.status.in_(["PROCESSED", "IGNORED", "RESOLVED"]),
                IntegrationEvent.updated_at < utc_now() - KEEP,
            )
            .limit(1000)
            .scalar_subquery()
        )
        deleted = await db.execute(
            sa.delete(IntegrationEvent).where(IntegrationEvent.id.in_(stale))
        )
        pruned = cast("CursorResult[Any]", deleted).rowcount
    for tenant_id, connection_id in rows:
        try:
            await maintain(tenant_id, connection_id)
        except Exception:
            log.exception(
                "integration maintenance failed", extra={"connection_id": str(connection_id)}
            )
    return {"connections": len(rows), "pruned": pruned or 0}
