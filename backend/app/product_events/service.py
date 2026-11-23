"""Record and count product funnel events.

Recording is best effort by design: a funnel row is never worth failing the
seller's action. :meth:`ProductEventService.record` runs in a savepoint, so a
duplicate or a database error rolls back only the event, never the caller's
transaction, and it never raises.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.feature_flags import FeatureFlagService, FlagKey
from app.core.clock import business_date, business_day_bounds, ensure_utc, utc_now
from app.core.context import current_context, current_tenant_id, use_context
from app.product_events.models import (
    FUNNEL_STEPS,
    ONCE_PER_SHOP,
    ProductEvent,
    ProductEventName,
)

__all__ = ["FunnelStep", "ProductEventService", "clean_props", "iso_week_key"]

log = logging.getLogger(__name__)

_ONCE = "once"
_PROP_KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
# Codes only: must start with a letter, so a phone number or an amount typed
# as text cannot pass; a run of 5+ digits is refused for the same reason.
_PROP_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
_DIGIT_RUN = re.compile(r"\d{5,}")
_MAX_PROPS = 8


def clean_props(props: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only allow-listed, PII-free values: booleans, integers and short codes."""
    cleaned: dict[str, Any] = {}
    for key, value in (props or {}).items():
        if len(cleaned) >= _MAX_PROPS or not isinstance(key, str) or not _PROP_KEY.match(key):
            continue
        if isinstance(value, bool | int) or (
            isinstance(value, str) and _PROP_CODE.match(value) and not _DIGIT_RUN.search(value)
        ):
            cleaned[key] = value
    return cleaned


def iso_week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


@dataclass(frozen=True)
class FunnelStep:
    name: str
    shops: int


class ProductEventService:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def record(
        self,
        name: ProductEventName,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        props: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
        occurred_at: datetime | None = None,
    ) -> bool:
        """Store one event. Returns whether a new row was written."""
        try:
            return await self._record(
                name,
                tenant_id=tenant_id,
                user_id=user_id,
                props=props,
                dedupe_key=dedupe_key,
                occurred_at=occurred_at,
            )
        except Exception:
            log.warning("product event not recorded", extra={"event_name": str(name)})
            return False

    async def core_action(
        self,
        name: ProductEventName,
        *,
        tenant_id: uuid.UUID,
        props: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> None:
        """A core-loop action: record ``name`` and mark the shop active this week."""
        user_id = current_context().user_id
        await self.record(
            name, tenant_id=tenant_id, user_id=user_id, props=props, dedupe_key=dedupe_key
        )
        await self.weekly_active(tenant_id=tenant_id, user_id=user_id)

    async def weekly_active(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID | None) -> bool:
        """Mark the shop active for the current ISO week (Asia/Dhaka)."""
        return await self.record(
            ProductEventName.WEEKLY_ACTIVE,
            tenant_id=tenant_id,
            user_id=user_id,
            dedupe_key=iso_week_key(business_date()),
        )

    async def _record(
        self,
        name: ProductEventName,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None,
        props: dict[str, Any] | None,
        dedupe_key: str | None,
        occurred_at: datetime | None,
    ) -> bool:
        if name in ONCE_PER_SHOP:
            key = _ONCE
        elif dedupe_key:
            key = dedupe_key[:64]
        else:
            # plan_paid and weekly_active are counted per payment / per week;
            # without a key a retry would double count.
            log.warning("product event without dedupe key", extra={"event_name": str(name)})
            return False

        active = current_tenant_id()
        if active is not None and active != tenant_id:
            log.error("product event for another tenant refused", extra={"event_name": str(name)})
            return False

        # Shop creation runs before a tenant is in scope; scope this write to
        # the shop it belongs to rather than bypassing the tenancy guard.
        scope = use_context(tenant_id=tenant_id) if active is None else nullcontext()
        # Every statement runs inside the savepoint: on PostgreSQL a failed
        # statement outside it would abort the caller's whole transaction.
        try:
            with scope:
                async with self._db.begin_nested():
                    flags = FeatureFlagService(self._db)
                    enabled = await flags.is_enabled(
                        FlagKey.PRODUCT_EVENTS_ENABLED, tenant_id=tenant_id
                    )
                    if not enabled or await self._exists(tenant_id, name, key):
                        return False
                    self._db.add(
                        ProductEvent(
                            tenant_id=tenant_id,
                            user_id=user_id,
                            name=str(name),
                            dedupe_key=key,
                            occurred_at=ensure_utc(occurred_at) if occurred_at else utc_now(),
                            props=clean_props(props),
                        )
                    )
                    await self._db.flush()
        except IntegrityError:
            return False
        except SQLAlchemyError:
            log.warning("product event not recorded", extra={"event_name": str(name)})
            return False
        return True

    async def _exists(self, tenant_id: uuid.UUID, name: ProductEventName, key: str) -> bool:
        statement = sa.select(sa.literal(1)).where(
            ProductEvent.tenant_id == tenant_id,
            ProductEvent.name == str(name),
            ProductEvent.dedupe_key == key,
        )
        return (await self._db.execute(statement.limit(1))).first() is not None

    async def funnel(self, *, start: date, end: date) -> list[FunnelStep]:
        """Distinct shops per step with ``occurred_at`` in the Dhaka days ``[start, end]``.

        Cross-tenant by nature: call it only from a system (admin) session.
        """
        window_start, _ = business_day_bounds(start)
        _, window_end = business_day_bounds(end)
        statement = (
            sa.select(ProductEvent.name, sa.func.count(sa.distinct(ProductEvent.tenant_id)))
            .where(ProductEvent.occurred_at >= window_start, ProductEvent.occurred_at < window_end)
            .group_by(ProductEvent.name)
        )
        counts = {str(n): int(c) for n, c in (await self._db.execute(statement)).all()}
        return [FunnelStep(name=str(step), shops=counts.get(str(step), 0)) for step in FUNNEL_STEPS]
