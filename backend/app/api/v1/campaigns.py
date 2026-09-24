"""Marketing campaigns and flows (V3.3).

Owners build and launch; anyone who runs orders can pause a send in progress
(an emergency stop is never gated behind the owner); everyone who can see
customers can watch the numbers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.analytics.customer_segments import Segment
from app.api.deps import DbSession, Principal, require_permission
from app.common.audit import AuditAction, record_audit
from app.common.operation_lock import lock_shop
from app.core.clock import utc_now
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.customers.crm_models import CustomerTag
from app.customers.models import Customer
from app.messaging import campaigns as engine
from app.messaging import service
from app.messaging.models import MARKETING, Campaign, CampaignRecipient, Message
from app.tenants.roles import Permission

router = APIRouter(prefix="/campaigns", tags=["campaigns"])
Reader = Annotated[Principal, Depends(require_permission(Permission.CUSTOMER_VIEW))]
Operator = Annotated[Principal, Depends(require_permission(Permission.ORDER_WRITE))]
Manager = Annotated[Principal, Depends(require_permission(Permission.SETTINGS_MANAGE))]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Audience(Input):
    segment: Segment | None = None
    tag_id: uuid.UUID | None = None
    min_orders: int | None = Field(default=None, ge=0, le=10_000)
    max_orders: int | None = Field(default=None, ge=0, le=10_000)
    last_order_before_days: int | None = Field(default=None, ge=1, le=3650)
    last_order_within_days: int | None = Field(default=None, ge=1, le=3650)


class CampaignInput(Input):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["ONE_OFF", "FLOW"] = "ONE_OFF"
    flow: Literal["WIN_BACK", "REPEAT_NUDGE", "INACTIVE"] | None = None
    flow_days: int | None = Field(default=None, ge=1, le=365)
    channel: Literal["EMAIL", "WHATSAPP"]
    template_key: str = Field(min_length=1, max_length=80)
    locale: Literal["bn", "en"] = "bn"
    audience: Audience = Field(default_factory=Audience)
    rate_per_minute: int = Field(default=30, ge=1, le=120)
    frequency_cap_hours: int = Field(default=72, ge=24, le=720)
    attribution_days: int = Field(default=7, ge=1, le=30)

    @model_validator(mode="after")
    def flow_shape(self) -> CampaignInput:
        if (self.kind == "FLOW") != (self.flow is not None):
            raise ValueError("A flow names its flow; a one-off campaign does not")
        if self.flow:
            _default, low, high = engine.FLOWS[self.flow]
            days = self.flow_days or _default
            if not low <= days <= high:
                raise ValueError(f"{self.flow} runs between {low} and {high} days")
        return self


class EstimateInput(Input):
    channel: Literal["EMAIL", "WHATSAPP"]
    audience: Audience = Field(default_factory=Audience)
    flow: Literal["WIN_BACK", "REPEAT_NUDGE", "INACTIVE"] | None = None
    flow_days: int | None = Field(default=None, ge=1, le=365)


class LaunchInput(Input):
    scheduled_at: AwareDatetime | None = None
    #: Must match the campaign's version: launching what was reviewed.
    version: int


class PreviewInput(Input):
    template_key: str = Field(min_length=1, max_length=80)
    locale: Literal["bn", "en"] = "bn"
    channel: Literal["EMAIL", "WHATSAPP"] = "EMAIL"


FIELDS = (
    "id name kind flow flow_days channel template_key locale audience status scheduled_at "
    "started_at completed_at cancelled_at last_run_at last_error rate_per_minute "
    "frequency_cap_hours attribution_days total_recipients version created_at updated_at"
)


def view(row: Campaign) -> dict[str, Any]:
    return {key: getattr(row, key) for key in FIELDS.split()}


async def _get(db: DbSession, campaign_id: uuid.UUID, *, lock: bool = False) -> Campaign:
    query = sa.select(Campaign).where(Campaign.id == campaign_id)
    row = await db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise NotFoundError()
    return row


async def _audience(db: DbSession, audience: Audience, actor: Principal) -> dict[str, Any]:
    data = audience.model_dump(mode="json")
    if audience.segment == Segment.HIGH_VALUE and not actor.can(Permission.MONEY_VIEW):
        raise ValidationError(
            "High-value customers are defined by revenue you cannot see",
            details={"code": "MONEY_VIEW_REQUIRED"},
        )
    data["money_allowed"] = audience.segment == Segment.HIGH_VALUE
    if audience.tag_id:
        tag = await db.scalar(sa.select(CustomerTag).where(CustomerTag.id == audience.tag_id))
        if tag is None or tag.archived:
            raise ValidationError("Choose an active tag")
    return data


async def _template_check(db: DbSession, body: CampaignInput) -> None:
    try:
        found = await service.template(db, body.template_key)
    except NotFoundError as exc:
        raise ValidationError("Choose a marketing template") from exc
    problem = service.usable(found, body.channel, MARKETING)
    # Approval can arrive later; everything else is a mistake in the draft.
    if problem and problem != "WHATSAPP_TEMPLATE_NOT_APPROVED":
        raise ValidationError("This template cannot carry this campaign", details={"code": problem})


async def _audit(db: DbSession, action: str, row: Campaign, **context: Any) -> None:
    await record_audit(
        db,
        action,
        entity_type="campaign",
        entity_id=row.id,
        context={"status": row.status, "version": row.version, **context},
    )


# ------------------------------------------------------------------ reads ---


@router.get("/catalog")
async def catalog(db: DbSession, actor: Reader) -> dict[str, Any]:
    tags = (
        await db.scalars(
            sa.select(CustomerTag)
            .where(CustomerTag.archived.is_(False))
            .order_by(CustomerTag.name)
            .limit(200)
        )
    ).all()
    segments = [
        str(s) for s in Segment if s != Segment.HIGH_VALUE or actor.can(Permission.MONEY_VIEW)
    ]
    return {
        "segments": segments,
        "tags": [{"id": row.id, "name": row.name} for row in tags],
        "templates": await service.templates(db, purpose=MARKETING),
        "flows": {
            name: {"default_days": d, "min_days": lo, "max_days": hi}
            for name, (d, lo, hi) in engine.FLOWS.items()
        },
        "limits": {
            "max_recipients": engine.MAX_RECIPIENTS,
            "flow_daily_limit": engine.FLOW_DAILY_LIMIT,
            "quiet_hours": [engine.QUIET_START, engine.QUIET_END],
            "flow_hour": engine.FLOW_HOUR,
        },
        "can_manage": actor.can(Permission.SETTINGS_MANAGE),
        "can_pause": actor.can(Permission.ORDER_WRITE),
    }


@router.get("")
async def listing(
    db: DbSession,
    _: Reader,
    kind: Literal["ONE_OFF", "FLOW"] | None = None,
    status: str | None = Query(None, max_length=16),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    query = sa.select(Campaign)
    if kind:
        query = query.where(Campaign.kind == kind)
    if status:
        query = query.where(Campaign.status == status)
    rows = (
        await db.scalars(query.order_by(Campaign.created_at.desc()).offset(offset).limit(50))
    ).all()
    counts: dict[uuid.UUID, dict[str, int]] = {row.id: {} for row in rows}
    if rows:
        for campaign_id, status_value, count in (
            await db.execute(
                sa.select(Message.campaign_id, Message.status, sa.func.count())
                .where(Message.campaign_id.in_([row.id for row in rows]))
                .group_by(Message.campaign_id, Message.status)
            )
        ).all():
            counts[campaign_id][status_value] = count
    return {"items": [{**view(row), "messages": counts[row.id]} for row in rows]}


@router.post("/estimate")
async def estimate(body: EstimateInput, db: DbSession, actor: Reader) -> dict[str, Any]:
    audience = await _audience(db, body.audience, actor)
    return await engine.estimate(
        db, channel=body.channel, audience=audience, flow=body.flow, flow_days=body.flow_days
    )


@router.post("/preview")
async def preview(body: PreviewInput, db: DbSession, _: Reader) -> dict[str, Any]:
    found = await service.template(db, body.template_key)
    values = {
        "customer_name": "রহিম" if body.locale == "bn" else "Rahim",
        "shop_name": await service.shop_name(db),
        "order_number": "1001",
    }
    subject, text, params = service.compose(found, body.locale, values, body.channel)
    if body.channel == "EMAIL" and found["purpose"] == MARKETING:
        text += service.render(
            service.MARKETING_FOOTER[body.locale],
            {"shop_name": values["shop_name"], "unsubscribe_url": "https://…/unsubscribe"},
        )
    return {"subject": subject, "body": text, "params": params}


@router.get("/{campaign_id}")
async def detail(campaign_id: uuid.UUID, db: DbSession, actor: Reader) -> dict[str, Any]:
    row = await _get(db, campaign_id)
    return {
        "campaign": view(row),
        "blocker": await engine.ready(db, row),
        "analytics": await engine.analytics(
            db, row, money_allowed=actor.can(Permission.MONEY_VIEW)
        ),
    }


@router.get("/{campaign_id}/recipients")
async def recipients(
    campaign_id: uuid.UUID,
    db: DbSession,
    _: Reader,
    status: Literal["PENDING", "QUEUED", "SKIPPED"] | None = None,
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    await _get(db, campaign_id)
    query = (
        sa.select(CampaignRecipient, Customer.name, Customer.phone_masked, Message.status)
        .join(Customer, Customer.id == CampaignRecipient.customer_id)
        .outerjoin(Message, Message.id == CampaignRecipient.message_id)
        .where(CampaignRecipient.campaign_id == campaign_id)
    )
    if status:
        query = query.where(CampaignRecipient.status == status)
    rows = (
        await db.execute(
            query.order_by(CampaignRecipient.created_at, CampaignRecipient.id)
            .offset(offset)
            .limit(50)
        )
    ).all()
    return {
        "items": [
            {
                "id": recipient.id,
                "customer_id": recipient.customer_id,
                "customer_name": name,
                "phone_masked": phone,
                "status": recipient.status,
                "skip_reason": recipient.skip_reason,
                "message_id": recipient.message_id,
                "message_status": message_status,
            }
            for recipient, name, phone, message_status in rows
        ]
    }


# ----------------------------------------------------------------- writes ---


@router.post("", status_code=201)
async def create(body: CampaignInput, db: DbSession, actor: Manager) -> dict[str, Any]:
    await lock_shop(db)
    await _template_check(db, body)
    row = Campaign(
        **body.model_dump(exclude={"audience"}),
        audience=await _audience(db, body.audience, actor),
        status="DRAFT",
        created_by=actor.user_id,
    )
    if row.kind == "FLOW":
        row.flow_days = row.flow_days or engine.FLOWS[row.flow or ""][0]
    db.add(row)
    await db.flush()
    await _audit(db, AuditAction.CAMPAIGN_CREATED, row)
    return view(row)


@router.put("/{campaign_id}")
async def update(
    campaign_id: uuid.UUID, body: CampaignInput, db: DbSession, actor: Manager
) -> dict[str, Any]:
    await lock_shop(db)
    row = await _get(db, campaign_id, lock=True)
    if row.status not in engine.EDITABLE or (row.kind == "ONE_OFF" and row.started_at):
        raise ConflictError(
            "Only a draft, scheduled or paused flow can be edited",
            details={"code": "CAMPAIGN_LOCKED"},
        )
    if body.kind != row.kind:
        raise ValidationError("A campaign cannot change between one-off and flow")
    await _template_check(db, body)
    for key, value in body.model_dump(exclude={"audience"}).items():
        setattr(row, key, value)
    row.audience = await _audience(db, body.audience, actor)
    if row.kind == "FLOW":
        row.flow_days = row.flow_days or engine.FLOWS[row.flow or ""][0]
    row.version += 1
    await db.flush()
    await _audit(db, AuditAction.CAMPAIGN_UPDATED, row)
    return view(row)


@router.post("/{campaign_id}/launch")
async def launch(
    campaign_id: uuid.UUID, body: LaunchInput, db: DbSession, actor: Manager
) -> dict[str, Any]:
    """Send a one-off now or at a time, or switch a flow on."""
    await lock_shop(db)
    row = await _get(db, campaign_id, lock=True)
    if body.version != row.version:
        raise ConflictError("The campaign changed; review it again", details={"code": "STALE"})
    if row.status not in {"DRAFT", "SCHEDULED"}:
        raise ConflictError("Only a draft can be launched", details={"code": "CAMPAIGN_LOCKED"})
    await engine.require_ready(db, row)
    now = utc_now()
    row.launched_by, row.last_error = actor.user_id, None
    if row.kind == "FLOW":
        row.status, row.started_at = "ACTIVE", now
    elif body.scheduled_at and body.scheduled_at > now + timedelta(minutes=1):
        if body.scheduled_at > now + timedelta(days=90):
            raise ValidationError("Schedule within the next 90 days")
        row.status, row.scheduled_at = "SCHEDULED", body.scheduled_at
    else:
        await start(db, row, now)
    await db.flush()
    await _audit(db, AuditAction.CAMPAIGN_LAUNCHED, row)
    return view(row)


async def start(db: DbSession, row: Campaign, now: datetime) -> None:
    row.status, row.started_at = "SENDING", now
    count = await engine.materialize(db, row, now)
    if count == 0:
        row.status, row.completed_at = "COMPLETED", now


@router.post("/{campaign_id}/pause")
async def pause(campaign_id: uuid.UUID, db: DbSession, _: Operator) -> dict[str, Any]:
    await lock_shop(db)
    row = await _get(db, campaign_id, lock=True)
    if row.status not in {"SCHEDULED", "SENDING", "ACTIVE"}:
        raise ConflictError("Nothing to pause", details={"code": "CAMPAIGN_NOT_RUNNING"})
    row.status = "PAUSED"
    await db.flush()
    await _audit(db, AuditAction.CAMPAIGN_PAUSED, row)
    return view(row)


@router.post("/{campaign_id}/resume")
async def resume(campaign_id: uuid.UUID, db: DbSession, _: Manager) -> dict[str, Any]:
    await lock_shop(db)
    row = await _get(db, campaign_id, lock=True)
    if row.status != "PAUSED":
        raise ConflictError("Only a paused campaign resumes", details={"code": "CAMPAIGN_LOCKED"})
    await engine.require_ready(db, row)
    if row.kind == "FLOW":
        row.status = "ACTIVE"
    elif row.started_at:
        row.status = "SENDING"
    else:
        row.status = "SCHEDULED" if row.scheduled_at else "DRAFT"
    row.last_error = None
    await db.flush()
    await _audit(db, AuditAction.CAMPAIGN_RESUMED, row)
    return view(row)


@router.post("/{campaign_id}/cancel")
async def cancel(campaign_id: uuid.UUID, db: DbSession, _: Manager) -> dict[str, Any]:
    await lock_shop(db)
    row = await _get(db, campaign_id, lock=True)
    if row.status in {"COMPLETED", "CANCELLED"}:
        raise ConflictError("Already finished", details={"code": "CAMPAIGN_LOCKED"})
    await engine.cancel(db, row, utc_now())
    await db.flush()
    await _audit(db, AuditAction.CAMPAIGN_CANCELLED, row)
    return view(row)
