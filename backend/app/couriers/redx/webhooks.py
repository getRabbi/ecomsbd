"""RedX callback verification and parsing.

RedX documents its callbacks plainly, and the documentation shapes everything
here:

*   **Authentication is a credential in the callback URL's query string.**
    "Any required credentials should be included in the query parameters of
    the URL", with ``https://example.com/callback?token=<token>`` as the
    example. There is no signature header and no HMAC. So ecomsbd generates a
    random secret per shop, puts it in the ``token`` parameter of the URL the
    seller pastes into RedX, stores it encrypted, and compares what arrives in
    constant time. It is only as strong as the URL is secret, and nothing here
    pretends otherwise: the URL is shown only to the shop owner, and the
    shared log redactor removes ``token=`` values from request lines.

*   **The route resolves the shop before trusting anything.** The callback URL
    also carries the account's opaque routing token in its path, exactly as the
    Pathao route does, so the right shop's secret is known before the body is
    parsed.

*   **The body is one parcel update.** ``tracking_number``, ``timestamp``,
    ``status``, ``message_en``, ``message_bn``, ``invoice_number``,
    ``delivery_type``. There is no event id, so the receiver's body hash is the
    dedupe key; inventing an id here would claim something RedX does not send.

Parsing is separable from verifying, as with Pathao: a body that fails
verification is still stored, and nothing acts on a parsed event until the body
it came from was accepted.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.couriers.redx.contract import PROVIDER

__all__ = [
    "RedxWebhookEvent",
    "RedxWebhookParser",
    "RedxWebhookVerifier",
    "parse_redx_events",
]


@dataclass(frozen=True, slots=True)
class RedxWebhookEvent:
    """One parcel update lifted out of a RedX callback body."""

    provider: str
    provider_event_id: str | None
    #: RedX's tracking id. It is the only parcel identifier RedX issues, so it
    #: is both the provider consignment id and the tracking code.
    provider_consignment_id: str | None
    merchant_reference: str | None
    raw_status: str | None
    delivery_type: str | None = None
    message_en: str | None = None
    message_bn: str | None = None
    occurred_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def tracking_code(self) -> str | None:
        return self.provider_consignment_id

    @property
    def has_reference(self) -> bool:
        return bool(self.provider_consignment_id or self.merchant_reference)


class RedxWebhookVerifier:
    """Checks a callback's query-string token against one shop's secret.

    Built per request, with both halves in hand, because RedX's credential
    arrives in the URL rather than in a header or the body — which is all the
    shared receiver passes to :meth:`verify`.
    """

    provider = PROVIDER

    def __init__(self, secret: str | None, *, provided_token: str | None) -> None:
        self._secret = (secret or "").strip()
        self._provided = (provided_token or "").strip()

    @property
    def is_configured(self) -> bool:
        """Whether this shop has a callback secret issued.

        ``False`` means :meth:`verify` cannot do its job, and the receiver must
        treat that as *not configured* — never as *verified*.
        """
        return bool(self._secret)

    def verify(self, *, headers: dict[str, str], body: bytes) -> bool:
        if not self._secret or not self._provided:
            return False
        return hmac.compare_digest(self._secret, self._provided)


class RedxWebhookParser:
    provider = PROVIDER

    def parse(self, *, headers: dict[str, str], body: bytes) -> list[RedxWebhookEvent]:
        return parse_redx_events(body)


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def _timestamp(value: Any) -> datetime | None:
    """RedX's ``timestamp``, when it reads as an ISO 8601 time.

    The documentation shows ``<TIMESTAMP>`` and no format. Its other endpoints
    use ISO 8601, so that is what is accepted; anything else is kept in ``raw``
    and not turned into a time we would have to guess the zone of.
    """
    text = _text(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_redx_events(body: bytes) -> list[RedxWebhookEvent]:
    """Read a RedX callback body. Never raises.

    RedX sends one parcel update per request; a list body is accepted anyway
    rather than dropping updates if that ever changes.
    """
    try:
        payload = json.loads(body.decode("utf-8", errors="replace")) if body.strip() else None
    except ValueError:
        return []
    if payload is None:
        return []

    records = payload if isinstance(payload, list) else [payload]
    events: list[RedxWebhookEvent] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        tracking = _text(record.get("tracking_number"))
        invoice = _text(record.get("invoice_number"))
        if not tracking and not invoice:
            # Nothing to attach this to. Kept out of the event stream rather
            # than guessed at; the body is still stored by the receiver.
            continue
        events.append(
            RedxWebhookEvent(
                provider=PROVIDER,
                provider_event_id=None,
                provider_consignment_id=tracking,
                merchant_reference=invoice,
                raw_status=_text(record.get("status")),
                delivery_type=_text(record.get("delivery_type")),
                message_en=_text(record.get("message_en")),
                message_bn=_text(record.get("message_bn")),
                occurred_at=_timestamp(record.get("timestamp")),
                raw=record,
            )
        )
    return events
