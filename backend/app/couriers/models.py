"""Courier account, credential and provider-evidence tables.

Master spec sections 5, 31, 47, 62.13, 78, 83 and 108; brief sections 3, 7, 8,
9, 10, 20, 22, 23, 37, 51.

Seven tables, and each exists to make one specific failure impossible:

``courier_accounts``
    One merchant's credentials for one provider, encrypted at rest. The
    plaintext exists only inside the vault call that decrypts it for one
    request; nothing in this module can return it to a client.

``courier_raw_payloads``
    What the provider actually sent, kept as evidence. Section 83's rule is
    that support must be able to explain a result months later, and that is
    impossible from a normalized row alone.

``courier_events``
    Immutable normalized observations. A parcel's history is the sequence of
    what the provider said, separate from the parcel's current state — so a
    mapping mistake stays diagnosable (section 62.13).

``courier_booking_attempts``
    One row per attempt to create a parcel, written **before** the call goes
    out. This is the table that makes ``BOOKING_UNKNOWN`` recoverable: it holds
    the stable invoice we sent, so a lost answer can be resolved by asking the
    provider about that invoice rather than by guessing.

``courier_return_requests``
    Return requests, with ecomsbd's own duplicate protection. Steadfast
    documents no idempotency, so a unique constraint and a row lock provide it.

``courier_provider_payments``
    Each provider payment seen exactly once. The dedupe key that stops a
    repeated sync from doubling a seller's settled total.

``courier_sync_cursors``
    Watermarks and next-run times for the polling and payment jobs, so a
    restart resumes rather than re-reads, and two workers do not duplicate work.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, Paisa, TZDateTime

__all__ = [
    "BookingAttemptState",
    "CourierAccount",
    "CourierAccountStatus",
    "CourierBookingAttempt",
    "CourierEvent",
    "CourierEventKind",
    "CourierEventSource",
    "CourierRawPayload",
    "CourierReturnRequest",
    "CourierSyncCursor",
    "CourierSyncKind",
    "CourierWebhookDelivery",
    "PaymentSyncState",
    "ProviderPayment",
    "RawPayloadKind",
    "ReturnRequestState",
    "WebhookDeliveryState",
    "payload_hash",
]


def payload_hash(payload: Any) -> str:
    """Stable SHA-256 of a provider payload.

    Sorted keys and a compact separator, so the same logical body hashes the
    same however the provider ordered it. This is the key that recognises a
    payment we have already imported and a webhook we have already processed,
    so it has to be insensitive to formatting and sensitive to content.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


# --------------------------------------------------------------- accounts --


class CourierAccountStatus(StrEnum):
    """Where a courier account stands (brief section 4).

    ``NEEDS_RECONNECT`` is separate from ``DISCONNECTED`` on purpose: one is a
    problem the seller has to fix, the other is a choice they made. Collapsing
    them would either nag a seller who deliberately disconnected, or silently
    stop booking for one whose key was revoked.
    """

    CONNECTED = "CONNECTED"
    #: Credentials were rejected repeatedly. A person must re-enter them.
    NEEDS_RECONNECT = "NEEDS_RECONNECT"
    #: The seller removed it. Credentials are erased, the row is kept for audit.
    DISCONNECTED = "DISCONNECTED"

    @property
    def is_usable(self) -> bool:
        return self is CourierAccountStatus.CONNECTED


class CredentialValidation(StrEnum):
    """The four outcomes of checking credentials (brief section 5).

    ``PROVIDER_UNAVAILABLE`` and ``UNKNOWN`` both mean "we did not find out",
    and neither may ever mark an account bad. A seller re-typing a working API
    key because the provider had a bad minute is a failure of this enum.
    """

    VALID = "VALID"
    INVALID = "INVALID"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"

    @property
    def is_conclusive(self) -> bool:
        """Whether this result may change the account's status."""
        return self in (CredentialValidation.VALID, CredentialValidation.INVALID)


#: Consecutive deterministic auth rejections before an account is moved to
#: ``NEEDS_RECONNECT``. More than one, because a single 401 during a provider
#: deployment should not log a shop out of its courier.
AUTH_FAILURES_BEFORE_RECONNECT = 3


#: Config keys a client may see. An allow-list rather than a deny-list: a key
#: added to an account's metadata for some future purpose is invisible until
#: someone deliberately puts it here, which is the right default for a field
#: that sits next to credentials.
_PUBLIC_CONFIG_KEYS: frozenset[str] = frozenset({"store_id", "store_name", "sandbox"})


class CourierAccount(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One shop's credentials for one courier provider.

    The credential columns hold AES-GCM envelopes from
    :class:`~app.core.security.CredentialVault`, bound to
    ``courier_account:<id>`` as additional authenticated data — so an envelope
    copied into another row fails to decrypt rather than silently working.
    """

    __tablename__ = "courier_accounts"
    __table_args__ = (
        # One account per provider per shop. A second Steadfast account would
        # make "which credentials booked this parcel?" unanswerable, and every
        # recovery path depends on that being answerable.
        sa.UniqueConstraint("tenant_id", "provider", name="uq_courier_accounts_tenant_id_provider"),
        sa.Index("ix_courier_accounts_tenant_status", "tenant_id", "status"),
        # The access path the unauthenticated callback route resolves by, and
        # the guarantee that two accounts can never share one token.
        sa.Index("uq_courier_accounts_webhook_token", "webhook_token", unique=True),
        sa.CheckConstraint("consecutive_auth_failures >= 0", name="failures_non_negative"),
    )

    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)

    #: Seller-chosen name. Optional; the provider's display name is the default.
    label: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)

    status: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, default=CourierAccountStatus.CONNECTED
    )

    # --- credentials -------------------------------------------------------
    #
    # Two separate envelopes rather than one JSON blob: rotating a secret
    # without re-encrypting the key is then a single-column write, and a
    # partial write cannot produce a half-decryptable pair.

    api_key_encrypted: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    secret_key_encrypted: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    #: The provider's webhook signing secret, for providers that verify
    #: callbacks with one. Encrypted with the same vault and context as the API
    #: credentials, and never returned to a client: a seller who loses it
    #: generates a new one rather than reading this back.
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    #: An opaque, unguessable token that identifies this account inside its
    #: webhook callback URL.
    #:
    #: A callback arrives unauthenticated and with no session, so *something*
    #: has to say which shop it belongs to before its body can be trusted. The
    #: alternative — reading the shop out of the body first — would mean parsing
    #: an unverified payload to decide how to verify it. This token is that
    #: something: it selects the account, and the account's secret then verifies
    #: the delivery. It is not itself a credential and proves nothing on its own.
    webhook_token: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)

    #: Which vault key version wrote these, so rotation can decrypt-old /
    #: encrypt-new without a flag day (master spec section 132).
    key_version: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)

    #: ``****abcd``. The only credential-derived value that ever reaches a
    #: client, and it is not reversible.
    masked_identifier: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)

    # --- validation --------------------------------------------------------

    last_verified_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_validation_result: Mapped[str | None] = mapped_column(sa.String(24), nullable=True)
    #: Seller-facing, already redacted. Never the provider's raw body.
    last_validation_message: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    consecutive_auth_failures: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    #: Capabilities as of the last successful validation. Cached so the UI can
    #: render without a provider round trip; the manifest remains the source of
    #: truth for what is *documented*.
    capabilities_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    connected_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    disconnected_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    connected_by: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    #: The last balance the provider reported, and when. Kept for the money
    #: screen, which shows it as *the provider's* figure — never merged with
    #: the ecomsbd COD outstanding total (brief section 19).
    reported_balance_paisa: Mapped[int | None] = mapped_column(Paisa, nullable=True)
    reported_balance_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    metadata_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    @property
    def account_status(self) -> CourierAccountStatus:
        return CourierAccountStatus(self.status)

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key_encrypted and self.secret_key_encrypted)

    @property
    def is_usable(self) -> bool:
        """Whether a provider call may be attempted with this account."""
        return self.account_status.is_usable and self.has_credentials

    @property
    def vault_context(self) -> str:
        """AAD binding the ciphertext to this row."""
        return f"courier_account:{self.id}"

    def public_view(self) -> dict[str, Any]:
        """Everything the client may see. No ciphertext, no plaintext, ever.

        Section 47 and brief section 3: after the initial submission, Flutter
        receives connection state and a masked hint and nothing else. This
        method is the only shape the API layer serialises, so there is one
        place to audit rather than one per endpoint.
        """
        return {
            "id": str(self.id),
            "provider": self.provider,
            "label": self.label,
            "status": self.status,
            "connected": self.account_status is CourierAccountStatus.CONNECTED,
            "needs_reconnect": self.account_status is CourierAccountStatus.NEEDS_RECONNECT,
            "masked_identifier": self.masked_identifier,
            "last_verified_at": (
                self.last_verified_at.isoformat() if self.last_verified_at else None
            ),
            "last_validation_result": self.last_validation_result,
            "last_validation_message": self.last_validation_message,
            "capabilities": dict(self.capabilities_json or {}),
            "reported_balance_paisa": self.reported_balance_paisa,
            "reported_balance_at": (
                self.reported_balance_at.isoformat() if self.reported_balance_at else None
            ),
            # Non-secret provider settings only — the chosen pickup store, the
            # sandbox flag. `metadata_json` is the one field on this model a
            # secret must never be written to, which is why the connect path
            # keeps secrets in their own encrypted columns.
            "config": {
                key: value
                for key, value in (self.metadata_json or {}).items()
                if key in _PUBLIC_CONFIG_KEYS
            },
            #: Whether a webhook secret is stored. Never the secret itself.
            "webhook_configured": bool(self.webhook_secret_encrypted),
        }


# ----------------------------------------------------------- raw payloads --


class RawPayloadKind(StrEnum):
    """What produced a stored payload."""

    BOOKING_RESPONSE = "BOOKING_RESPONSE"
    BULK_BOOKING_RESPONSE = "BULK_BOOKING_RESPONSE"
    STATUS_RESPONSE = "STATUS_RESPONSE"
    BALANCE_RESPONSE = "BALANCE_RESPONSE"
    RETURN_RESPONSE = "RETURN_RESPONSE"
    PAYMENT_LIST_RESPONSE = "PAYMENT_LIST_RESPONSE"
    PAYMENT_DETAIL_RESPONSE = "PAYMENT_DETAIL_RESPONSE"
    LOCATION_RESPONSE = "LOCATION_RESPONSE"
    WEBHOOK_DELIVERY = "WEBHOOK_DELIVERY"


class CourierRawPayload(Base, TenantOwned, PrimaryKeyMixin):
    """What the provider actually said, kept for support and for evidence.

    Two things are deliberately true of every row:

    *   **No credential is in here.** The request payload stored alongside a
        response is the *redacted* one — phone masked, email removed — and
        request headers are never stored at all, because that is where the API
        key lives.
    *   **It is never the source of a money decision.** Money moves from
        normalized domain state; this table explains *why* it moved.
    """

    __tablename__ = "courier_raw_payloads"
    __table_args__ = (
        sa.Index("ix_courier_raw_payloads_tenant_kind", "tenant_id", "kind", "received_at"),
        sa.Index("ix_courier_raw_payloads_correlation", "correlation_id"),
        sa.Index("ix_courier_raw_payloads_hash", "tenant_id", "payload_sha256"),
    )

    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    courier_account_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("courier_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    http_status: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    #: Ties this payload to the log lines and the event it produced.
    correlation_id: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)

    #: The redacted request body, where there was one.
    request_payload: Mapped[dict | None] = mapped_column(JSONColumn, nullable=True)
    #: The response body. A list response is wrapped so the column is always an
    #: object, which keeps JSONB containment queries usable.
    response_payload: Mapped[dict | None] = mapped_column(JSONColumn, nullable=True)

    payload_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: Whether the endpoint's response schema is undocumented, so a reader
    #: knows the typed fields beside it were inferred rather than specified.
    schema_undocumented: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    received_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utc_now, index=True
    )


# ---------------------------------------------------------------- events --


class CourierEventKind(StrEnum):
    BOOKING = "BOOKING"
    STATUS = "STATUS"
    RETURN = "RETURN"
    PAYMENT = "PAYMENT"
    BALANCE = "BALANCE"


class CourierEventSource(StrEnum):
    """How we came to know. Polling is the V1 path; webhook is built, not on."""

    POLL = "POLL"
    BOOKING_CALL = "BOOKING_CALL"
    RECOVERY = "RECOVERY"
    WEBHOOK = "WEBHOOK"
    MANUAL = "MANUAL"


class CourierEvent(Base, TenantOwned, PrimaryKeyMixin):
    """One immutable observation of what a provider said about a parcel.

    **Immutable in content, deduplicated by content.** Polling a parcel every
    hour for a week would otherwise write 168 identical rows; instead the first
    observation is kept and ``last_seen_at``/``observation_count`` record that
    it was seen again. So the row's *meaning* never changes — which is what
    "immutable event" has to guarantee — while the table stays a size a single
    VPS can hold.

    ``normalized_status`` is derived at write time and stored, rather than
    computed on read, so a later change to the mapping table does not silently
    rewrite history. ``raw_status`` is what actually arrived, always.
    """

    __tablename__ = "courier_events"
    __table_args__ = (
        # The dedupe key. Includes the raw status, so a genuine status *change*
        # is a new row while a repeat observation is not.
        sa.UniqueConstraint(
            "tenant_id", "provider", "dedupe_key", name="uq_courier_events_tenant_dedupe"
        ),
        sa.Index("ix_courier_events_consignment", "consignment_id", "observed_at"),
        sa.Index("ix_courier_events_tenant_kind", "tenant_id", "kind", "observed_at"),
        sa.Index("ix_courier_events_provider_ref", "tenant_id", "provider_consignment_id"),
        sa.CheckConstraint("observation_count > 0", name="observation_count_positive"),
    )

    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    courier_account_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("courier_accounts.id", ondelete="SET NULL"), nullable=True
    )

    kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    source: Mapped[str] = mapped_column(sa.String(16), nullable=False)

    #: Null when an observation cannot be attached to a parcel yet — a payment
    #: line naming an unknown consignment, say. Dropping it instead would hide
    #: money that genuinely arrived.
    consignment_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("consignments.id", ondelete="SET NULL"), nullable=True
    )

    provider_consignment_id: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    tracking_code: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    merchant_reference: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)

    #: Verbatim. Section 62.13: a mapping mistake must stay diagnosable.
    raw_status: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    #: What the mapping table said at the time this was written, or null for a
    #: status we had never seen documented.
    normalized_status: Mapped[str | None] = mapped_column(sa.String(24), nullable=True)
    #: True when ``raw_status`` was not one of the provider's documented values.
    #: Queried by the observability job that notices a provider adding a state.
    status_undocumented: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    raw_payload_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("courier_raw_payloads.id", ondelete="SET NULL"), nullable=True
    )

    dedupe_key: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)

    #: When the provider says it happened, when one is given. Steadfast's status
    #: response carries no timestamp, so this is usually null and
    #: ``first_seen_at`` is what the timeline uses — labelled as an observation
    #: time rather than presented as an event time.
    occurred_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    observation_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utc_now, index=True
    )

    @staticmethod
    def build_dedupe_key(
        *,
        kind: CourierEventKind,
        reference: str,
        raw_status: str | None,
        extra: str = "",
    ) -> str:
        """Content key for one observation.

        Hashed rather than concatenated so an unusually long provider reference
        cannot overflow the column and silently collide with a truncated one.
        """
        material = f"{kind}|{reference}|{raw_status or ''}|{extra}"
        return hashlib.sha256(material.encode()).hexdigest()[:64]


# ----------------------------------------------------- booking attempts --


class BookingAttemptState(StrEnum):
    """The lifecycle of one attempt to create a parcel.

    Written ``PENDING`` **before** the call goes out, which is what makes the
    invoice we are about to send durable. An attempt that ends ``UNKNOWN`` and
    is later proved to exist becomes ``RECOVERED``, not ``SUCCEEDED``, so the
    history shows a parcel that needed reconciliation.
    """

    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    #: The provider answered and refused. Nothing exists; a new attempt is safe.
    FAILED = "FAILED"
    #: The answer was lost. A parcel may exist. Never retried automatically.
    UNKNOWN = "UNKNOWN"
    #: Reconciliation proved the parcel exists and attached it.
    RECOVERED = "RECOVERED"
    #: Recovery could not decide, and a person took it off the automatic path.
    MANUAL_REVIEW = "MANUAL_REVIEW"
    #: A person decided this attempt produced nothing. Only a person may.
    ABANDONED = "ABANDONED"

    @property
    def is_open(self) -> bool:
        return self in (BookingAttemptState.PENDING, BookingAttemptState.UNKNOWN)

    @property
    def produced_a_parcel(self) -> bool:
        return self in (BookingAttemptState.SUCCEEDED, BookingAttemptState.RECOVERED)


class CourierBookingAttempt(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One attempt to create one parcel at a provider.

    The row exists before the HTTP call and survives it whatever happens. That
    ordering is the entire safety property: a create whose answer is lost still
    has a persisted record of the exact invoice that was sent, so recovery can
    ask the provider about *that* invoice instead of creating a second parcel.
    """

    __tablename__ = "courier_booking_attempts"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "consignment_id",
            "attempt_number",
            name="uq_courier_booking_attempts_consignment_attempt",
        ),
        # The recovery lookup: "what did we send for this invoice?"
        sa.Index(
            "ix_courier_booking_attempts_reference",
            "tenant_id",
            "provider",
            "merchant_reference",
        ),
        sa.Index("ix_courier_booking_attempts_state", "tenant_id", "state", "next_recovery_at"),
        sa.CheckConstraint("attempt_number > 0", name="attempt_number_positive"),
        sa.CheckConstraint("recovery_attempts >= 0", name="recovery_attempts_non_negative"),
    )

    consignment_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("consignments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    courier_account_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("courier_accounts.id", ondelete="SET NULL"), nullable=True
    )

    #: The invoice that was sent, verbatim. **Never regenerated for a retry of
    #: the same logical booking** — that is what makes recovery possible at all
    #: (brief section 7).
    merchant_reference: Mapped[str] = mapped_column(sa.String(120), nullable=False)

    attempt_number: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    state: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default=BookingAttemptState.PENDING
    )

    #: True when this attempt went out inside a bulk request. A bulk timeout is
    #: more dangerous than a single one, and recovery treats these per item.
    is_bulk: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    #: Groups the attempts that shared one HTTP request.
    batch_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True, index=True)

    provider_consignment_id: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    tracking_code: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)

    #: What was actually asked of the courier, in whole taka, and the paisa that
    #: could not be. Stored because a receivable computed against the order's
    #: COD would otherwise be a few paisa away from the collectable cash with
    #: nothing recording why.
    requested_cod_taka: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    cod_residual_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    #: The masked provider-format phone, plus the transform applied. Enough to
    #: debug a rejected booking — did the leading zero survive, is it 11 digits
    #: — without a second plaintext copy of a customer's number.
    provider_phone_masked: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)

    error_code: Mapped[str | None] = mapped_column(sa.String(60), nullable=True)
    #: Redacted. Support detail, not seller-facing copy.
    error_message: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    correlation_id: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    raw_payload_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("courier_raw_payloads.id", ondelete="SET NULL"), nullable=True
    )

    started_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    # --- recovery ----------------------------------------------------------

    recovery_attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    last_recovery_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    next_recovery_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    #: What the last recovery attempt concluded, in words, for the support
    #: screen and the audit trail.
    recovery_note: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    @property
    def attempt_state(self) -> BookingAttemptState:
        return BookingAttemptState(self.state)


# ---------------------------------------------------------------- returns --


class ReturnRequestState(StrEnum):
    """ecomsbd's view of a provider return request.

    Distinct from the provider's own status string, which is stored beside it.
    ``REQUESTED`` means we sent it and it was accepted; ``UNKNOWN`` means the
    answer was lost, and — exactly as with a booking — that must never become a
    second request.
    """

    REQUESTED = "REQUESTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"

    @property
    def is_terminal(self) -> bool:
        return self in (
            ReturnRequestState.COMPLETED,
            ReturnRequestState.CANCELLED,
            ReturnRequestState.FAILED,
        )

    @property
    def blocks_a_new_request(self) -> bool:
        """Whether an existing request in this state forbids sending another.

        ``UNKNOWN`` blocks too. A lost answer may well have created a return
        request at the provider, and asking twice is how a parcel gets collected
        twice.
        """
        return not self.is_terminal


class CourierReturnRequest(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """A return asked of the provider, and ecomsbd's duplicate protection.

    Steadfast documents no idempotency key for
    ``POST /create_return_request``, so the guarantee is ours: a unique
    constraint on the operator's idempotency key, plus a row lock and an
    active-request check in the service.
    """

    __tablename__ = "courier_return_requests"
    __table_args__ = (
        # Blocks a double tap outright, before any provider call.
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_courier_return_requests_idempotency"
        ),
        # Recognises a provider record we already hold.
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "provider_return_id",
            name="uq_courier_return_requests_provider_id",
        ),
        sa.Index("ix_courier_return_requests_consignment", "consignment_id", "state"),
        sa.Index("ix_courier_return_requests_tenant_state", "tenant_id", "state"),
    )

    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    courier_account_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("courier_accounts.id", ondelete="SET NULL"), nullable=True
    )
    consignment_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("consignments.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: Which of the three documented references was sent, and its value.
    reference_kind: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    reference_value: Mapped[str] = mapped_column(sa.String(120), nullable=False)

    #: Null until the provider answers. A null with state ``UNKNOWN`` is the
    #: ambiguous case and is never retried automatically.
    provider_return_id: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)

    state: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    #: The provider's own status word, verbatim.
    provider_status: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)

    reason: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    #: Derived from the consignment and the operator's request, so two taps of
    #: the same button collide here rather than at the provider.
    idempotency_key: Mapped[str] = mapped_column(sa.String(120), nullable=False)

    requested_by: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    last_synced_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    correlation_id: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    raw_payload_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("courier_raw_payloads.id", ondelete="SET NULL"), nullable=True
    )

    @property
    def request_state(self) -> ReturnRequestState:
        return ReturnRequestState(self.state)


# --------------------------------------------------------------- payments --


class PaymentSyncState(StrEnum):
    """How far payment ingestion has got with one provider payment."""

    #: Seen in the list. Detail not fetched yet.
    SEEN = "SEEN"
    #: Detail fetched and stored.
    DETAILED = "DETAILED"
    #: Turned into a payout and handed to reconciliation.
    IMPORTED = "IMPORTED"
    #: The provider's copy changed after we imported it. Needs a controlled
    #: re-run, never a silent rewrite of settled ledger entries.
    CHANGED = "CHANGED"
    #: Something went wrong. The row keeps the reason and is retried.
    FAILED = "FAILED"


class ProviderPayment(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One payment the provider says it made, seen exactly once.

    The unique constraint on ``(tenant, provider, provider_payment_id)`` is the
    whole idempotency story for payment sync (brief section 23): re-running the
    job finds the row and updates ``last_seen_at`` rather than creating a second
    payout and a second set of ledger entries.

    ``payload_sha256`` answers the other half — whether the provider's copy has
    *changed* since we imported it. A changed payment moves to ``CHANGED`` and
    is re-examined through the domain, because silently rewriting a settled
    ledger entry would make a seller's reconciled month stop reconciling.
    """

    __tablename__ = "courier_provider_payments"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "provider_payment_id",
            name="uq_courier_provider_payments_provider_id",
        ),
        sa.Index("ix_courier_provider_payments_tenant_state", "tenant_id", "sync_state"),
        sa.Index("ix_courier_provider_payments_seen", "tenant_id", "first_seen_at"),
    )

    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    courier_account_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("courier_accounts.id", ondelete="SET NULL"), nullable=True
    )

    provider_payment_id: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    #: The provider's own human reference, when it gives one distinct from the id.
    provider_reference: Mapped[str | None] = mapped_column(sa.String(160), nullable=True)

    sync_state: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default=PaymentSyncState.SEEN
    )

    #: Null when the list response did not carry an amount we could read.
    #: Explicitly not zero — zero is a real amount.
    total_paisa: Mapped[int | None] = mapped_column(Paisa, nullable=True)
    provider_status: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    consignment_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    payload_sha256: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    detail_fetched_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    imported_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: The payout this became. One payment, one payout, forever.
    payout_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("payouts.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    raw_payload_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("courier_raw_payloads.id", ondelete="SET NULL"), nullable=True
    )

    error_message: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)
    #: Which typed fields the provider actually sent. Turns the first live
    #: response into documentation instead of leaving the schema UNVERIFIED.
    observed_fields: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)

    @property
    def state(self) -> PaymentSyncState:
        return PaymentSyncState(self.sync_state)


# ---------------------------------------------------------------- cursors --


class CourierSyncKind(StrEnum):
    """Which background job a cursor belongs to."""

    STATUS_POLL = "STATUS_POLL"
    BOOKING_RECOVERY = "BOOKING_RECOVERY"
    RETURN_SYNC = "RETURN_SYNC"
    PAYMENT_SYNC = "PAYMENT_SYNC"
    CREDENTIAL_HEALTH = "CREDENTIAL_HEALTH"


class CourierSyncCursor(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """Where a background job got to, per shop, per provider, per job.

    Exists so a restart resumes rather than re-reads a year of payments, and so
    two workers claiming the same shop's sync contend on a row lock rather than
    both importing the same payment.

    The watermark is deliberately applied with an **overlap**: a job resumes a
    little before where it finished. Re-seeing a payment is free — the unique
    constraint on ``provider_payment_id`` absorbs it — while missing one because
    the provider's clock and ours disagreed by a second is money that never
    arrives on the seller's screen.
    """

    __tablename__ = "courier_sync_cursors"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "courier_account_id",
            "kind",
            name="uq_courier_sync_cursors_scope",
        ),
        sa.Index("ix_courier_sync_cursors_due", "kind", "next_run_at"),
        sa.CheckConstraint("consecutive_failures >= 0", name="failures_non_negative"),
    )

    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    courier_account_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("courier_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(sa.String(24), nullable=False)

    #: Opaque provider cursor, when the provider gives one. Steadfast does not,
    #: so the time watermark below is what the payment job actually uses.
    cursor_value: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    watermark_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    last_run_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True, index=True)

    consecutive_failures: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    #: Counters for the run, for the ops screen. Never money.
    stats_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    @property
    def sync_kind(self) -> CourierSyncKind:
        return CourierSyncKind(self.kind)


# --------------------------------------------------------------- webhooks --


class WebhookDeliveryState(StrEnum):
    """What happened to one inbound webhook request.

    ``NOT_CONFIGURED`` is the state every Steadfast delivery lands in today, and
    it is deliberately distinct from ``REJECTED``. "We have no verified contract
    for this provider, so we stored your request and did nothing" and "your
    signature was wrong" are different facts, and an operator debugging a
    provider integration needs to be able to tell them apart.
    """

    RECEIVED = "RECEIVED"
    #: Signature verified and queued for processing.
    ACCEPTED = "ACCEPTED"
    PROCESSED = "PROCESSED"
    #: Verification failed. A real security signal.
    REJECTED = "REJECTED"
    #: Already seen. Healthy: it means deduplication is absorbing retries.
    DUPLICATE = "DUPLICATE"
    #: The provider has no verified webhook contract, so nothing can be
    #: verified and nothing is processed. The body is still kept.
    NOT_CONFIGURED = "NOT_CONFIGURED"
    FAILED = "FAILED"


class CourierWebhookDelivery(Base, PrimaryKeyMixin):
    """One inbound webhook request, stored before anything is decided about it.

    **Not** :class:`~app.db.base.TenantOwned`: a webhook arrives before we know
    whose it is, and refusing to store it until the tenant is resolved would
    discard exactly the requests that are hardest to debug. ``tenant_id`` is
    filled in once resolution succeeds.

    Every field here is provider-agnostic. There is no Steadfast signature
    header, no Steadfast payload shape and no Steadfast event id, because the
    supplied documentation has none — see
    :mod:`app.couriers.webhooks` for how that absence is handled rather than
    guessed around.
    """

    __tablename__ = "courier_webhook_deliveries"
    __table_args__ = (
        # Replay protection. A provider retrying a delivery hashes the same,
        # and the second one is recognised rather than re-processed.
        sa.UniqueConstraint("provider", "body_sha256", name="uq_courier_webhook_deliveries_body"),
        sa.Index("ix_courier_webhook_deliveries_state", "provider", "state", "received_at"),
        sa.Index("ix_courier_webhook_deliveries_tenant", "tenant_id", "received_at"),
        sa.CheckConstraint("delivery_count > 0", name="delivery_count_positive"),
    )

    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    #: Resolved after the fact, when it can be. Null is a normal state.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    courier_account_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    state: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default=WebhookDeliveryState.RECEIVED
    )

    #: The raw body, as text, exactly as received. Not parsed, not normalized.
    raw_body: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    body_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)

    #: Header *names* only, sorted. Never values: a signature header is a
    #: credential-adjacent secret and an Authorization header is a credential.
    header_names: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)

    #: The provider's own event id, once a verified contract names one. Null
    #: for every provider that has no such contract.
    provider_event_id: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)

    #: Why it was rejected or could not be processed. Redacted, support-facing.
    reason: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)

    delivery_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    received_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    processed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)

    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utc_now, index=True
    )

    @property
    def delivery_state(self) -> WebhookDeliveryState:
        return WebhookDeliveryState(self.state)
