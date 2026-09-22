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
