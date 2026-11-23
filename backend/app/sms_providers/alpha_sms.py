"""Alpha SMS (``sms.net.bd``) behind :class:`~app.notifications.transport.SmsTransport` (V4 WP7.1).

Contract: ``docs/providers/alpha_sms/CONTRACT.md`` (the provider's own API page,
read 2026-11-23). One call is used: ``POST /sendsms`` with form fields
``api_key``, ``msg``, ``to`` and, when approved, ``sender_id``. Form fields, not
a query string, so the key never sits in a URL that a proxy or client logs.

The API takes no idempotency key, so a send that *may* have reached the
provider is :attr:`~app.notifications.transport.DeliveryOutcome.UNKNOWN` and is
never sent again. Only an answer proving nothing was sent (no connection, or
the documented "insufficient balance") is ``FAILED``, the retryable outcome.
The provider reports no segment count or price, so the GSM / UCS-2 estimate
stands (``provider_segments`` stays ``None``).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger
from app.notifications.transport import (
    DeliveryOutcome,
    SmsGatewayTransport,
    SmsMessage,
    TransportResult,
)

__all__ = ["ALPHA_SMS_PROVIDER", "AlphaSmsTransport", "outcome_for_code"]

log = get_logger(__name__)

ALPHA_SMS_PROVIDER = "alpha_sms"
DEFAULT_BASE_URL = "https://api.sms.net.bd"

#: CONTRACT.md §5: refused for good — fix the request or the account, not retry.
_REJECTED = frozenset({400, 403, 404, 405, 410, 411, 412, 413, 414, 415, 416, 420, 421})
#: CONTRACT.md §5: refused before sending, safe to try again later.
_NOT_SENT_RETRYABLE = frozenset({417})


def outcome_for_code(code: int) -> DeliveryOutcome:
    """The documented ``error`` value → what happened. Undocumented → UNKNOWN."""
    if code == 0:
        return DeliveryOutcome.SENT
    if code in _REJECTED:
        return DeliveryOutcome.REJECTED
    if code in _NOT_SENT_RETRYABLE:
        return DeliveryOutcome.FAILED
    # 409 "unknown error on the server" and every undocumented code: the
    # provider may have accepted it.
    return DeliveryOutcome.UNKNOWN


def _error_code(body: Any) -> int | None:
    if not isinstance(body, dict):
        return None
    value = body.get("error")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _request_id(body: dict[str, Any]) -> str | None:
    data = body.get("data")
    if not isinstance(data, dict):
        return None
    value = data.get("request_id")
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    return text[:120] or None


class AlphaSmsTransport:
    """Sends one SMS through Alpha SMS. Never raises into a business path."""

    name = ALPHA_SMS_PROVIDER

    def __init__(
        self,
        api_key: str,
        *,
        sender_id: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._sender_id = sender_id
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._timeout = timeout_seconds

    def __repr__(self) -> str:  # never the key
        return f"AlphaSmsTransport(sender_id={self._sender_id!r})"

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key.strip())

    async def send(self, message: SmsMessage) -> TransportResult:
        from app.common.phone import try_normalize_bd_phone

        problem = SmsGatewayTransport._validate(message)
        if problem is not None:
            return TransportResult(DeliveryOutcome.REJECTED, self.name, detail=problem)
        if not self.is_configured:
            return TransportResult(
                DeliveryOutcome.NOT_CONFIGURED, self.name, detail="ALPHA_SMS_KEY_MISSING"
            )
        phone = try_normalize_bd_phone(message.phone_e164)
        if phone is None:  # pragma: no cover - _validate checked it
            return TransportResult(DeliveryOutcome.REJECTED, self.name, detail="ALPHA_SMS_NUMBER")
        form = {
            "api_key": self._api_key,
            "msg": message.body,
            # CONTRACT.md §2: "must start with country code (880)".
            "to": phone.e164.lstrip("+"),
        }
        sender_id = message.sender_id or self._sender_id
        if sender_id:
            form["sender_id"] = sender_id

        try:
            response = await self._post(f"{self._base_url}/sendsms", form)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            # No connection was made: nothing reached the provider.
            log.warning("alpha sms unreachable", extra=message.redacted())
            return TransportResult(
                DeliveryOutcome.FAILED, self.name, detail="ALPHA_SMS_UNREACHABLE"
            )
        except httpx.HTTPError:
            # Sent, but no answer came back: it may have been accepted.
            log.warning("alpha sms answer lost", extra=message.redacted())
            return TransportResult(DeliveryOutcome.UNKNOWN, self.name, detail="ALPHA_SMS_NO_ANSWER")

        try:
            body: Any = response.json()
        except ValueError:
            body = None
        code = _error_code(body)
        if code is None:
            log.warning(
                "alpha sms unreadable answer",
                extra={**message.redacted(), "http_status": response.status_code},
            )
            return TransportResult(
                DeliveryOutcome.UNKNOWN, self.name, detail="ALPHA_SMS_UNREADABLE_ANSWER"
            )
        outcome = outcome_for_code(code)
        if outcome is DeliveryOutcome.SENT:
            reference = _request_id(body) if isinstance(body, dict) else None
            return TransportResult(outcome, self.name, provider_reference=reference)
        log.warning(
            "alpha sms not sent",
            extra={**message.redacted(), "provider_code": code, "outcome": str(outcome)},
        )
        return TransportResult(outcome, self.name, detail=f"ALPHA_SMS_{code}")

    async def _post(self, url: str, form: dict[str, str]) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(url, data=form, timeout=self._timeout)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.post(url, data=form)
