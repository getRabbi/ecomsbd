"""Login identities.

A user is one ecomsbd account. An *identity* is one way of proving you are
that account: a password, a Google `sub`, an Apple `sub`, a phone number. One
user may hold several; the user row, its shops and its sessions do not care
which one was used to sign in.

Two rules hold the model together, and both exist because of a specific way
this normally goes wrong.

**The provider's subject is the identity, not the email.** A Google account's
email can change; its `sub` cannot. Matching on email would mean that giving up
an address hands the account to whoever gets it next. So ``provider`` plus
``provider_subject`` is the unique key, and ``normalized_email`` is a
*candidate* for linking rather than an identifier.

**An unverified email proves nothing.** It is not enough to know that some row
claims an address — the row must say the address was proven. That single
condition is what stops the pre-hijack attack: register `victim@example.com`
with a password, never verify it, wait for the real owner to arrive through
Google, and inherit their shop. Here that link is refused and the real owner
gets their own account.
"""

from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.base import Base, PrimaryKeyMixin, TimestampMixin
from app.db.types import GUID, TZDateTime

__all__ = [
    "MAX_EMAIL_LENGTH",
    "AuthIdentity",
    "AuthProvider",
    "AuthToken",
    "AuthTokenPurpose",
    "normalize_email",
]

#: Long enough for any deliverable address; short enough to index.
MAX_EMAIL_LENGTH = 254


class AuthProvider(StrEnum):
    """How a sign-in was proven."""

    PASSWORD = "PASSWORD"
    GOOGLE = "GOOGLE"
    APPLE = "APPLE"
    #: Retained so existing OTP accounts keep working. Deferred in production
    #: (``PHONE_OTP_LOGIN_ENABLED=false``), not deleted.
    PHONE = "PHONE"


class AuthTokenPurpose(StrEnum):
    EMAIL_VERIFICATION = "EMAIL_VERIFICATION"
    PASSWORD_RESET = "PASSWORD_RESET"


def normalize_email(raw: str) -> str:
    """Canonical form used for uniqueness and lookup.

    Unicode-normalised, trimmed, lowercased. Nothing more: provider-specific
    folding — stripping dots or ``+tag`` suffixes the way Gmail does — is
    **not** applied. It is not true of most providers, and applying it would
    silently merge two addresses that a different mail server treats as two
    different people.
    """
    return unicodedata.normalize("NFKC", raw).strip().lower()


class AuthIdentity(Base, PrimaryKeyMixin, TimestampMixin):
    """One way of signing in to one user account."""

    __tablename__ = "auth_identities"
    __table_args__ = (
        # The identity key. For PASSWORD the subject *is* the normalized email;
        # for GOOGLE and APPLE it is the provider's `sub`; for PHONE it is the
        # phone search HMAC, so no clear-text number lands here either.
        sa.UniqueConstraint(
            "provider", "provider_subject", name="uq_auth_identities_provider_subject"
        ),
        # One identity per provider per user: a user cannot hold two Google
        # accounts on one ecomsbd login, which keeps "which Google account owns
        # this shop?" answerable.
        sa.UniqueConstraint("user_id", "provider", name="uq_auth_identities_user_provider"),
        sa.Index("ix_auth_identities_email", "normalized_email"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(sa.String(16), nullable=False, index=True)
    provider_subject: Mapped[str] = mapped_column(sa.String(255), nullable=False)

    #: What the provider says this identity's address is. Nullable: Apple
    #: supplies one only on first authorization, and a phone identity has none.
    normalized_email: Mapped[str | None] = mapped_column(sa.String(MAX_EMAIL_LENGTH), nullable=True)
    #: Whether *this* identity proved the address. Linking reads this, never
    #: the address alone.
    email_verified: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    #: PHC-encoded Argon2id digest. Only ever set for PASSWORD.
    password_hash: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    #: Bumped when the password changes, so a stale reset token is recognisable
    #: even before its row is consumed.
    password_updated_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    @property
    def is_password(self) -> bool:
        return self.provider == AuthProvider.PASSWORD

    def can_be_linked_to(self, *, email: str | None, email_verified: bool) -> bool:
        """Whether an incoming provider identity may join this identity's user.

        Both sides must have proven the same address. One verified side is not
        enough in either direction: an unverified local row is a claim nobody
        checked, and an unverified provider assertion is the provider telling
        us it did not check either.
        """
        return bool(
            email
            and email_verified
            and self.email_verified
            and self.normalized_email is not None
            and self.normalized_email == email
        )


class AuthToken(Base, PrimaryKeyMixin):
    """A single-use, expiring link token: verify this email, reset this password.

    Stored hashed for the same reason a refresh token is: the table is a list of
    live account-takeover keys otherwise. Consumption is recorded rather than
    the row deleted, so a replay is *visible* — it can be audited as an attempt
    instead of looking like an unknown token.
    """

    __tablename__ = "auth_tokens"
    __table_args__ = (
        sa.UniqueConstraint("token_hash", name="uq_auth_tokens_token_hash"),
        sa.Index("ix_auth_tokens_identity_purpose", "identity_id", "purpose"),
        sa.Index("ix_auth_tokens_expires_at", "expires_at"),
    )

    identity_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("auth_identities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)

    issued_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    #: Superseded by a newer token for the same purpose, or by the action
    #: happening another way.
    invalidated_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    request_ip_hash: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    def is_expired(self, *, at: datetime | None = None) -> bool:
        return self.expires_at <= (at or utc_now())

    def is_usable(self, *, at: datetime | None = None) -> bool:
        return (
            self.consumed_at is None and self.invalidated_at is None and not self.is_expired(at=at)
        )
