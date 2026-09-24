from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import JSONColumn, TZDateTime


class NetworkPreference(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "network_preferences"
    __table_args__ = (sa.UniqueConstraint("tenant_id"),)
    opted_in: Mapped[bool] = mapped_column(default=False)
    opted_in_at: Mapped[datetime | None] = mapped_column(TZDateTime)


class NetworkBenchmark(Base, PrimaryKeyMixin, TimestampMixin):
    """An immutable anonymous monthly release, containing no shop identifiers."""

    __tablename__ = "network_benchmarks"
    period: Mapped[str] = mapped_column(sa.String(7), unique=True)
    policy_version: Mapped[str] = mapped_column(sa.String(24))
    status: Mapped[str] = mapped_column(sa.String(24))
    facts: Mapped[dict] = mapped_column(JSONColumn)


class NetworkBenchmarkCell(Base, PrimaryKeyMixin, TimestampMixin):
    """One cohort figure of a closed month (V3.7). Immutable once written.

    Holds a rounded value and coarse bands only, never a shop, a count or a
    per-shop value. A cohort that fails the privacy thresholds is still
    written, as ``DATA_NOT_SUFFICIENT`` with no value, so the set of cohorts
    is fixed and its presence says nothing.
    """

    __tablename__ = "network_benchmark_cells"
    __table_args__ = (
        sa.UniqueConstraint("period", "policy_version", "metric", "dimension", "cohort"),
    )
    period: Mapped[str] = mapped_column(sa.String(7), index=True)
    policy_version: Mapped[str] = mapped_column(sa.String(24))
    metric: Mapped[str] = mapped_column(sa.String(32))
    dimension: Mapped[str] = mapped_column(sa.String(16))
    cohort: Mapped[str] = mapped_column(sa.String(32))
    status: Mapped[str] = mapped_column(sa.String(24))
    value: Mapped[int | None] = mapped_column()
    precision: Mapped[int | None] = mapped_column()
    unit: Mapped[str] = mapped_column(sa.String(12))
    shops_band: Mapped[str | None] = mapped_column(sa.String(12))
    sample_band: Mapped[str | None] = mapped_column(sa.String(12))
