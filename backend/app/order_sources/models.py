import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn


class OrderSource(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "external_order_sources"
    name: Mapped[str] = mapped_column(sa.String(120))
    provider: Mapped[str] = mapped_column(sa.String(32))
    enabled: Mapped[bool] = mapped_column(default=False)
    mapping: Mapped[dict] = mapped_column(JSONColumn, default=dict)


class ExternalOrder(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "external_orders"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "source_id", "external_order_id"),)
    source_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("external_order_sources.id"))
    external_order_id: Mapped[str] = mapped_column(sa.String(200))
    request_hash: Mapped[str] = mapped_column(sa.String(64))
    order_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("orders.id"))
