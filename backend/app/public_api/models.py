import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, TZDateTime


class ApiKey(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "public_api_keys"
    name: Mapped[str] = mapped_column(sa.String(100))
    secret_hash: Mapped[str] = mapped_column(sa.String(64))
    scopes: Mapped[list] = mapped_column(JSONColumn)
    rate_limit: Mapped[int] = mapped_column(default=60)
    created_by: Mapped[uuid.UUID] = mapped_column(GUID)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    #: Refreshed at most once a minute; feeds integration health, not billing.
    last_used_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    #: Optional (V3.8). An expired key is refused like a revoked one.
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime)


class ApiWriteReceipt(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "public_api_write_receipts"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "key_id", "endpoint", "idempotency_key"),)
    key_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("public_api_keys.id"))
    endpoint: Mapped[str] = mapped_column(sa.String(160))
    idempotency_key: Mapped[str] = mapped_column(sa.String(120))
    request_hash: Mapped[str] = mapped_column(sa.String(64))
    response: Mapped[dict] = mapped_column(JSONColumn)


class WebhookEndpoint(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "outbound_webhook_endpoints"
    url: Mapped[str] = mapped_column(sa.String(1000))
    secret_enc: Mapped[str] = mapped_column(sa.Text)
    topics: Mapped[list] = mapped_column(JSONColumn)
    enabled: Mapped[bool] = mapped_column(default=True)


class WebhookDelivery(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "outbound_webhook_deliveries"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "endpoint_id", "event_id"),
        sa.Index("ix_webhook_due", "status", "next_attempt_at"),
    )
    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("outbound_webhook_endpoints.id")
    )
    event_id: Mapped[uuid.UUID] = mapped_column(GUID)
    payload: Mapped[dict] = mapped_column(JSONColumn)
    status: Mapped[str] = mapped_column(sa.String(24), default="PENDING")
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(TZDateTime)
    last_status: Mapped[int | None] = mapped_column()
    last_error: Mapped[str | None] = mapped_column(sa.String(80))


class WebhookAttempt(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "outbound_webhook_attempts"
    delivery_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("outbound_webhook_deliveries.id")
    )
    status_code: Mapped[int | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(sa.String(80))
