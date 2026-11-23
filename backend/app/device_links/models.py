"""Ingest keys and pairing codes for linked devices (V4 WP1.1).

A linked phone or extension is an ``integration_connections`` row with
provider ``DEVICE`` (rule L2). It authenticates captures with a scoped ingest
key (rule L3), never a seller session. Only the key's SHA-256 is stored; the
key itself is shown once.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, TZDateTime

__all__ = ["INTAKE_CAPTURE", "DeviceIngestKey", "DevicePairingCode"]

#: The only scope an ingest key carries today.
INTAKE_CAPTURE = "intake.capture"


class DeviceIngestKey(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "device_ingest_keys"
    __table_args__ = (
        sa.UniqueConstraint("key_hash", name="uq_device_ingest_keys_hash"),
        sa.Index("ix_device_ingest_keys_connection", "connection_id"),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("integration_connections.id", ondelete="CASCADE"), nullable=False
    )
    key_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    key_last4: Mapped[str] = mapped_column(sa.String(4), nullable=False)
    scope: Mapped[str] = mapped_column(sa.String(32), nullable=False, default=INTAKE_CAPTURE)
    created_by: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class DevicePairingCode(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """A 6-digit code a seller shows on the web for a phone to claim, once."""

    __tablename__ = "device_pairing_codes"
    __table_args__ = (sa.Index("ix_device_pairing_codes_hash", "code_hash"),)

    code_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
