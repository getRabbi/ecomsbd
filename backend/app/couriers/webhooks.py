"""Courier webhook infrastructure.

Brief sections 18 and 41. ``STEADFAST_WEBHOOK_CONTRACT_REQUIRED``.

The supplied Steadfast V1 documentation has **no webhook section at all** — no
endpoint, no signature header, no HMAC algorithm, no payload shape, no event id,
no retry contract. So this module contains every part of a webhook pipeline
*except* the four things that would have to be invented:

    built and tested                        absent, and deliberately so
    ------------------------------------    -------------------------------
    the receiving route                     the signature header name
    raw-body persistence before parsing     the HMAC algorithm and secret
    replay protection by body hash          the payload field names
    delivery state machine                  the provider's event id
    queue hand-off to status sync           the retry/ack contract

A verifier that returns ``True`` because there is nothing to check is not a
disabled webhook — it is an open endpoint that lets anyone change a parcel's
status and, through it, a seller's money. So the Steadfast verifier returns
``False`` unconditionally, the route answers with a clear *not configured*
state, and the body is still stored, because the first thing anyone will want
when a real contract arrives is examples of what the provider actually sends.

Polling (:mod:`app.couriers.status_sync`) is the complete V1 synchronisation
path and does not depend on any of this.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.core.clock import utc_now
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.couriers.metrics import CourierMetric, record_metric
from app.couriers.models import CourierWebhookDelivery, WebhookDeliveryState

__all__ = [
    "WEBHOOK_BLOCKER",
    "CourierWebhookEvent",
    "CourierWebhookParser",
    "CourierWebhookVerifier",
    "SteadfastWebhookParser",
    "SteadfastWebhookVerifier",
    "WebhookIngestResult",
    "WebhookReceiver",
    "constant_time_signature_matches",
]

log = get_logger(__name__)

#: The operator-facing identifier. Appears verbatim in the provider manifest,
#: in docs/RELEASE_READINESS.md and in the admin console, so all three read the
#: same string.
WEBHOOK_BLOCKER = "STEADFAST_WEBHOOK_CONTRACT_REQUIRED"

#: Bodies larger than this are refused unread. A status callback is a few
#: hundred bytes; a megabyte of it is an attack or a bug.
MAX_WEBHOOK_BODY_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class CourierWebhookEvent:
    """One normalized event lifted out of a webhook body.

    Provider-agnostic by construction. When a real Steadfast contract arrives,
    its parser fills these fields; nothing downstream changes, because status
    sync already applies exactly this shape.
    """

    provider: str
    #: The provider's own event id, when it has one. The preferred dedupe key
    #: (master spec section 78) — the body hash is the fallback this build uses.
    provider_event_id: str | None
    provider_consignment_id: str | None
    tracking_code: str | None
    merchant_reference: str | None
    raw_status: str | None
    occurred_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_reference(self) -> bool:
        return bool(self.provider_consignment_id or self.tracking_code or self.merchant_reference)


class CourierWebhookVerifier(Protocol):
    """Decides whether a delivery genuinely came from the provider."""

    @property
    def is_configured(self) -> bool:
        """Whether a verified contract and a secret exist.

        ``False`` means :meth:`verify` cannot do its job. A caller must treat
        that as *not configured*, never as *verified*.
        """
        ...

    def verify(self, *, headers: dict[str, str], body: bytes) -> bool: ...


class CourierWebhookParser(Protocol):
    """Turns a verified body into normalized events."""

    def parse(self, *, headers: dict[str, str], body: bytes) -> list[CourierWebhookEvent]: ...


def constant_time_signature_matches(expected: str, provided: str | None) -> bool:
    """Compare two signatures without leaking timing.

    Ready for the day a real contract arrives. Used by nothing today, and that
    is the point: the primitive being present is not the same as a scheme being
    invented.
    """
    if not provided:
        return False
    return hmac.compare_digest(expected, provided)


class SteadfastWebhookVerifier:
    """Refuses every delivery. There is nothing to verify against.

    This is a deliberate, documented refusal rather than a stub. The supplied
    V1 documentation names no signature header and no algorithm, so there is no
    correct implementation — and an incorrect one that accepted requests would
    let anyone POST a ``delivered`` status for any parcel and move a seller's
    money.
    """

    provider = "steadfast"
    blocker = WEBHOOK_BLOCKER

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def is_configured(self) -> bool:
        """Always ``False``.

        The config flag alone is not enough: it would only mean an operator had
        switched something on. What is missing is the *contract*, and no flag
        supplies that. ``Settings`` additionally refuses to boot a deployed
        environment with the flag set, so this cannot drift.
        """
        return False

    def verify(self, *, headers: dict[str, str], body: bytes) -> bool:
        return False


class SteadfastWebhookParser:
    """Parses nothing. No payload shape is documented.

    Returning an empty list rather than raising keeps the receiver's control
    flow honest: a body we cannot interpret is stored and reported as *not
    configured*, which is a different outcome from a body that was rejected.
    """

    provider = "steadfast"

    def parse(self, *, headers: dict[str, str], body: bytes) -> list[CourierWebhookEvent]:
        return []


@dataclass(frozen=True, slots=True)
class WebhookIngestResult:
    """What became of one inbound delivery."""

    delivery_id: uuid.UUID
    state: WebhookDeliveryState
    #: What the route should answer. A 2xx for anything we have accepted
    #: responsibility for storing, so a provider does not retry forever over a
    #: state that will never change on its own.
    http_status: int
    message: str
    events: int = 0

    @property
    def was_processed(self) -> bool:
        return self.state is WebhookDeliveryState.PROCESSED


class WebhookReceiver:
    """Stores, deduplicates and — when a contract exists — verifies deliveries.

    The order is the point: **store first, decide second.** A body that is
    rejected is at least as interesting as one that is accepted, and an
    integration that discards what it refuses cannot be debugged.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        verifier: CourierWebhookVerifier,
        parser: CourierWebhookParser,
        provider: str = "steadfast",
    ) -> None:
        self._db = session
        self._verifier = verifier
        self._parser = parser
        self._provider = provider

    async def ingest(self, *, headers: dict[str, str], body: bytes) -> WebhookIngestResult:
        """Take one inbound request as far as it can safely go."""
        if len(body) > MAX_WEBHOOK_BODY_BYTES:
            # Not stored: an oversized body is not evidence, it is a payload.
            return WebhookIngestResult(
                delivery_id=uuid.uuid4(),
                state=WebhookDeliveryState.REJECTED,
                http_status=413,
                message="That webhook body is too large to accept.",
            )

        digest = hashlib.sha256(body).hexdigest()
        existing = await self._existing(digest)
        if existing is not None:
            # Replay protection. A provider retrying a delivery hashes the
            # same, and a duplicate is healthy — it means deduplication is
            # absorbing retries rather than reprocessing them.
            existing.delivery_count += 1
            existing.last_seen_at = utc_now()
            await self._db.flush()
            record_metric(CourierMetric.WEBHOOK_DUPLICATE, provider=self._provider)
            return WebhookIngestResult(
                delivery_id=existing.id,
                state=WebhookDeliveryState.DUPLICATE,
                http_status=200,
                message="Already received.",
            )

        delivery = CourierWebhookDelivery(
            provider=self._provider,
            state=str(WebhookDeliveryState.RECEIVED),
            raw_body=_safe_text(body),
            body_sha256=digest,
            # Names only. A signature header is credential-adjacent and an
            # Authorization header is a credential; neither belongs in a row
            # that support will read.
            header_names=sorted(headers),
            received_at=utc_now(),
            last_seen_at=utc_now(),
        )
        self._db.add(delivery)
        await self._db.flush()
        record_metric(CourierMetric.WEBHOOK_RECEIVED, provider=self._provider)

        if not self._verifier.is_configured:
            # The honest answer. Not "verified", not "rejected" — there is no
            # contract to check the request against, so nothing is processed
            # and the body is kept for whoever writes that contract.
            delivery.state = str(WebhookDeliveryState.NOT_CONFIGURED)
            delivery.reason = (
                f"{WEBHOOK_BLOCKER}: no verified webhook contract exists for "
                f"{self._provider}, so this delivery was stored and not processed. "
                "Parcel status is kept current by polling."
            )
            await self._db.flush()
            record_metric(CourierMetric.WEBHOOK_NOT_CONFIGURED, provider=self._provider)
            await record_audit(
                self._db,
                AuditAction.COURIER_WEBHOOK_RECEIVED,
                entity_type="courier_webhook_delivery",
                entity_id=delivery.id,
                context={
                    "provider": self._provider,
                    "state": delivery.state,
                    "blocker": WEBHOOK_BLOCKER,
                },
            )
            log.info(
                "courier webhook received while unconfigured",
                extra={
                    "provider": self._provider,
                    "operation": "webhook_ingest",
                    "blocker": WEBHOOK_BLOCKER,
                    "body_bytes": len(body),
                },
            )
            return WebhookIngestResult(
                delivery_id=delivery.id,
                state=WebhookDeliveryState.NOT_CONFIGURED,
                # 202: we have taken responsibility for storing it, and there is
                # nothing the sender can do differently. A 4xx would invite a
                # retry loop over a state that will not change on its own.
                http_status=202,
                message=(
                    "Received and stored. This provider has no verified webhook "
                    "contract, so the delivery was not processed."
                ),
            )

        if not self._verifier.verify(headers=headers, body=body):
            # A real security signal, and distinct from "not configured".
            delivery.state = str(WebhookDeliveryState.REJECTED)
            delivery.reason = "Signature verification failed."
            await self._db.flush()
            log.warning(
                "courier webhook signature rejected",
                extra={"provider": self._provider, "operation": "webhook_ingest"},
            )
            return WebhookIngestResult(
                delivery_id=delivery.id,
                state=WebhookDeliveryState.REJECTED,
                http_status=401,
                message="Signature verification failed.",
            )

        events = self._parser.parse(headers=headers, body=body)
        delivery.state = str(WebhookDeliveryState.ACCEPTED)
        delivery.provider_event_id = events[0].provider_event_id if events else None
        await self._db.flush()

        return WebhookIngestResult(
            delivery_id=delivery.id,
            state=WebhookDeliveryState.ACCEPTED,
            http_status=200,
            message="Accepted.",
            events=len(events),
        )

    async def _existing(self, digest: str) -> CourierWebhookDelivery | None:
        result = await self._db.execute(
            sa.select(CourierWebhookDelivery).where(
                CourierWebhookDelivery.provider == self._provider,
                CourierWebhookDelivery.body_sha256 == digest,
            )
        )
        return result.scalar_one_or_none()

    async def pending(self, *, limit: int = 100) -> list[CourierWebhookDelivery]:
        """Deliveries accepted but not yet processed.

        The queue a processor would drain. Empty in this build, because nothing
        is ever accepted — which is exactly what makes it safe to ship the
        processor alongside it.
        """
        result = await self._db.execute(
            sa.select(CourierWebhookDelivery)
            .where(
                CourierWebhookDelivery.provider == self._provider,
                CourierWebhookDelivery.state == str(WebhookDeliveryState.ACCEPTED),
            )
            .order_by(CourierWebhookDelivery.received_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def unconfigured_count(self) -> int:
        """How many deliveries arrived that we could not act on.

        Worth an operator's attention: a non-zero and rising count means the
        provider *is* sending callbacks and the contract is worth chasing.
        """
        return int(
            await self._db.scalar(
                sa.select(sa.func.count())
                .select_from(CourierWebhookDelivery)
                .where(
                    CourierWebhookDelivery.provider == self._provider,
                    CourierWebhookDelivery.state == str(WebhookDeliveryState.NOT_CONFIGURED),
                )
            )
            or 0
        )


def _safe_text(body: bytes) -> str:
    """Decode a body for storage without letting bad bytes break the write."""
    return body.decode("utf-8", errors="replace")


def build_receiver(
    session: AsyncSession, *, provider: str = "steadfast", settings: Settings | None = None
) -> WebhookReceiver:
    """The receiver for a provider, with its verifier and parser wired in."""
    return WebhookReceiver(
        session,
        verifier=SteadfastWebhookVerifier(settings),
        parser=SteadfastWebhookParser(),
        provider=provider,
    )
