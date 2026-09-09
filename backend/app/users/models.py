"""User model.

A user is identified by a Bangladeshi mobile number and may belong to more than
one shop, so this table is *not* tenant-owned; membership lives in
``tenant_users``.

Phone storage follows master spec section 133:

*   ``phone_enc`` — AES-GCM ciphertext, for display;
*   ``phone_search_hmac`` — keyed HMAC of the canonical E.164 form, unique, the
    only thing exact lookups match on;
*   ``phone_last4`` — for support and masked display.

The canonical number is never stored in clear text and never logged. A plain
unsalted hash would not help: the Bangladeshi mobile keyspace is roughly a
billion numbers, which is trivially enumerable offline.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PrimaryKeyMixin, SoftDeleteMixin, TimestampMixin
from app.db.types import TZDateTime

if TYPE_CHECKING:
    from app.tenants.models import TenantUser

__all__ = ["User", "UserStatus"]


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    #: Blocked by platform operations (abuse). Sessions are revoked with it.
    SUSPENDED = "SUSPENDED"
    PENDING_DELETION = "PENDING_DELETION"


class User(Base, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """A person who signs in with a phone number."""

    __tablename__ = "users"
    __table_args__ = (sa.UniqueConstraint("phone_search_hmac", name="uq_users_phone_search_hmac"),)

    #: Keyed HMAC of the canonical ``+8801XXXXXXXXX`` form. Exact lookups only.
    phone_search_hmac: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    #: AES-GCM envelope of the canonical number.
    phone_enc: Mapped[str] = mapped_column(sa.Text, nullable=False)
    phone_last4: Mapped[str] = mapped_column(sa.String(4), nullable=False)

    display_name: Mapped[str | None] = mapped_column(sa.String(160), nullable=True)
    status: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, default=UserStatus.ACTIVE, index=True
    )
    #: Language for server-generated copy. Bangla-first (master spec section 52).
    locale: Mapped[str] = mapped_column(sa.String(8), nullable=False, default="bn")

    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    memberships: Mapped[list[TenantUser]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE and self.deleted_at is None

    @property
    def masked_phone(self) -> str:
        """``*******78`` — the last four are all this row can reveal on its own."""
        return f"*******{self.phone_last4[-2:]}"
