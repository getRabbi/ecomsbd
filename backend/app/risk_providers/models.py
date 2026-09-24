"""A shop's external risk provider connection and its lookups (V3.7)."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, TZDateTime


class RiskProviderConnection(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "risk_provider_connections"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "provider_id"),)

    provider_id: Mapped[str] = mapped_column(sa.String(40))
    enabled: Mapped[bool] = mapped_column(default=False)
    #: AES-GCM envelope of the credential fields, bound to shop and provider.
    credentials_enc: Mapped[str | None] = mapped_column(sa.Text)
    #: Last four characters of the first credential, for recognition only.
    credential_hint: Mapped[str | None] = mapped_column(sa.String(8))
    #: Non-secret settings the adapter declares.
    config: Mapped[dict] = mapped_column(JSONColumn, default=dict)
    cache_ttl_hours: Mapped[int] = mapped_column(default=72)
    #: UNKNOWN, HEALTHY, DEGRADED or DOWN.
    health: Mapped[str] = mapped_column(sa.String(16), default="UNKNOWN")
    last_tested_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_test_result: Mapped[str | None] = mapped_column(sa.String(32))
    last_success_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_failure_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_error_code: Mapped[str | None] = mapped_column(sa.String(40))
    consecutive_failures: Mapped[int] = mapped_column(default=0)
    rate_limited_until: Mapped[datetime | None] = mapped_column(TZDateTime)


class ExternalRiskLookup(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One provider call, successful or not. Also the audit trail of lookups."""

    __tablename__ = "external_risk_lookups"
    __table_args__ = (
        sa.Index("ix_external_risk_lookups_customer", "tenant_id", "customer_id", "provider_id"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("customers.id", ondelete="CASCADE")
    )
    provider_id: Mapped[str] = mapped_column(sa.String(40))
    #: FOUND, NOT_FOUND or FAILED.
    status: Mapped[str] = mapped_column(sa.String(16))
    error_code: Mapped[str | None] = mapped_column(sa.String(40))
    facts: Mapped[list] = mapped_column(JSONColumn, default=list)
    dropped_facts: Mapped[int] = mapped_column(default=0)
    provider_observed_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    sample_size: Mapped[int | None] = mapped_column()
    confidence: Mapped[str | None] = mapped_column(sa.String(24))
    provider_reference: Mapped[str | None] = mapped_column(sa.String(120))
    checked_at: Mapped[datetime] = mapped_column(TZDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    #: MANUAL, REFRESH or AUTOMATION.
    trigger: Mapped[str] = mapped_column(sa.String(16))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(GUID)
    attempts: Mapped[int] = mapped_column(default=1)
    duration_ms: Mapped[int] = mapped_column(default=0)
