"""The platform's SMS gateway accounts (V4 WP7.1). Platform-level: no shop owns one."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TimestampMixin
from app.db.types import TZDateTime

__all__ = ["SmsProviderConnection"]


class SmsProviderConnection(Base, PrimaryKeyMixin, TimestampMixin):
    """One gateway account. One row per provider."""

    __tablename__ = "sms_provider_connections"
    __table_args__ = (sa.UniqueConstraint("provider", name="uq_sms_provider_connections_provider"),)

    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    #: Off until an operator turns it on with credentials stored.
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    #: One vault envelope over a JSON object of the manifest's
    #: ``credential_fields``. Context ``sms_provider_connection:<id>``.
    credentials_encrypted: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    key_version: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    #: ``****abcd`` of the first credential; the only key-derived value shown.
    masked_identifier: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    #: The approved Sender ID, when the provider approved one. Not a secret.
    sender_id: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    created_by: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    updated_by: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    credentials_rotated_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    @property
    def has_credentials(self) -> bool:
        return bool(self.credentials_encrypted)
