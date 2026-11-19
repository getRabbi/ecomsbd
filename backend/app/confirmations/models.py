"""Customer confirmation of an order (V4 WP2.1, rule L7).

A confirmation is its own entity, separate from the order's status: the order
says where the parcel is in its life, the confirmation says whether the
customer really wants it and how we found out. An order has at most one open
(non-terminal) confirmation; an edit that changes what the customer agreed to
supersedes it and a new one starts.

``confirmation_attempts`` is one row per call or link sent. ``confirmation_links``
holds the SMS link tokens (WP2.3), hashed; the token itself is never stored.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, Paisa, TZDateTime

__all__ = [
    "OPEN_STATES",
    "TERMINAL_STATES",
    "AttemptChannel",
    "ConfirmationAttempt",
    "ConfirmationDecision",
    "ConfirmationLink",
    "ConfirmationMethod",
    "ConfirmationState",
    "CostQuality",
    "OrderConfirmation",
    "OrderOutcome",
]


class ConfirmationState(StrEnum):
    PENDING = "PENDING"
    CALL_QUEUED = "CALL_QUEUED"
    CALLING = "CALLING"
    NO_ANSWER = "NO_ANSWER"
    SMS_LINK_SENT = "SMS_LINK_SENT"
    SELLER_REVIEW = "SELLER_REVIEW"
    # terminal
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_STATES


TERMINAL_STATES: frozenset[ConfirmationState] = frozenset(
    {ConfirmationState.CONFIRMED, ConfirmationState.CANCELLED, ConfirmationState.SUPERSEDED}
)
OPEN_STATES: frozenset[ConfirmationState] = frozenset(ConfirmationState) - TERMINAL_STATES


class ConfirmationDecision(StrEnum):
    """What the policy (WP2.2) decided for the order."""

    AUTO_CONFIRM = "AUTO_CONFIRM"
    CALL = "CALL"
    SELLER_REVIEW = "SELLER_REVIEW"


class ConfirmationMethod(StrEnum):
    """How a confirmation was resolved."""

    IVR = "IVR"
    SMS_LINK = "SMS_LINK"
    MANUAL_CALL = "MANUAL_CALL"
    SELLER_CHAT = "SELLER_CHAT"
    AUTO_TRUSTED = "AUTO_TRUSTED"
    SELLER = "SELLER"


class AttemptChannel(StrEnum):
    IVR = "IVR"
    SMS_LINK = "SMS_LINK"
    MANUAL_CALL = "MANUAL_CALL"


class CostQuality(StrEnum):
    ACTUAL = "ACTUAL"
    ESTIMATED = "ESTIMATED"


class OrderOutcome(StrEnum):
    """What a customer's answer did to the order (recorded on terminal rows)."""

    #: The order moved (``DRAFT → CONFIRMED``, or ``→ CANCELLED`` on decline).
    APPLIED = "APPLIED"
    #: The order was edited after the confirmation started; the seller decides.
    ORDER_CHANGED = "ORDER_CHANGED"
    #: The order had already left ``DRAFT``; the seller decides.
    ORDER_NOT_DRAFT = "ORDER_NOT_DRAFT"


#: One open confirmation per order. A partial unique index; both PostgreSQL and
#: SQLite support the ``WHERE`` clause, so the rule holds on either database.
_OPEN_PREDICATE = "state NOT IN ('CONFIRMED', 'CANCELLED', 'SUPERSEDED')"


class OrderConfirmation(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "order_confirmations"
    __table_args__ = (
        sa.Index(
            "uq_order_confirmations_open_order",
            "order_id",
            unique=True,
            postgresql_where=sa.text(_OPEN_PREDICATE),
            sqlite_where=sa.text(_OPEN_PREDICATE),
        ),
        sa.Index("ix_order_confirmations_order", "order_id", "created_at"),
        sa.Index("ix_order_confirmations_due", "tenant_id", "state", "next_action_at"),
        sa.CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        sa.CheckConstraint("cod_paisa_snapshot >= 0", name="cod_snapshot_non_negative"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default=ConfirmationState.PENDING
    )
    #: Set by the policy (WP2.2); empty until it has run.
    decision: Mapped[str | None] = mapped_column(sa.String(16), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    #: The facts the policy saw, for the "why" display (WP2.2).
    facts: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    #: How it was resolved; empty while open and when the order itself ended it.
    method: Mapped[str | None] = mapped_column(sa.String(16), nullable=True)
    attempt_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    next_action_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    #: The seller who resolved it; empty when the customer or the system did.
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    #: The confirmation that replaced this one after a material order edit.
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    #: ``Order.version`` and ``Order.cod_amount_paisa`` when this confirmation
    #: started: what the customer is asked to agree to.
    order_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    cod_paisa_snapshot: Mapped[int] = mapped_column(Paisa, nullable=False)
    order_outcome: Mapped[str | None] = mapped_column(sa.String(16), nullable=True)

    @property
    def confirmation_state(self) -> ConfirmationState:
        return ConfirmationState(self.state)


class ConfirmationAttempt(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "confirmation_attempts"
    __table_args__ = (
        sa.UniqueConstraint(
            "confirmation_id", "attempt_no", name="uq_confirmation_attempts_confirmation_no"
        ),
        sa.CheckConstraint("attempt_no >= 1", name="attempt_no_positive"),
        sa.CheckConstraint("cost_paisa IS NULL OR cost_paisa >= 0", name="cost_non_negative"),
    )

    confirmation_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("order_confirmations.id", ondelete="CASCADE"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    channel: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    provider: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    provider_ref: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    status: Mapped[str] = mapped_column(sa.String(24), nullable=False, default="STARTED")
    dtmf: Mapped[str | None] = mapped_column(sa.String(4), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    #: Only what the provider reported; never computed here.
    cost_paisa: Mapped[int | None] = mapped_column(Paisa, nullable=True)
    cost_quality: Mapped[str | None] = mapped_column(sa.String(12), nullable=True)
    #: SMS link attempts (WP2.3): segments the provider charged, else the
    #: GSM 03.38 count of the text sent.
    segments: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class ConfirmationLink(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """An SMS confirm/cancel link (WP2.3). Only the token's SHA-256 is kept."""

    __tablename__ = "confirmation_links"
    __table_args__ = (
        sa.UniqueConstraint("token_hash", name="uq_confirmation_links_token_hash"),
        sa.Index("ix_confirmation_links_confirmation", "confirmation_id"),
    )

    confirmation_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("order_confirmations.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    #: ``CONFIRM`` or ``CANCEL`` once used.
    result: Mapped[str | None] = mapped_column(sa.String(12), nullable=True)
