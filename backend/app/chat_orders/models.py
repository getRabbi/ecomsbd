"""Inbound customer messages, who sent them, and the draft orders they become.

Nothing here is an order. A draft is the parser's reading of a short burst of
customer messages; it turns into an order only when a seller confirms it, and
then only through the canonical order service (``OrderService.create``).

Privacy shape:

*   the sender's PSID or WhatsApp id is stored encrypted (``external_id_enc``)
    and looked up by a keyed hash (``external_key``), the same way customer
    phone numbers are;
*   message text is kept only for the retention window
    (:data:`app.chat_orders.service.MESSAGE_RETENTION`), then the row is gone;
*   attachments are never downloaded: a row records only that one was sent;
*   no access token, signature or provider secret is ever written here.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, Paisa, TZDateTime

#: Draft states. The first three are open; the rest are final.
COLLECTING = "COLLECTING"
NEEDS_INFO = "NEEDS_INFO"
READY_FOR_REVIEW = "READY_FOR_REVIEW"
CONFIRMED = "CONFIRMED"
IGNORED = "IGNORED"
EXPIRED = "EXPIRED"
OPEN_STATES = (COLLECTING, NEEDS_INFO, READY_FOR_REVIEW)
FINAL_STATES = (CONFIRMED, IGNORED, EXPIRED)


class ChatIdentity(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One customer as one connected Page or number sees them.

    A Messenger PSID is scoped to a Page and a WhatsApp id to a business
    number, so the identity is always (connection, sender): the same PSID on
    another Page is a different row, never merged.
    """

    __tablename__ = "chat_identities"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "connection_id", "external_key"),)

    connection_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("integration_connections.id"), nullable=False
    )
    #: MESSENGER or WHATSAPP.
    provider: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    #: Keyed hash of provider, account and sender id: the lookup key.
    external_key: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: Vault envelope of the PSID or WhatsApp id.
    external_id_enc: Mapped[str] = mapped_column(sa.Text, nullable=False)
    #: The Page id or WhatsApp phone_number_id the customer wrote to.
    account_ref: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: WhatsApp's profile name from the webhook itself; nothing is fetched.
    display_name: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    #: WhatsApp sender as ``01712****78``; Messenger gives no phone.
    phone_masked: Mapped[str | None] = mapped_column(sa.String(24), nullable=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("customers.id"), nullable=True
    )
    #: PHONE_MATCH (WhatsApp number equals a customer's) or CONFIRMED_ORDER.
    customer_link_source: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    customer_linked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class ChatMessage(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One inbound customer message, as the provider's verified webhook gave it."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        # A provider retry of the same message is a no-op.
        sa.UniqueConstraint("connection_id", "provider", "provider_message_id"),
        sa.Index("ix_chat_messages_due", "processing_status", "received_at"),
        sa.Index("ix_chat_messages_identity", "identity_id", "provider_timestamp"),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("integration_connections.id"), nullable=False
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("chat_identities.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    provider_message_id: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    #: The Page id or phone_number_id it arrived on (the thread context).
    account_ref: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: TEXT, IMAGE, AUDIO, VIDEO, DOCUMENT, STICKER or OTHER.
    message_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    #: TEXT only, capped. Never logged; deleted with the row at retention.
    text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    attachment_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    provider_timestamp: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    received_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    #: RECEIVED, PROCESSED, IGNORED or FAILED.
    processing_status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    #: Why it was ignored or failed; a code, never message content.
    processing_code: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    linked_customer_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    linked_draft_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    linked_attention_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)


class ChatOrderDraft(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """A candidate order read from one customer's burst of messages."""

    __tablename__ = "chat_order_drafts"
    __table_args__ = (
        sa.Index("ix_chat_order_drafts_identity_status", "identity_id", "status"),
        sa.Index("ix_chat_order_drafts_status_updated", "tenant_id", "status", "updated_at"),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("integration_connections.id"), nullable=False
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("chat_identities.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False)

    #: The shop's customer this draft is for, when one is known or suggested.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    #: NONE, MATCHED (identity already linked, or the WhatsApp number itself),
    #: SUGGESTED (a phone in the chat belongs to a customer) or AMBIGUOUS.
    customer_match: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="NONE")
    customer_candidates: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)

    customer_name: Mapped[str | None] = mapped_column(sa.String(160), nullable=True)
    phones: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)
    selected_phone: Mapped[str | None] = mapped_column(sa.String(24), nullable=True)
    address: Mapped[str | None] = mapped_column(sa.String(1000), nullable=True)
    #: [{name, quantity, size, color, match: {status, product_id, ...}}]
    items: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)
    cod_amount_paisa: Mapped[int | None] = mapped_column(Paisa, nullable=True)
    #: PARSED (the customer wrote it) or CATALOG (price x quantity).
    cod_source: Mapped[str | None] = mapped_column(sa.String(12), nullable=True)
    notes: Mapped[str | None] = mapped_column(sa.String(1000), nullable=True)
    warnings: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)
    uncertain_fields: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)
    missing_fields: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)

    source_message_ids: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)
    message_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    attachment_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    first_message_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    last_message_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    ready_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    #: Set once, when the "new order from chat" push was raised.
    notified_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    closed_by: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    confirmed_order_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("orders.id"), nullable=True
    )


class ChatAttention(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """A later message that may be about an existing order.

    Only ever a prompt for the seller: nothing is cancelled or changed from a
    customer's text.
    """

    __tablename__ = "chat_attention_items"
    __table_args__ = (sa.Index("ix_chat_attention_status", "tenant_id", "status", "created_at"),)

    connection_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("integration_connections.id"), nullable=False
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("chat_identities.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    #: CANCEL_REQUEST, ADDRESS_CHANGE or STATUS_QUESTION.
    intent: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    #: OPEN or DONE.
    status: Mapped[str] = mapped_column(sa.String(12), nullable=False, default="OPEN")
    customer_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("orders.id"), nullable=True
    )
    #: The message, capped; removed with the message at retention.
    excerpt: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    message_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
