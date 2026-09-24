"""The Integrations Hub API.

Owners manage connections (``settings.manage``); anyone who can see orders can
see health and issues; anyone who can write orders can retry a failed import.
No response ever carries a provider credential. A Custom Website API key and
webhook signing secret are returned once, at creation, with ``no-store``.
"""

from __future__ import annotations

import uuid
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbSession, Principal, require_permission
from app.api.v1 import automation as automation_api
from app.common.audit import AuditAction
from app.common.operation_lock import lock_shop
from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.integrations import custom_website, meta, recipes, service, shopify, woocommerce
from app.integrations import sync as sync_engine
from app.integrations.http import ProviderError
from app.integrations.models import IntegrationConnection, IntegrationEvent, IntegrationSyncRun
from app.integrations.receiver import select_page
from app.tenants.roles import Permission

router = APIRouter(prefix="/integrations", tags=["integrations"])
Viewer = Annotated[Principal, Depends(require_permission(Permission.ORDER_VIEW))]
Operator = Annotated[Principal, Depends(require_permission(Permission.ORDER_WRITE))]
Owner = Annotated[Principal, Depends(require_permission(Permission.SETTINGS_MANAGE))]

#: Shopify serves the last 60 days without the protected read_all_orders scope.
MAX_DAYS = {"SHOPIFY": 60, "WOOCOMMERCE": 365}


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateInput(Input):
    provider: Literal["SHOPIFY", "WOOCOMMERCE", "CUSTOM_WEBSITE", "MESSENGER", "WHATSAPP"]
    name: str = Field(min_length=1, max_length=120)


class ConnectInput(Input):
    shop: str | None = Field(default=None, max_length=255)
    store_url: str | None = Field(default=None, max_length=255)


class KeysInput(Input):
    consumer_key: str = Field(pattern=r"^ck_[A-Za-z0-9]{8,96}$")
    consumer_secret: str = Field(pattern=r"^cs_[A-Za-z0-9]{8,96}$")


class PageInput(Input):
    page_id: str = Field(pattern=r"^[0-9]{1,40}$")


class UpdateInput(Input):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None


class SyncInput(Input):
    days: int | None = Field(default=None, ge=1, le=365)
    since: datetime | None = None
    until: datetime | None = None


class WebhookInput(Input):
    url: str = Field(min_length=10, max_length=1000)
    topics: list[str] | None = Field(default=None, max_length=20)


class KeyInput(Input):
    #: Let the website change stock through the Public API.
    stock_write: bool = False


class RecipeInput(Input):
    locale: Literal["en", "bn"] = "bn"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


async def _view(
    db: DbSession, rows: list[IntegrationConnection], manage: bool
) -> list[dict[str, Any]]:
    counts = await service.facts(db, rows)
    views = []
    for row in rows:
        view = service.connection_view(row, counts[row.id], manage=manage)
        if row.provider == "CUSTOM_WEBSITE":
            view = custom_website.overlay(view, await custom_website.facts(db, row))
        views.append(view)
    return views


async def _one(db: DbSession, row: IntegrationConnection, manage: bool) -> dict[str, Any]:
    # Flushed here, inside the tenant scope: the request session commits after
    # the principal's context is gone, and a pending write would be refused.
    await db.flush()
    return (await _view(db, [row], manage))[0]


# --------------------------------------------------------- hub and lists ---


@router.get("")
async def hub(db: DbSession, actor: Viewer) -> dict[str, Any]:
    rows = list(
        (
            await db.scalars(
                sa.select(IntegrationConnection).order_by(IntegrationConnection.created_at)
            )
        ).all()
    )
    manage = actor.can(Permission.SETTINGS_MANAGE)
    return {
        "providers": [service.availability(p) for p in service.PROVIDERS],
        "items": await _view(db, rows, manage),
        "can_manage": manage,
        "can_retry": actor.can(Permission.ORDER_WRITE),
    }


@router.get("/issues")
async def issues(
    db: DbSession,
    _: Viewer,
    status: Literal["open", "resolved"] = "open",
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    wanted = ["FAILED"] if status == "open" else ["RESOLVED"]
    rows = (
        await db.scalars(
            sa.select(IntegrationEvent)
            .where(IntegrationEvent.status.in_(wanted))
            .order_by(IntegrationEvent.updated_at.desc(), IntegrationEvent.id)
            .offset(offset)
            .limit(50)
        )
    ).all()
    return {"items": [service.event_view(row) for row in rows], "next_offset": offset + len(rows)}


@router.get("/recipes")
async def recipe_list(db: DbSession, _: Viewer) -> dict[str, Any]:
    return {"items": await recipes.catalog(db)}


@router.post("/recipes/{key}", status_code=201)
async def recipe_install(
    key: str, body: RecipeInput, db: DbSession, actor: Owner
) -> dict[str, Any]:
    # The V2 create path: its validation, its 50-rule limit, its audit trail.
    return await automation_api.create_rule(recipes.rule_input(key, body.locale), db, actor)


@router.post("/events/{event_id}/retry")
async def retry(event_id: uuid.UUID, db: DbSession, _: Operator) -> dict[str, Any]:
    await lock_shop(db)
    event = await db.scalar(
        sa.select(IntegrationEvent).where(IntegrationEvent.id == event_id).with_for_update()
    )
    if event is None:
        raise NotFoundError()
    if not service.retryable(event):
        raise ConflictError("This problem cannot be retried", details={"code": "NOT_RETRYABLE"})
    conn = await service.get(db, event.connection_id)
    if conn.state != "CONNECTED":
        raise ConflictError("Reconnect the store first", details={"code": "RECONNECT_REQUIRED"})
    # The same order id goes back through the same dedupe: a retry can finish
    # an import, never duplicate one.
    event.status, event.next_attempt_at, event.resolved_at = "QUEUED", utc_now(), None
    await service.audit(db, AuditAction.INTEGRATION_EVENT_RETRIED, conn, event_id=str(event.id))
    await db.flush()
    return service.event_view(event)


@router.post("/events/{event_id}/resolve")
async def resolve(event_id: uuid.UUID, db: DbSession, _: Operator) -> dict[str, Any]:
    event = await db.scalar(
        sa.select(IntegrationEvent).where(IntegrationEvent.id == event_id).with_for_update()
    )
    if event is None:
        raise NotFoundError()
    if event.status != "FAILED":
        raise ConflictError("Only open problems can be marked resolved")
    event.status, event.resolved_at = "RESOLVED", utc_now()
    await db.flush()
    return service.event_view(event)


@router.post("", status_code=201)
async def create(
    body: CreateInput, db: DbSession, actor: Owner, response: Response
) -> dict[str, Any]:
    if body.provider == "CUSTOM_WEBSITE":
        conn, key = await custom_website.create(db, body.name, actor.user_id)
        _no_store(response)
        return {
            "connection": await _one(db, conn, True),
            "api_key": key,
            "package": custom_website.package(conn),
        }
    conn = await service.create(db, body.provider, body.name, actor.user_id)
    return {"connection": await _one(db, conn, True)}


# ------------------------------------------------------------ one connection ---


@router.get("/{connection_id}")
async def detail(connection_id: uuid.UUID, db: DbSession, actor: Viewer) -> dict[str, Any]:
    conn = await service.get(db, connection_id)
    manage = actor.can(Permission.SETTINGS_MANAGE)
    runs = (
        await db.scalars(
            sa.select(IntegrationSyncRun)
            .where(IntegrationSyncRun.connection_id == conn.id)
            .order_by(IntegrationSyncRun.created_at.desc())
            .limit(10)
        )
    ).all()
    events = (
        await db.scalars(
            sa.select(IntegrationEvent)
            .where(IntegrationEvent.connection_id == conn.id)
            .order_by(IntegrationEvent.updated_at.desc(), IntegrationEvent.id)
            .limit(50)
        )
    ).all()
    result: dict[str, Any] = {
        "connection": await _one(db, conn, manage),
        "runs": [service.run_view(run) for run in runs],
        "events": [service.event_view(event) for event in events],
        "availability": service.availability(conn.provider),
        "can_manage": manage,
        "can_retry": actor.can(Permission.ORDER_WRITE),
    }
    if conn.provider == "CUSTOM_WEBSITE" and manage:
        result["custom"] = {
            **custom_website.package(conn),
            **await custom_website.facts(db, conn),
        }
    return result


@router.post("/{connection_id}/connect")
async def connect(
    connection_id: uuid.UUID, body: ConnectInput, db: DbSession, _: Owner
) -> dict[str, Any]:
    conn = await service.get(db, connection_id, lock=True)
    status = service.availability(conn.provider)
    if not status["available"]:
        raise ConflictError("Official setup required", details={"blocker": status["blocker"]})
    if conn.provider == "SHOPIFY":
        shop = shopify.normalize_shop(body.shop or "")
        await service.ensure_unlinked(db, conn, shop)
        conn.account_id = shop
        # Scopes follow the sync features the seller turned on, nothing more.
        wanted = sync_engine.features(sync_engine.settings_of(conn))
        url = shopify.authorize_url(shop, service.new_state(conn), wanted)
        await db.flush()
        return {"authorize_url": url}
    if conn.provider == "WOOCOMMERCE":
        store = woocommerce.normalize_store(body.store_url or "")
        await service.ensure_unlinked(db, conn, store)
        conn.account_id = store
        conn.account_name = store.removeprefix("https://")[:200]
        if not status.get("one_click"):
            await db.flush()
            return {"authorize_url": None, "manual": True}
        web = (get_settings().public_web_url or "").rstrip("/")
        url = woocommerce.authorize_url(
            store, service.new_state(conn), f"{web}/integrations/{conn.id}?result=woocommerce"
        )
        await db.flush()
        return {"authorize_url": url, "manual": False}
    if conn.provider == "MESSENGER":
        url = meta.authorize_url(service.new_state(conn))
        await db.flush()
        return {"authorize_url": url}
    raise ValidationError("This connection has no sign-in step")


@router.post("/{connection_id}/woocommerce/keys")
async def woocommerce_keys(
    connection_id: uuid.UUID, body: KeysInput, db: DbSession, _: Owner
) -> dict[str, Any]:
    conn = await service.get(db, connection_id, lock=True)
    if conn.provider != "WOOCOMMERCE" or not conn.account_id:
        raise ValidationError(
            "Enter the store address first", details={"code": "STORE_URL_INVALID"}
        )
    service.seal(conn, {"consumer_key": body.consumer_key, "consumer_secret": body.consumer_secret})
    if conn.state == "CONNECTED":
        conn.state = "AUTH_EXPIRED"  # New keys are proven before they are trusted.
    result = await service.health_check(db, conn)
    return {**result, "connection": await _one(db, conn, True)}


@router.post("/{connection_id}/messenger/page")
async def messenger_page(
    connection_id: uuid.UUID, body: PageInput, db: DbSession, _: Owner
) -> dict[str, Any]:
    conn = await service.get(db, connection_id, lock=True)
    if conn.provider != "MESSENGER":
        raise ValidationError("Not a Messenger connection")
    try:
        await select_page(db, conn, body.page_id)
    except ProviderError as exc:
        service.mark_error(conn, exc.code)
        raise ConflictError("Meta refused the Page connection", details={"code": exc.code}) from exc
    return {"connection": await _one(db, conn, True)}


class WhatsAppInput(Input):
    phone_number_id: str = Field(pattern=r"^[0-9]{5,30}$")
    waba_id: str = Field(pattern=r"^[0-9]{5,30}$")
    access_token: str = Field(min_length=20, max_length=1000)


@router.post("/{connection_id}/whatsapp")
async def whatsapp_number(
    connection_id: uuid.UUID, body: WhatsAppInput, db: DbSession, _: Owner, response: Response
) -> dict[str, Any]:
    conn = await service.get(db, connection_id, lock=True)
    if conn.provider != "WHATSAPP":
        raise ValidationError("Not a WhatsApp connection")
    status = service.availability(conn.provider)
    if not status["available"]:
        raise ConflictError("Official setup required", details={"blocker": status["blocker"]})
    _no_store(response)
    try:
        await service.connect_whatsapp(
            db,
            conn,
            phone_number_id=body.phone_number_id,
            waba_id=body.waba_id,
            access_token=body.access_token,
        )
    except ProviderError as exc:
        service.mark_error(conn, exc.code)
        raise ConflictError("Meta refused the WhatsApp number", details={"code": exc.code}) from exc
    return {"connection": await _one(db, conn, True)}


@router.post("/{connection_id}/whatsapp/templates")
async def whatsapp_templates(connection_id: uuid.UUID, db: DbSession, _: Owner) -> dict[str, Any]:
    conn = await service.get(db, connection_id, lock=True)
    if conn.provider != "WHATSAPP" or conn.state != "CONNECTED":
        raise ConflictError(
            "Connect the WhatsApp number first", details={"code": "CONNECT_REQUIRED"}
        )
    try:
        updated = await service.sync_whatsapp_templates(db, conn)
    except ProviderError as exc:
        service.mark_error(conn, exc.code)
        raise ConflictError("Meta refused the template list", details={"code": exc.code}) from exc
    return {"updated": updated}


@router.post("/{connection_id}/test")
async def test(connection_id: uuid.UUID, db: DbSession, _: Owner) -> dict[str, Any]:
    conn = await service.get(db, connection_id, lock=True)
    if conn.state == "DISCONNECTED":
        raise ConflictError("Connect again first", details={"code": "RECONNECT_REQUIRED"})
    if conn.provider == "CUSTOM_WEBSITE":
        data = await custom_website.facts(db, conn)
        checks = [
            {"key": "API_KEY", "ok": data["key_active"]},
            {"key": "FIRST_REQUEST", "ok": data["last_api_call_at"] is not None},
            {"key": "FIRST_ORDER", "ok": data["last_order_at"] is not None},
        ]
        if data["webhook_url"]:
            checks.append(
                {"key": "WEBHOOK", "ok": data["deliveries"]["last_status"] == "DELIVERED"}
            )
        view = await _one(db, conn, True)
        return {"ok": all(c["ok"] for c in checks), "checks": checks, "health": view["health"]}
    if conn.provider == "SHOPIFY" and conn.state == "PENDING":
        raise ConflictError("Sign in to Shopify first", details={"code": "CONNECT_REQUIRED"})
    result = await service.health_check(db, conn)
    return {**result, "connection": await _one(db, conn, True)}


@router.patch("/{connection_id}")
async def update(
    connection_id: uuid.UUID, body: UpdateInput, db: DbSession, _: Owner
) -> dict[str, Any]:
    conn = await service.get(db, connection_id, lock=True)
    if body.name is not None:
        conn.name = body.name
    if body.enabled is False and conn.state == "CONNECTED":
        conn.state = "DISABLED"
        await service.set_source_enabled(db, conn, False)
        await service.cancel_runs(db, conn)
    elif body.enabled is True and conn.state == "DISABLED":
        conn.state = "CONNECTED"
        await service.set_source_enabled(db, conn, True)
        if conn.provider in service.ORDER_PROVIDERS:
            # Orders placed while it was off arrive through a catch-up import.
            mark = conn.config.get("high_water") or conn.config.get("import_from")
            with suppress(ConflictError):
                await service.start_sync(
                    db,
                    conn,
                    kind="INCREMENTAL",
                    since=datetime.fromisoformat(mark) if mark else utc_now() - timedelta(days=1),
                    until=utc_now(),
                    actor_id=None,
                )
    await service.audit(
        db, AuditAction.INTEGRATION_CONFIGURED, conn, enabled=body.enabled, renamed=bool(body.name)
    )
    return {"connection": await _one(db, conn, True)}


@router.post("/{connection_id}/disconnect")
async def disconnect(connection_id: uuid.UUID, db: DbSession, _: Owner) -> dict[str, Any]:
    conn = await service.get(db, connection_id, lock=True)
    if conn.state != "DISCONNECTED":
        if conn.provider == "CUSTOM_WEBSITE":
            await custom_website.disconnect(db, conn)
        else:
            await service.disconnect(db, conn)
    return {"connection": await _one(db, conn, True)}


@router.post("/{connection_id}/sync", status_code=201)
async def sync(
    connection_id: uuid.UUID, body: SyncInput, db: DbSession, actor: Owner
) -> dict[str, Any]:
    conn = await service.get(db, connection_id, lock=True)
    now = utc_now()
    until = min(body.until or now, now)
    since = body.since or (now - timedelta(days=body.days or 7))
    limit = MAX_DAYS.get(conn.provider, 0)
    if since >= until:
        raise ValidationError("Choose a start date before the end date")
    if since < now - timedelta(days=limit):
        raise ValidationError(
            f"You can import up to {limit} days of orders",
            details={"code": "WINDOW_TOO_LONG", "max_days": limit},
        )
    run = await service.start_sync(
        db, conn, kind="INITIAL", since=since, until=until, actor_id=actor.user_id
    )
    await db.flush()
    return service.run_view(run)


@router.post("/{connection_id}/sync/{run_id}/cancel")
async def cancel_sync(
    connection_id: uuid.UUID, run_id: uuid.UUID, db: DbSession, _: Owner
) -> dict[str, Any]:
    run = await db.scalar(
        sa.select(IntegrationSyncRun)
        .where(IntegrationSyncRun.id == run_id, IntegrationSyncRun.connection_id == connection_id)
        .with_for_update()
    )
    if run is None:
        raise NotFoundError()
    if run.status in {"QUEUED", "RUNNING"}:
        run.status, run.finished_at = "CANCELLED", utc_now()
    await db.flush()
    return service.run_view(run)


@router.post("/{connection_id}/go-live")
async def go_live(connection_id: uuid.UUID, db: DbSession, _: Owner) -> dict[str, Any]:
    conn = await service.get(db, connection_id, lock=True)
    if conn.provider != "CUSTOM_WEBSITE":
        raise ValidationError("Only a Custom Website goes live manually")
    if conn.state == "DISCONNECTED":
        raise ConflictError("This connection is disconnected")
    await custom_website.go_live(db, conn)
    return {"connection": await _one(db, conn, True)}


@router.post("/{connection_id}/api-key", status_code=201)
async def rotate_key(
    connection_id: uuid.UUID,
    db: DbSession,
    actor: Owner,
    response: Response,
    body: KeyInput | None = None,
) -> dict[str, Any]:
    conn = await service.get(db, connection_id, lock=True)
    if conn.provider != "CUSTOM_WEBSITE":
        raise ValidationError("Only a Custom Website has an API key")
    key = await custom_website.rotate_key(
        db, conn, actor.user_id, stock_write=bool(body and body.stock_write)
    )
    _no_store(response)
    return {"api_key": key, "connection": await _one(db, conn, True)}


@router.post("/{connection_id}/webhook", status_code=201)
async def set_webhook(
    connection_id: uuid.UUID, body: WebhookInput, db: DbSession, _: Owner, response: Response
) -> dict[str, Any]:
    conn = await service.get(db, connection_id, lock=True)
    if conn.provider != "CUSTOM_WEBSITE":
        raise ValidationError("Only a Custom Website has an outbound webhook")
    secret = await custom_website.set_webhook(db, conn, body.url, body.topics)
    _no_store(response)
    return {"signing_secret": secret, "connection": await _one(db, conn, True)}


@router.post("/{connection_id}/webhook/test", status_code=202)
async def test_webhook(connection_id: uuid.UUID, db: DbSession, actor: Owner) -> dict[str, Any]:
    conn = await service.get(db, connection_id)
    if conn.provider != "CUSTOM_WEBSITE":
        raise ValidationError("Only a Custom Website has an outbound webhook")
    delivery = await custom_website.test_webhook(db, conn, actor.require_tenant())
    await db.flush()
    return {"delivery_id": delivery.id, "status": delivery.status}
