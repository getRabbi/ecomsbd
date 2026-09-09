"""Authentication models.

Master spec sections 4, 47 and 89.

*   ``otp_challenges`` — hashed six-digit codes, five-minute expiry, five
    attempts, never stored or logged in clear text.
*   ``auth_sessions`` — one per signed-in device, revocable server-side.
*   ``refresh_tokens`` — opaque, stored hashed, **rotated on every use** with a
    ``replaced_by`` chain. Presenting an already-rotated token is treated as
    theft and revokes the whole session (master spec section 47, and the
    ``REFRESH_TOKEN_REUSE_DETECTED`` audit action).
*   ``devices`` — app version, platform, push token, last seen, revoked.

Login is OTP-based, so there is no password to change: revocation has to work
through these tables or it does not work at all.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utc_now
from app.db.base import Base, PrimaryKeyMixin, TimestampMixin
from app.db.types import GUID, TZDateTime

__all__ = [
    "AuthSession",
    "Device",
    "DevicePlatform",
    "OtpChallenge",
    "OtpDeliveryStatus",
    "OtpPurpose",
    "RefreshToken",
    "RevocationReason",
]


class OtpPurpose(StrEnum):
    LOGIN = "LOGIN"
    #: Reserved: confirming a changed shop contact number.
    PHONE_CHANGE = "PHONE_CHANGE"


class OtpDeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    #: Development provider: recorded locally, never sent to a network.
    DEV_LOGGED = "DEV_LOGGED"


class RevocationReason(StrEnum):
    USER_LOGOUT = "USER_LOGOUT"
    LOGOUT_ALL = "LOGOUT_ALL"
    TOKEN_REUSE_DETECTED = "TOKEN_REUSE_DETECTED"
    ADMIN_ACTION = "ADMIN_ACTION"
    USER_SUSPENDED = "USER_SUSPENDED"
    EXPIRED = "EXPIRED"


class DevicePlatform(StrEnum):
    ANDROID = "ANDROID"
    IOS = "IOS"
    WEB = "WEB"
    UNKNOWN = "UNKNOWN"


class OtpChallenge(Base, PrimaryKeyMixin):
    """A pending one-time-code verification.

    Not tenant-owned: it exists before we know which shop the caller belongs to.
    The phone is stored the same way as on ``users`` — encrypted plus a keyed
    search hash — so an OTP table dump is not a phone list.
    """

    __tablename__ = "otp_challenges"
    __table_args__ = (
        sa.Index("ix_otp_challenges_phone_created", "phone_search_hmac", "created_at"),
        sa.Index("ix_otp_challenges_expires_at", "expires_at"),
        sa.CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        sa.CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
    )

    phone_search_hmac: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    phone_enc: Mapped[str] = mapped_column(sa.Text, nullable=False)
    phone_last4: Mapped[str] = mapped_column(sa.String(4), nullable=False)

    #: HMAC of the code, bound to this challenge id. The code itself is never stored.
    code_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(sa.String(24), nullable=False, default=OtpPurpose.LOGIN)

    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=5)

    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    #: Set when attempts are exhausted, so a burnt challenge cannot be reused.
    locked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    delivery_status: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default=OtpDeliveryStatus.PENDING
    )
    delivery_provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    delivery_reference: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    delivery_error: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    #: Hashed, never the raw address (master spec section 47 logging rules).
    request_ip_hash: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utc_now, index=True
    )

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    @property
    def is_locked(self) -> bool:
        return self.locked_at is not None or self.attempts >= self.max_attempts

    def is_expired(self, *, at: datetime | None = None) -> bool:
        return self.expires_at <= (at or utc_now())

    @property
    def attempts_remaining(self) -> int:
        return max(0, self.max_attempts - self.attempts)


class Device(Base, PrimaryKeyMixin, TimestampMixin):
    """A signed-in installation (master spec section 89)."""

    __tablename__ = "devices"
    __table_args__ = (
        sa.UniqueConstraint("user_id", "install_id", name="uq_devices_user_id_install_id"),
        sa.Index("ix_devices_user_last_seen", "user_id", "last_seen_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Client-generated stable installation id, so reinstalling creates a new row.
    install_id: Mapped[str] = mapped_column(sa.String(120), nullable=False)

    platform: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default=DevicePlatform.UNKNOWN
    )
    app_version: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    os_version: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    model: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)

    push_token: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    push_token_updated_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None


class AuthSession(Base, PrimaryKeyMixin, TimestampMixin):
    """One signed-in session. Revoking it invalidates its whole token chain."""

    __tablename__ = "auth_sessions"
    __table_args__ = (sa.Index("ix_auth_sessions_user_revoked", "user_id", "revoked_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The shop this session is acting for. NULL until onboarding picks one.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )

    app_version: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    client_ip_hash: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)

    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)

    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, *, at: datetime | None = None) -> bool:
        return self.expires_at <= (at or utc_now())

    def is_usable(self, *, at: datetime | None = None) -> bool:
        return not self.is_revoked and not self.is_expired(at=at)


class RefreshToken(Base, PrimaryKeyMixin):
    """One link in a session's rotating refresh-token chain.

    Only the hash is stored. Each successful refresh marks the presented token
    used and records the replacement, which is what makes reuse detectable: a
    second presentation of a token that already has ``used_at`` set can only mean
    the token was captured, so the entire session is revoked.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        sa.Index("ix_refresh_tokens_session_issued", "session_id", "issued_at"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("auth_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)

    issued_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)

    session: Mapped[AuthSession] = relationship(back_populates="refresh_tokens")

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, *, at: datetime | None = None) -> bool:
        return self.expires_at <= (at or utc_now())

    def is_usable(self, *, at: datetime | None = None) -> bool:
        return not self.is_used and not self.is_revoked and not self.is_expired(at=at)
