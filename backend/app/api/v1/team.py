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
    CurrentPrincipal,
    DbSession,
    EntitlementsDep,
    Principal,
    SettingsDep,
    get_hasher,
    require_permission,
)
from app.common.audit import AuditAction, record_audit
from app.core.errors import ConflictError, NotFoundError
from app.tenants.invitations import InvitationService
from app.tenants.models import TenantInvitation, TenantUser
from app.tenants.roles import Permission, TenantRole, permissions_for
from app.users.models import User

router = APIRouter(prefix="/team", tags=["team"])

TeamManager = Annotated[Principal, Depends(require_permission(Permission.TEAM_MANAGE))]


class TeamMemberResponse(BaseModel):
    """A shop member. The phone is masked; the full number is never listed."""

    user_id: uuid.UUID
    role: str
    is_active: bool
    masked_phone: str | None
    display_name: str | None
    joined_at: str
    permissions: list[str]
    is_self: bool


class InviteMemberPayload(BaseModel):
    phone: str = Field(min_length=6, max_length=20)
    role: TenantRole = TenantRole.ORDER_OPERATOR
    display_name: str | None = Field(default=None, max_length=120)


class InvitationResponse(BaseModel):
    """An invitation as the *shop* sees it.

    The number is shown as its last four only. An owner needs to recognise
    which invitation is which, not to read the number back out of the system.
    """

    id: uuid.UUID
    role: str
    status: str
    phone_last4: str
    display_name: str | None
    invited_at: str
    expires_at: str
    permissions: list[str]


class PendingInvitationResponse(BaseModel):
    """An invitation as the *invitee* sees it, before they join.

    Carries the shop's name and nothing else about the shop: someone who has
    not accepted is not a member, and being invited must not become a way to
    read a shop's data.
    """

    invitation_id: uuid.UUID
    shop_name: str
    role: str
    invited_at: str
    expires_at: str
    permissions: list[str]


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


def _invitation_response(invitation: TenantInvitation) -> InvitationResponse:
    return InvitationResponse(
        id=invitation.id,
        role=invitation.role,
        # Derived, so an invitation that has run out reads as EXPIRED even
        # though no sweeper has touched the row.
        status=str(invitation.effective_status()),
        phone_last4=invitation.phone_last4,
        display_name=invitation.display_name,
        invited_at=invitation.created_at.isoformat(),
        expires_at=invitation.expires_at.isoformat(),
        permissions=sorted(str(p) for p in permissions_for(invitation.role)),
    )


def _invitations(db: DbSession, settings: SettingsDep, entitlements) -> InvitationService:
    from app.api.deps import get_vault

    return InvitationService(
        db,
        hasher=get_hasher(settings),
        vault=get_vault(settings),
        entitlements=entitlements,
    )


@router.post(
    "/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite someone to this shop",
)
async def invite_member(
    payload: InviteMemberPayload,
    principal: TeamManager,
    db: DbSession,
    settings: SettingsDep,
    entitlements: EntitlementsDep,
) -> InvitationResponse:
    """Offer membership to a phone number.

    An offer, not a membership: the person joins when they accept. V1 added
    them the instant the number was typed, which made someone a member of a
    shop before they had agreed to anything.

    There is no emailed link. Sign-in is OTP on a Bangladeshi mobile, so the
    number already is the identity; a token would add a second secret to leak
    while proving less than the OTP the invitee has to pass anyway.

    A pending invitation holds a seat against the team limit, so a shop cannot
    outrun its plan by sending more invitations than it has room for.
    """
    invitation = await _invitations(db, settings, entitlements).invite(
        tenant_id=principal.require_tenant(),
        phone=payload.phone,
        role=payload.role,
        invited_by=principal.user_id,
        display_name=payload.display_name,
    )
    return _invitation_response(invitation)


@router.get(
    "/invitations",
    response_model=list[InvitationResponse],
    summary="Invitations this shop is waiting on",
)
async def list_invitations(
    principal: TeamManager,
    db: DbSession,
    settings: SettingsDep,
    entitlements: EntitlementsDep,
    include_closed: bool = False,
) -> list[InvitationResponse]:
    rows = await _invitations(db, settings, entitlements).list_for_shop(
        principal.require_tenant(), include_closed=include_closed
    )
    return [_invitation_response(row) for row in rows]


@router.delete(
    "/invitations/{invitation_id}",
    response_model=InvitationResponse,
    summary="Withdraw an invitation",
)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    principal: TeamManager,
    db: DbSession,
    settings: SettingsDep,
    entitlements: EntitlementsDep,
) -> InvitationResponse:
    invitation = await _invitations(db, settings, entitlements).revoke(
        principal.require_tenant(), invitation_id
    )
    return _invitation_response(invitation)


# --------------------------------------------------------------------------- #
# The invitee's side. These two are the only routes in this file that a
# non-member may call: by definition the caller does not belong to the shop yet,
# so they are authenticated but **not** tenant-scoped and carry no permission
# requirement. Both are keyed on the caller's own phone hash, so they can only
# ever reach invitations addressed to them.
# --------------------------------------------------------------------------- #


@router.get(
    "/invitations/mine",
    response_model=list[PendingInvitationResponse],
    summary="Shops waiting for you to join",
)
async def my_invitations(
    principal: CurrentPrincipal,
    db: DbSession,
    settings: SettingsDep,
) -> list[PendingInvitationResponse]:
    rows = await _invitations(db, settings, None).pending_for_user(principal.user)
    return [
        PendingInvitationResponse(
            invitation_id=row.invitation_id,
            shop_name=row.shop_name,
            role=row.role,
            invited_at=row.invited_at,
            expires_at=row.expires_at,
            permissions=sorted(str(p) for p in permissions_for(row.role)),
        )
        for row in rows
    ]


@router.post(
    "/invitations/{invitation_id}/accept",
    response_model=TeamMemberResponse,
    summary="Join a shop you were invited to",
)
async def accept_invitation(
    invitation_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    settings: SettingsDep,
    entitlements: EntitlementsDep,
) -> TeamMemberResponse:
    """Accept an invitation and become a member.

    The seat limit, the shop and the caller's existing membership are all
    re-checked here rather than trusted from the invitation: it is a claim
    about the past, and every one of those can have changed since it was sent.

    An invitation that does not exist and one addressed to somebody else give
    the same answer, so this cannot be used to discover which ids are real.
    """
    membership = await _invitations(db, settings, entitlements).accept(
        invitation_id=invitation_id, user=principal.user
    )
    return _to_response(membership, principal.user, viewer_id=principal.user_id)


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
