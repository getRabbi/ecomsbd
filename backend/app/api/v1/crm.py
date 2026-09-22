"""Authenticated seller CRM. Reuses customer.view, order.write and money.view."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.analytics.customer_segments import Segment
from app.analytics.rto import RtoService
from app.api.deps import DbSession, EntitlementsDep, TenantPrincipal, require_permission
from app.api.v1.customers import CustomerServiceDep, _to_detail
from app.api.v1.rto_schemas import ObservationResponse, OutcomeCountsResponse, ParcelEventResponse
from app.common.audit import record_audit
from app.common.pagination import Page, apply_cursor, decode_cursor
from app.core.clock import business_date, utc_now
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.customers import risk
from app.customers.crm import CrmService
from app.customers.crm_models import CustomerFollowUp, CustomerTag
from app.entitlements.catalog import UNLIMITED, Entitlement
from app.profit.models import ProfitSnapshot
from app.tenants.models import TenantUser
from app.tenants.roles import Permission, permissions_for
from app.users.models import User

router = APIRouter(
    prefix="/customers",
    tags=["crm"],
    dependencies=[Depends(require_permission(Permission.CUSTOMER_VIEW))],
)
write = [Depends(require_permission(Permission.ORDER_WRITE))]
Limit = Annotated[int, Query(ge=1, le=100)]


class TextPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    text: str = Field(min_length=1, max_length=2000)


class TagPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=60)


class BulkTagPayload(BaseModel):
    customer_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    tag_id: uuid.UUID
    remove: bool = False


class FollowUpPayload(TextPayload):
    due_at: AwareDatetime
    assignee_id: uuid.UUID | None = None


class CompletePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    completed: bool


async def money_lock(
    principal: TenantPrincipal, entitlements: EntitlementsDep, db: DbSession
) -> str | None:
    if not principal.can(Permission.MONEY_VIEW):
        return "PERMISSION"
    days = await entitlements.limit(principal.require_tenant(), Entitlement.PROFIT_HISTORY_DAYS)
    if days != UNLIMITED:
        earliest = business_date() - timedelta(days=max(0, days - 1))
        first = await db.scalar(sa.select(sa.func.min(ProfitSnapshot.business_date)))
        if first and first < earliest:
            return "PLAN"
    return None


@router.get("/crm")
async def list_crm(
    principal: TenantPrincipal,
    db: DbSession,
    customers: CustomerServiceDep,
    entitlements: EntitlementsDep,
    limit: Limit = 30,
    cursor: str | None = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    segment: Segment | None = None,
    tag_id: uuid.UUID | None = None,
    min_orders: Annotated[int | None, Query(ge=0)] = None,
    max_orders: Annotated[int | None, Query(ge=0)] = None,
    last_from: AwareDatetime | None = None,
    last_until: AwareDatetime | None = None,
    repeat_only: bool = False,
    flag: str | None = None,
) -> dict:
    if (min_orders is not None and max_orders is not None and min_orders > max_orders) or (
        last_from and last_until and last_from > last_until
    ):
        raise ValidationError("Invalid filter range")
    lock = await money_lock(principal, entitlements, db)
    if segment == Segment.HIGH_VALUE and lock:
        raise ForbiddenError("Historical value is unavailable")
    result = await CrmService(db, customers).listing(
        limit=limit,
        cursor=decode_cursor(cursor) if cursor else None,
        search=search,
        segment=segment,
        tag_id=tag_id,
        min_orders=max(2, min_orders or 0) if repeat_only else min_orders,
        max_orders=max_orders,
        last_from=last_from,
        last_until=last_until,
        flag=flag,
        money_allowed=lock is None,
    )
    result.update(can_write=principal.can(Permission.ORDER_WRITE), money_locked=lock)
    return result


@router.get("/crm/tags")
async def tags(
    principal: TenantPrincipal, db: DbSession, limit: Limit = 50, cursor: str | None = None
) -> Page[dict]:
    stmt = apply_cursor(
        sa.select(CustomerTag).where(CustomerTag.archived.is_(False)),
        CustomerTag,
        decode_cursor(cursor) if cursor else None,
    )
    rows = list(
        (
            await db.scalars(
                stmt.order_by(CustomerTag.created_at.desc(), CustomerTag.id.desc()).limit(limit + 1)
            )
        ).all()
    )
    return Page[dict].build(
        rows, limit=limit, serializer=lambda row: {"id": row.id, "name": row.name}
    )


@router.post("/crm/tags", dependencies=write, status_code=201)
async def create_tag(
    payload: TagPayload, principal: TenantPrincipal, db: DbSession, customers: CustomerServiceDep
) -> dict:
    row = await CrmService(db, customers).create_tag(payload.name)
    return {"id": row.id, "name": row.name}


@router.delete("/crm/tags/{tag_id}", dependencies=write)
async def archive_tag(tag_id: uuid.UUID, principal: TenantPrincipal, db: DbSession) -> dict:
    tag = await db.get(CustomerTag, tag_id)
    if tag is None:
        raise NotFoundError("Tag not found")
    tag.archived = True
    await record_audit(db, "crm.tag_archived", entity_type="customer_tag", entity_id=tag.id)
    await db.flush()
    return {"archived": True}


@router.post("/crm/bulk-tags", dependencies=write)
async def bulk_tags(
    payload: BulkTagPayload,
    principal: TenantPrincipal,
    db: DbSession,
    customers: CustomerServiceDep,
) -> dict:
    await CrmService(db, customers).bulk_tag(payload.customer_ids, payload.tag_id, payload.remove)
    return {"ok": True}


@router.get("/crm/members")
async def members(
    principal: TenantPrincipal, db: DbSession, limit: Limit = 50, cursor: str | None = None
) -> Page[dict]:
    roles = [
        str(role)
        for role in ("OWNER", "MANAGER", "ORDER_OPERATOR", "PACKER")
        if Permission.ORDER_WRITE in permissions_for(role)
    ]
    stmt = apply_cursor(
        sa.select(TenantUser, User.display_name)
        .join(User, User.id == TenantUser.user_id)
        .where(TenantUser.is_active.is_(True), TenantUser.role.in_(roles)),
        TenantUser,
        decode_cursor(cursor) if cursor else None,
    )
    rows = (
        await db.execute(
            stmt.order_by(TenantUser.created_at.desc(), TenantUser.id.desc()).limit(limit + 1)
        )
    ).all()
    names = {row[0].id: row[1] for row in rows}
    return Page[dict].build(
        [row[0] for row in rows],
        limit=limit,
        serializer=lambda row: {"id": row.user_id, "name": names[row.id]},
    )


@router.get("/{customer_id}/crm")
async def detail(
    customer_id: uuid.UUID,
    principal: TenantPrincipal,
    db: DbSession,
    customers: CustomerServiceDep,
    entitlements: EntitlementsDep,
) -> dict:
    customer = await customers.get(customer_id)
    crm = CrmService(db, customers)
    lock = await money_lock(principal, entitlements, db)
    result = await crm.listing(limit=1, customer_id=customer_id, money_allowed=lock is None)
    history = await RtoService(db).customer_history(customer_id)
    row = {**_to_detail(customer, history).model_dump(), **result["items"][0]}
    assessment = risk.assess(history)
    row.update(
        definitions=result["definitions"],
        can_write=principal.can(Permission.ORDER_WRITE),
        can_reveal=principal.can(Permission.CUSTOMER_EXPORT),
        money_locked=lock,
        risk={
            "state": str(assessment.state),
            "reasons": [str(r) for r in assessment.reasons],
            "parcels": OutcomeCountsResponse.of(history.counts),
            "recent": [ParcelEventResponse.of(event) for event in history.recent],
            "observations": [ObservationResponse.of(obs) for obs in history.observations],
        }
        if principal.can(Permission.CUSTOMER_RISK_VIEW)
        else None,
    )
    return row


@router.get("/{customer_id}/timeline")
async def timeline(
    customer_id: uuid.UUID,
    principal: TenantPrincipal,
    db: DbSession,
    customers: CustomerServiceDep,
    cursor: str | None = None,
    limit: Limit = 30,
    notes_only: bool = False,
) -> dict:
    position, separator, kind = cursor.partition(".") if cursor else ("", "", "")
    return await CrmService(db, customers).timeline(
        customer_id,
        cursor=decode_cursor(position) if position else None,
        cursor_kind=kind if separator else None,
        limit=limit,
        notes_only=notes_only,
    )


@router.post("/{customer_id}/notes", dependencies=write, status_code=201)
async def add_note(
    customer_id: uuid.UUID,
    payload: TextPayload,
    principal: TenantPrincipal,
    db: DbSession,
    customers: CustomerServiceDep,
) -> dict:
    await customers.get(customer_id)
    row = await CrmService(db, customers).activity(customer_id, "NOTE", text=payload.text)
    return {"id": row.id, "text": row.text, "actor_id": row.actor_id, "created_at": row.created_at}


def followup_response(row: CustomerFollowUp, names: dict) -> dict:
    return {
        "id": row.id,
        "text": row.text,
        "due_at": row.due_at,
        "created_at": row.created_at,
        "created_by": row.created_by,
        "author_name": names.get(row.created_by),
        "assignee_id": row.assignee_id,
        "assignee_name": names.get(row.assignee_id),
        "completed_at": row.completed_at,
        "completed_by": row.completed_by,
        "state": "COMPLETED"
        if row.completed_at
        else "OVERDUE"
        if row.due_at <= utc_now()
        else "UPCOMING",
    }


@router.get("/{customer_id}/follow-ups")
async def followups(
    customer_id: uuid.UUID,
    principal: TenantPrincipal,
    db: DbSession,
    customers: CustomerServiceDep,
    cursor: str | None = None,
    limit: Limit = 30,
    completed: bool | None = None,
) -> Page[dict]:
    await customers.get(customer_id)
    stmt = sa.select(CustomerFollowUp).where(CustomerFollowUp.customer_id == customer_id)
    if completed is not None:
        stmt = stmt.where(
            CustomerFollowUp.completed_at.is_not(None)
            if completed
            else CustomerFollowUp.completed_at.is_(None)
        )
    rows = list(
        (
            await db.scalars(
                apply_cursor(stmt, CustomerFollowUp, decode_cursor(cursor) if cursor else None)
                .order_by(CustomerFollowUp.created_at.desc(), CustomerFollowUp.id.desc())
                .limit(limit + 1)
            )
        ).all()
    )
    names = await CrmService(db, customers).actor_names(
        [value for row in rows for value in (row.created_by, row.assignee_id) if value]
    )
    return Page[dict].build(rows, limit=limit, serializer=lambda row: followup_response(row, names))


@router.post("/{customer_id}/follow-ups", dependencies=write, status_code=201)
async def add_followup(
    customer_id: uuid.UUID,
    payload: FollowUpPayload,
    principal: TenantPrincipal,
    db: DbSession,
    customers: CustomerServiceDep,
) -> dict:
    row = await CrmService(db, customers).create_followup(
        customer_id, payload.text, payload.due_at, payload.assignee_id
    )
    return followup_response(row, {})


@router.patch("/{customer_id}/follow-ups/{followup_id}", dependencies=write)
async def complete_followup(
    customer_id: uuid.UUID,
    followup_id: uuid.UUID,
    payload: CompletePayload,
    principal: TenantPrincipal,
    db: DbSession,
    customers: CustomerServiceDep,
) -> dict:
    row = await CrmService(db, customers).complete_followup(
        customer_id, followup_id, payload.completed
    )
    return followup_response(row, {})
