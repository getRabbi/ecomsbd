"""The platform's voice provider accounts (V4 WP3.1).

Platform-level, not tenant-owned: ecomsbd places confirmation calls on its own
provider account and meters them per shop as credits (WP3.4). A seller never
sees or supplies these credentials.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TimestampMixin
from app.db.types import JSONColumn, TZDateTime

__all__ = ["VoiceProviderConnection"]


class VoiceProviderConnection(Base, PrimaryKeyMixin, TimestampMixin):
    """One provider account. One row per provider."""

    __tablename__ = "voice_provider_connections"
    __table_args__ = (
        sa.UniqueConstraint("provider", name="uq_voice_provider_connections_provider"),
    )

    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    #: Off until an operator turns it on; turning on needs a ready manifest,
    #: credentials and a callback secret.
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    #: One vault envelope over a JSON object of the manifest's
    #: ``credential_fields``. Context ``voice_provider_connection:<id>``.
    credentials_encrypted: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    #: Verifies the provider's callbacks. Unset → every callback is refused.
    callback_secret_encrypted: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    key_version: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    #: ``****abcd`` of the first credential field; the only credential-derived
    #: value that ever leaves the server.
    masked_identifier: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)

    #: The platform's own outgoing number (E.164), only where the manifest
    #: verifies ``caller_id``. Not a customer number.
    caller_id: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    #: Non-secret provider settings (region, application id).
    config: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)

    created_by: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    updated_by: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    credentials_rotated_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    @property
    def has_credentials(self) -> bool:
        return bool(self.credentials_encrypted)

    @property
    def has_callback_secret(self) -> bool:
        return bool(self.callback_secret_encrypted)
