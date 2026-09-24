"""Connection lifecycle, health and the bridge into the V2 order pipeline.

Every order a connector brings in goes provider -> ``to_native`` (normalize) ->
``order_sources.service.ingest`` (validate, dedupe, the V2 order service). The
hub adds no second order domain and no second dedupe table.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_vault
from app.common.audit import AuditAction, record_audit
from app.common.operation_lock import lock_shop
from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.errors import (
    ConflictError,
    EntitlementRequiredError,
    NotFoundError,
    ValidationError,
)
from app.db.session import session_scope
from app.db.tenancy import allow_cross_tenant
from app.integrations import meta, shopify, whatsapp, woocommerce
from app.integrations.http import ProviderError
from app.integrations.models import IntegrationConnection, IntegrationEvent, IntegrationSyncRun
from app.integrations.normalize import Reject, Skip
from app.order_sources.models import ExternalOrder, OrderSource
from app.order_sources.service import ingest

PROVIDERS = ("SHOPIFY", "WOOCOMMERCE", "CUSTOM_WEBSITE", "MESSENGER", "WHATSAPP")
ORDER_PROVIDERS = frozenset({"SHOPIFY", "WOOCOMMERCE"})
CONNECTORS: dict[str, Any] = {"SHOPIFY": shopify, "WOOCOMMERCE": woocommerce}
MAX_CONNECTIONS = 20
STATE_TTL = timedelta(minutes=15)
#: Failures a person can clear by fixing the order or the store, then retrying.
RETRYABLE = frozenset(
    {
        "MISSING_PHONE",
        "INVALID_PHONE",
        "NO_ITEMS",
        "CURRENCY_NOT_BDT",
        "ORDER_REJECTED_BY_MAPPING",
        "CUSTOMER_BLOCKED",
        "ORDER_LIMIT_REACHED",
        "CONNECTION_NOT_ACTIVE",
        "AUTH_EXPIRED",
        "PERMISSION_MISSING",
        "NOT_FOUND_AT_PROVIDER",
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_RATE_LIMITED",
        "PROVIDER_REJECTED",
        "STORE_UNREACHABLE",
        "STORE_REDIRECTED",
        "MALFORMED_RESPONSE",
    }
)
#: Connection-level problems whose fix is signing in to the provider again.
RECONNECT = frozenset({"AUTH_EXPIRED", "PERMISSION_MISSING", "APP_UNINSTALLED"})
AUTO_RETRY_LIMIT = 5


# --------------------------------------------------------------- basics ---


def availability(provider: str) -> dict[str, Any]:
    blocker = None
    if provider == "SHOPIFY" and not shopify.configured():
        blocker = "SHOPIFY_APP_SETUP_REQUIRED"
    if provider in {"MESSENGER", "WHATSAPP"} and not meta.configured():
        blocker = "META_APP_SETUP_REQUIRED"
    result: dict[str, Any] = {
        "provider": provider,
        "available": blocker is None,
        "blocker": blocker,
    }
    if provider == "WOOCOMMERCE":
        result["one_click"] = woocommerce.auth_endpoint_available()
    return result


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_state(conn: IntegrationConnection) -> str:
    raw = secrets.token_urlsafe(32)
    conn.state_hash, conn.state_expires_at = digest(raw), utc_now() + STATE_TTL
    return raw


def seal(conn: IntegrationConnection, data: dict[str, Any] | None) -> None:
    conn.credentials_enc = (
        get_vault().encrypt(json.dumps(data), context=f"integration:{conn.id}") if data else None
    )


def unseal(conn: IntegrationConnection) -> dict[str, Any]:
    if not conn.credentials_enc:
        return {}
    return json.loads(get_vault().decrypt(conn.credentials_enc, context=f"integration:{conn.id}"))


def webhook_secret(conn: IntegrationConnection) -> str | None:
    if not conn.webhook_secret_enc:
        return None
    return get_vault().decrypt(conn.webhook_secret_enc, context=f"integration-hook:{conn.id}")


def webhook_url(conn: IntegrationConnection) -> str:
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/v1/webhooks/integrations/{conn.provider.lower()}/{conn.webhook_token}"


def can_receive_webhooks() -> bool:
    return get_settings().public_base_url.startswith("https://")


async def get(
    db: AsyncSession, connection_id: uuid.UUID, *, lock: bool = False
) -> IntegrationConnection:
    query = sa.select(IntegrationConnection).where(IntegrationConnection.id == connection_id)
    row = await db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise NotFoundError()
    return row


async def by_state(db: AsyncSession, provider: str, raw: str) -> IntegrationConnection | None:
    """Find a pending OAuth by its state, across shops: a callback has no session."""
    if not raw or len(raw) > 200:
        return None
    with allow_cross_tenant(f"{provider} OAuth state resolution"):
        row = await db.scalar(
            sa.select(IntegrationConnection).where(
                IntegrationConnection.state_hash == digest(raw),
                IntegrationConnection.provider == provider,
            )
        )
    if row is None or row.state_expires_at is None or row.state_expires_at < utc_now():
        return None
    return row


async def by_token(db: AsyncSession, provider: str, token: str) -> IntegrationConnection | None:
    if not token or len(token) > 64:
        return None
    with allow_cross_tenant(f"{provider} webhook connection resolution"):
        return await db.scalar(
            sa.select(IntegrationConnection).where(
                IntegrationConnection.webhook_token == token,
                IntegrationConnection.provider == provider,
            )
        )


async def by_account(db: AsyncSession, provider: str, account: str) -> list[IntegrationConnection]:
    with allow_cross_tenant(f"{provider} account resolution"):
        return list(
            (
                await db.scalars(
                    sa.select(IntegrationConnection).where(
                        IntegrationConnection.provider == provider,
                        IntegrationConnection.account_key == account,
                    )
                )
            ).all()
        )


async def ensure_unlinked(db: AsyncSession, conn: IntegrationConnection, account: str) -> None:
    """Refuse an account another shop holds, or a different one than this row linked."""
    linked = conn.config.get("account")
    if linked and linked != account:
        raise ValidationError(
            "This connection belongs to a different store. Add a new connection instead.",
            details={"code": "ACCOUNT_MISMATCH"},
        )
    for other in await by_account(db, conn.provider, account):
        if other.id != conn.id:
            raise ConflictError(
                "This store is already connected to another ecomsbd shop",
                details={"code": "ALREADY_LINKED"},
            )


async def link(
    db: AsyncSession, conn: IntegrationConnection, account: str, name: str | None
) -> None:
    await ensure_unlinked(db, conn, account)
    conn.account_key, conn.account_id = account, account
    conn.account_name = (name or account)[:200]
    conn.config = {**conn.config, "account": account}
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        conn.account_key = None
        raise ConflictError(
            "This store is already connected to another ecomsbd shop",
            details={"code": "ALREADY_LINKED"},
        ) from exc


async def ensure_source(db: AsyncSession, conn: IntegrationConnection) -> OrderSource:
    source = await db.get(OrderSource, conn.source_id) if conn.source_id else None
    if source is None:
        label = {"SHOPIFY": "Shopify", "WOOCOMMERCE": "WooCommerce"}.get(conn.provider, "Website")
        source = OrderSource(
            name=f"{label} · {conn.account_name or conn.name}"[:120],
            provider=conn.provider if conn.provider in ORDER_PROVIDERS else "CUSTOM_PUSH",
            enabled=True,
            mapping={},
        )
        db.add(source)
        await db.flush()
        conn.source_id = source.id
    source.enabled = True
    return source


async def set_source_enabled(db: AsyncSession, conn: IntegrationConnection, enabled: bool) -> None:
    if conn.source_id:
        source = await db.get(OrderSource, conn.source_id)
        if source is not None:
            source.enabled = enabled


def mark_ok(conn: IntegrationConnection, *, webhook: bool = False, sync: bool = False) -> None:
    now = utc_now()
    conn.last_success_at = now
    if webhook:
        conn.last_webhook_at, conn.webhook_state = now, "ACTIVE"
    if sync:
        conn.last_sync_at = now


def mark_error(conn: IntegrationConnection, code: str) -> None:
    conn.last_error_at, conn.last_error_code = utc_now(), code
    if code in RECONNECT and conn.state == "CONNECTED":
        conn.state = "AUTH_EXPIRED"


async def audit(
    db: AsyncSession, action: AuditAction, conn: IntegrationConnection, **context: Any
) -> None:
    await record_audit(
        db,
        action,
        entity_type="integration",
        entity_id=conn.id,
        context={"provider": conn.provider, **context},
    )


# --------------------------------------------------------------- issues ---


async def record_issue(
    db: AsyncSession,
    conn: IntegrationConnection,
    *,
    kind: str,
    topic: str,
    code: str,
    external_ref: str | None = None,
    order_id: uuid.UUID | None = None,
    event: IntegrationEvent | None = None,
) -> IntegrationEvent:
    """One open row per (problem, order): a failing store does not flood the list.

    ``event`` is the webhook delivery being processed. It becomes the issue,
    or is marked ``COALESCED`` when an open issue already says the same thing.
    """
    query = sa.select(IntegrationEvent).where(
        IntegrationEvent.connection_id == conn.id,
        IntegrationEvent.status == "FAILED",
        IntegrationEvent.code == code,
        IntegrationEvent.external_ref == external_ref
        if external_ref
        else IntegrationEvent.external_ref.is_(None),
    )
    if event is not None:
        query = query.where(IntegrationEvent.id != event.id)
    existing = await db.scalar(query.order_by(IntegrationEvent.created_at.desc()).limit(1))
    if existing is not None:
        existing.attempts += 1
        existing.order_id = order_id or existing.order_id
        if event is not None:
            event.status, event.code = "IGNORED", "COALESCED"
        return existing
    if event is not None:
        event.status, event.code = "FAILED", code
        event.order_id = order_id or event.order_id
        await _announce_issue(db, conn, event)
        return event
    row = IntegrationEvent(
        connection_id=conn.id,
        provider=conn.provider,
        kind=kind,
        topic=topic,
        external_ref=external_ref,
        status="FAILED",
        code=code,
        attempts=1,
        order_id=order_id,
    )
    db.add(row)
    await db.flush()
    await _announce_issue(db, conn, row)
    return row


async def _announce_issue(
    db: AsyncSession, conn: IntegrationConnection, row: IntegrationEvent
) -> None:
    """A new open problem is a fact workflows may react to (V3.4).

    Once per problem: a repeat of the same problem is coalesced above and never
    reaches here. Codes only; no provider body or credential.
    """
    from app.common.outbox import OutboxTopic, enqueue

    await enqueue(
        db,
        OutboxTopic.INTEGRATION_ISSUE_OPENED,
        {
            "integration_event_id": str(row.id),
            "connection_id": str(conn.id),
            "provider": conn.provider,
            "code": row.code,
        },
        tenant_id=conn.tenant_id,
    )


async def resolve_ref(db: AsyncSession, conn: IntegrationConnection, external_ref: str) -> None:
    """An order that imported clears the earlier failures about it."""
    rows = (
        await db.scalars(
            sa.select(IntegrationEvent).where(
                IntegrationEvent.connection_id == conn.id,
                IntegrationEvent.external_ref == external_ref,
                IntegrationEvent.status == "FAILED",
                IntegrationEvent.code != "ORDER_CANCELLED_AT_PROVIDER",
            )
        )
    ).all()
    for row in rows:
        row.status, row.resolved_at = "RESOLVED", utc_now()


def retryable(event: IntegrationEvent) -> bool:
    if event.status != "FAILED" or event.code not in RETRYABLE:
        return False
    if event.kind == "OUTBOUND":
        return True  # an outbound push re-reads current state before it runs
    return event.provider in ORDER_PROVIDERS and bool(event.external_ref)


async def retry_event(db: AsyncSession, event: IntegrationEvent) -> None:
    """Put a failed, retryable problem back in the queue (seller or workflow).

    The same order id goes back through the same dedupe: a retry can finish an
    import, never duplicate one. ``event`` must be locked by the caller.
    """
    if not retryable(event):
        raise ConflictError("This problem cannot be retried", details={"code": "NOT_RETRYABLE"})
    conn = await get(db, event.connection_id)
    if conn.state != "CONNECTED":
        raise ConflictError("Reconnect the store first", details={"code": "RECONNECT_REQUIRED"})
    event.status, event.next_attempt_at, event.resolved_at = "QUEUED", utc_now(), None
    await audit(db, AuditAction.INTEGRATION_EVENT_RETRIED, conn, event_id=str(event.id))
    await db.flush()


def event_view(event: IntegrationEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "connection_id": event.connection_id,
        "provider": event.provider,
        "kind": event.kind,
        "operation": event.operation,
        "topic": event.topic,
        "external_ref": event.external_ref,
        "status": event.status,
        "code": event.code,
        "attempts": event.attempts,
        "order_id": event.order_id,
        "retryable": retryable(event),
        "action": "RECONNECT"
        if event.code in RECONNECT
        else ("RETRY" if retryable(event) else None),
        "created_at": event.created_at,
        "updated_at": event.updated_at,
        "resolved_at": event.resolved_at,
    }


# --------------------------------------------------------------- import ---


@dataclass
class Outcome:
    status: str  # PROCESSED, IGNORED or FAILED
    code: str | None = None
    order_id: uuid.UUID | None = None


def _ingest_failure(exc: Exception) -> str:
    if isinstance(exc, EntitlementRequiredError):
        return "ORDER_LIMIT_REACHED"
    details = getattr(exc, "details", None) or {}
    if isinstance(exc, ValidationError):
        return "INVALID_PHONE" if details.get("field") == "phone" else "ORDER_REJECTED_BY_MAPPING"
    if isinstance(exc, ConflictError):
        return "CUSTOMER_BLOCKED" if details.get("customer_id") else "CONNECTION_NOT_ACTIVE"
    raise exc


async def import_order(
    db: AsyncSession,
    conn: IntegrationConnection,
    node: dict[str, Any],
    *,
    kind: str,
    event: IntegrationEvent | None = None,
) -> Outcome:
    """One provider order through normalize -> ingest. Never raises for order data."""
    connector = CONNECTORS[conn.provider]
    ref = connector.external_id(node)
    existing = await db.scalar(
        sa.select(ExternalOrder).where(
            ExternalOrder.source_id == conn.source_id, ExternalOrder.external_order_id == ref
        )
    )
    if existing is not None:
        from app.integrations import inbound  # two-way sync builds on this module

        handled = await inbound.on_existing(db, conn, node, existing, connector)
        if handled is not None:
            return Outcome(handled[0], handled[1], existing.order_id)
        if connector.is_cancelled(node):
            await record_issue(
                db,
                conn,
                kind=kind,
                topic="order.cancelled",
                code="ORDER_CANCELLED_AT_PROVIDER",
                external_ref=ref,
                order_id=existing.order_id,
                event=event,
            )
            return Outcome("FAILED", "ORDER_CANCELLED_AT_PROVIDER", existing.order_id)
        return Outcome("IGNORED", "DUPLICATE_IGNORED", existing.order_id)
    try:
        payload = connector.to_native(node)
    except Skip as skip:
        return Outcome("IGNORED", skip.code)
    except Reject as reject:
        await record_issue(
            db,
            conn,
            kind=kind,
            topic="order.import",
            code=reject.code,
            external_ref=ref,
            event=event,
        )
        return Outcome("FAILED", reject.code)
    if conn.source_id is None:
        return Outcome("FAILED", "CONNECTION_NOT_ACTIVE")
    identity = store_identity(payload)
    payload = await resolve_lines(db, conn, payload)
    try:
        async with db.begin_nested():
            result = await ingest(db, conn.source_id, ref, payload, managed=True, identity=identity)
    except (ValidationError, ConflictError, EntitlementRequiredError) as exc:
        code = _ingest_failure(exc)
        await record_issue(
            db, conn, kind=kind, topic="order.import", code=code, external_ref=ref, event=event
        )
        return Outcome("FAILED", code)
    order_id = uuid.UUID(result["order_id"])
    await resolve_ref(db, conn, ref)
    if result["replayed"]:
        return Outcome("IGNORED", "DUPLICATE_IGNORED", order_id)
    return Outcome("PROCESSED", None, order_id)


def store_identity(payload: dict[str, Any]) -> str:
    """Hash of the store's order data alone: line mappings are ecomsbd's business."""
    from app.common.idempotency import request_hash

    items = [{k: v for k, v in item.items() if k != "_link"} for item in payload.get("items") or []]
    return request_hash({**payload, "items": items})


async def resolve_lines(
    db: AsyncSession, conn: IntegrationConnection, payload: dict[str, Any]
) -> dict[str, Any]:
    """Point each line at the mapped ecomsbd product, so booking moves real stock."""
    from app.integrations.sync_models import IntegrationLink

    keys = {item["_link"] for item in payload.get("items") or [] if item.get("_link")}
    mapped: dict[str, tuple[uuid.UUID | None, uuid.UUID | None]] = {}
    if keys:
        rows = await db.execute(
            sa.select(
                IntegrationLink.external_key, IntegrationLink.product_id, IntegrationLink.variant_id
            ).where(
                IntegrationLink.connection_id == conn.id,
                IntegrationLink.state == "MATCHED",
                IntegrationLink.external_key.in_(sorted(keys)),
            )
        )
        mapped = {key: (product, variant) for key, product, variant in rows.all()}
    items = []
    for item in payload.get("items") or []:
        line = {k: v for k, v in item.items() if k != "_link"}
        product_id, variant_id = mapped.get(item.get("_link") or "", (None, None))
        if product_id is not None:
            line["product_id"] = str(product_id)
            if variant_id is not None:
                line["variant_id"] = str(variant_id)
        items.append(line)
    return {**payload, "items": items}


# ---------------------------------------------------------- credentials ---


@dataclass(frozen=True)
class Link:
    """What a job needs from a connection once its session has closed."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    provider: str
    state: str
    account_id: str | None
    config: dict[str, Any]


def snapshot(conn: IntegrationConnection) -> Link:
    return Link(
        conn.id, conn.tenant_id, conn.provider, conn.state, conn.account_id, dict(conn.config)
    )


async def fresh_credentials(connection_id: uuid.UUID) -> tuple[Link, dict[str, Any]]:
    """Credentials ready to use, refreshing Shopify's hourly token when due.

    Runs in its own committed transaction, so a rotated refresh token is never
    lost to a later rollback. An expired grant is recorded before raising.
    """
    failure: ProviderError | None = None
    async with session_scope() as db:
        conn = await get(db, connection_id, lock=True)
        credentials = unseal(conn)
        if not credentials:
            raise ProviderError("AUTH_EXPIRED")
        if conn.provider == "SHOPIFY" and shopify.needs_refresh(credentials):
            try:
                fresh = await shopify.refresh(conn.account_id or "", credentials["refresh_token"])
            except ProviderError as exc:
                failure = exc
                if exc.code == "AUTH_EXPIRED":
                    mark_error(conn, exc.code)
                    await record_issue(db, conn, kind="AUTH", topic="auth.refresh", code=exc.code)
            else:
                credentials |= fresh
                seal(conn, credentials)
        link_view = snapshot(conn)
    if failure is not None:
        raise failure
    return link_view, credentials


def _with_variant(link_view: Link) -> bool:
    return link_view.provider == "SHOPIFY" and not shopify.missing_scopes(
        link_view.config.get("scopes"), "catalog"
    )


async def fetch_order(link_view: Link, credentials: dict[str, Any], ref: str) -> dict[str, Any]:
    if link_view.provider == "SHOPIFY":
        return await shopify.fetch_order(
            link_view.account_id or "", credentials, ref, with_variant=_with_variant(link_view)
        )
    connector = CONNECTORS[link_view.provider]
    return await connector.fetch_order(link_view.account_id or "", credentials, ref)


async def fetch_page(
    link_view: Link, credentials: dict[str, Any], run: IntegrationSyncRun
) -> tuple[list[dict[str, Any]], str | None, int | None]:
    if run.kind == "CATALOG":
        from app.integrations import shopify_sync, woocommerce_sync

        module: Any = shopify_sync if link_view.provider == "SHOPIFY" else woocommerce_sync
        items, cursor = await module.catalog_page(
            link_view.account_id or "", credentials, run.cursor
        )
        return items, cursor, None
    created_from = datetime.fromisoformat(
        link_view.config.get("import_from") or run.since.isoformat()
    )
    updated = run.kind == "INCREMENTAL"
    if link_view.provider == "SHOPIFY":
        query = shopify.search(run.since, run.until, updated=updated, created_from=created_from)
        nodes, cursor = await shopify.orders_page(
            link_view.account_id or "",
            credentials,
            query,
            run.cursor,
            with_variant=_with_variant(link_view),
        )
        return nodes, cursor, None
    return await woocommerce.orders_page(
        link_view.account_id or "",
        credentials,
        since=run.since,
        until=run.until,
        updated=updated,
        created_from=created_from,
        cursor=run.cursor,
    )


# --------------------------------------------------------------- health ---


def health(conn: IntegrationConnection, open_issues: int) -> str:
    if conn.state == "PENDING":
        return "SETUP_INCOMPLETE"
    if conn.state in {"DISABLED", "DISCONNECTED", "AUTH_EXPIRED"}:
        return conn.state
    if conn.webhook_state == "FAILING":
        return "WEBHOOK_FAILING"
    if conn.sync_state == "FAILING":
        return "SYNC_FAILING"
    return "DEGRADED" if open_issues else "CONNECTED"


def today_start() -> datetime:
    zone = ZoneInfo(get_settings().default_timezone)
    local = utc_now().astimezone(zone)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


async def facts(
    db: AsyncSession, rows: list[IntegrationConnection]
) -> dict[uuid.UUID, dict[str, int]]:
    """Counts for many connections in three grouped queries; no provider calls."""
    ids = [row.id for row in rows]
    result = {
        row.id: {"open_issues": 0, "open_conflicts": 0, "orders_today": 0, "failed_today": 0}
        for row in rows
    }
    if not ids:
        return result
    since = today_start()
    open_rows = await db.execute(
        sa.select(IntegrationEvent.connection_id, sa.func.count())
        .where(IntegrationEvent.connection_id.in_(ids), IntegrationEvent.status == "FAILED")
        .group_by(IntegrationEvent.connection_id)
    )
    for connection_id, count in open_rows.all():
        result[connection_id]["open_issues"] = count
    failed_rows = await db.execute(
        sa.select(IntegrationEvent.connection_id, sa.func.count())
        .where(
            IntegrationEvent.connection_id.in_(ids),
            IntegrationEvent.status == "FAILED",
            IntegrationEvent.updated_at >= since,
        )
        .group_by(IntegrationEvent.connection_id)
    )
    for connection_id, count in failed_rows.all():
        result[connection_id]["failed_today"] = count
    from app.integrations.sync_models import IntegrationConflict

    conflict_rows = await db.execute(
        sa.select(IntegrationConflict.connection_id, sa.func.count())
        .where(IntegrationConflict.connection_id.in_(ids), IntegrationConflict.status == "OPEN")
        .group_by(IntegrationConflict.connection_id)
    )
    for connection_id, count in conflict_rows.all():
        result[connection_id]["open_conflicts"] = count
    by_source = {row.source_id: row.id for row in rows if row.source_id}
    if by_source:
        order_rows = await db.execute(
            sa.select(ExternalOrder.source_id, sa.func.count())
            .where(ExternalOrder.source_id.in_(list(by_source)), ExternalOrder.created_at >= since)
            .group_by(ExternalOrder.source_id)
        )
        for source_id, count in order_rows.all():
            result[by_source[source_id]]["orders_today"] = count
    return result


def connection_view(
    conn: IntegrationConnection, counts: dict[str, int], *, manage: bool
) -> dict[str, Any]:
    view: dict[str, Any] = {
        "id": conn.id,
        "provider": conn.provider,
        "name": conn.name,
        "state": conn.state,
        "health": health(conn, counts.get("open_issues", 0) + counts.get("open_conflicts", 0)),
        "account_name": conn.account_name,
        "webhook_state": conn.webhook_state,
        "sync_state": conn.sync_state,
        "last_success_at": conn.last_success_at,
        "last_webhook_at": conn.last_webhook_at,
        "last_sync_at": conn.last_sync_at,
        "last_error_at": conn.last_error_at,
        "last_error_code": conn.last_error_code,
        "created_at": conn.created_at,
        **counts,
    }
    if manage:
        view["account_id"] = conn.account_id
        view["import_from"] = conn.config.get("import_from")
        view["pages"] = conn.config.get("pages")
    return view


# ----------------------------------------------------------- lifecycle ---


async def create(
    db: AsyncSession, provider: str, name: str, actor_id: uuid.UUID
) -> IntegrationConnection:
    await lock_shop(db)
    status = availability(provider)
    if not status["available"]:
        raise ConflictError("Official setup required", details={"blocker": status["blocker"]})
    count = await db.scalar(
        sa.select(sa.func.count())
        .select_from(IntegrationConnection)
        .where(IntegrationConnection.state != "DISCONNECTED")
    )
    if (count or 0) >= MAX_CONNECTIONS:
        raise ConflictError(f"At most {MAX_CONNECTIONS} connections per shop")
    conn = IntegrationConnection(
        provider=provider,
        name=name,
        state="PENDING",
        config={},
        created_by=actor_id,
        webhook_token=secrets.token_urlsafe(32),
    )
    db.add(conn)
    await db.flush()
    await audit(db, AuditAction.INTEGRATION_CREATED, conn)
    return conn


async def cancel_runs(db: AsyncSession, conn: IntegrationConnection) -> None:
    runs = (
        await db.scalars(
            sa.select(IntegrationSyncRun).where(
                IntegrationSyncRun.connection_id == conn.id,
                IntegrationSyncRun.status.in_(["QUEUED", "RUNNING"]),
            )
        )
    ).all()
    for run in runs:
        run.status, run.finished_at = "CANCELLED", utc_now()


async def went_live(db: AsyncSession, conn: IntegrationConnection) -> None:
    """Common tail of every successful connect or reconnect."""
    again = bool(conn.config.get("connected_once"))
    conn.state = "CONNECTED"
    conn.last_error_code = None
    # Only orders placed from now on, unless the seller imports a window.
    conn.config = {"import_from": utc_now().isoformat(), **conn.config, "connected_once": True}
    await audit(
        db,
        AuditAction.INTEGRATION_RECONNECTED if again else AuditAction.INTEGRATION_CONNECTED,
        conn,
    )
    # Failures that only a reconnect could clear are now retryable again.
    rows = (
        await db.scalars(
            sa.select(IntegrationEvent).where(
                IntegrationEvent.connection_id == conn.id,
                IntegrationEvent.status == "FAILED",
                IntegrationEvent.code.in_(sorted(RECONNECT)),
                IntegrationEvent.external_ref.is_(None),
            )
        )
    ).all()
    for row in rows:
        row.status, row.resolved_at = "RESOLVED", utc_now()


async def register_webhooks(
    db: AsyncSession, conn: IntegrationConnection, credentials: dict[str, Any]
) -> None:
    """Best effort: polling still imports orders if the store will not call us."""
    if not can_receive_webhooks():
        conn.webhook_state = "NOT_REGISTERED"
        return
    try:
        if conn.provider == "SHOPIFY":
            ids: list[Any] = await shopify.register_webhooks(
                conn.account_id or "", credentials, webhook_url(conn)
            )
        else:
            secret = webhook_secret(conn)
            if secret is None:
                secret = secrets.token_urlsafe(32)
                conn.webhook_secret_enc = get_vault().encrypt(
                    secret, context=f"integration-hook:{conn.id}"
                )
            old = conn.config.get("webhook_ids") or []
            if old:
                await woocommerce.unregister_webhooks(conn.account_id or "", credentials, old)
            ids = await woocommerce.register_webhooks(
                conn.account_id or "", credentials, webhook_url(conn), secret
            )
    except ProviderError as exc:
        conn.webhook_state = "FAILING"
        mark_error(
            conn, "WEBHOOK_REGISTRATION_FAILED" if exc.code == "PROVIDER_REJECTED" else exc.code
        )
        await record_issue(
            db, conn, kind="WEBHOOK", topic="webhook.register", code="WEBHOOK_REGISTRATION_FAILED"
        )
        return
    conn.config = {**conn.config, "webhook_ids": ids}
    conn.webhook_state = "ACTIVE"


async def complete_shopify(
    db: AsyncSession, conn: IntegrationConnection, shop: str, code: str
) -> None:
    tokens = await shopify.exchange_code(shop, code)
    info = await shopify.shop_info(shop, tokens)
    if info.get("currencyCode") != "BDT":
        raise ProviderError("CURRENCY_NOT_BDT")
    await link(db, conn, shop, info.get("name"))
    seal(conn, tokens)
    conn.config = {**conn.config, "scopes": tokens.get("scope")}
    await ensure_source(db, conn)
    mark_ok(conn)
    await went_live(db, conn)
    await register_webhooks(db, conn, tokens)


async def complete_woocommerce(db: AsyncSession, conn: IntegrationConnection) -> None:
    """Verify stored keys against the store, then link, subscribe and go live."""
    credentials = unseal(conn)
    if not credentials:
        raise ProviderError("AUTH_EXPIRED")
    store = conn.account_id or ""
    await woocommerce.verify(store, credentials)
    await link(db, conn, store, conn.account_name or store.removeprefix("https://"))
    await ensure_source(db, conn)
    mark_ok(conn)
    await went_live(db, conn)
    await register_webhooks(db, conn, credentials)


async def health_check(db: AsyncSession, conn: IntegrationConnection) -> dict[str, Any]:
    """Ask the provider, record the answer, and say what a seller should do."""
    checks: list[dict[str, Any]] = []
    try:
        if conn.provider == "WOOCOMMERCE" and conn.state in {
            "PENDING",
            "AUTH_EXPIRED",
            "DISCONNECTED",
        }:
            await complete_woocommerce(db, conn)
            checks.append({"key": "AUTH", "ok": True})
        elif conn.provider in ORDER_PROVIDERS:
            credentials = unseal(conn)
            if not credentials:
                raise ProviderError("AUTH_EXPIRED")
            if conn.provider == "SHOPIFY":
                if shopify.needs_refresh(credentials):
                    credentials |= await shopify.refresh(
                        conn.account_id or "", credentials["refresh_token"]
                    )
                    seal(conn, credentials)
                await shopify.shop_info(conn.account_id or "", credentials)
                checks.append({"key": "AUTH", "ok": True})
                active = can_receive_webhooks() and await shopify.webhooks_active(
                    conn.account_id or "", credentials, webhook_url(conn)
                )
            else:
                await woocommerce.verify(conn.account_id or "", credentials)
                checks.append({"key": "AUTH", "ok": True})
                ids = conn.config.get("webhook_ids") or []
                active = bool(ids) and await woocommerce.webhooks_active(
                    conn.account_id or "", credentials, ids
                )
            if not active and can_receive_webhooks() and conn.state == "CONNECTED":
                await register_webhooks(db, conn, credentials)
                active = conn.webhook_state == "ACTIVE"
            conn.webhook_state = (
                "ACTIVE" if active else ("FAILING" if can_receive_webhooks() else "NOT_REGISTERED")
            )
            checks.append({"key": "WEBHOOKS", "ok": active})
        elif conn.provider == "MESSENGER":
            credentials = unseal(conn)
            if not credentials.get("page_token"):
                raise ProviderError("AUTH_EXPIRED")
            await meta.check(conn.account_id or "", credentials["page_token"])
            checks.append({"key": "AUTH", "ok": True})
        elif conn.provider == "WHATSAPP":
            credentials = unseal(conn)
            if not credentials.get("access_token"):
                raise ProviderError("AUTH_EXPIRED")
            await whatsapp.phone_number(conn.account_id or "", credentials["access_token"])
            checks.append({"key": "AUTH", "ok": True})
        if conn.state == "AUTH_EXPIRED":
            await went_live(db, conn)
        mark_ok(conn)
    except ProviderError as exc:
        mark_error(conn, exc.code)
        if exc.code in RECONNECT:
            await record_issue(db, conn, kind="AUTH", topic="auth.check", code=exc.code)
        checks.append({"key": "AUTH", "ok": False, "code": exc.code})
    conn.config = {**conn.config, "checked_at": utc_now().isoformat()}
    return {
        "ok": all(check["ok"] for check in checks) if checks else True,
        "checks": checks,
        "health": health(conn, 0),
    }


async def disconnect(db: AsyncSession, conn: IntegrationConnection, *, remote: bool = True) -> None:
    """Forget the grant here and, where the provider allows it, there."""
    credentials = unseal(conn) if remote else {}
    ids = conn.config.get("webhook_ids") or []
    try:
        if credentials and ids and conn.provider == "SHOPIFY":
            await shopify.unregister_webhooks(conn.account_id or "", credentials, ids)
        elif credentials and ids and conn.provider == "WOOCOMMERCE":
            await woocommerce.unregister_webhooks(conn.account_id or "", credentials, ids)
    except ProviderError:
        pass  # Our side is revoked regardless; the store may already be gone.
    seal(conn, None)
    conn.webhook_secret_enc = None
    conn.account_key = None
    conn.state_hash = conn.state_expires_at = None
    conn.state = "DISCONNECTED"
    conn.webhook_state = conn.sync_state = None
    conn.config = {k: v for k, v in conn.config.items() if k not in {"webhook_ids", "pages"}}
    await set_source_enabled(db, conn, False)
    await cancel_runs(db, conn)
    await audit(db, AuditAction.INTEGRATION_DISCONNECTED, conn)


async def start_sync(
    db: AsyncSession,
    conn: IntegrationConnection,
    *,
    kind: str,
    since: datetime,
    until: datetime,
    actor_id: uuid.UUID | None,
) -> IntegrationSyncRun:
    if conn.provider not in ORDER_PROVIDERS:
        raise ValidationError("This connection does not import orders")
    if conn.state != "CONNECTED":
        raise ConflictError("Connect the store first", details={"code": "CONNECTION_NOT_ACTIVE"})
    # Catalog scans and order imports run side by side; each kind one at a time.
    kinds = ["CATALOG"] if kind == "CATALOG" else ["INITIAL", "INCREMENTAL"]
    active = await db.scalar(
        sa.select(IntegrationSyncRun.id).where(
            IntegrationSyncRun.connection_id == conn.id,
            IntegrationSyncRun.status.in_(["QUEUED", "RUNNING"]),
            IntegrationSyncRun.kind.in_(kinds),
        )
    )
    if active is not None:
        raise ConflictError(
            "An import is already running", details={"code": "SYNC_ALREADY_RUNNING"}
        )
    if kind == "INITIAL":
        current = conn.config.get("import_from")
        earliest = min(since, datetime.fromisoformat(current)) if current else since
        conn.config = {**conn.config, "import_from": earliest.isoformat()}
    run = IntegrationSyncRun(
        connection_id=conn.id,
        kind=kind,
        status="QUEUED",
        since=since,
        until=until,
        next_run_at=utc_now(),
        created_by=actor_id,
    )
    db.add(run)
    await db.flush()
    if kind == "INITIAL":
        await audit(
            db,
            AuditAction.INTEGRATION_SYNC_STARTED,
            conn,
            since=since.isoformat(),
            until=until.isoformat(),
        )
    return run


def run_view(run: IntegrationSyncRun) -> dict[str, Any]:
    return {
        key: getattr(run, key)
        for key in (
            "id",
            "connection_id",
            "kind",
            "status",
            "since",
            "until",
            "pages",
            "imported",
            "duplicates",
            "skipped",
            "failed",
            "total",
            "last_error_code",
            "started_at",
            "finished_at",
            "created_at",
        )
    }


# ------------------------------------------------------------- WhatsApp ---


async def connect_whatsapp(
    db: AsyncSession,
    conn: IntegrationConnection,
    *,
    phone_number_id: str,
    waba_id: str,
    access_token: str,
) -> None:
    """Link a WhatsApp Business number the shop owns, proving the token first."""
    info = await whatsapp.phone_number(phone_number_id, access_token)
    await ensure_unlinked(db, conn, phone_number_id)
    label = " · ".join(
        part for part in (info.get("verified_name"), info.get("display_phone_number")) if part
    )
    await link(db, conn, phone_number_id, label or None)
    seal(
        conn,
        {"access_token": access_token, "phone_number_id": phone_number_id, "waba_id": waba_id},
    )
    conn.config = {**conn.config, "waba_id": waba_id}
    try:
        await whatsapp.subscribe(waba_id, access_token)
        conn.webhook_state = "ACTIVE"
    except ProviderError as exc:
        # Sending works without it; delivery receipts and STOP replies do not.
        conn.webhook_state = "FAILING"
        conn.last_error_code = exc.code
    mark_ok(conn)
    await went_live(db, conn)


async def sync_whatsapp_templates(db: AsyncSession, conn: IntegrationConnection) -> int:
    """Copy Meta's review status onto the shop's WhatsApp templates. Returns rows updated."""
    from app.messaging.models import MessageTemplate

    credentials = unseal(conn)
    waba = credentials.get("waba_id") or conn.config.get("waba_id") or ""
    remote = await whatsapp.templates(waba, credentials.get("access_token", ""))
    by_key = {(row["name"], row["language"]): row for row in remote}
    rows = (
        await db.scalars(
            sa.select(MessageTemplate).where(
                MessageTemplate.channel == "WHATSAPP", MessageTemplate.archived.is_(False)
            )
        )
    ).all()
    now = utc_now()
    for row in rows:
        languages = [code for code in (row.provider_language_bn, row.provider_language_en) if code]
        found = [by_key.get((row.provider_name or "", code)) for code in languages]
        statuses = [item["status"] if item else "NOT_FOUND" for item in found]
        row.provider_status = next(
            (status for status in statuses if status != "APPROVED"), "APPROVED"
        )
        row.provider_category = next((item["category"] for item in found if item), None)
        row.provider_checked_at = now
    mark_ok(conn)
    await db.flush()
    return len(rows)
