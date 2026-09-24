"""The account deletion workflow.

Master spec section 100, step by step, with the retention rule stated where it
is enforced rather than only in a policy document.

What is **anonymised**: anything that identifies a person — the customer's
encrypted phone, their name, their address, the search HMAC that makes them
findable, and the shop owner's own contact details.

What is **retained**: the financial ledger, receivables, payouts, consignment
outcomes and profit snapshots. Those are records of money that moved between
three parties, and section 81 makes them append-only. Deleting them would break
the invariants that let a seller trust every figure in the product, and would
destroy the courier-side evidence the seller may need themselves. After
anonymisation those rows still balance and no longer point at anybody.

What is **revoked immediately** when the request is made, before any waiting:
nothing. Access continues through the cooling-off window on purpose — a seller
who changes their mind on day three should find their shop working, not a
half-dismantled one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identities import AuthIdentity, AuthProvider
from app.auth.models import AuthSession, Device, RefreshToken, RevocationReason
from app.auth.supabase import delete_auth_identity
from app.billing.models import BillingProviderKind
from app.common.audit import AuditAction, record_audit
from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.customers.models import Customer, CustomerAddress
from app.entitlements.models import Subscription, SubscriptionStatus
from app.privacy.models import DELETION_GRACE_DAYS, DeletionRequest, DeletionStatus
from app.tenants.models import Tenant, TenantStatus, TenantUser
from app.tenants.roles import TenantRole
from app.users.models import User, UserStatus

__all__ = ["RETENTION_NOTE", "DeletionOutcome", "PrivacyService"]

#: Shown to the seller when they ask to delete, and repeated in docs/PRIVACY.md.
#: Written as a promise the code actually keeps.
RETENTION_NOTE = (
    "Your customers' names, phone numbers and addresses are permanently "
    "anonymised. Your money records — the ledger, payouts and profit history — "
    "are kept in anonymised form because they are the record of money that "
    "moved between you, your courier and your customers."
)


@dataclass(frozen=True, slots=True)
class DeletionOutcome:
    request: DeletionRequest
    steps: dict[str, int | str]


class PrivacyService:
    """Requesting, cancelling and executing account deletion."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    # ------------------------------------------------------------ requesting ---

    async def request_deletion(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        reason: str | None = None,
    ) -> DeletionRequest:
        """Step 1: confirm ownership, then schedule.

        Ownership is checked against an active ``OWNER`` membership rather than
        taken from the caller's token claims, so a stale token cannot delete a
        shop the person was removed from.
        """
        membership = (
            await self._db.execute(
                sa.select(TenantUser).where(
                    TenantUser.tenant_id == tenant_id,
                    TenantUser.user_id == user_id,
                    TenantUser.role == str(TenantRole.OWNER),
                    TenantUser.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            raise ForbiddenError("Only an active owner can delete this shop")

        existing = await self.pending_request(tenant_id)
        if existing is not None:
            raise ConflictError(
                "Deletion is already scheduled for this shop",
                details={"scheduled_for": existing.scheduled_for.isoformat()},
            )

        request = DeletionRequest(
            tenant_id=tenant_id,
            requested_by_user_id=user_id,
            status=str(DeletionStatus.SCHEDULED),
            reason=reason,
            scheduled_for=utc_now() + timedelta(days=DELETION_GRACE_DAYS),
            steps={"retention_note": RETENTION_NOTE},
        )
        self._db.add(request)
        await self._db.flush()

        tenant = await self._db.get(Tenant, tenant_id)
        if tenant is not None:
            tenant.status = str(TenantStatus.PENDING_DELETION)

        await record_audit(
            self._db,
            AuditAction.ACCOUNT_DELETION_REQUESTED,
            entity_type="tenant",
            entity_id=tenant_id,
            reason=reason,
            context={
                "scheduled_for": request.scheduled_for.isoformat(),
                "grace_days": DELETION_GRACE_DAYS,
            },
            tenant_id=tenant_id,
        )
        return request

    async def cancel_deletion(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> DeletionRequest:
        request = await self.pending_request(tenant_id)
        if request is None:
            raise NotFoundError("No deletion is scheduled for this shop")

        request.status = str(DeletionStatus.CANCELLED)
        request.cancelled_at = utc_now()

        tenant = await self._db.get(Tenant, tenant_id)
        if tenant is not None:
            tenant.status = str(TenantStatus.ACTIVE)

        await record_audit(
            self._db,
            AuditAction.ACCOUNT_DELETION_CANCELLED,
            entity_type="tenant",
            entity_id=tenant_id,
            context={"cancelled_by": str(user_id)},
            tenant_id=tenant_id,
        )
        return request

    async def pending_request(self, tenant_id: uuid.UUID) -> DeletionRequest | None:
        return (
            await self._db.execute(
                sa.select(DeletionRequest)
                .where(
                    DeletionRequest.tenant_id == tenant_id,
                    DeletionRequest.status.in_(
                        [str(DeletionStatus.REQUESTED), str(DeletionStatus.SCHEDULED)]
                    ),
                )
                .order_by(DeletionRequest.requested_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    # ------------------------------------------------------------- executing ---

    async def execute(self, request: DeletionRequest) -> DeletionOutcome:
        """Steps 2–7. Runs on a system session, after the grace period.

        Ordering matters and is section 100's: stop the money first, then take
        away access, then anonymise. Anonymising while a session is still live
        would let the app write a fresh customer row a second later.
        """
        request.status = str(DeletionStatus.PROCESSING)
        tenant_id = request.tenant_id
        steps: dict[str, int | str] = {"retention_note": RETENTION_NOTE}

        steps["subscriptions_cancelled"] = await self._cancel_subscriptions(tenant_id)
        steps["sessions_revoked"] = await self._revoke_sessions(tenant_id)
        steps["devices_revoked"] = await self._revoke_devices(tenant_id)
        steps["integrations_disabled"] = await self._disable_integrations(tenant_id)
        settings = get_settings()
        if settings.r2_configured:
            from app.common.object_storage import ObjectStorage

            await ObjectStorage(settings).delete_tenant(tenant_id)
            steps["personal_files_deleted"] = "R2 imports and exports; payout evidence retained"
        steps["customers_anonymised"] = await self._anonymise_customers(tenant_id)
        steps["addresses_anonymised"] = await self._anonymise_addresses(tenant_id)
        steps["owners_detached"] = await self._detach_members(tenant_id)
        steps["financial_records_retained"] = "ledger, payouts, receivables, profit snapshots"

        tenant = await self._db.get(Tenant, tenant_id)
        if tenant is not None:
            tenant.status = str(TenantStatus.PENDING_DELETION)
            tenant.name = f"Deleted shop {str(tenant_id)[:8]}"
            tenant.pickup_contact_name = None
            tenant.pickup_address_raw = None

        request.status = str(DeletionStatus.COMPLETED)
        request.completed_at = utc_now()
        request.steps = steps

        await record_audit(
            self._db,
            AuditAction.ACCOUNT_DELETION_EXECUTED,
            entity_type="tenant",
            entity_id=tenant_id,
            context=dict(steps),
            tenant_id=tenant_id,
        )
        return DeletionOutcome(request=request, steps=steps)

    async def run_due(self, *, now: datetime | None = None, limit: int = 20) -> int:
        """Execute every deletion whose grace period has ended."""
        moment = now or utc_now()
        due = (
            (
                await self._db.execute(
                    sa.select(DeletionRequest)
                    .where(
                        DeletionRequest.status == str(DeletionStatus.SCHEDULED),
                        DeletionRequest.scheduled_for <= moment,
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for request in due:
            await self.execute(request)
        return len(due)

    # --------------------------------------------------------------- steps ---

    async def _cancel_subscriptions(self, tenant_id: uuid.UUID) -> int:
        rows = (
            (
                await self._db.execute(
                    sa.select(Subscription).where(Subscription.tenant_id == tenant_id)
                )
            )
            .scalars()
            .all()
        )
        now = utc_now()
        for subscription in rows:
            if SubscriptionStatus(subscription.status) in (
                SubscriptionStatus.CANCELLED,
                SubscriptionStatus.EXPIRED,
            ):
                continue
            subscription.status = str(SubscriptionStatus.CANCELLED)
            subscription.cancelled_at = now
            subscription.current_period_end = subscription.current_period_end or now
            subscription.status_reason = "Account deletion"
            # The provider-side subscription is *not* cancelled from here.
            # Play cancellations happen in Play, and cancelling a bKash mandate
            # needs a merchant call this deployment cannot make. The seller is
            # told so; pretending otherwise would leave them being charged.
            subscription.metadata_json = {
                **subscription.metadata_json,
                "provider_cancellation": (
                    "must be completed by the seller at the provider"
                    if subscription.source != str(BillingProviderKind.MANUAL_ADMIN)
                    else "not applicable"
                ),
            }
        return len(rows)

    async def _revoke_sessions(self, tenant_id: uuid.UUID) -> int:
        sessions = (
            (
                await self._db.execute(
                    sa.select(AuthSession).where(
                        AuthSession.tenant_id == tenant_id,
                        AuthSession.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        now = utc_now()
        for session in sessions:
            session.revoked_at = now
            session.revoked_reason = str(RevocationReason.USER_SUSPENDED)
            tokens = (
                (
                    await self._db.execute(
                        sa.select(RefreshToken).where(
                            RefreshToken.session_id == session.id,
                            RefreshToken.revoked_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for token in tokens:
                token.revoked_at = now
        return len(sessions)

    async def _revoke_devices(self, tenant_id: uuid.UUID) -> int:
        """Revoke devices belonging to this shop's members.

        The push token goes with them: a deleted shop must not keep receiving
        notifications, and a push token is a contact detail.
        """
        user_ids = list(
            (
                await self._db.execute(
                    sa.select(TenantUser.user_id).where(TenantUser.tenant_id == tenant_id)
                )
            ).scalars()
        )
        if not user_ids:
            return 0

        devices = (
            (
                await self._db.execute(
                    sa.select(Device).where(
                        Device.user_id.in_(user_ids), Device.revoked_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        now = utc_now()
        for device in devices:
            device.revoked_at = now
            device.push_token = None
        return len(devices)

    async def _disable_integrations(self, tenant_id: uuid.UUID) -> int:
        """Turn off anything that would keep calling out on the shop's behalf.

        No courier account storage exists yet (Phase C is blocked), so today
        this closes the provider-health breakers, which is what stops scheduled
        work from retrying against a deleted shop.
        """
        from app.common.provider_health import BreakerState, ProviderHealth, scope_key_for

        rows = (
            (
                await self._db.execute(
                    sa.select(ProviderHealth).where(
                        ProviderHealth.scope_key == scope_key_for(tenant_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.breaker_state = str(BreakerState.OPEN)
            row.breaker_opened_at = utc_now()
        return len(rows)

    async def _anonymise_customers(self, tenant_id: uuid.UUID) -> int:
        """Make every customer unidentifiable, keeping their counts.

        The encrypted phone and the search HMAC both go: without the HMAC the
        row cannot be found by number even by someone holding the key, and
        without the ciphertext there is nothing to decrypt. The order and
        delivery counts stay, because they are the shop's own trading history
        and identify nobody once the contact details are gone.
        """
        from app.customers.crm_models import CustomerActivity, CustomerFollowUp, CustomerTag
        from app.messaging.models import ConsentEvent, Conversation, Message

        # Free text can itself identify a person. Scrub CRM alongside the customer,
        # and messaging: addresses, rendered bodies (they carry names) and evidence.
        model: Any
        for model, values in (
            (CustomerActivity, {"text": None, "actor_id": None}),
            (CustomerFollowUp, {"text": "", "assignee_id": None, "completed_by": None}),
            (CustomerTag, {"name": "", "archived": True}),
            (
                Conversation,
                {
                    "recipient_enc": "",
                    "recipient_masked": "***",
                    "recipient_hash": None,
                    "unsubscribe_hash": None,
                    "unsubscribe_enc": None,
                    "consent": False,
                    "marketing_consent": False,
                },
            ),
            (Message, {"subject": "", "body": "", "params": None}),
            (ConsentEvent, {"evidence": "", "actor_id": None}),
        ):
            await self._db.execute(
                sa.update(model).where(model.tenant_id == tenant_id).values(**values)
            )
        # Normalised tag names are personal free text too; keep uniqueness via ID.
        tags = (
            await self._db.scalars(sa.select(CustomerTag).where(CustomerTag.tenant_id == tenant_id))
        ).all()
        for tag in tags:
            tag.name_key = f"deleted:{tag.id}"
        customers = (
            (await self._db.execute(sa.select(Customer).where(Customer.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
        for index, customer in enumerate(customers, start=1):
            customer.name = f"Customer {index}"
            customer.phone_enc = ""
            # A distinct value per row: reusing one would make every anonymised
            # customer collide on the unique index.
            customer.phone_search_hmac = f"deleted:{customer.id.hex}"
            customer.phone_masked = "**********"
            customer.phone_last4 = "0000"
            customer.alt_phone_enc = None
            customer.alt_phone_masked = None
            customer.flag_reason = None
            customer.notes = None
        return len(customers)

    async def _anonymise_addresses(self, tenant_id: uuid.UUID) -> int:
        addresses = (
            (
                await self._db.execute(
                    sa.select(CustomerAddress).where(CustomerAddress.tenant_id == tenant_id)
                )
            )
            .scalars()
            .all()
        )
        for address in addresses:
            address.raw_address = "[deleted]"
            address.normalized_address = None
            address.label = None
            address.postal_code = None
            # District and area stay: they are geography, not identity, and the
            # shop's own delivery-rate-by-area history is built on them.
        return len(addresses)

    async def _detach_members(self, tenant_id: uuid.UUID) -> int:
        """Deactivate memberships and clear the owner's contact details.

        A user who owns only this shop is anonymised too. One who is a member
        of another shop is left alone — deleting this shop is not a request to
        delete their other business.
        """
        memberships = (
            (await self._db.execute(sa.select(TenantUser).where(TenantUser.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
        for membership in memberships:
            membership.is_active = False

            others = int(
                (
                    await self._db.execute(
                        sa.select(sa.func.count())
                        .select_from(TenantUser)
                        .where(
                            TenantUser.user_id == membership.user_id,
                            TenantUser.tenant_id != tenant_id,
                            TenantUser.is_active.is_(True),
                        )
                    )
                ).scalar_one()
            )
            if others:
                continue

            user = await self._db.get(User, membership.user_id)
            if user is None:
                continue
            identities = (
                (
                    await self._db.execute(
                        sa.select(AuthIdentity).where(
                            AuthIdentity.user_id == user.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for identity in identities:
                if identity.provider == AuthProvider.SUPABASE:
                    await delete_auth_identity(identity.provider_subject, get_settings())
                identity.normalized_email = None
                identity.password_hash = None
                if identity.provider != AuthProvider.SUPABASE:
                    identity.provider_subject = f"deleted:{identity.id}"
            user.status = str(UserStatus.PENDING_DELETION)
            user.display_name = None
            user.phone_enc = ""
            user.phone_search_hmac = f"deleted:{user.id.hex}"
            user.phone_last4 = "0000"
        return len(memberships)
