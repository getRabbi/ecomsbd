"""``product_events``: one row per funnel step a shop reaches.

A row carries no personal data. ``props`` holds only allow-listed codes and
counts (see :func:`app.product_events.service.clean_props`), never a phone
number, a name, an address or message text.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, TZDateTime

__all__ = ["FUNNEL_STEPS", "ONCE_PER_SHOP", "ProductEvent", "ProductEventName"]


class ProductEventName(StrEnum):
    SHOP_CREATED = "shop_created"
    FIRST_ORDER = "first_order"
    FIRST_COURIER_CONNECTED = "first_courier_connected"
    FIRST_BOOKING = "first_booking"
    FIRST_PAYOUT_IMPORT = "first_payout_import"
    FIRST_RECONCILE = "first_reconcile"
    FIRST_CAPTURE = "first_capture"
    FIRST_CONFIRMATION_CALL = "first_confirmation_call"
    FIRST_AUTOPILOT_ORDER = "first_autopilot_order"
    PLAN_PAID = "plan_paid"
    WEEKLY_ACTIVE = "weekly_active"


#: Funnel order for ``GET /v1/admin/ops/funnel``.
FUNNEL_STEPS: tuple[ProductEventName, ...] = tuple(ProductEventName)

#: Events a shop reaches at most once. Their dedupe key is constant, so the
#: unique ``(tenant_id, name, dedupe_key)`` constraint acts as a unique
#: ``(tenant_id, name)`` for them.
ONCE_PER_SHOP: frozenset[ProductEventName] = frozenset(
    name
    for name in ProductEventName
    if name is ProductEventName.SHOP_CREATED or name.value.startswith("first_")
)


class ProductEvent(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "product_events"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "name", "dedupe_key", name="uq_product_events_dedupe"),
        sa.Index("ix_product_events_name_occurred", "name", "occurred_at"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    name: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    #: ``once`` for once-per-shop events, the ISO week for ``weekly_active``,
    #: the payment reference for ``plan_paid``.
    dedupe_key: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    props: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
