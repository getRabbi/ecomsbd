"""Intake rules for devices and the seller's per-app choices (V4 WP1.2).

* ``GET /v1/intake/rules`` — ingest key. The active rule set with this shop's
  overrides applied. Strong ETag; a matching ``If-None-Match`` is a 304, so a
  device refreshing daily costs one tiny response.
* ``GET/PATCH /v1/intake/settings`` — seller session (``settings.manage``):
  which apps the shop's linked phones read. ``covered_packages`` is shown but
  only the server sets it.
* ``POST /v1/intake/captures`` — ingest key (WP1.3): one conversation's
  order-like messages from a linked phone, for the chat-order worker.
* ``POST /v1/intake/captures/manual`` — seller session (``order.write``): a
  shared or pasted message, or a tapped notification; read into a draft at once.

All behind ``intake_device_enabled`` (the ingest-key dependency checks it too).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.deps import DbSession, Principal, TenantPrincipal, require_permission
from app.chat_orders import service as chat_orders
from app.chat_orders.models import (
    DEVICE,
    DEVICE_NOTIFICATION,
    ChatMessage,
    ChatOrderDraft,
)
from app.common.audit import AuditAction, record_audit
from app.common.cache import RateLimiter
from app.common.feature_flags import FeatureFlagService, FlagKey
from app.common.idempotency import begin_idempotent, complete_idempotent
from app.core.clock import utc_now
from app.core.errors import AppError, ErrorCode, ForbiddenError, NotFoundError, RateLimitedError
from app.device_links.auth import IngestPrincipal, require_ingest_key
from app.device_links.models import INTAKE_CAPTURE
from app.intake import rules
from app.intake import service as intake
from app.intake.models import IntakeRuleSet, ShopIntakeSettings
from app.integrations.models import IntegrationConnection
from app.tenants.roles import Permission

router = APIRouter(prefix="/intake", tags=["intake"])

_MANAGE = [Depends(require_permission(Permission.SETTINGS_MANAGE))]
IngestCapture = Annotated[IngestPrincipal, Depends(require_ingest_key(INTAKE_CAPTURE))]
OrderWriter = Annotated[Principal, Depends(require_permission(Permission.ORDER_WRITE))]
BatchKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=120)]
OptionalKey = Annotated[str | None, Header(alias="Idempotency-Key", min_length=8, max_length=120)]

#: Spec WP1.3: a batch is at most 50 messages of at most 4,000 characters.
MAX_BATCH = 50
MAX_TEXT = 4000
#: Uploads per minute per linked device.
CAPTURES_PER_MINUTE = 120
_DEVICE_APP = r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$"
_ANY_APP = r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$"


class IntakeRulesResponse(BaseModel):
    """Documentation of the 200 body; the bytes are served canonically."""

    version: int
    schema_version: int
    rules: dict[str, Any]
    district_lists: dict[str, dict[str, list[str]]]
    enabled_packages: list[str]
    covered_packages: list[str]
    refresh_after_seconds: int


class PackageSettingResponse(BaseModel):
    package: str
    label: str
    default_enabled: bool
    toggle: bool | None
    covered: bool
    enabled: bool


class IntakeSettingsResponse(BaseModel):
    rules_version: int
    packages: list[PackageSettingResponse]


class IntakeSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: ``true``/``false`` = the seller's choice; ``null`` = back to the default.
    package_toggles: dict[str, bool | None] = Field(min_length=1, max_length=20)


async def _require_flag(db: DbSession, principal: TenantPrincipal) -> None:
    if not await FeatureFlagService(db).is_enabled(
        FlagKey.INTAKE_DEVICE_ENABLED, tenant_id=principal.require_tenant()
    ):
        raise AppError("Linked devices are not enabled", code=ErrorCode.FEATURE_DISABLED)


def _settings(rule_set: IntakeRuleSet, row: ShopIntakeSettings | None) -> IntakeSettingsResponse:
    return IntakeSettingsResponse(
        rules_version=rule_set.version,
        packages=[
            PackageSettingResponse(
                package=state.package,
                label=state.label,
                default_enabled=state.default_enabled,
                toggle=state.toggle,
                covered=state.covered,
                enabled=state.enabled,
            )
            for state in rules.package_states(rule_set.payload, row)
        ],
    )


@router.get(
    "/rules",
    response_model=IntakeRulesResponse,
    summary="Detection rules for this device",
    responses={304: {"description": "Unchanged since the ETag in If-None-Match"}},
)
async def get_device_rules(
    device: IngestCapture,
    db: DbSession,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    rule_set = await rules.active_rule_set(db)
    result = rules.device_rules(rule_set, await rules.shop_settings(db, device.tenant_id))
    headers = {"ETag": result.etag, "Cache-Control": "private, no-cache"}
    if rules.etag_matches(if_none_match, result.etag):
        return Response(status_code=304, headers=headers)
    return Response(content=result.body, media_type="application/json", headers=headers)


@router.get(
    "/settings",
    response_model=IntakeSettingsResponse,
    summary="Which apps the shop's phones read",
    dependencies=_MANAGE,
)
async def get_intake_settings(db: DbSession, principal: TenantPrincipal) -> IntakeSettingsResponse:
    await _require_flag(db, principal)
    rule_set = await rules.active_rule_set(db)
    return _settings(rule_set, await rules.shop_settings(db, principal.require_tenant()))


@router.patch(
    "/settings",
    response_model=IntakeSettingsResponse,
    summary="Turn reading an app on or off",
    dependencies=_MANAGE,
)
async def update_intake_settings(
    payload: IntakeSettingsPatch, db: DbSession, principal: TenantPrincipal
) -> IntakeSettingsResponse:
    await _require_flag(db, principal)
    tenant_id = principal.require_tenant()
    rule_set = await rules.active_rule_set(db)
    row = await rules.update_toggles(
        db,
        tenant_id,
        payload.package_toggles,
        known_packages=[rule["package"] for rule in rule_set.payload["packages"]],
    )
    await record_audit(
        db,
        AuditAction.INTAKE_SETTINGS_UPDATED,
        entity_type="shop_intake_settings",
        entity_id=row.id,
        context={"packages": sorted(payload.package_toggles)},
        tenant_id=tenant_id,
    )
    return _settings(rule_set, row)


# -------------------------------------------------------------- captures ---


class CaptureMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_TEXT)
    #: When the customer sent it, as the app showed it. Omitted = now.
    sent_at: datetime | None = None

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text is empty")
        return value

    @field_validator("sent_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class CaptureBatch(BaseModel):
    """One conversation's buffered messages, as the device read them."""

    model_config = ConfigDict(extra="forbid")

    package: str = Field(pattern=_DEVICE_APP, max_length=64)
    #: The conversation as the app named it (the notification's title). Used
    #: only as a keyed hash and an encrypted value; never shown or logged.
    conversation_key: str = Field(min_length=1, max_length=200)
    sender_display: str | None = Field(default=None, max_length=120)
    messages: list[CaptureMessageIn] = Field(min_length=1, max_length=MAX_BATCH)


class CaptureResponse(BaseModel):
    batch_id: str
    accepted: int
    duplicates: int
    replayed: bool = False


class ManualCapture(BaseModel):
    """A share, a paste, or the buffer of a notification the seller tapped."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["SHARE", "PASTE", "DEVICE_NOTIFICATION"]
    messages: list[CaptureMessageIn] = Field(min_length=1, max_length=MAX_BATCH)
    package: str | None = Field(default=None, pattern=_ANY_APP, max_length=64)
    conversation_key: str | None = Field(default=None, min_length=1, max_length=200)
    sender_display: str | None = Field(default=None, max_length=120)
    #: The linked phone it came from, so a tap joins what that phone uploaded.
    connection_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _notification_names_its_chat(self) -> ManualCapture:
        if self.source == DEVICE_NOTIFICATION and not (self.package and self.conversation_key):
            raise ValueError("a notification capture needs package and conversation_key")
        return self


def _messages(items: list[CaptureMessageIn]) -> list[intake.CapturedMessage]:
    return [intake.CapturedMessage(text=item.text, at=item.sent_at) for item in items]


@router.post(
    "/captures",
    response_model=CaptureResponse,
    summary="Upload one conversation's order-like messages from a linked device",
)
async def capture_batch(
    payload: CaptureBatch, device: IngestCapture, db: DbSession, batch_id: BatchKey
) -> CaptureResponse:
    limited = await RateLimiter().hit(
        "intake-capture",
        str(device.connection_id),
        limit=CAPTURES_PER_MINUTE,
        window_seconds=60,
    )
    if not limited.allowed:
        raise RateLimitedError(retry_after_seconds=limited.retry_after_seconds)
    conn = await db.get(IntegrationConnection, device.connection_id)
    if conn is None:
        raise NotFoundError()
    if (conn.config or {}).get("platform") == "extension":
        # Phase 5 turns this on behind `extension_enabled`.
        raise AppError(
            "The browser extension is not available yet", code=ErrorCode.FEATURE_DISABLED
        )
    rule_set = await rules.active_rule_set(db)
    enabled = {
        state.package
        for state in rules.package_states(
            rule_set.payload, await rules.shop_settings(db, device.tenant_id)
        )
        if state.enabled
    }
    if payload.package not in enabled:
        # The device's cached rules are out of date: it refreshes and drops it.
        raise ForbiddenError(
            "This app is not turned on for this shop",
            details={"reason": "PACKAGE_NOT_ENABLED", "rules_version": rule_set.version},
        )
    claim = await begin_idempotent(
        db,
        key=batch_id,
        endpoint=f"intake.captures:{conn.id}",
        payload=payload.model_dump(mode="json"),
    )
    if claim.replayed and claim.record.response_body is not None:
        return CaptureResponse(**claim.record.response_body, replayed=True)
    result = await intake.capture(
        db,
        tenant_id=device.tenant_id,
        connection=conn,
        source=DEVICE_NOTIFICATION,
        provider=DEVICE,
        account_ref=payload.package,
        conversation_key=payload.conversation_key,
        sender_display=payload.sender_display,
        messages=_messages(payload.messages),
    )
    conn.last_success_at = utc_now()
    body = {"batch_id": batch_id, "accepted": len(result.stored), "duplicates": result.duplicates}
    await complete_idempotent(db, claim.record, status_code=200, body=body)
    return CaptureResponse(**body)


async def _manual_connection(
    db: DbSession, principal: Principal, connection_id: uuid.UUID | None
) -> IntegrationConnection:
    tenant_id = principal.require_tenant()
    if connection_id is None:
        return await intake.manual_connection(db, tenant_id=tenant_id, user_id=principal.user_id)
    conn = await db.get(IntegrationConnection, connection_id)
    if (
        conn is None
        or conn.tenant_id != tenant_id
        or conn.provider != DEVICE
        or conn.state != "CONNECTED"
        or intake.is_manual(conn)
    ):
        raise NotFoundError("No such linked device")
    return conn


async def _manual_result(db: DbSession, message_id: uuid.UUID | None) -> dict[str, Any]:
    from app.api.v1.chat_orders import _views

    row = await db.get(ChatMessage, message_id) if message_id is not None else None
    draft = (
        await db.get(ChatOrderDraft, row.linked_draft_id)
        if row is not None and row.linked_draft_id is not None
        else None
    )
    return {
        "draft": (await _views(db, [draft]))[0] if draft is not None else None,
        # A known customer writing about an order they already placed.
        "attention_id": str(row.linked_attention_id)
        if row is not None and row.linked_attention_id is not None
        else None,
    }


@router.post("/captures/manual", summary="Read a shared or pasted message into a draft")
async def capture_manual(
    payload: ManualCapture,
    db: DbSession,
    principal: OrderWriter,
    idempotency_key: OptionalKey = None,
) -> dict[str, Any]:
    await _require_flag(db, principal)
    tenant_id = principal.require_tenant()
    claim = None
    if idempotency_key is not None:
        claim = await begin_idempotent(
            db,
            key=idempotency_key,
            endpoint="intake.captures.manual",
            payload=payload.model_dump(mode="json"),
        )
        stored_body = claim.record.response_body
        if claim.replayed and stored_body is not None:
            last = stored_body.get("message_id")
            return {
                "accepted": stored_body["accepted"],
                "duplicates": stored_body["duplicates"],
                "replayed": True,
                **await _manual_result(db, uuid.UUID(last) if last else None),
            }
    conn = await _manual_connection(db, principal, payload.connection_id)
    result = await intake.capture(
        db,
        tenant_id=tenant_id,
        connection=conn,
        source=payload.source,
        provider=DEVICE,
        account_ref=payload.package or payload.source.lower(),
        # Without a conversation, every share is its own sender.
        conversation_key=payload.conversation_key or f"capture:{uuid.uuid4().hex}",
        sender_display=payload.sender_display,
        messages=_messages(payload.messages),
    )
    # The seller is waiting on the quick-order sheet: read it now, through the
    # same code the worker runs (anything already folded is skipped).
    for message_id in [*result.existing, *result.stored]:
        await chat_orders.process_message(db, message_id)
    ids = result.stored or result.existing
    last = ids[-1] if ids else None
    # The request commits after its tenant scope ends: write everything now.
    await db.flush()
    if claim is not None:
        # Ids and counts only: the draft (phone, address) is re-read on replay.
        await complete_idempotent(
            db,
            claim.record,
            status_code=200,
            body={
                "accepted": len(result.stored),
                "duplicates": result.duplicates,
                "message_id": str(last) if last else None,
            },
        )
    return {
        "accepted": len(result.stored),
        "duplicates": result.duplicates,
        "replayed": False,
        **await _manual_result(db, last),
    }


__all__ = ["router"]
