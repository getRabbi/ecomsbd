"""Stored demand-forecast snapshots (V3.6).

One row per item per day, written by the daily job. They exist so a forecast
can be scored against what actually sold once its horizon has passed — a
forecast recomputed today would always look right about the past — and so a
newly at-risk item is announced once, not every day.
"""

from __future__ import annotations

import uuid
from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID


class DemandForecast(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "demand_forecasts"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "as_of", "item_key"),
        sa.Index("ix_demand_forecasts_as_of", "tenant_id", "as_of"),
    )

    as_of: Mapped[date] = mapped_column(sa.Date)
    product_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("products.id", ondelete="CASCADE")
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("product_variants.id", ondelete="CASCADE")
    )
    item_key: Mapped[str] = mapped_column(sa.String(80))
    confidence: Mapped[str] = mapped_column(sa.String(16))
    #: Units per day × 1000; ``None`` when the history was insufficient.
    rate_milli: Mapped[int | None] = mapped_column()
    horizon_days: Mapped[int] = mapped_column()
    predicted_units: Mapped[int | None] = mapped_column()
    on_hand: Mapped[int] = mapped_column()
    incoming: Mapped[int] = mapped_column()
    lead_time_days: Mapped[int] = mapped_column()
    reorder_point: Mapped[int | None] = mapped_column()
    suggested_quantity: Mapped[int | None] = mapped_column()
    stockout_on: Mapped[date | None] = mapped_column(sa.Date)
    at_risk: Mapped[bool] = mapped_column(default=False)
