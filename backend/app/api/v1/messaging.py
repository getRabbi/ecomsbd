from __future__ import annotations

import uuid
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbSession, Principal, require_permission
from app.common.operation_lock import lock_shop
from app.core.errors import ConflictError
from app.messaging import service
from app.messaging.models import (
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


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChannelInput(Input):
    enabled: bool


class ContactInput(Input):
    customer_id: uuid.UUID
    channel: Literal["EMAIL"] = "EMAIL"
    recipient: str = Field(min_length=3, max_length=254)
    consent: bool
    evidence: str = Field(min_length=3, max_length=500)


class ConsentInput(Input):
    consent: bool
    evidence: str = Field(min_length=3, max_length=500)


class TemplateInput(Input):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    subject_en: str = Field(min_length=1, max_length=160)
    subject_bn: str = Field(min_length=1, max_length=160)
    body_en: str = Field(min_length=1, max_length=2000)
    body_bn: str = Field(min_length=1, max_length=2000)


class SendInput(Input):
    conversation_id: uuid.UUID
    order_id: uuid.UUID
    template_key: str = Field(min_length=1, max_length=80)
    locale: Literal["bn", "en"] = "bn"
    idempotency_key: str = Field(min_length=8, max_length=200)


def view(row, fields):
    return {key: getattr(row, key) for key in fields.split()}


MESSAGE_FIELDS = "id conversation_id order_id template_key locale subject body status attempts provider_reference last_error created_at"
CONTACT_FIELDS = "id customer_id channel recipient_masked consent consent_version"


@router.get("/channels")
async def channels(db: DbSession, _: Reader):
    configured = {row.kind: row.enabled for row in (await db.scalars(sa.select(Channel))).all()}
    return {
        "items": [
            {
                "kind": kind,
                "available": service.capability(kind)[0],
                "blocker": service.capability(kind)[1],
                "enabled": configured.get(kind, False),
            }
            for kind in ("EMAIL", "SMS", "WHATSAPP", "MESSENGER")
        ]
    }


@router.post("/channels/{kind}")
async def configure_channel(
    kind: Literal["EMAIL", "SMS", "WHATSAPP", "MESSENGER"],
    body: ChannelInput,
    db: DbSession,
    _: Manager,
):
    await lock_shop(db)
    if body.enabled and not service.capability(kind)[0]:
        raise ConflictError(
            "Official provider is required", details={"blocker": service.capability(kind)[1]}
        )
    row = await db.scalar(sa.select(Channel).where(Channel.kind == kind))
    if row is None:
        row = Channel(kind=kind)
        db.add(row)
    row.enabled = body.enabled
    await db.flush()
    return {"kind": kind, "enabled": row.enabled}


@router.get("/templates")
async def list_templates(db: DbSession, _: Reader):
    return {"items": await service.templates(db)}


@router.post("/templates", status_code=201)
async def save_template(body: TemplateInput, db: DbSession, _: Manager):
    await lock_shop(db)
    service.validate_template(body.model_dump())
    if body.key == service.BUILTIN["key"] or await db.scalar(
        sa.select(MessageTemplate.id).where(MessageTemplate.key == body.key)
    ):
        raise ConflictError("Template key already exists")
    db.add(MessageTemplate(**body.model_dump()))
    await db.flush()
    return body


@router.post("/conversations", status_code=201)
async def contact(body: ContactInput, db: DbSession, actor: Writer):
    row = await service.set_contact(db, **body.model_dump(), actor_id=actor.user_id)
    return view(row, CONTACT_FIELDS)


@router.get("/conversations")
async def conversations(
    db: DbSession, _: Reader, customer_id: uuid.UUID | None = None, offset: int = Query(0, ge=0)
):
    query = sa.select(Conversation)
    if customer_id:
        query = query.where(Conversation.customer_id == customer_id)
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


@router.patch("/conversations/{conversation_id}/consent")
async def consent(conversation_id: uuid.UUID, body: ConsentInput, db: DbSession, actor: Writer):
    await lock_shop(db)
    row = await db.scalar(
        sa.select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    )
    if row is None:
        row = await service.required(db, Conversation, conversation_id)
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


@router.post("/messages", status_code=202)
async def send(body: SendInput, db: DbSession, _: Writer):
    return view(await service.queue_message(db, **body.model_dump()), MESSAGE_FIELDS)


@router.get("/messages")
async def messages(
    db: DbSession,
    _: Reader,
    conversation_id: uuid.UUID | None = None,
    order_id: uuid.UUID | None = None,
    offset: int = Query(0, ge=0),
):
    query = sa.select(Message)
    if conversation_id:
        query = query.where(Message.conversation_id == conversation_id)
    if order_id:
        query = query.where(Message.order_id == order_id)
    return {
        "items": [
            view(row, MESSAGE_FIELDS)
            for row in (
                await db.scalars(query.order_by(Message.created_at.desc()).offset(offset).limit(50))
            ).all()
        ]
    }


@router.post("/messages/{message_id}/retry")
async def retry(message_id: uuid.UUID, db: DbSession, _: Writer):
    return view(await service.retry_message(db, message_id), MESSAGE_FIELDS)


@router.get("/messages/{message_id}/attempts")
async def attempts(message_id: uuid.UUID, db: DbSession, _: Reader):
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
