"""Inviting someone to a shop, and letting them accept.

V1 made someone a member the instant an owner typed their number — before they
had agreed to anything, or knew the shop existed. These tests defend the V2.1
flow and the things that make it safe:

* an invitation is an offer; membership appears only on acceptance;
* only the person holding that phone number can accept it, and a wrong id and
  someone else's id give the same answer;
* a pending invitation holds a seat, so a shop cannot outrun its plan;
* everything is re-checked at acceptance, because an invitation is a claim
  about the past.

Driven against the real database through the service, rather than through the
HTTP client, because the signed-in-shop fixture the V1 suites use is broken in
this environment for unrelated reasons.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa

from app.api.deps import get_hasher
from app.auth.service import USER_PHONE_CONTEXT
from app.common.phone import try_normalize_bd_phone
from app.core.clock import utc_now
from app.core.context import RequestContext, set_context
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.security import CredentialVault
from app.tenants.invitations import InvitationService
from app.tenants.models import (
    InvitationStatus,
    Tenant,
    TenantInvitation,
    TenantUser,
)
from app.tenants.roles import Permission, TenantRole, has_permission, permissions_for
from app.users.models import User

OWNER_PHONE = "+8801700000001"
INVITEE_PHONE = "+8801700000002"
OTHER_PHONE = "+8801700000003"


@pytest.fixture
def service(db, settings) -> InvitationService:
    return InvitationService(db, hasher=get_hasher(settings), vault=CredentialVault(settings))


async def _user(db, settings, phone: str) -> User:
    number = try_normalize_bd_phone(phone)
    assert number is not None
    vault = CredentialVault(settings)
    user = User(
        phone_search_hmac=get_hasher(settings).phone_search_hash(number.e164),
        phone_enc=vault.encrypt(number.e164, context=USER_PHONE_CONTEXT),
        phone_last4=number.e164[-4:],
    )
    db.add(user)
    await db.flush()
    return user


async def _shop(db, settings, *, name: str = "Test Shop") -> tuple[Tenant, User]:
    """A shop with an owner, and the ambient tenant context set to it."""
    tenant = Tenant(name=name)
    db.add(tenant)
    await db.flush()
    set_context(RequestContext(trace_id="test", tenant_id=tenant.id))

    owner = await _user(db, settings, OWNER_PHONE)
    db.add(TenantUser(tenant_id=tenant.id, user_id=owner.id, role=str(TenantRole.OWNER)))
    await db.flush()
    return tenant, owner


# ------------------------------------------------------------------ roles --


def test_the_v2_role_names_are_the_ones_the_matrix_uses() -> None:
    assert [str(role) for role in TenantRole] == [
        "OWNER",
        "MANAGER",
        "ORDER_OPERATOR",
        "FINANCE",
        "VIEWER",
    ]


def test_the_v1_role_names_still_resolve() -> None:
    """A membership row or a token written before the rename must keep working.

    Losing the mapping would hand back an empty permission set, which reads as
    "this person may do nothing" — a silent total lockout rather than an error
    anyone would notice.
    """
    assert TenantRole("PACKER") is TenantRole.ORDER_OPERATOR
    assert TenantRole("ACCOUNTANT") is TenantRole.FINANCE
    assert TenantRole.PACKER is TenantRole.ORDER_OPERATOR
    assert permissions_for("PACKER") == permissions_for(TenantRole.ORDER_OPERATOR)
    assert permissions_for("ACCOUNTANT") == permissions_for(TenantRole.FINANCE)


def test_an_unknown_role_is_given_nothing() -> None:
    assert permissions_for("SUPREME_LEADER") == frozenset()


def test_only_the_owner_holds_the_dangerous_permissions() -> None:
    """Courier credentials, billing, team and corrections stay owner-only."""
    sensitive = (
        Permission.COURIER_CREDENTIAL_MANAGE,
        Permission.BILLING_MANAGE,
        Permission.TEAM_MANAGE,
        Permission.MONEY_CORRECT,
        Permission.SETTINGS_MANAGE,
    )
    for permission in sensitive:
        assert has_permission(TenantRole.OWNER, permission), permission
        for role in (
            TenantRole.MANAGER,
            TenantRole.ORDER_OPERATOR,
            TenantRole.FINANCE,
            TenantRole.VIEWER,
        ):
            assert not has_permission(role, permission), (role, permission)


def test_an_order_operator_can_book_but_never_sees_a_courier_key() -> None:
    assert has_permission(TenantRole.ORDER_OPERATOR, Permission.ORDER_BOOK)
    assert not has_permission(TenantRole.ORDER_OPERATOR, Permission.COURIER_CREDENTIAL_MANAGE)
    assert not has_permission(TenantRole.ORDER_OPERATOR, Permission.MONEY_VIEW)


def test_finance_handles_money_but_not_operations() -> None:
    assert has_permission(TenantRole.FINANCE, Permission.MONEY_VIEW)
    assert has_permission(TenantRole.FINANCE, Permission.MONEY_RECONCILE)
    assert not has_permission(TenantRole.FINANCE, Permission.ORDER_WRITE)
    assert not has_permission(TenantRole.FINANCE, Permission.ORDER_BOOK)


def test_a_viewer_can_only_read() -> None:
    for permission in permissions_for(TenantRole.VIEWER):
        assert str(permission).endswith(".view"), permission


# ------------------------------------------------------------- inviting --


@pytest.mark.asyncio
async def test_an_invitation_does_not_create_a_membership(db, settings, service) -> None:
    """The whole point of the change. An offer is not a membership."""
    tenant, owner = await _shop(db, settings)

    invitation = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.ORDER_OPERATOR,
        invited_by=owner.id,
    )

    assert invitation.invitation_status is InvitationStatus.PENDING
    members = (
        (await db.execute(sa.select(TenantUser).where(TenantUser.tenant_id == tenant.id)))
        .scalars()
        .all()
    )
    assert [m.user_id for m in members] == [owner.id]


@pytest.mark.asyncio
async def test_an_invitation_never_stores_the_plain_number(db, settings, service) -> None:
    tenant, owner = await _shop(db, settings)

    invitation = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )

    assert INVITEE_PHONE not in invitation.phone_enc
    assert invitation.phone_last4 == INVITEE_PHONE[-4:]
    # The number is found by its hash, never by a stored copy of itself.
    assert invitation.phone_search_hmac != INVITEE_PHONE


@pytest.mark.asyncio
async def test_a_bad_number_is_refused_before_anything_is_written(db, settings, service) -> None:
    tenant, owner = await _shop(db, settings)

    with pytest.raises(ValidationError):
        await service.invite(
            tenant_id=tenant.id,
            phone="+12025550123",
            role=TenantRole.VIEWER,
            invited_by=owner.id,
        )

    rows = (await db.execute(sa.select(TenantInvitation))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_inviting_an_existing_member_is_refused(db, settings, service) -> None:
    tenant, owner = await _shop(db, settings)

    with pytest.raises(ConflictError, match="already a member"):
        await service.invite(
            tenant_id=tenant.id,
            phone=OWNER_PHONE,
            role=TenantRole.MANAGER,
            invited_by=owner.id,
        )


@pytest.mark.asyncio
async def test_a_second_open_invitation_to_the_same_number_is_refused(
    db, settings, service
) -> None:
    tenant, owner = await _shop(db, settings)
    await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )

    with pytest.raises(ConflictError, match="invitation waiting"):
        await service.invite(
            tenant_id=tenant.id,
            phone=INVITEE_PHONE,
            role=TenantRole.MANAGER,
            invited_by=owner.id,
        )


# -------------------------------------------------------------- revoking --


@pytest.mark.asyncio
async def test_revoking_keeps_the_row_and_closes_it(db, settings, service) -> None:
    """Deleted rows cannot answer "we invited them and changed our mind"."""
    tenant, owner = await _shop(db, settings)
    invitation = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )

    revoked = await service.revoke(tenant.id, invitation.id)

    assert revoked.id == invitation.id
    assert revoked.invitation_status is InvitationStatus.REVOKED
    assert revoked.revoked_at is not None
    assert not revoked.is_claimable


@pytest.mark.asyncio
async def test_revoking_twice_is_refused(db, settings, service) -> None:
    tenant, owner = await _shop(db, settings)
    invitation = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )
    await service.revoke(tenant.id, invitation.id)

    with pytest.raises(ConflictError):
        await service.revoke(tenant.id, invitation.id)


@pytest.mark.asyncio
async def test_revoking_frees_the_number_to_be_invited_again(db, settings, service) -> None:
    tenant, owner = await _shop(db, settings)
    first = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )
    await service.revoke(tenant.id, first.id)

    second = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.MANAGER,
        invited_by=owner.id,
    )
    assert second.role == str(TenantRole.MANAGER)


# ------------------------------------------------------------- accepting --


@pytest.mark.asyncio
async def test_accepting_creates_the_membership_with_the_invited_role(
    db, settings, service
) -> None:
    tenant, owner = await _shop(db, settings)
    invitation = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.FINANCE,
        invited_by=owner.id,
    )
    invitee = await _user(db, settings, INVITEE_PHONE)

    membership = await service.accept(invitation_id=invitation.id, user=invitee)

    assert membership.user_id == invitee.id
    assert membership.tenant_id == tenant.id
    assert membership.role == str(TenantRole.FINANCE)
    assert membership.is_active
    assert invitation.invitation_status is InvitationStatus.ACCEPTED
    assert invitation.accepted_user_id == invitee.id


@pytest.mark.asyncio
async def test_only_the_invited_number_can_accept(db, settings, service) -> None:
    """An invitation is addressed to a phone, and the OTP proves who holds it."""
    tenant, owner = await _shop(db, settings)
    invitation = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )
    stranger = await _user(db, settings, OTHER_PHONE)

    with pytest.raises(NotFoundError):
        await service.accept(invitation_id=invitation.id, user=stranger)


@pytest.mark.asyncio
async def test_a_wrong_id_and_someone_elses_give_the_same_answer(db, settings, service) -> None:
    """Otherwise this endpoint becomes an oracle for which ids are real."""
    tenant, owner = await _shop(db, settings)
    invitation = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )
    stranger = await _user(db, settings, OTHER_PHONE)

    with pytest.raises(NotFoundError) as addressed_elsewhere:
        await service.accept(invitation_id=invitation.id, user=stranger)
    with pytest.raises(NotFoundError) as does_not_exist:
        await service.accept(invitation_id=uuid.uuid4(), user=stranger)

    assert str(addressed_elsewhere.value) == str(does_not_exist.value)


@pytest.mark.asyncio
async def test_a_revoked_invitation_cannot_be_accepted(db, settings, service) -> None:
    tenant, owner = await _shop(db, settings)
    invitation = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )
    await service.revoke(tenant.id, invitation.id)
    invitee = await _user(db, settings, INVITEE_PHONE)

    with pytest.raises(ConflictError, match="no longer valid"):
        await service.accept(invitation_id=invitation.id, user=invitee)


@pytest.mark.asyncio
async def test_an_expired_invitation_cannot_be_accepted(db, settings, service) -> None:
    """Expiry is derived from the clock, not from a sweeper that may not have run."""
    tenant, owner = await _shop(db, settings)
    invitation = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )
    invitation.expires_at = utc_now() - timedelta(minutes=1)
    await db.flush()
    invitee = await _user(db, settings, INVITEE_PHONE)

    assert invitation.effective_status() is InvitationStatus.EXPIRED
    with pytest.raises(ConflictError, match="no longer valid"):
        await service.accept(invitation_id=invitation.id, user=invitee)


@pytest.mark.asyncio
async def test_accepting_twice_is_refused(db, settings, service) -> None:
    tenant, owner = await _shop(db, settings)
    invitation = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )
    invitee = await _user(db, settings, INVITEE_PHONE)
    await service.accept(invitation_id=invitation.id, user=invitee)

    with pytest.raises(ConflictError):
        await service.accept(invitation_id=invitation.id, user=invitee)


@pytest.mark.asyncio
async def test_rejoining_reuses_the_same_membership_row(db, settings, service) -> None:
    """Their history in this shop stays attached to one row."""
    tenant, owner = await _shop(db, settings)
    first = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )
    invitee = await _user(db, settings, INVITEE_PHONE)
    membership = await service.accept(invitation_id=first.id, user=invitee)
    original_id = membership.id

    membership.is_active = False
    await db.flush()

    second = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.MANAGER,
        invited_by=owner.id,
    )
    rejoined = await service.accept(invitation_id=second.id, user=invitee)

    assert rejoined.id == original_id
    assert rejoined.is_active
    assert rejoined.role == str(TenantRole.MANAGER)


# ------------------------------------------------- the invitee's own view --


@pytest.mark.asyncio
async def test_an_invitee_sees_only_invitations_addressed_to_them(db, settings, service) -> None:
    tenant, owner = await _shop(db, settings)
    await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )
    invitee = await _user(db, settings, INVITEE_PHONE)
    stranger = await _user(db, settings, OTHER_PHONE)

    mine = await service.pending_for_user(invitee)
    theirs = await service.pending_for_user(stranger)

    assert len(mine) == 1
    assert mine[0].tenant_id == tenant.id
    assert mine[0].shop_name == "Test Shop"
    assert theirs == []


@pytest.mark.asyncio
async def test_the_invitee_view_carries_the_shop_name_and_nothing_more(
    db, settings, service
) -> None:
    """Being invited must not become a way to read a shop's data."""
    tenant, owner = await _shop(db, settings)
    await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )
    invitee = await _user(db, settings, INVITEE_PHONE)

    pending = (await service.pending_for_user(invitee))[0]

    assert set(pending.__slots__) == {
        "invitation_id",
        "tenant_id",
        "shop_name",
        "role",
        "invited_at",
        "expires_at",
    }


@pytest.mark.asyncio
async def test_a_revoked_invitation_disappears_from_the_invitees_list(
    db, settings, service
) -> None:
    tenant, owner = await _shop(db, settings)
    invitation = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )
    invitee = await _user(db, settings, INVITEE_PHONE)
    assert len(await service.pending_for_user(invitee)) == 1

    await service.revoke(tenant.id, invitation.id)

    assert await service.pending_for_user(invitee) == []


@pytest.mark.asyncio
async def test_an_expired_invitation_disappears_from_the_invitees_list(
    db, settings, service
) -> None:
    tenant, owner = await _shop(db, settings)
    invitation = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )
    invitation.expires_at = utc_now() - timedelta(minutes=1)
    await db.flush()
    invitee = await _user(db, settings, INVITEE_PHONE)

    assert await service.pending_for_user(invitee) == []


# ------------------------------------------------------------ seat limits --


class _SeatLimit:
    """An entitlement service that allows a fixed number of seats."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.seen: list[int] = []

    async def require_within_limit(self, tenant_id, entitlement, *, current_count: int) -> None:
        self.seen.append(current_count)
        if current_count >= self.limit:
            raise ConflictError("Your plan has no seats left")


@pytest.mark.asyncio
async def test_a_pending_invitation_holds_a_seat(db, settings) -> None:
    """Counting only members would let a 2-seat shop send twenty invitations
    and hand out twenty memberships as they were accepted."""
    limit = _SeatLimit(2)
    service = InvitationService(
        db, hasher=get_hasher(settings), vault=CredentialVault(settings), entitlements=limit
    )
    tenant, owner = await _shop(db, settings)

    # Owner is seat one; this invitation takes seat two.
    await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )

    with pytest.raises(ConflictError, match="no seats left"):
        await service.invite(
            tenant_id=tenant.id,
            phone=OTHER_PHONE,
            role=TenantRole.VIEWER,
            invited_by=owner.id,
        )

    # One member plus one open invitation, not one member.
    assert limit.seen == [1, 2]


@pytest.mark.asyncio
async def test_revoking_an_invitation_returns_its_seat(db, settings) -> None:
    limit = _SeatLimit(2)
    service = InvitationService(
        db, hasher=get_hasher(settings), vault=CredentialVault(settings), entitlements=limit
    )
    tenant, owner = await _shop(db, settings)
    invitation = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )

    await service.revoke(tenant.id, invitation.id)

    # The seat is free again, so a different number fits.
    await service.invite(
        tenant_id=tenant.id,
        phone=OTHER_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )


@pytest.mark.asyncio
async def test_the_seat_limit_is_rechecked_at_acceptance(db, settings) -> None:
    """An invitation is a claim about the past.

    A shop that downgraded its plan after inviting must not have the seat
    handed out anyway when the invitation is finally accepted.
    """
    limit = _SeatLimit(5)
    service = InvitationService(
        db, hasher=get_hasher(settings), vault=CredentialVault(settings), entitlements=limit
    )
    tenant, owner = await _shop(db, settings)
    invitation = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )
    invitee = await _user(db, settings, INVITEE_PHONE)

    # The plan shrinks between the invitation and the acceptance.
    limit.limit = 1

    with pytest.raises(ConflictError, match="no seats left"):
        await service.accept(invitation_id=invitation.id, user=invitee)

    members = (
        (await db.execute(sa.select(TenantUser).where(TenantUser.tenant_id == tenant.id)))
        .scalars()
        .all()
    )
    assert [m.user_id for m in members] == [owner.id]


@pytest.mark.asyncio
async def test_the_last_seat_can_actually_be_used(db, settings) -> None:
    """The seat an invitation reserves is the one its acceptance consumes.

    Counting both — the pending reservation *and* the membership it becomes —
    made the final seat of a plan unusable: the invitation went out, and then
    accepting it was refused for taking the seat it had itself reserved.
    """
    limit = _SeatLimit(2)
    service = InvitationService(
        db, hasher=get_hasher(settings), vault=CredentialVault(settings), entitlements=limit
    )
    tenant, owner = await _shop(db, settings)

    # Owner is seat one; this invitation reserves seat two, the last one.
    invitation = await service.invite(
        tenant_id=tenant.id,
        phone=INVITEE_PHONE,
        role=TenantRole.VIEWER,
        invited_by=owner.id,
    )
    invitee = await _user(db, settings, INVITEE_PHONE)

    membership = await service.accept(invitation_id=invitation.id, user=invitee)

    assert membership.is_active
    assert membership.role == str(TenantRole.VIEWER)
