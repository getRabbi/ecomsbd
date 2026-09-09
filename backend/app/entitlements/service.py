"""Entitlement resolution and enforcement.

Master spec section 43::

    await entitlements.require(tenant_id, "bulk_booking")

Every paid or metered operation asks this service, server-side. The mobile app's
copy of the plan is a display hint and is never trusted (section 27.3).

Usage counters (orders this month, risk checks today, SMS segments) are declared
here as part of the interface but are **not** implemented in Phase A: counting
orders requires the orders table, which arrives in Phase B. :meth:`consume` is
therefore explicit about being unimplemented rather than silently returning
"allowed", which would let a quota be bypassed the day the feature ships.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.core.clock import utc_now
from app.core.errors import EntitlementRequiredError
from app.entitlements.catalog import UNLIMITED, Entitlement, PlanCode, PlanDefinition, get_plan
from app.entitlements.models import Subscription

__all__ = ["EntitlementService", "EntitlementSnapshot"]


@dataclass(frozen=True, slots=True)
class EntitlementSnapshot:
    """What the client is told about its plan (master spec section 27.3)."""

    plan: PlanCode
    status: str
    source: str | None
    valid_until: datetime | None
    entitlements: dict[str, int | bool]

    def as_dict(self) -> dict[str, object]:
        return {
            "plan": str(self.plan),
            "status": self.status,
            "source": self.source,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "entitlements": {str(k): v for k, v in self.entitlements.items()},
        }


class EntitlementService:
    """Resolves a tenant's plan and enforces its entitlements."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session
        self._cache: dict[uuid.UUID, PlanDefinition] = {}

    async def _load_subscription(self, tenant_id: uuid.UUID) -> Subscription | None:
        return (
            await self._db.execute(
                sa.select(Subscription)
                .where(Subscription.tenant_id == tenant_id)
                .order_by(Subscription.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def plan_for(self, tenant_id: uuid.UUID) -> PlanDefinition:
        """The tenant's effective plan.

        No subscription row, or one that is not currently valid, resolves to
        Free. Downgrading restricts automation and volume; it never removes the
        seller's data (section 51).
        """
        if tenant_id in self._cache:
            return self._cache[tenant_id]

        subscription = await self._load_subscription(tenant_id)
        if subscription is None or not subscription.is_current(at=utc_now()):
            plan = get_plan(PlanCode.FREE)
        else:
            plan = get_plan(subscription.plan_code)

        self._cache[tenant_id] = plan
        return plan

    async def snapshot(self, tenant_id: uuid.UUID) -> EntitlementSnapshot:
        """The entitlement payload returned to the client."""
        subscription = await self._load_subscription(tenant_id)
        plan = await self.plan_for(tenant_id)
        current = subscription is not None and subscription.is_current(at=utc_now())
        return EntitlementSnapshot(
            plan=PlanCode(plan.code),
            status=subscription.status if (subscription and current) else "none",
            source=subscription.source if (subscription and current) else None,
            valid_until=subscription.current_period_end if current and subscription else None,
            entitlements={str(k): v for k, v in plan.entitlements.items()},
        )

    async def is_allowed(self, tenant_id: uuid.UUID, entitlement: Entitlement) -> bool:
        plan = await self.plan_for(tenant_id)
        return plan.is_allowed(entitlement)

    async def limit(self, tenant_id: uuid.UUID, entitlement: Entitlement) -> int:
        """Numeric cap for an entitlement. ``UNLIMITED`` (-1) means no cap."""
        plan = await self.plan_for(tenant_id)
        return plan.limit(entitlement)

    async def require(self, tenant_id: uuid.UUID, entitlement: Entitlement) -> None:
        """Raise unless the tenant's plan grants ``entitlement``."""
        if await self.is_allowed(tenant_id, entitlement):
            return

        plan = await self.plan_for(tenant_id)
        await record_audit(
            self._db,
            AuditAction.ENTITLEMENT_DENIED,
            entity_type="tenant",
            entity_id=tenant_id,
            context={"entitlement": str(entitlement), "plan": str(plan.code)},
            tenant_id=tenant_id,
        )
        raise EntitlementRequiredError(
            str(entitlement),
            f"The {plan.name} plan does not include {entitlement}",
        )

    async def consume(
        self, tenant_id: uuid.UUID, entitlement: Entitlement, *, amount: int = 1
    ) -> None:
        """Record usage against a metered entitlement.

        Not implemented in Phase A. Usage counters need the tables that own the
        metered actions (orders, risk checks, SMS, AI parses), which ship with
        their features in later phases.

        This raises rather than no-ops on purpose: a silent success here would
        mean the first quota-metered feature ships with its quota unenforced,
        and the failure would be invisible until a bill arrived.
        """
        raise NotImplementedError(
            "Usage metering ships with the metered feature (master spec section 43). "
            f"Requested {entitlement} x{amount} for tenant {tenant_id}."
        )

    def invalidate(self, tenant_id: uuid.UUID | None = None) -> None:
        if tenant_id is None:
            self._cache.clear()
        else:
            self._cache.pop(tenant_id, None)


def within_limit(used: int, limit: int) -> bool:
    """Whether ``used`` is inside ``limit``, treating ``UNLIMITED`` as no cap."""
    return limit == UNLIMITED or used < limit
