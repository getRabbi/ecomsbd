"""Carrybee webhook verification and parsing (V4 WP7.2).

CONTRACT.md §8: Carrybee sends a shared secret in
``X-CB-Webhook-Integration-Header`` (no HMAC, no timestamp), and the reply must
echo that header with the exact secret and use status 202 for the integration
event. The secret is per merchant, so verification needs the shop's account:
the route resolves it from an opaque token in the callback URL before any body
is trusted. A shop with no secret stored has every request refused.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.couriers.carrybee.contract import PROVIDER, WEBHOOK_SECRET_HEADER

__all__ = [
    "CarrybeeWebhookEvent",
    "CarrybeeWebhookParser",
    "CarrybeeWebhookVerifier",
    "parse_carrybee_events",
]

_HEADER = WEBHOOK_SECRET_HEADER.lower()


@dataclass(frozen=True, slots=True)
class CarrybeeWebhookEvent:
    provider: str
    event: str | None
    provider_event_id: str | None
    provider_consignment_id: str | None
    merchant_reference: str | None
    raw_status: str | None
    occurred_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_reference(self) -> bool:
        return bool(self.provider_consignment_id or self.merchant_reference)


class CarrybeeWebhookVerifier:
    """Verifies a callback against one merchant's stored webhook secret."""

    provider = PROVIDER

    def __init__(self, secret: str | None) -> None:
        self._secret = (secret or "").strip()

    @property
    def is_configured(self) -> bool:
        return bool(self._secret)

    def verify(self, *, headers: dict[str, str], body: bytes) -> bool:
        """Constant-time comparison of the header with the stored secret."""
        if not self._secret:
            return False
        provided = headers.get(_HEADER)
        if not provided:
            return False
        return hmac.compare_digest(self._secret.encode(), provided.strip().encode())


class CarrybeeWebhookParser:
    provider = PROVIDER

    def parse(self, *, headers: dict[str, str], body: bytes) -> list[CarrybeeWebhookEvent]:
        return parse_carrybee_events(body)


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def _when(value: Any) -> datetime | None:
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    # CONTRACT.md §3: ISO 8601 *with timezone*. A naive value is not trusted.
    return parsed if parsed.tzinfo is not None else None


def parse_carrybee_events(body: bytes) -> list[CarrybeeWebhookEvent]:
    """Read a callback body. Never raises; an unreadable body yields ``[]``.

    One event per request is documented; a list body is accepted anyway.
    Carrybee publishes no event id, so none is claimed (the receiver dedupes
    by body hash).
    """
    try:
        payload = json.loads(body.decode("utf-8", errors="replace")) if body.strip() else None
    except ValueError:
        return []
    records = payload if isinstance(payload, list) else [payload]
    events: list[CarrybeeWebhookEvent] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        event_name = _text(record.get("event"))
        consignment_id = _text(record.get("consignment_id"))
        merchant_reference = _text(record.get("merchant_order_id"))
        if event_name is None or not (consignment_id or merchant_reference):
            continue
        events.append(
            CarrybeeWebhookEvent(
                provider=PROVIDER,
                event=event_name,
                provider_event_id=None,
                provider_consignment_id=consignment_id,
                merchant_reference=merchant_reference,
                raw_status=event_name,
                occurred_at=_when(record.get("timestamptz")),
                # The rider's number is not kept with the event.
                raw={key: value for key, value in record.items() if key != "agent_phone"},
            )
        )
    return events
