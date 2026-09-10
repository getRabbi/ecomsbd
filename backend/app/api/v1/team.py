"""Team management (master spec sections 88, 89).

V1's UI exposes only the Owner, but the authorisation primitives have to be
complete now or every endpoint gets rewritten when the team screen ships. These
routes are that completeness, exercised end to end:

*   membership is created, re-roled and removed server-side;
*   ``team_member_limit`` is a **standing** cap, counted from ``tenant_users``
    rather than from a usage counter — a removed seat frees itself, and a month
    rollover must never hand out a fifth one;
*   a shop can never be left without an Owner, and an Owner cannot demote or
    remove themselves into that state.

Everything here requires ``TEAM_MANAGE``, which only the Owner holds.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.api.deps import (
    DbSession,
    EntitlementsDep,
    Principal,
    SettingsDep,
    get_hasher,
    require_permission,
)
from app.common.audit import AuditAction, record_audit
from app.common.phone import try_normalize_bd_phone
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.entitlements.catalog import Entitlement
from app.tenants.models import TenantUser
from app.tenants.roles import Permission, TenantRole, permissions_for
from app.users.models import User

router = APIRouter(prefix="/team", tags=["team"])

TeamManager = Annotated[Principal, Depends(require_permission(Permission.TEAM_MANAGE))]


class TeamMemberResponse(BaseModel):
    """A shop member. The phone is masked; the full number is never listed."""

    user_id: uuid.UUID
    role: str
    is_active: bool
    masked_phone: str
    display_name: str | None
    joined_at: str
    permissions: list[str]
    is_self: bool


class InviteMemberPayload(BaseModel):
    phone: str = Field(min_length=6, max_length=20)
    role: TenantRole = TenantRole.PACKER
    display_name: str | None = Field(default=None, max_length=120)


class ChangeRolePayload(BaseModel):
    role: TenantRole


async def _members(db: DbSession, tenant_id: uuid.UUID) -> list[tuple[TenantUser, User]]:
    return list(
        (
            await db.execute(
                sa.select(TenantUser, User)
                .join(User, User.id == TenantUser.user_id)
                .where(TenantUser.tenant_id == tenant_id)
                .order_by(TenantUser.joined_at)
            )
        )
        .tuples()
        .all()
    )


def _to_response(membership: TenantUser, user: User, *, viewer_id: uuid.UUID) -> TeamMemberResponse:
    return TeamMemberResponse(
        user_id=user.id,
        role=membership.role,
        is_active=membership.is_active,
        masked_phone=user.masked_phone,
        display_name=user.display_name,
        joined_at=membership.joined_at.isoformat(),
        permissions=sorted(str(p) for p in permissions_for(membership.role)),
        is_self=user.id == viewer_id,
    )


@router.get("", response_model=list[TeamMemberResponse], summary="Everyone in this shop")
async def list_members(principal: TeamManager, db: DbSession) -> list[TeamMemberResponse]:
    rows = await _members(db, principal.require_tenant())
    return [
        _to_response(membership, user, viewer_id=principal.user_id) for membership, user in rows
    ]


@router.get("/roles", response_model=dict[str, list[str]], summary="The permission matrix")
async def list_roles(principal: TeamManager) -> dict[str, list[str]]:
    """What each role can do (master spec section 88).

    Returned to the client so the team screen explains a role by what it
    unlocks rather than by its name, and so the matrix has exactly one
    definition — this one.
    """
    return {str(role): sorted(str(p) for p in permissions_for(role)) for role in TenantRole}


@router.post(
    "",
    response_model=TeamMemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add someone to this shop",
)
async def invite_member(
    payload: InviteMemberPayload,
    principal: TeamManager,
    db: DbSession,
    settings: SettingsDep,
    entitlements: EntitlementsDep,
) -> TeamMemberResponse:
    """Add a member by phone number.

    There is no email invitation: sign-in is OTP on a Bangladeshi mobile
    number, so the number *is* the identity. Adding someone who has never
    opened the app creates their user record; they get access the moment they
    sign in with that number.

    A second Owner is allowed — a shop with one owner and one lost phone is a
    support ticket nobody enjoys.
    """
    tenant_id = principal.require_tenant()
    number = try_normalize_bd_phone(payload.phone)
    if number is None:
        raise ValidationError("A valid Bangladeshi mobile number is required")

    active_count = int(
        (
            await db.execute(
                sa.select(sa.func.count())
                .select_from(TenantUser)
                .where(TenantUser.tenant_id == tenant_id, TenantUser.is_active.is_(True))
            )
        ).scalar_one()
    )
    await entitlements.require_within_limit(
        tenant_id, Entitlement.TEAM_MEMBER_LIMIT, current_count=active_count
    )

    hasher = get_hasher(settings)
    search_hash = hasher.phone_search_hash(number.e164)

    user = (
        await db.execute(sa.select(User).where(User.phone_search_hmac == search_hash))
    ).scalar_one_or_none()
    if user is None:
        from app.api.deps import get_vault
        from app.auth.service import USER_PHONE_CONTEXT

        vault = get_vault(settings)
        user = User(
            phone_search_hmac=search_hash,
            phone_enc=vault.encrypt(number.e164, context=USER_PHONE_CONTEXT),
            phone_last4=number.e164[-4:],
            display_name=payload.display_name,
        )
        db.add(user)
        await db.flush()

    existing = (
        await db.execute(
            sa.select(TenantUser).where(
                TenantUser.tenant_id == tenant_id, TenantUser.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.is_active:
            raise ConflictError("That number is already a member of this shop")
        # Re-activating is a role change, not a new membership: their history
        # in this shop stays attached to the same row.
        existing.is_active = True
        existing.role = str(payload.role)
        await db.flush()
        await record_audit(
            db,
            AuditAction.TENANT_MEMBER_ADDED,
            entity_type="tenant_user",
            entity_id=existing.id,
            context={"role": str(payload.role), "reactivated": True},
            tenant_id=tenant_id,
        )
        return _to_response(existing, user, viewer_id=principal.user_id)

    membership = TenantUser(
        tenant_id=tenant_id,
        user_id=user.id,
        role=str(payload.role),
        invited_by_user_id=principal.user_id,
    )
    db.add(membership)
    await db.flush()

    await record_audit(
        db,
        AuditAction.TENANT_MEMBER_ADDED,
        entity_type="tenant_user",
        entity_id=membership.id,
        context={"role": str(payload.role)},
        tenant_id=tenant_id,
    )
    return _to_response(membership, user, viewer_id=principal.user_id)


@router.patch("/{user_id}", response_model=TeamMemberResponse, summary="Change someone's role")
async def change_role(
    user_id: uuid.UUID,
    payload: ChangeRolePayload,
    principal: TeamManager,
    db: DbSession,
) -> TeamMemberResponse:
    tenant_id = principal.require_tenant()
    membership, user = await _member_or_404(db, tenant_id, user_id)

    if membership.role == str(TenantRole.OWNER) and payload.role is not TenantRole.OWNER:
        await _require_another_owner(db, tenant_id, excluding=user_id)

    previous = membership.role
    membership.role = str(payload.role)
    # Flushed inside the request, while the tenant is still in ambient scope.
    # The commit in `session_scope` runs after the principal dependency has
    # torn its context down, and the tenancy write guard refuses a flush with
    # no tenant to stamp.
    await db.flush()
    await record_audit(
        db,
        AuditAction.TENANT_MEMBER_ROLE_CHANGED,
        entity_type="tenant_user",
        entity_id=membership.id,
        context={"from": previous, "to": str(payload.role)},
        tenant_id=tenant_id,
    )
    return _to_response(membership, user, viewer_id=principal.user_id)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove someone")
async def remove_member(
    user_id: uuid.UUID,
    principal: TeamManager,
    db: DbSession,
) -> None:
    """Deactivate a membership.

    Deactivated, not deleted: the orders they created keep pointing at a real
    person, and a rejoin reuses the same row. Their live sessions are revoked
    so access ends immediately rather than at token expiry.
    """
    tenant_id = principal.require_tenant()
    membership, _user = await _member_or_404(db, tenant_id, user_id)

    if membership.role == str(TenantRole.OWNER):
        await _require_another_owner(db, tenant_id, excluding=user_id)

    membership.is_active = False

    from app.auth.models import AuthSession, RevocationReason

    sessions = (
        (
            await db.execute(
                sa.select(AuthSession).where(
                    AuthSession.user_id == user_id,
                    AuthSession.tenant_id == tenant_id,
                    AuthSession.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    from app.core.clock import utc_now

    for session_row in sessions:
        session_row.revoked_at = utc_now()
        session_row.revoked_reason = str(RevocationReason.ADMIN_ACTION)

    await db.flush()  # see change_role: flush while the tenant is in scope
    await record_audit(
        db,
        AuditAction.TENANT_MEMBER_REMOVED,
        entity_type="tenant_user",
        entity_id=membership.id,
        context={"role": membership.role, "sessions_revoked": len(sessions)},
        tenant_id=tenant_id,
    )


async def _member_or_404(
    db: DbSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[TenantUser, User]:
    row = (
        await db.execute(
            sa.select(TenantUser, User)
            .join(User, User.id == TenantUser.user_id)
            .where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == user_id)
        )
    ).first()
    if row is None:
        raise NotFoundError("That person is not a member of this shop")
    return row[0], row[1]


async def _require_another_owner(
    db: DbSession, tenant_id: uuid.UUID, *, excluding: uuid.UUID
) -> None:
    """Refuse to leave a shop with no Owner.

    Only an Owner holds billing, credentials and team management. A shop with
    none is locked out of its own settings and needs support to recover.
    """
    remaining = int(
        (
            await db.execute(
                sa.select(sa.func.count())
                .select_from(TenantUser)
                .where(
                    TenantUser.tenant_id == tenant_id,
                    TenantUser.role == str(TenantRole.OWNER),
                    TenantUser.is_active.is_(True),
                    TenantUser.user_id != excluding,
                )
            )
        ).scalar_one()
    )
    if remaining == 0:
        raise ConflictError(
            "A shop must always have at least one Owner. Make someone else an Owner first."
        )


__all__ = ["router"]
