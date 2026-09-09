"""Shop creation and profile updates.

Creating a shop is the one operation that has to *introduce* a tenant into a
request that started without one. It does so by creating the row, then entering
that tenant's context for the membership insert — the same guard that protects
every other write then applies to this one, rather than being bypassed.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.outbox import OutboxTopic, enqueue
from app.common.phone import try_normalize_bd_phone
from app.core.clock import utc_now
from app.core.context import use_context
from app.core.errors import ConflictError, NotFoundError
from app.core.security import CredentialVault
from app.db.tenancy import allow_cross_tenant
from app.entitlements.catalog import PlanCode
from app.entitlements.models import Subscription, SubscriptionSource, SubscriptionStatus
from app.tenants.models import BusinessCategory, OnboardingStep, Tenant, TenantUser
from app.tenants.roles import TenantRole

__all__ = ["TenantService"]

_PICKUP_PHONE_CONTEXT = "tenant.pickup_phone"

#: A seller can run several shops, but not an unbounded number from one phone.
#: This is an abuse guard, not a plan limit.
MAX_SHOPS_PER_USER = 5


class TenantService:
    """Create and maintain a seller's shop."""

    def __init__(self, session: AsyncSession, *, vault: CredentialVault) -> None:
        self._db = session
        self._vault = vault

    async def create_shop(
        self,
        *,
        owner_user_id: uuid.UUID,
        name: str,
        business_category: BusinessCategory = BusinessCategory.OTHER,
        pickup_contact_name: str | None = None,
        pickup_phone: str | None = None,
        pickup_address: str | None = None,
        pickup_district: str | None = None,
        pickup_area: str | None = None,
    ) -> Tenant:
        """Create a shop and make the caller its owner."""
        # Counting a user's shops is inherently cross-tenant and is scoped by
        # user_id. Declared through the logged bypass rather than hidden.
        with allow_cross_tenant("shop creation: count existing shops for this owner"):
            existing_count = (
                await self._db.execute(
                    sa.select(sa.func.count())
                    .select_from(TenantUser)
                    .where(TenantUser.user_id == owner_user_id, TenantUser.is_active.is_(True))
                )
            ).scalar_one()
        if existing_count >= MAX_SHOPS_PER_USER:
            raise ConflictError(
                f"A user cannot own more than {MAX_SHOPS_PER_USER} shops",
            )

        phone = try_normalize_bd_phone(pickup_phone)
        tenant = Tenant(
            name=name.strip(),
            business_category=business_category,
            pickup_contact_name=pickup_contact_name,
            pickup_phone_enc=(
                self._vault.encrypt(phone.e164, context=_PICKUP_PHONE_CONTEXT) if phone else None
            ),
            pickup_phone_last4=phone.last4 if phone else None,
            pickup_address_raw=pickup_address,
            # The raw seller text is kept as evidence; a normalized copy is
            # derived separately and never overwrites it (master spec section 71).
            pickup_address_normalized=(pickup_address or "").strip() or None,
            pickup_district=pickup_district,
            pickup_area=pickup_area,
            onboarding_step=(
                OnboardingStep.COURIER_CHOICE if pickup_address else OnboardingStep.PICKUP_ADDRESS
            ),
        )
        self._db.add(tenant)
        await self._db.flush()

        # From here on the request *has* a tenant, so the membership row is
        # written under the normal tenant guard rather than around it.
        with use_context(tenant_id=tenant.id):
            self._db.add(
                TenantUser(
                    tenant_id=tenant.id,
                    user_id=owner_user_id,
                    role=TenantRole.OWNER,
                )
            )
            # Every shop starts on Free. Paid plans require a verified purchase
            # (master spec sections 27.3, 90); nothing here can grant one.
            self._db.add(
                Subscription(
                    tenant_id=tenant.id,
                    plan_code=PlanCode.FREE,
                    status=SubscriptionStatus.ACTIVE,
                    source=SubscriptionSource.MANUAL_ADMIN,
                    grant_reason="Default free plan on shop creation",
                )
            )
            await record_audit(
                self._db,
                AuditAction.TENANT_CREATED,
                entity_type="tenant",
                entity_id=tenant.id,
                context={"name": tenant.name, "category": str(business_category)},
                tenant_id=tenant.id,
                actor_id=owner_user_id,
            )
            await enqueue(
                self._db,
                OutboxTopic.TENANT_CREATED,
                {"tenant_id": str(tenant.id), "owner_user_id": str(owner_user_id)},
                tenant_id=tenant.id,
            )
            await self._db.flush()

        return tenant

    async def get(self, tenant_id: uuid.UUID) -> Tenant:
        tenant = await self._db.get(Tenant, tenant_id)
        if tenant is None or tenant.deleted_at is not None:
            raise NotFoundError("Shop not found")
        return tenant

    async def update(
        self,
        tenant_id: uuid.UUID,
        *,
        name: str | None = None,
        business_category: BusinessCategory | None = None,
        pickup_contact_name: str | None = None,
        pickup_phone: str | None = None,
        pickup_address: str | None = None,
        pickup_district: str | None = None,
        pickup_area: str | None = None,
        onboarding_step: OnboardingStep | None = None,
    ) -> Tenant:
        tenant = await self.get(tenant_id)
        changed: dict[str, object] = {}

        if name is not None and name.strip() != tenant.name:
            tenant.name = name.strip()
            changed["name"] = tenant.name
        if business_category is not None:
            tenant.business_category = business_category
            changed["business_category"] = str(business_category)
        if pickup_contact_name is not None:
            tenant.pickup_contact_name = pickup_contact_name
            changed["pickup_contact_name"] = True
        if pickup_phone is not None:
            phone = try_normalize_bd_phone(pickup_phone)
            tenant.pickup_phone_enc = (
                self._vault.encrypt(phone.e164, context=_PICKUP_PHONE_CONTEXT) if phone else None
            )
            tenant.pickup_phone_last4 = phone.last4 if phone else None
            changed["pickup_phone"] = True
        if pickup_address is not None:
            tenant.pickup_address_raw = pickup_address
            tenant.pickup_address_normalized = pickup_address.strip() or None
            changed["pickup_address"] = True
        if pickup_district is not None:
            tenant.pickup_district = pickup_district
            changed["pickup_district"] = pickup_district
        if pickup_area is not None:
            tenant.pickup_area = pickup_area
            changed["pickup_area"] = pickup_area

        if onboarding_step is not None:
            tenant.onboarding_step = onboarding_step
            changed["onboarding_step"] = str(onboarding_step)
            if (
                onboarding_step is OnboardingStep.COMPLETE
                and tenant.onboarding_completed_at is None
            ):
                tenant.onboarding_completed_at = utc_now()

        if changed:
            await record_audit(
                self._db,
                AuditAction.TENANT_UPDATED,
                entity_type="tenant",
                entity_id=tenant.id,
                context={"changed": sorted(changed)},
                tenant_id=tenant.id,
            )
        await self._db.flush()
        return tenant
