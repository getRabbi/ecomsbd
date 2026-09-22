from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, TZDateTime


class Channel(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messaging_channels"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "kind"),)
    kind: Mapped[str] = mapped_column(sa.String(24))
    enabled: Mapped[bool] = mapped_column(default=False)


class Conversation(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messaging_conversations"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "customer_id", "channel"),)
    customer_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("customers.id"))
    channel: Mapped[str] = mapped_column(sa.String(24))
    recipient_enc: Mapped[str] = mapped_column(sa.Text)
    recipient_masked: Mapped[str] = mapped_column(sa.String(200))
    consent: Mapped[bool] = mapped_column(default=False)
    consent_version: Mapped[int] = mapped_column(default=1)


class ConsentEvent(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messaging_consents"
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("messaging_conversations.id")
    )
    consent: Mapped[bool] = mapped_column()
    evidence: Mapped[str] = mapped_column(sa.String(500))
    actor_id: Mapped[uuid.UUID] = mapped_column(GUID)


class MessageTemplate(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messaging_templates"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "key"),)
    key: Mapped[str] = mapped_column(sa.String(80))
    subject_en: Mapped[str] = mapped_column(sa.String(160))
    subject_bn: Mapped[str] = mapped_column(sa.String(160))
    body_en: Mapped[str] = mapped_column(sa.String(2000))
    body_bn: Mapped[str] = mapped_column(sa.String(2000))


class Message(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messaging_messages"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.Index("ix_message_due", "status", "next_attempt_at"),
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("messaging_conversations.id")
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(GUID, sa.ForeignKey("orders.id"))
    template_key: Mapped[str] = mapped_column(sa.String(80))
    locale: Mapped[str] = mapped_column(sa.String(2))
    subject: Mapped[str] = mapped_column(sa.String(200))
    body: Mapped[str] = mapped_column(sa.Text)
    consent_version: Mapped[int] = mapped_column()
    idempotency_key: Mapped[str] = mapped_column(sa.String(200))
    request_hash: Mapped[str] = mapped_column(sa.String(64))
    status: Mapped[str] = mapped_column(sa.String(24), default="QUEUED")
    attempts: Mapped[int] = mapped_column(default=0)
    first_attempt_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    next_attempt_at: Mapped[datetime] = mapped_column(TZDateTime)
    provider_reference: Mapped[str | None] = mapped_column(sa.String(200))
    last_error: Mapped[str | None] = mapped_column(sa.String(80))


class MessageAttempt(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messaging_attempts"
    message_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("messaging_messages.id"))
    outcome: Mapped[str] = mapped_column(sa.String(32))
