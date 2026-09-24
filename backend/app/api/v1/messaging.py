from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.deps import DbSession, Principal, require_permission
from app.common.operation_lock import lock_shop
from app.core.clock import utc_now
from app.core.errors import ConflictError, ValidationError
from app.messaging import providers, service
from app.messaging.models import (
    MARKETING,
    Channel,
    ConsentEvent,
    Conversation,
    Message,
    MessageAttempt,
    MessageTemplate,
)
from app.tenants.roles import Permission

router = APIRouter(prefix="/messaging", tags=["messaging"])
Reader = Annotated[Principal, Depends(require_permission(Permission.CUSTOMER_VIEW))]
Writer = Annotated[Principal, Depends(require_permission(Permission.ORDER_WRITE))]
Manager = Annotated[Principal, Depends(require_permission(Permission.SETTINGS_MANAGE))]
Purpose = Literal["TRANSACTIONAL", "MARKETING"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChannelInput(Input):
    enabled: bool


class ContactInput(Input):
    customer_id: uuid.UUID
    channel: Literal["EMAIL", "WHATSAPP"] = "EMAIL"
    recipient: str | None = Field(default=None, min_length=3, max_length=254)
    #: WhatsApp only: use the phone number already on the customer.
    use_customer_phone: bool = False
    consent: bool
    marketing_consent: bool = False
    evidence: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def address(self) -> ContactInput:
        if not self.recipient and not (self.use_customer_phone and self.channel == "WHATSAPP"):
            raise ValueError("recipient is required")
        return self


class ConsentInput(Input):
    consent: bool
    evidence: str = Field(min_length=3, max_length=500)


class TemplateInput(Input):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    purpose: Purpose = "TRANSACTIONAL"
    channel: Literal["EMAIL", "WHATSAPP"] = "EMAIL"
    subject_en: str = Field(default="", max_length=160)
    subject_bn: str = Field(default="", max_length=160)
    body_en: str = Field(min_length=1, max_length=2000)
    body_bn: str = Field(min_length=1, max_length=2000)
    provider_name: str | None = Field(default=None, max_length=512)
    provider_language_bn: str | None = Field(default=None, pattern=r"^[a-z]{2}(_[A-Z]{2})?$")
    provider_language_en: str | None = Field(default=None, pattern=r"^[a-z]{2}(_[A-Z]{2})?$")
    variables: list[Literal["customer_name", "order_number", "shop_name"]] = Field(
        default_factory=list, max_length=10
    )


class TemplateUpdate(Input):
    archived: bool | None = None


class SendInput(Input):
    conversation_id: uuid.UUID
    order_id: uuid.UUID
    template_key: str = Field(min_length=1, max_length=80)
    locale: Literal["bn", "en"] = "bn"
    idempotency_key: str = Field(min_length=8, max_length=200)


def view(row: object, fields: str) -> dict[str, Any]:
    return {key: getattr(row, key) for key in fields.split()}


MESSAGE_FIELDS = (
    "id conversation_id order_id campaign_id purpose channel template_key locale subject body "
    "status attempts provider_reference last_error sent_at delivered_at read_at failed_at "
    "created_at"
)
CONTACT_FIELDS = (
    "id customer_id channel recipient_masked consent consent_version marketing_consent "
    "marketing_consent_at marketing_opted_out_at undeliverable_at undeliverable_reason"
)


@router.get("/channels")
async def channels(db: DbSession, _: Reader) -> dict[str, Any]:
    configured = {row.kind: row.enabled for row in (await db.scalars(sa.select(Channel))).all()}
    items = []
    for kind in providers.KINDS:
        available, blocker = service.capability(kind)
        shop = await providers.shop_blocker(db, kind) if available else None
        items.append(
            {
                "kind": kind,
                "available": available,
                "blocker": blocker,
                "shop_blocker": shop,
                "enabled": configured.get(kind, False),
                "reports": list(providers.reports(kind)),
                "contact": kind in providers.CONTACT_KINDS,
            }
        )
    return {"items": items}


@router.post("/channels/{kind}")
async def configure_channel(
    kind: Literal["EMAIL", "SMS", "WHATSAPP", "MESSENGER"],
    body: ChannelInput,
    db: DbSession,
    _: Manager,
) -> dict[str, Any]:
    await lock_shop(db)
    if body.enabled and not service.capability(kind)[0]:
        raise ConflictError(
            "Official provider is required", details={"blocker": service.capability(kind)[1]}
        )
    if body.enabled:
        blocker = await providers.shop_blocker(db, kind)
        if blocker:
            raise ConflictError("Finish the channel setup first", details={"blocker": blocker})
    row = await db.scalar(sa.select(Channel).where(Channel.kind == kind))
    if row is None:
        row = Channel(kind=kind)
        db.add(row)
    row.enabled = body.enabled
    await db.flush()
    return {"kind": kind, "enabled": row.enabled}


@router.get("/templates")
async def list_templates(
    db: DbSession, _: Reader, purpose: Purpose | None = None, archived: bool = False
) -> dict[str, Any]:
    return {"items": await service.templates(db, purpose=purpose, include_archived=archived)}


@router.post("/templates", status_code=201)
async def save_template(body: TemplateInput, db: DbSession, _: Manager) -> dict[str, Any]:
    await lock_shop(db)
    values = body.model_dump()
    service.validate_template(values)
    if body.key == service.BUILTIN["key"] or await db.scalar(
        sa.select(MessageTemplate.id).where(MessageTemplate.key == body.key)
    ):
        raise ConflictError("Template key already exists")
    if body.channel == "WHATSAPP":
        # Unknown until Meta is asked: sync templates from the WhatsApp connection.
        values |= {"provider_status": "UNCHECKED", "subject_en": "", "subject_bn": ""}
    else:
        values |= {"provider_name": None, "provider_language_bn": None}
        values |= {"provider_language_en": None, "variables": None}
    row = MessageTemplate(**values)
    db.add(row)
    await db.flush()
    return service.template_view(row)


@router.patch("/templates/{key}")
async def update_template(
    key: str, body: TemplateUpdate, db: DbSession, _: Manager
) -> dict[str, Any]:
    await lock_shop(db)
    row = await db.scalar(
        sa.select(MessageTemplate).where(MessageTemplate.key == key).with_for_update()
    )
    if row is None:
        raise ValidationError("Built-in templates cannot be changed")
    if body.archived is not None:
        row.archived = body.archived
    await db.flush()
    return service.template_view(row)


@router.post("/conversations", status_code=201)
async def contact(body: ContactInput, db: DbSession, actor: Writer) -> dict[str, Any]:
    row = await service.set_contact(db, **body.model_dump(), actor_id=actor.user_id)
    return view(row, CONTACT_FIELDS)


@router.get("/conversations")
async def conversations(
    db: DbSession,
    _: Reader,
    customer_id: uuid.UUID | None = None,
    channel: Literal["EMAIL", "WHATSAPP"] | None = None,
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    query = sa.select(Conversation)
    if customer_id:
        query = query.where(Conversation.customer_id == customer_id)
    if channel:
        query = query.where(Conversation.channel == channel)
    return {
        "items": [
            view(row, CONTACT_FIELDS)
            for row in (
                await db.scalars(
                    query.order_by(Conversation.created_at.desc()).offset(offset).limit(50)
                )
            ).all()
        ]
    }


async def _locked(db: DbSession, conversation_id: uuid.UUID) -> Conversation:
    await lock_shop(db)
    row = await db.scalar(
        sa.select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    )
    if row is None:
        row = await service.required(db, Conversation, conversation_id)
    return row


@router.patch("/conversations/{conversation_id}/consent")
async def consent(
    conversation_id: uuid.UUID, body: ConsentInput, db: DbSession, actor: Writer
) -> dict[str, Any]:
    row = await _locked(db, conversation_id)
    row.consent, row.consent_version = body.consent, row.consent_version + 1
    db.add(
        ConsentEvent(
            conversation_id=row.id,
            consent=body.consent,
            evidence=body.evidence,
            actor_id=actor.user_id,
        )
    )
    await db.flush()
    return view(row, CONTACT_FIELDS)


@router.patch("/conversations/{conversation_id}/marketing-consent")
async def marketing_consent(
    conversation_id: uuid.UUID, body: ConsentInput, db: DbSession, actor: Writer
) -> dict[str, Any]:
    row = await _locked(db, conversation_id)
    await service.set_marketing_consent(db, row, body.consent, body.evidence, actor.user_id)
    return view(row, CONTACT_FIELDS)


@router.get("/conversations/{conversation_id}/consents")
async def consent_history(conversation_id: uuid.UUID, db: DbSession, _: Reader) -> dict[str, Any]:
    await service.required(db, Conversation, conversation_id)
    rows = (
        await db.scalars(
            sa.select(ConsentEvent)
            .where(ConsentEvent.conversation_id == conversation_id)
            .order_by(ConsentEvent.created_at.desc())
            .limit(50)
        )
    ).all()
    return {
        "items": [
            view(row, "id scope consent source evidence campaign_id actor_id created_at")
            for row in rows
        ]
    }


@router.post("/messages", status_code=202)
async def send(body: SendInput, db: DbSession, _: Writer) -> dict[str, Any]:
    return view(await service.queue_message(db, **body.model_dump()), MESSAGE_FIELDS)


@router.get("/messages")
async def messages(
    db: DbSession,
    _: Reader,
    conversation_id: uuid.UUID | None = None,
    order_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
    purpose: Purpose | None = None,
    status: str | None = Query(None, max_length=24),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    query = sa.select(Message)
    for column, value in (
        (Message.conversation_id, conversation_id),
        (Message.order_id, order_id),
        (Message.campaign_id, campaign_id),
        (Message.purpose, purpose),
        (Message.status, status),
    ):
        if value:
            query = query.where(column == value)
    return {
        "items": [
            view(row, MESSAGE_FIELDS)
            for row in (
                await db.scalars(query.order_by(Message.created_at.desc()).offset(offset).limit(50))
            ).all()
        ]
    }


@router.post("/messages/{message_id}/retry")
async def retry(message_id: uuid.UUID, db: DbSession, _: Writer) -> dict[str, Any]:
    return view(await service.retry_message(db, message_id), MESSAGE_FIELDS)


@router.get("/messages/{message_id}/attempts")
async def attempts(message_id: uuid.UUID, db: DbSession, _: Reader) -> dict[str, Any]:
    await service.required(db, Message, message_id)
    rows = (
        await db.scalars(
            sa.select(MessageAttempt)
            .where(MessageAttempt.message_id == message_id)
            .order_by(MessageAttempt.created_at.desc())
            .limit(50)
        )
    ).all()
    return {"items": [view(row, "id outcome created_at") for row in rows]}


@router.get("/overview")
async def overview(db: DbSession, _: Reader, days: int = Query(30, ge=1, le=90)) -> dict[str, Any]:
    """Message volume by purpose, channel and status, for monitoring."""
    since = utc_now() - timedelta(days=days)
    rows = (
        await db.execute(
            sa.select(Message.purpose, Message.channel, Message.status, sa.func.count())
            .where(Message.created_at >= since)
            .group_by(Message.purpose, Message.channel, Message.status)
        )
    ).all()
    opt_outs = await db.scalar(
        sa.select(sa.func.count())
        .select_from(ConsentEvent)
        .where(
            ConsentEvent.created_at >= since,
            ConsentEvent.scope == MARKETING,
            ConsentEvent.consent.is_(False),
        )
    )
    marketing_contacts = await db.scalar(
        sa.select(sa.func.count())
        .select_from(Conversation)
        .where(Conversation.marketing_consent.is_(True), Conversation.undeliverable_at.is_(None))
    )
    return {
        "days": days,
        "items": [
            {"purpose": purpose, "channel": channel, "status": status, "count": count}
            for purpose, channel, status, count in rows
        ],
        "marketing_opt_outs": opt_outs or 0,
        "marketing_contacts": marketing_contacts or 0,
    }
