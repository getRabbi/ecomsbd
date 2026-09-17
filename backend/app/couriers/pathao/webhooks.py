"""Pathao webhook verification and parsing.

Unlike Steadfast, Pathao **does** publish a webhook contract, so this is a real
verifier rather than a documented refusal. Three properties of that contract
shape everything here:

*   **The signature is a shared secret, not an HMAC.** Pathao's own plugin
    compares the ``X-PATHAO-Signature`` header for equality against the secret
    the merchant configured. ecomsbd compares it in constant time — a hardening
    of Pathao's scheme, not a different one — and never logs either side.

*   **The secret is per merchant.** So verification cannot happen on the
    stateless adapter protocol; it needs the shop's account. That is why this
    verifier is constructed with a secret and the route resolves the account
    first, from an opaque token in the callback URL, before any body is
    trusted.

*   **Pathao requires a fixed header on the response.** Every reply must carry
    ``X-Pathao-Merchant-Webhook-Integration-Secret``, including the reply to the
    ``webhook_integration`` handshake Pathao sends when a merchant saves the
    URL. A reply without it is treated by Pathao as a failed integration.

Parsing is deliberately separable from verifying: a body that fails
verification is still stored, and being able to parse it later is how an
integration gets debugged. Nothing acts on a parsed event until the body it
came from was accepted.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.couriers.pathao.contract import (
    HEADER_WEBHOOK_SIGNATURE,
    WEBHOOK_INTEGRATION_EVENT,
    WEBHOOK_INTEGRATION_SECRET_HEADER,
    WEBHOOK_INTEGRATION_SECRET_VALUE,
)

__all__ = [
    "WEBHOOK_INTEGRATION_SECRET_HEADER",
    "WEBHOOK_INTEGRATION_SECRET_VALUE",
    "PathaoWebhookEvent",
    "PathaoWebhookParser",
    "PathaoWebhookVerifier",
    "is_integration_handshake",
    "parse_pathao_events",
]

log = get_logger(__name__)

PROVIDER = "pathao"


@dataclass(frozen=True, slots=True)
class PathaoWebhookEvent:
    """One normalized event lifted out of a Pathao callback body."""

    provider: str
    event: str | None
    provider_event_id: str | None
    provider_consignment_id: str | None
    merchant_reference: str | None
    raw_status: str | None
    #: Pathao's quoted delivery fee, in taka, exactly as sent. Not converted to
    #: paisa and not recorded as a cost here — that is a money decision.
    delivery_fee: float | None = None
    occurred_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_reference(self) -> bool:
        return bool(self.provider_consignment_id or self.merchant_reference)


class PathaoWebhookVerifier:
    """Verifies a callback against one merchant's configured webhook secret."""

    provider = PROVIDER

    def __init__(self, secret: str | None) -> None:
        self._secret = (secret or "").strip()

    @property
    def is_configured(self) -> bool:
        """Whether this shop has a Pathao webhook secret stored.

        ``False`` means :meth:`verify` cannot do its job, and the receiver must
        treat that as *not configured* — never as *verified*.
        """
        return bool(self._secret)

    def verify(self, *, headers: dict[str, str], body: bytes) -> bool:
        """Constant-time comparison of the signature header with the secret."""
        if not self._secret:
            return False
        provided = headers.get(HEADER_WEBHOOK_SIGNATURE)
        if not provided:
            return False
        return hmac.compare_digest(self._secret, provided.strip())


class PathaoWebhookParser:
    """Turns a Pathao callback body into normalized events."""

    provider = PROVIDER

    def parse(self, *, headers: dict[str, str], body: bytes) -> list[PathaoWebhookEvent]:
        return parse_pathao_events(body)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _decimal_like(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _load(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8", errors="replace")) if body.strip() else None
    except ValueError:
        return None


def is_integration_handshake(body: bytes) -> bool:
    """Whether this body is Pathao's ``webhook_integration`` handshake.

    Pathao sends it when a merchant saves the webhook URL. It names no parcel,
    so it is acknowledged and never processed as a delivery event.
    """
    payload = _load(body)
    if not isinstance(payload, dict):
        return False
    return _text(payload.get("event")) == WEBHOOK_INTEGRATION_EVENT


def parse_pathao_events(body: bytes) -> list[PathaoWebhookEvent]:
    """Read a Pathao callback body.

    Returns an empty list — never raises — for a body that is not the published
    shape. An unreadable callback is stored and reported, not crashed on.

    Pathao sends one event per request; a list body is accepted anyway rather
    than dropping deliveries if that ever changes.
    """
    payload = _load(body)
    if payload is None:
        return []

    records = payload if isinstance(payload, list) else [payload]
    events: list[PathaoWebhookEvent] = []

    for record in records:
        if not isinstance(record, dict):
            continue
        event_name = _text(record.get("event"))
        if event_name == WEBHOOK_INTEGRATION_EVENT:
            # A handshake, not a parcel event.
            continue

        consignment_id = _text(record.get("consignment_id"))
        merchant_reference = _text(record.get("merchant_order_id"))
        if not consignment_id and not merchant_reference:
            # Nothing to attach this to. Kept out of the event stream rather
            # than guessed at; the body is still stored by the receiver.
            continue

        events.append(
            PathaoWebhookEvent(
                provider=PROVIDER,
                event=event_name,
                # Pathao publishes no event id. The receiver's body hash is the
                # dedupe key; claiming an id here would invent one.
                provider_event_id=None,
                provider_consignment_id=consignment_id,
                merchant_reference=merchant_reference,
                raw_status=_text(record.get("order_status")),
                delivery_fee=_decimal_like(record.get("delivery_fee")),
                # Pathao sends no event timestamp. Filling in `now()` would make
                # an observation look like an event.
                occurred_at=None,
                raw=record,
            )
        )

    return events
