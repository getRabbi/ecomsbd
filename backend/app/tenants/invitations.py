"""Inviting someone to a shop, and letting them accept.

V1 added a member the instant an owner typed a number: the person became active
before they had agreed to anything, and before they knew the shop existed. V2.1
makes it an offer.

Three decisions shape this module.

**The invitation is keyed on the phone number, not a mailed token.** Sign-in is
OTP on a Bangladeshi mobile, so the number already *is* the identity. A token
would add a second secret that can be forwarded, screenshotted or leaked, and it
would prove strictly less than the OTP the invitee must pass anyway. Acceptance
therefore requires an authenticated session whose own phone matches the
invitation — possession of the number, verified exactly the way every login is.

**A pending invitation holds a seat.** The team limit counts active members
*plus* open invitations. Counting only members would let a shop on three seats
send thirty invitations and hand out thirty memberships the moment they were
accepted.

**Accepting re-checks everything.** An invitation is a claim about the past: the
seat limit, the role, the shop's status and the person's membership can all have
changed since it was sent. Every one of them is re-evaluated at acceptance, not
trusted from the row.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

#: The vault context for an invitee's number, shared with the user record so a
#: key rotation covers both and neither holds a plaintext number.
from app.auth.service import USER_PHONE_CONTEXT as _PHONE_CONTEXT
from app.common.audit import AuditAction, record_audit
from app.common.phone import try_normalize_bd_phone
from app.core.clock import utc_now
from app.core.errors import ConflictError, ErrorCode, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import CredentialVault, SecretHasher
from app.db.tenancy import allow_cross_tenant
from app.entitlements.catalog import Entitlement
from app.entitlements.service import EntitlementService
from app.tenants.models import InvitationStatus, Tenant, TenantInvitation, TenantUser
from app.tenants.roles import TenantRole
from app.users.models import User

__all__ = [
    "INVITATION_TTL_DAYS",
    "InvitationService",
    "PendingInvitation",
]

log = get_logger(__name__)

#: How long an invitation stays claimable. Long enough that a seller on a trip
#: does not lose it, short enough that a number typed wrong a month ago cannot
#: still be claimed by whoever holds it now.
INVITATION_TTL_DAYS = 14


@dataclass(frozen=True, slots=True)
class PendingInvitation:
    """An invitation as the *invitee* sees it, before they belong to the shop.

    Carries the shop's name because that is the one thing they need to decide,
    and nothing else about the shop: someone who has not accepted yet is not a
    member and must not be able to read a shop's data by being invited to it.
    """

    invitation_id: uuid.UUID
    tenant_id: uuid.UUID
    shop_name: str
    role: str
    invited_at: str
    expires_at: str


class InvitationService:
    """Creates, lists, revokes and accepts invitations."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        hasher: SecretHasher,
        vault: CredentialVault,
        entitlements: EntitlementService | None = None,
    ) -> None:
        self._db = session
        self._hasher = hasher
        self._vault = vault
        self._entitlements = entitlements

    # ------------------------------------------------------------ inviting --

    async def invite(
        self,
        *,
        tenant_id: uuid.UUID,
        phone: str,
        role: TenantRole,
        invited_by: uuid.UUID,
        display_name: str | None = None,
    ) -> TenantInvitation:
        """Offer membership to a phone number.

        Refuses before writing anything if the number is not a valid
        Bangladeshi mobile, is already an active member, or already has an open
        invitation to this shop.
        """
        number = try_normalize_bd_phone(phone)
        if number is None:
            raise ValidationError("A valid Bangladeshi mobile number is required")

        search_hash = self._hasher.phone_search_hash(number.e164)

        existing_user = (
            await self._db.execute(sa.select(User).where(User.phone_search_hmac == search_hash))
        ).scalar_one_or_none()
        if existing_user is not None:
            membership = (
                await self._db.execute(
                    sa.select(TenantUser).where(
                        TenantUser.tenant_id == tenant_id,
                        TenantUser.user_id == existing_user.id,
                        TenantUser.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if membership is not None:
                raise ConflictError("That number is already a member of this shop")

        open_invite = await self._open_invitation(tenant_id, search_hash)
        if open_invite is not None:
            raise ConflictError(
                "That number already has an invitation waiting. Revoke it first to change the role."
            )

        await self._require_seat(tenant_id)

        invitation = TenantInvitation(
            tenant_id=tenant_id,
            phone_search_hmac=search_hash,
            # Encrypted with the same context the user record uses, so one key
            # rotation covers both and neither holds a plaintext number.
            phone_enc=self._vault.encrypt(number.e164, context=_PHONE_CONTEXT),
            phone_last4=number.e164[-4:],
            role=str(role),
            status=str(InvitationStatus.PENDING),
            display_name=display_name,
            invited_by_user_id=invited_by,
            expires_at=utc_now() + timedelta(days=INVITATION_TTL_DAYS),
        )
        self._db.add(invitation)
        await self._db.flush()

        await record_audit(
            self._db,
            AuditAction.TENANT_MEMBER_INVITED,
            entity_type="tenant_invitation",
            entity_id=invitation.id,
            context={
                "role": str(role),
                # Last four only: an audit row is read by support.
                "phone_last4": invitation.phone_last4,
            },
            tenant_id=tenant_id,
        )
        return invitation

    async def list_for_shop(
        self, tenant_id: uuid.UUID, *, include_closed: bool = False
    ) -> list[TenantInvitation]:
        """Invitations this shop has sent, open ones first."""
        statement = sa.select(TenantInvitation).where(TenantInvitation.tenant_id == tenant_id)
        if not include_closed:
            statement = statement.where(TenantInvitation.status == str(InvitationStatus.PENDING))
        rows = list(
            (await self._db.execute(statement.order_by(TenantInvitation.created_at.desc())))
            .scalars()
            .all()
        )
        return rows

    async def revoke(self, tenant_id: uuid.UUID, invitation_id: uuid.UUID) -> TenantInvitation:
        """Withdraw an invitation that has not been accepted.

        The row is kept and marked, not deleted: "we invited them and changed
        our mind" is exactly the kind of thing someone asks about later.
        """
        invitation = (
            await self._db.execute(
                sa.select(TenantInvitation).where(
                    TenantInvitation.tenant_id == tenant_id,
                    TenantInvitation.id == invitation_id,
                )
            )
        ).scalar_one_or_none()
        if invitation is None:
            raise NotFoundError("That invitation does not exist")
        if not invitation.invitation_status.is_open:
            raise ConflictError("That invitation is no longer open")

        invitation.status = str(InvitationStatus.REVOKED)
        invitation.revoked_at = utc_now()
        await self._db.flush()

        await record_audit(
            self._db,
            AuditAction.TENANT_INVITATION_REVOKED,
            entity_type="tenant_invitation",
            entity_id=invitation.id,
            context={"role": invitation.role, "phone_last4": invitation.phone_last4},
            tenant_id=tenant_id,
        )
        return invitation

    # ------------------------------------------------------------ accepting --

    async def pending_for_user(self, user: User) -> list[PendingInvitation]:
        """Invitations waiting for this person, across every shop.

        A deliberate, narrow cross-tenant read: the invitee is by definition not
        a member of the shop yet, so there is no tenant to scope to. It is
        keyed on their own phone hash, so it can only ever return invitations
        addressed to them, and it returns the shop's *name* and nothing else.
        """
        if not user.phone_search_hmac:
            return []

        now = utc_now()
        with allow_cross_tenant("listing invitations addressed to this phone number"):
            rows = list(
                (
                    await self._db.execute(
                        sa.select(TenantInvitation, Tenant)
                        .join(Tenant, Tenant.id == TenantInvitation.tenant_id)
                        .where(
                            TenantInvitation.phone_search_hmac == user.phone_search_hmac,
                            TenantInvitation.status == str(InvitationStatus.PENDING),
                            TenantInvitation.expires_at > now,
                        )
                        .order_by(TenantInvitation.created_at.desc())
                    )
                )
                .tuples()
                .all()
            )

        return [
            PendingInvitation(
                invitation_id=invitation.id,
                tenant_id=invitation.tenant_id,
                shop_name=tenant.name,
                role=invitation.role,
                invited_at=invitation.created_at.isoformat(),
                expires_at=invitation.expires_at.isoformat(),
            )
            for invitation, tenant in rows
        ]

    async def accept(self, *, invitation_id: uuid.UUID, user: User) -> TenantUser:
        """Join a shop by accepting its invitation.

        Everything is re-checked here rather than trusted from the row, because
        an invitation is a claim about the past: the seat limit, the shop's
        existence and the person's membership can all have changed since it was
        sent.
        """
        with allow_cross_tenant("accepting an invitation to a shop not yet joined"):
            invitation = (
                await self._db.execute(
                    sa.select(TenantInvitation).where(TenantInvitation.id == invitation_id)
                )
            ).scalar_one_or_none()

        # The same answer for "no such invitation" and "not addressed to you".
        # Distinguishing them would turn this endpoint into an oracle for which
        # invitation ids exist.
        if invitation is None or invitation.phone_search_hmac != user.phone_search_hmac:
            raise NotFoundError("That invitation does not exist")

        if not invitation.is_claimable:
            raise ConflictError(
                "That invitation is no longer valid. Ask the shop to send a new one.",
                code=ErrorCode.CONFLICT,
            )

        tenant_id = invitation.tenant_id

        with allow_cross_tenant("completing an invitation acceptance"):
            existing = (
                await self._db.execute(
                    sa.select(TenantUser).where(
                        TenantUser.tenant_id == tenant_id,
                        TenantUser.user_id == user.id,
                    )
                )
            ).scalar_one_or_none()

            if existing is not None and existing.is_active:
                # Already in. Close the invitation rather than leaving it open
                # forever, and report it rather than silently succeeding.
                invitation.status = str(InvitationStatus.ACCEPTED)
                invitation.accepted_at = utc_now()
                invitation.accepted_user_id = user.id
                await self._db.flush()
                raise ConflictError("You are already a member of that shop")

            # Excluding this invitation: it is still PENDING, and the seat it
            # has been holding is the very one this acceptance consumes.
            # Counting both made the last seat of a plan unusable — the
            # invitation reserved it and then the acceptance was refused for
            # taking it.
            await self._require_seat(
                tenant_id, cross_tenant=True, excluding_invitation_id=invitation.id
            )

            if existing is not None:
                # Rejoining: the same row keeps their history in this shop.
                existing.is_active = True
                existing.role = invitation.role
                membership = existing
            else:
                membership = TenantUser(
                    tenant_id=tenant_id,
                    user_id=user.id,
                    role=invitation.role,
                    invited_by_user_id=invitation.invited_by_user_id,
                )
                self._db.add(membership)

            invitation.status = str(InvitationStatus.ACCEPTED)
            invitation.accepted_at = utc_now()
            invitation.accepted_user_id = user.id
            await self._db.flush()

            await record_audit(
                self._db,
                AuditAction.TENANT_MEMBER_ADDED,
                entity_type="tenant_user",
                entity_id=membership.id,
                context={
                    "role": membership.role,
                    "via": "invitation",
                    "invitation_id": str(invitation.id),
                },
                tenant_id=tenant_id,
            )

        log.info(
            "invitation accepted",
            extra={"operation": "invitation_accept", "role": membership.role},
        )
        return membership

    # ----------------------------------------------------------- internals --

    async def _open_invitation(
        self, tenant_id: uuid.UUID, search_hash: str
    ) -> TenantInvitation | None:
        rows = (
            (
                await self._db.execute(
                    sa.select(TenantInvitation).where(
                        TenantInvitation.tenant_id == tenant_id,
                        TenantInvitation.phone_search_hmac == search_hash,
                        TenantInvitation.status == str(InvitationStatus.PENDING),
                    )
                )
            )
            .scalars()
            .all()
        )
        # Expiry is derived, so an invitation that has run out is not "open"
        # even though its stored status still says PENDING.
        for row in rows:
            if row.is_claimable:
                return row
        return None

    async def _require_seat(
        self,
        tenant_id: uuid.UUID,
        *,
        cross_tenant: bool = False,
        excluding_invitation_id: uuid.UUID | None = None,
    ) -> None:
        """Refuse when the shop has no seat free.

        Counts active members **plus** open invitations. Counting only members
        would let a three-seat shop send thirty invitations and hand out thirty
        memberships as they were accepted.

        ``excluding_invitation_id`` is passed when an invitation is being
        accepted, so the seat it was holding is not counted twice — once as the
        reservation and again as the membership it is about to become.
        """
        if self._entitlements is None:
            return

        async def _count() -> int:
            members = int(
                (
                    await self._db.execute(
                        sa.select(sa.func.count())
                        .select_from(TenantUser)
                        .where(
                            TenantUser.tenant_id == tenant_id,
                            TenantUser.is_active.is_(True),
                        )
                    )
                ).scalar_one()
            )
            pending = 0
            for row in (
                (
                    await self._db.execute(
                        sa.select(TenantInvitation).where(
                            TenantInvitation.tenant_id == tenant_id,
                            TenantInvitation.status == str(InvitationStatus.PENDING),
                        )
                    )
                )
                .scalars()
                .all()
            ):
                if row.id == excluding_invitation_id:
                    continue
                if row.is_claimable:
                    pending += 1
            return members + pending

        if cross_tenant:
            with allow_cross_tenant("counting seats for an invitation acceptance"):
                used = await _count()
        else:
            used = await _count()

        await self._entitlements.require_within_limit(
            tenant_id, Entitlement.TEAM_MEMBER_LIMIT, current_count=used
        )
