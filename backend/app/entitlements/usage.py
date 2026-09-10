"""Metered usage counters.

Master spec section 43: *"usage counters are server authoritative."*

Three properties are non-negotiable and each is enforced by construction rather
than by convention:

*   **Atomic.** The increment is a single conditional ``UPDATE`` whose ``WHERE``
    clause carries the quota check. Two concurrent requests at the boundary
    cannot both read "99 of 100 used" and both write 100 — one of them updates
    zero rows and is refused. A read-then-write in Python cannot give that
    guarantee at any isolation level short of ``SERIALIZABLE``.
*   **Timezone-aware.** A period key is derived from the *tenant's* business
    date (section 69), so "risk checks today" resets at midnight in Dhaka, not
    at 06:00 because the server keeps UTC.
*   **Refusing, not clamping.** Over quota raises
    :class:`~app.core.errors.EntitlementRequiredError`. Silently capping would
    let the caller believe the metered action happened.

The counter is a *quota*, not a financial record. It is the ledger and the
domain tables that say what actually happened; this table only says how much of
an allowance has been spent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import business_date, utc_now
from app.db.base import Base, PrimaryKeyMixin, TenantOwned
from app.db.types import TZDateTime
from app.entitlements.catalog import UNLIMITED, Entitlement

__all__ = [
    "METERED_ENTITLEMENTS",
    "UsageCounter",
    "UsagePeriod",
    "UsageView",
    "period_key_for",
]


class UsagePeriod(StrEnum):
    """How often a counter resets."""

    DAILY = "DAILY"
    MONTHLY = "MONTHLY"


#: Which entitlements are metered, and on what cycle. An entitlement absent
#: here is a capability (on/off) or a standing limit counted from its own table
#: (team members, courier accounts) — neither of which is a spendable quota.
METERED_ENTITLEMENTS: dict[Entitlement, UsagePeriod] = {
    Entitlement.ORDERS_MONTHLY_LIMIT: UsagePeriod.MONTHLY,
    Entitlement.RISK_CHECKS_DAILY: UsagePeriod.DAILY,
    Entitlement.SMS_SEGMENTS_MONTHLY: UsagePeriod.MONTHLY,
    Entitlement.AI_PARSE_MONTHLY: UsagePeriod.MONTHLY,
}


def period_key_for(
    entitlement: Entitlement,
    *,
    timezone_name: str | None = None,
    at: datetime | None = None,
) -> str:
    """The period bucket a usage event falls into, in the tenant's timezone.

    ``2026-09`` for a monthly quota, ``2026-09-10`` for a daily one. Storing the
    key as text rather than a date range keeps the unique constraint trivial and
    makes a support query (`"show me September"`) readable.
    """
    period = METERED_ENTITLEMENTS.get(entitlement)
    if period is None:
        raise ValueError(f"{entitlement} is not a metered entitlement")
    day: date = business_date(timezone_name, at=at)
    return day.isoformat() if period is UsagePeriod.DAILY else f"{day.year:04d}-{day.month:02d}"


class UsageCounter(Base, TenantOwned, PrimaryKeyMixin):
    """One tenant's spend against one metered entitlement in one period."""

    __tablename__ = "usage_counters"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "entitlement_key",
            "period_key",
            name="uq_usage_counters_tenant_id_entitlement_key_period_key",
        ),
        sa.Index("ix_usage_counters_tenant_period", "tenant_id", "period_key"),
        sa.CheckConstraint("used >= 0", name="used_non_negative"),
    )

    entitlement_key: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    period_key: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    period_type: Mapped[str] = mapped_column(sa.String(10), nullable=False)

    used: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    #: The cap in force when the row was last touched. Kept for two reasons: the
    #: conditional UPDATE needs it in SQL, and an upgrade mid-period must raise
    #: the ceiling without wiping what has been spent.
    limit_value: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=UNLIMITED)
    #: The plan the limit came from, so a support answer can say *why* the cap
    #: was 20 rather than only that it was.
    plan_code: Mapped[str] = mapped_column(sa.String(32), nullable=False)

    first_used_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    last_used_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)

    @property
    def remaining(self) -> int | None:
        """Remaining allowance, or ``None`` when the entitlement is unlimited."""
        if self.limit_value == UNLIMITED:
            return None
        return max(0, self.limit_value - self.used)


@dataclass(frozen=True, slots=True)
class UsageView:
    """One metered entitlement as the client sees it."""

    entitlement: str
    period: str
    period_key: str
    used: int
    limit: int
    remaining: int | None
    resets_at: datetime | None

    def as_dict(self) -> dict[str, object]:
        return {
            "entitlement": self.entitlement,
            "period": self.period,
            "period_key": self.period_key,
            "used": self.used,
            "limit": self.limit,
            "remaining": self.remaining,
            "unlimited": self.limit == UNLIMITED,
            "resets_at": self.resets_at.isoformat() if self.resets_at else None,
        }


async def increment_atomically(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    entitlement: Entitlement,
    period_key: str,
    amount: int,
    limit_value: int,
) -> bool:
    """Add ``amount`` to a counter if the quota allows it. Returns whether it did.

    The whole check lives in the ``WHERE`` clause:

    .. code-block:: sql

        UPDATE usage_counters
           SET used = used + :amount
         WHERE tenant_id = :tenant AND entitlement_key = :key AND period_key = :period
           AND (limit_value = -1 OR used + :amount <= :limit)

    A bulk ``UPDATE`` bypasses the ORM tenancy guards (they hook object flushes,
    not Core statements), which is why ``tenant_id`` is in the predicate
    explicitly and a regression test asserts it stays there.
    """
    if amount <= 0:
        raise ValueError("usage amount must be positive")

    now = utc_now()
    statement = (
        sa.update(UsageCounter)
        .where(
            UsageCounter.tenant_id == tenant_id,
            UsageCounter.entitlement_key == str(entitlement),
            UsageCounter.period_key == period_key,
            sa.or_(
                sa.literal(limit_value == UNLIMITED),
                UsageCounter.used + amount <= limit_value,
            ),
        )
        .values(used=UsageCounter.used + amount, limit_value=limit_value, last_used_at=now)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(statement)
    # ``rowcount`` is on CursorResult; the async wrapper's return type is the
    # wider Result, so the cast keeps mypy --strict honest without loosening
    # the signature. Zero rows updated means the quota predicate failed.
    return bool(cast("CursorResult[Any]", result).rowcount)
