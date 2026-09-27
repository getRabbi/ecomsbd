"""Entitlement resolution and enforcement.

Master spec section 43::

    await entitlements.require(tenant_id, "bulk_booking")

Every paid or metered operation asks this service, server-side. The mobile app's
copy of the plan is a display hint and is never trusted (section 27.3).

The pipeline is::

    Subscription -> EntitlementResolver -> EntitlementSnapshot
                                             |-> require(...)      capability gate
                                             |-> limit(...)        standing cap
                                             `-> consume(...)      metered quota

:class:`EntitlementResolver` is a pure function of a subscription and an
instant. It has no database and no clock of its own, which is what makes the
grace/trial/downgrade rules testable without fixtures — and means there is
exactly one place that decides whether a subscription is currently worth
anything.

**Downgrade never removes data** (section 51). A lapsed subscription resolves to
Free, which restricts automation, volume and collaboration. Nothing in this
module — or anywhere else — gates *reading a seller's own history* behind a
plan.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.core.clock import utc_now
from app.core.config import Settings, get_settings
from app.core.errors import EntitlementRequiredError, RateLimitedError
from app.entitlements.catalog import UNLIMITED, Entitlement, PlanCode, PlanDefinition, get_plan
from app.entitlements.models import Subscription, SubscriptionStatus
from app.entitlements.usage import (
    METERED_ENTITLEMENTS,
    UsageCounter,
    UsageView,
    increment_atomically,
    period_key_for,
)

__all__ = [
    "EntitlementResolver",
    "EntitlementService",
    "EntitlementSnapshot",
    "ResolvedEntitlements",
    "within_limit",
]


@dataclass(frozen=True, slots=True)
class ResolvedEntitlements:
    """What a subscription resolves to at one instant. Pure data."""

    plan: PlanDefinition
    status: str
    source: str | None
    valid_until: datetime | None
    #: True while a paid plan is being kept alive by a dunning grace window.
    in_grace: bool
    #: True when the seller has cancelled and is running out the paid period.
    ends_at_period_end: bool


class EntitlementResolver:
    """Turns a subscription row into the plan it currently entitles.

    Deliberately synchronous and dependency-free: given the same row and the
    same instant it always returns the same answer, so the trial/grace/expiry
    rules can be tested exhaustively without a database.
    """

    @staticmethod
    def resolve(subscription: Subscription | None, *, at: datetime) -> ResolvedEntitlements:
        if subscription is None or not subscription.is_current(at=at):
            # No subscription, or a lapsed one. Free is the floor, never an
            # error and never a lockout: the seller's own history stays open.
            return ResolvedEntitlements(
                plan=get_plan(PlanCode.FREE),
                status=str(SubscriptionStatus.EXPIRED) if subscription else "none",
                source=subscription.source if subscription else None,
                valid_until=subscription.access_until() if subscription else None,
                in_grace=False,
                ends_at_period_end=False,
            )

        status = SubscriptionStatus(subscription.status)
        in_grace = status is SubscriptionStatus.GRACE or (
            subscription.grace_until is not None
            and subscription.current_period_end is not None
            and subscription.current_period_end <= at < subscription.grace_until
        )
        return ResolvedEntitlements(
            plan=get_plan(subscription.plan_code),
            status=str(status),
            source=subscription.source,
            valid_until=subscription.access_until(),
            in_grace=in_grace,
            ends_at_period_end=subscription.cancel_at_period_end
            or status is SubscriptionStatus.CANCEL_AT_PERIOD_END,
        )


@dataclass(frozen=True, slots=True)
class EntitlementSnapshot:
    """What the client is told about its plan (master spec section 27.3)."""

    plan: PlanCode
    status: str
    source: str | None
    valid_until: datetime | None
    entitlements: dict[str, int | bool]
    in_grace: bool = False
    ends_at_period_end: bool = False
    usage: tuple[UsageView, ...] = ()
    free_launch_mode: bool = False
    billing_enabled: bool = False
    effective_access: str = "plan"

    def as_dict(self) -> dict[str, object]:
        return {
            "plan": str(self.plan),
            "status": self.status,
            "source": self.source,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "entitlements": {str(k): v for k, v in self.entitlements.items()},
            "in_grace": self.in_grace,
            "ends_at_period_end": self.ends_at_period_end,
            "usage": [view.as_dict() for view in self.usage],
            "free_launch_mode": self.free_launch_mode,
            "billing_enabled": self.billing_enabled,
            "effective_access": self.effective_access,
        }


class EntitlementService:
    """Resolves a tenant's plan and enforces its entitlements."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        timezone_name: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._db = session
        self._settings = settings or get_settings()
        self._timezone = timezone_name
        self._cache: dict[uuid.UUID, ResolvedEntitlements] = {}

    # ------------------------------------------------------------- loading ---

    async def _load_subscription(self, tenant_id: uuid.UUID) -> Subscription | None:
        return (
            await self._db.execute(
                sa.select(Subscription)
                .where(Subscription.tenant_id == tenant_id)
                .order_by(Subscription.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _resolved(self, tenant_id: uuid.UUID) -> ResolvedEntitlements:
        """Resolve once per request.

        The cache is per-service-instance, and a service instance is per
        request (or per job). One subscription read serves every entitlement
        check in a request, so a handler that gates four things does not issue
        four queries — section 44's N+1 rule applied to the gate itself.
        """
        cached = self._cache.get(tenant_id)
        if cached is not None:
            return cached

        resolved = EntitlementResolver.resolve(
            await self._load_subscription(tenant_id), at=utc_now()
        )
        self._cache[tenant_id] = resolved
        return resolved

    async def plan_for(self, tenant_id: uuid.UUID) -> PlanDefinition:
        """Effective capabilities, retaining the real plan's identity.

        This copy is never persisted. All enforcement paths (including workers,
        imports, standing limits and usage counters) consume this same policy.
        Provider switches, permissions and operational limits live outside it.
        """
        plan = (await self._resolved(tenant_id)).plan
        if not self._settings.free_launch_mode:
            return plan
        values = {
            key: True if isinstance(value, bool) else UNLIMITED
            for key, value in plan.entitlements.items()
        }
        values[Entitlement.RISK_CHECKS_DAILY] = self._settings.risk_checks_daily_safety_limit
        return replace(plan, entitlements=values)

    # ------------------------------------------------------------ snapshot ---

    async def snapshot(
        self, tenant_id: uuid.UUID, *, include_usage: bool = False
    ) -> EntitlementSnapshot:
        """The entitlement payload returned to the client."""
        resolved = await self._resolved(tenant_id)
        effective = await self.plan_for(tenant_id)
        usage: tuple[UsageView, ...] = ()
        if include_usage:
            usage = tuple(await self.usage(tenant_id))
        return EntitlementSnapshot(
            plan=PlanCode(resolved.plan.code),
            status=resolved.status,
            source=resolved.source,
            valid_until=resolved.valid_until,
            entitlements={str(k): v for k, v in effective.entitlements.items()},
            free_launch_mode=self._settings.free_launch_mode,
            billing_enabled=self._settings.billing_enabled,
            effective_access="full_access" if self._settings.free_launch_mode else "plan",
            in_grace=resolved.in_grace,
            ends_at_period_end=resolved.ends_at_period_end,
            usage=usage,
        )

    # ----------------------------------------------------------- capability ---

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
        await self._deny(tenant_id, entitlement, reason="not_in_plan")

    async def require_within_limit(
        self, tenant_id: uuid.UUID, entitlement: Entitlement, *, current_count: int
    ) -> None:
        """Raise unless one more of something fits under a *standing* cap.

        Standing caps (team members, courier accounts) are counted from the
        table that owns them rather than from a usage counter: a seat that was
        removed frees itself, and a period reset must never hand out a fifth
        courier account.
        """
        cap = await self.limit(tenant_id, entitlement)
        if within_limit(current_count, cap):
            return
        await self._deny(
            tenant_id, entitlement, reason="limit_reached", used=current_count, cap=cap
        )

    async def _deny(
        self,
        tenant_id: uuid.UUID,
        entitlement: Entitlement,
        *,
        reason: str,
        used: int | None = None,
        cap: int | None = None,
    ) -> None:
        if self._settings.free_launch_mode and entitlement is Entitlement.RISK_CHECKS_DAILY:
            raise RateLimitedError(
                "The daily risk-check safety limit has been reached.",
                message_bn="আজকের নিরাপদ রিস্ক চেকের সীমা শেষ হয়েছে।",
                details={"reason": "safety_limit", "limit": cap},
            )
        plan = await self.plan_for(tenant_id)
        context: dict[str, object] = {
            "entitlement": str(entitlement),
            "plan": str(plan.code),
            "reason": reason,
        }
        if used is not None:
            context["used"] = used
        if cap is not None:
            context["limit"] = cap

        await record_audit(
            self._db,
            AuditAction.ENTITLEMENT_DENIED,
            entity_type="tenant",
            entity_id=tenant_id,
            context=context,
            tenant_id=tenant_id,
        )
        details = {"plan": str(plan.code), "reason": reason}
        if cap is not None:
            details["limit"] = str(cap)
        raise EntitlementRequiredError(
            str(entitlement),
            f"The {plan.name} plan does not include {entitlement}",
            details=details,
        )

    # -------------------------------------------------------------- metered ---

    async def consume(
        self,
        tenant_id: uuid.UUID,
        entitlement: Entitlement,
        *,
        amount: int = 1,
    ) -> UsageView:
        """Spend ``amount`` of a metered quota, or raise.

        Ordering matters. The row is created first with ``used = 0``, then the
        spend is a conditional ``UPDATE``. Creating it already incremented would
        make the create path itself a quota bypass under a race, because two
        concurrent inserts would each think they were first.
        """
        if entitlement not in METERED_ENTITLEMENTS:
            raise ValueError(
                f"{entitlement} is not metered; use require() or require_within_limit()"
            )
        if amount <= 0:
            raise ValueError("usage amount must be positive")

        plan = await self.plan_for(tenant_id)
        cap = plan.limit(entitlement)
        period_key = period_key_for(entitlement, timezone_name=self._timezone)

        if cap == 0:
            # The plan includes none of this at all — a quota of zero is a
            # capability denial, and saying so is clearer than "0 of 0 used".
            await self._deny(tenant_id, entitlement, reason="not_in_plan", used=0, cap=0)

        await self._ensure_counter(tenant_id, entitlement, period_key, cap, str(plan.code))
        granted = await increment_atomically(
            self._db,
            tenant_id=tenant_id,
            entitlement=entitlement,
            period_key=period_key,
            amount=amount,
            limit_value=cap,
        )
        if not granted:
            counter = await self._counter(tenant_id, entitlement, period_key)
            await self._deny(
                tenant_id,
                entitlement,
                reason="quota_exhausted",
                used=counter.used if counter else 0,
                cap=cap,
            )

        counter = await self._counter(tenant_id, entitlement, period_key)
        assert counter is not None  # noqa: S101 - just updated it
        return _view(counter)

    async def _ensure_counter(
        self,
        tenant_id: uuid.UUID,
        entitlement: Entitlement,
        period_key: str,
        cap: int,
        plan_code: str,
    ) -> None:
        """Create the period's counter row if it does not exist yet.

        A unique constraint decides the race, not a check: two requests opening
        a shop's first order of the month will both try, one will lose, and the
        loser simply proceeds to the conditional update.
        """
        existing = await self._counter(tenant_id, entitlement, period_key)
        if existing is not None:
            if existing.limit_value != cap:
                # Plan changed mid-period. Move the ceiling, keep the spend:
                # an upgrade should grant headroom immediately, and a downgrade
                # should not pretend the used allowance was never used.
                existing.limit_value = cap
                existing.plan_code = plan_code
            return

        savepoint = await self._db.begin_nested()
        try:
            self._db.add(
                UsageCounter(
                    tenant_id=tenant_id,
                    entitlement_key=str(entitlement),
                    period_key=period_key,
                    period_type=str(METERED_ENTITLEMENTS[entitlement]),
                    used=0,
                    limit_value=cap,
                    plan_code=plan_code,
                )
            )
            await savepoint.commit()
        except IntegrityError:
            await savepoint.rollback()

    async def _counter(
        self, tenant_id: uuid.UUID, entitlement: Entitlement, period_key: str
    ) -> UsageCounter | None:
        """Read a counter, always from the database.

        ``populate_existing`` matters here. The spend is a Core ``UPDATE`` with
        ``synchronize_session=False``, so an instance already in the identity
        map still holds the pre-spend ``used``. Without this the endpoint would
        answer "1 of 20" to a seller who had just spent their twenty-first.
        """
        return (
            await self._db.execute(
                sa.select(UsageCounter)
                .where(
                    UsageCounter.tenant_id == tenant_id,
                    UsageCounter.entitlement_key == str(entitlement),
                    UsageCounter.period_key == period_key,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def usage(self, tenant_id: uuid.UUID) -> list[UsageView]:
        """Every metered entitlement for the current period, spent or not.

        Entitlements with no row yet are reported at zero rather than omitted:
        a client showing "0 of 100 risk checks" is right, and a client showing
        nothing looks broken.
        """
        plan = await self.plan_for(tenant_id)
        keys = {
            entitlement: period_key_for(entitlement, timezone_name=self._timezone)
            for entitlement in METERED_ENTITLEMENTS
        }
        rows = (
            await self._db.execute(
                sa.select(UsageCounter)
                .where(
                    UsageCounter.tenant_id == tenant_id,
                    UsageCounter.period_key.in_(set(keys.values())),
                )
                .execution_options(populate_existing=True)
            )
        ).scalars()
        by_key = {(row.entitlement_key, row.period_key): row for row in rows}

        views: list[UsageView] = []
        for entitlement, period_key in keys.items():
            row = by_key.get((str(entitlement), period_key))
            cap = plan.limit(entitlement)
            if row is not None:
                views.append(_view(row, limit_override=cap))
            else:
                views.append(
                    UsageView(
                        entitlement=str(entitlement),
                        period=str(METERED_ENTITLEMENTS[entitlement]),
                        period_key=period_key,
                        used=0,
                        limit=cap,
                        remaining=None if cap == UNLIMITED else max(0, cap),
                        resets_at=None,
                    )
                )
        return views

    def invalidate(self, tenant_id: uuid.UUID | None = None) -> None:
        if tenant_id is None:
            self._cache.clear()
        else:
            self._cache.pop(tenant_id, None)


def _view(counter: UsageCounter, *, limit_override: int | None = None) -> UsageView:
    cap = counter.limit_value if limit_override is None else limit_override
    return UsageView(
        entitlement=counter.entitlement_key,
        period=counter.period_type,
        period_key=counter.period_key,
        used=counter.used,
        limit=cap,
        remaining=None if cap == UNLIMITED else max(0, cap - counter.used),
        resets_at=None,
    )


def within_limit(used: int, limit: int) -> bool:
    """Whether ``used`` is inside ``limit``, treating ``UNLIMITED`` as no cap."""
    return limit == UNLIMITED or used < limit
