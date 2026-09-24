"""One row per seller connection, its event log and its sync runs.

Orders never live here. A connection points at a V2 ``external_order_sources``
row and every order it brings in goes through ``order_sources.service.ingest``,
so dedupe, the order service, CRM and inventory are the V2 ones.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, TZDateTime


class IntegrationConnection(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "integration_connections"
    __table_args__ = (
        # Deliberately not tenant-scoped: one store or Page links to one shop.
        # account_key is cleared on disconnect, and NULLs never collide.
        sa.UniqueConstraint("provider", "account_key"),
        sa.UniqueConstraint("webhook_token"),
        sa.UniqueConstraint("state_hash"),
    )
    provider: Mapped[str] = mapped_column(sa.String(24))
    name: Mapped[str] = mapped_column(sa.String(120))
    #: PENDING, CONNECTED, DISABLED, AUTH_EXPIRED or DISCONNECTED.
    state: Mapped[str] = mapped_column(sa.String(24), default="PENDING")
    account_id: Mapped[str | None] = mapped_column(sa.String(255))
    account_name: Mapped[str | None] = mapped_column(sa.String(200))
    account_key: Mapped[str | None] = mapped_column(sa.String(255))
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("external_order_sources.id")
    )
    #: Vault envelope of a JSON object. Never returned by any endpoint.
    credentials_enc: Mapped[str | None] = mapped_column(sa.Text)
    #: Routes an unauthenticated provider callback to this row; not a credential.
    webhook_token: Mapped[str | None] = mapped_column(sa.String(64))
    webhook_secret_enc: Mapped[str | None] = mapped_column(sa.Text)
    #: SHA-256 of a pending OAuth state; single use, cleared on callback.
    state_hash: Mapped[str | None] = mapped_column(sa.String(64))
    state_expires_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    #: Non-secret settings: import window, webhook ids, linked API key ids.
    config: Mapped[dict] = mapped_column(JSONColumn, default=dict)
    webhook_state: Mapped[str | None] = mapped_column(sa.String(24))
    sync_state: Mapped[str | None] = mapped_column(sa.String(24))
    last_success_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_webhook_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_sync_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_error_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_error_code: Mapped[str | None] = mapped_column(sa.String(64))
    created_by: Mapped[uuid.UUID] = mapped_column(GUID)


class IntegrationEvent(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "integration_events"
    __table_args__ = (
        # A provider delivery id is accepted once: webhook retries are no-ops.
        sa.UniqueConstraint("tenant_id", "connection_id", "delivery_id"),
        sa.Index("ix_integration_event_due", "status", "next_attempt_at"),
        sa.Index("ix_integration_event_connection", "connection_id", "created_at"),
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("integration_connections.id")
    )
    provider: Mapped[str] = mapped_column(sa.String(24))
    #: WEBHOOK, SYNC, AUTH or SECURITY.
    kind: Mapped[str] = mapped_column(sa.String(16))
    topic: Mapped[str] = mapped_column(sa.String(64))
    delivery_id: Mapped[str | None] = mapped_column(sa.String(200))
    external_ref: Mapped[str | None] = mapped_column(sa.String(200))
    #: A non-PII fact read from a verified body, such as "cancelled".
    hint: Mapped[str | None] = mapped_column(sa.String(32))
    #: QUEUED, PROCESSED, IGNORED, FAILED or RESOLVED.
    status: Mapped[str] = mapped_column(sa.String(16), default="QUEUED")
    #: Seller-safe reason code. Raw provider errors are never stored.
    code: Mapped[str | None] = mapped_column(sa.String(64))
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    order_id: Mapped[uuid.UUID | None] = mapped_column(GUID, sa.ForeignKey("orders.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime)


class IntegrationSyncRun(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "integration_sync_runs"
    __table_args__ = (
        sa.Index("ix_integration_sync_due", "status", "next_run_at"),
        sa.Index("ix_integration_sync_connection", "connection_id", "created_at"),
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("integration_connections.id")
    )
    #: INITIAL (a seller-chosen window) or INCREMENTAL (catch-up after webhooks).
    kind: Mapped[str] = mapped_column(sa.String(16))
    #: QUEUED, RUNNING, COMPLETED, FAILED or CANCELLED.
    status: Mapped[str] = mapped_column(sa.String(16), default="QUEUED")
    since: Mapped[datetime] = mapped_column(TZDateTime)
    until: Mapped[datetime] = mapped_column(TZDateTime)
    #: Provider page cursor, committed with the page's orders: resume is exact.
    cursor: Mapped[str | None] = mapped_column(sa.String(500))
    pages: Mapped[int] = mapped_column(default=0)
    imported: Mapped[int] = mapped_column(default=0)
    duplicates: Mapped[int] = mapped_column(default=0)
    skipped: Mapped[int] = mapped_column(default=0)
    failed: Mapped[int] = mapped_column(default=0)
    total: Mapped[int | None] = mapped_column()
    attempts: Mapped[int] = mapped_column(default=0)
    next_run_at: Mapped[datetime] = mapped_column(TZDateTime)
    last_error_code: Mapped[str | None] = mapped_column(sa.String(64))
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    created_by: Mapped[uuid.UUID | None] = mapped_column(GUID)
