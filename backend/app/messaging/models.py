from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, TZDateTime

#: Messages carry exactly one purpose. Marketing needs its own opt-in; a
#: transactional consent never implies it (V3.3).
TRANSACTIONAL = "TRANSACTIONAL"
MARKETING = "MARKETING"


class Channel(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messaging_channels"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "kind"),)
    kind: Mapped[str] = mapped_column(sa.String(24))
    enabled: Mapped[bool] = mapped_column(default=False)


class Conversation(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messaging_conversations"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "customer_id", "channel"),
        sa.UniqueConstraint("unsubscribe_hash", name="uq_messaging_conversation_unsubscribe"),
        sa.Index("ix_messaging_conversation_recipient", "tenant_id", "channel", "recipient_hash"),
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("customers.id"))
    channel: Mapped[str] = mapped_column(sa.String(24))
    recipient_enc: Mapped[str] = mapped_column(sa.Text)
    recipient_masked: Mapped[str] = mapped_column(sa.String(200))
    #: Keyed hash of the normalised address, so a provider callback that names
    #: only a phone number (a WhatsApp STOP) finds the conversation.
    recipient_hash: Mapped[str | None] = mapped_column(sa.String(64))
    #: Transactional consent (order updates).
    consent: Mapped[bool] = mapped_column(default=False)
    consent_version: Mapped[int] = mapped_column(default=1)
    #: Marketing consent is separate and explicit; an opt-out always wins.
    marketing_consent: Mapped[bool] = mapped_column(default=False)
    marketing_consent_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    marketing_opted_out_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_marketing_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    #: A provider said the address cannot receive (hard bounce, invalid number).
    undeliverable_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    undeliverable_reason: Mapped[str | None] = mapped_column(sa.String(40))
    #: One-click unsubscribe: SHA-256 for lookup, the token itself sealed.
    unsubscribe_hash: Mapped[str | None] = mapped_column(sa.String(64))
    unsubscribe_enc: Mapped[str | None] = mapped_column(sa.Text)


class ConsentEvent(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messaging_consents"
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("messaging_conversations.id")
    )
    consent: Mapped[bool] = mapped_column()
    evidence: Mapped[str] = mapped_column(sa.String(500))
    #: None when the customer acted (unsubscribe link, STOP, complaint).
    actor_id: Mapped[uuid.UUID | None] = mapped_column(GUID)
    #: TRANSACTIONAL or MARKETING.
    scope: Mapped[str] = mapped_column(sa.String(16), default=TRANSACTIONAL)
    #: SELLER, UNSUBSCRIBE_LINK, KEYWORD or PROVIDER_COMPLAINT.
    source: Mapped[str] = mapped_column(sa.String(24), default="SELLER")
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(GUID)


class MessageTemplate(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messaging_templates"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "key"),)
    key: Mapped[str] = mapped_column(sa.String(80))
    subject_en: Mapped[str] = mapped_column(sa.String(160))
    subject_bn: Mapped[str] = mapped_column(sa.String(160))
    body_en: Mapped[str] = mapped_column(sa.String(2000))
    body_bn: Mapped[str] = mapped_column(sa.String(2000))
    purpose: Mapped[str] = mapped_column(sa.String(16), default=TRANSACTIONAL)
    #: EMAIL renders subject/body here; WHATSAPP sends a Meta-approved template
    #: by name and the bodies are the seller's preview of it.
    channel: Mapped[str] = mapped_column(sa.String(16), default="EMAIL")
    provider_name: Mapped[str | None] = mapped_column(sa.String(512))
    provider_language_bn: Mapped[str | None] = mapped_column(sa.String(16))
    provider_language_en: Mapped[str | None] = mapped_column(sa.String(16))
    #: As Meta reports them; never inferred.
    provider_category: Mapped[str | None] = mapped_column(sa.String(24))
    provider_status: Mapped[str | None] = mapped_column(sa.String(24))
    provider_checked_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    #: Placeholder names in the order the provider template numbers them.
    variables: Mapped[list | None] = mapped_column(JSONColumn)
    archived: Mapped[bool] = mapped_column(default=False)


class Message(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messaging_messages"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.Index("ix_message_due", "status", "next_attempt_at"),
        sa.Index("ix_message_campaign", "tenant_id", "campaign_id", "status"),
        sa.Index("ix_message_provider_ref", "provider", "provider_reference"),
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
    purpose: Mapped[str] = mapped_column(sa.String(16), default=TRANSACTIONAL)
    channel: Mapped[str] = mapped_column(sa.String(16), default="EMAIL")
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("messaging_campaigns.id")
    )
    provider: Mapped[str | None] = mapped_column(sa.String(24))
    #: Provider template parameters, rendered at queue time.
    params: Mapped[list | None] = mapped_column(JSONColumn)
    #: Set only from what a provider reported: accepted, delivered, read, failed.
    sent_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    read_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    failed_at: Mapped[datetime | None] = mapped_column(TZDateTime)


class MessageAttempt(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messaging_attempts"
    message_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("messaging_messages.id"))
    outcome: Mapped[str] = mapped_column(sa.String(32))


class Campaign(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """A marketing send to an audience, once (ONE_OFF) or continuously (FLOW)."""

    __tablename__ = "messaging_campaigns"
    __table_args__ = (sa.Index("ix_campaign_due", "status", "scheduled_at"),)
    name: Mapped[str] = mapped_column(sa.String(120))
    kind: Mapped[str] = mapped_column(sa.String(16), default="ONE_OFF")
    #: FLOW only: WIN_BACK, REPEAT_NUDGE or INACTIVE.
    flow: Mapped[str | None] = mapped_column(sa.String(24))
    flow_days: Mapped[int | None] = mapped_column()
    channel: Mapped[str] = mapped_column(sa.String(16))
    template_key: Mapped[str] = mapped_column(sa.String(80))
    locale: Mapped[str] = mapped_column(sa.String(2))
    audience: Mapped[dict] = mapped_column(JSONColumn, default=dict)
    #: DRAFT, SCHEDULED, SENDING, PAUSED, COMPLETED, CANCELLED (ONE_OFF);
    #: DRAFT, ACTIVE, PAUSED, CANCELLED (FLOW).
    status: Mapped[str] = mapped_column(sa.String(16), default="DRAFT")
    scheduled_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_run_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    #: Why the engine paused it (a channel or template became unusable).
    last_error: Mapped[str | None] = mapped_column(sa.String(64))
    rate_per_minute: Mapped[int] = mapped_column(default=30)
    frequency_cap_hours: Mapped[int] = mapped_column(default=72)
    attribution_days: Mapped[int] = mapped_column(default=7)
    total_recipients: Mapped[int] = mapped_column(default=0)
    version: Mapped[int] = mapped_column(default=1)
    created_by: Mapped[uuid.UUID] = mapped_column(GUID)
    launched_by: Mapped[uuid.UUID | None] = mapped_column(GUID)


class CampaignRecipient(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """The audience snapshot. One row per customer per cycle, so a flow sends once per lapse."""

    __tablename__ = "messaging_campaign_recipients"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "campaign_id", "customer_id", "cycle_key"),
        sa.Index("ix_campaign_recipient_due", "campaign_id", "status", "due_at"),
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("messaging_campaigns.id"))
    customer_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("customers.id"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("messaging_conversations.id")
    )
    cycle_key: Mapped[str] = mapped_column(sa.String(64))
    #: PENDING, QUEUED or SKIPPED.
    status: Mapped[str] = mapped_column(sa.String(16), default="PENDING")
    skip_reason: Mapped[str | None] = mapped_column(sa.String(32))
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("messaging_messages.id")
    )
    due_at: Mapped[datetime] = mapped_column(TZDateTime)
