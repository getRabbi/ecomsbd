"""ecomsbd Public API: a thin Python client and webhook verifier.

Standard library only. Copy this file into your project.

    from ecomsbd import Client, verify_webhook

    client = Client(api_key="ec_live_...", base_url="https://YOUR-API-HOST/public/v1")
    order = client.send_source_order(SOURCE_ID, "WEB-1001", {...})
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import OrderedDict
from typing import Any

__all__ = [
    "ROUTES",
    "Client",
    "EcomsbdError",
    "ReplayGuard",
    "sign",
    "verify_webhook",
]

#: Every call this client makes, as (method, path template). Kept in step with
#: the API by ecomsbd's own test suite.
ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/me"),
    ("GET", "/orders"),
    ("GET", "/orders/{order_id}"),
    ("POST", "/orders"),
    ("POST", "/orders/{order_id}/status"),
    ("POST", "/sources/{source_id}/orders"),
    ("GET", "/customers"),
    ("POST", "/customers"),
    ("GET", "/products"),
    ("GET", "/inventory/{product_id}"),
    ("POST", "/inventory/{product_id}/adjustments"),
)

SIGNATURE_HEADER = "X-Ecomsbd-Signature"
EVENT_ID_HEADER = "X-Ecomsbd-Event-Id"
TOLERANCE_SECONDS = 300


class EcomsbdError(Exception):
    """An API error. ``code`` is the stable machine code from the response body."""

    def __init__(self, status: int, body: dict[str, Any], retry_after: int | None = None):
        self.status = status
        self.body = body
        self.code = body.get("code")
        self.retry_after = retry_after
        super().__init__(f"{status} {self.code}: {body.get('message_en')}")


class Client:
    def __init__(self, api_key: str, base_url: str, *, timeout: float = 15.0) -> None:
        if not api_key.startswith("ec_live_"):
            raise ValueError("Use an ecomsbd API key (ec_live_...)")
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        query: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        url = self._base + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        headers = {"Authorization": f"Bearer {self._key}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if method == "POST":
            # Reuse the same key when retrying the same request; never for a new one.
            headers["Idempotency-Key"] = idempotency_key or str(uuid.uuid4())
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                payload = json.loads(raw) if raw else {}
            except ValueError:
                payload = {"message_en": raw.decode(errors="replace")[:200]}
            retry = error.headers.get("Retry-After")
            if error.code == 409 and payload.get("result") == "CONFLICT":
                return payload  # a website cancel after booking: a conflict, not a failure
            raise EcomsbdError(error.code, payload, int(retry) if retry else None) from None

    # --------------------------------------------------------------- calls --

    def me(self) -> dict[str, Any]:
        """Which key this is, its shop and scopes. A safe connection test."""
        return self._request("GET", "/me")

    def send_source_order(
        self,
        source_id: str,
        external_order_id: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Custom Website: send an order. The same external_order_id is never duplicated."""
        return self._request(
            "POST",
            f"/sources/{source_id}/orders",
            {"external_order_id": external_order_id, "payload": payload},
            idempotency_key=idempotency_key or f"order-{external_order_id}",
        )

    def create_order(
        self, order: dict[str, Any], *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return self._request("POST", "/orders", order, idempotency_key=idempotency_key)

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._request("GET", f"/orders/{order_id}")

    def list_orders(self, *, limit: int = 30, offset: int = 0) -> dict[str, Any]:
        return self._request("GET", "/orders", query={"limit": limit, "offset": offset})

    def set_order_status(
        self,
        order_id: str,
        status: str,
        *,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """CONFIRMED or CANCELLED. A booked order answers result=CONFLICT (HTTP 409)."""
        return self._request(
            "POST",
            f"/orders/{order_id}/status",
            {"status": status, "reason": reason},
            idempotency_key=idempotency_key or f"status-{order_id}-{status}",
        )

    def find_products_by_sku(self, sku: str) -> list[dict[str, Any]]:
        return self._request("GET", "/products", query={"sku": sku})["items"]

    def get_inventory(self, product_id: str) -> dict[str, Any]:
        return self._request("GET", f"/inventory/{product_id}")

    def adjust_inventory(
        self,
        product_id: str,
        quantity_delta: int,
        *,
        reason: str = "MANUAL_ADJUSTMENT",
        variant_id: str | None = None,
        note: str | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Needs the inventory:write scope. ``idempotency_key`` is required here."""
        body: dict[str, Any] = {"quantity_delta": quantity_delta, "reason": reason}
        if variant_id:
            body["variant_id"] = variant_id
        if note:
            body["note"] = note
        return self._request(
            "POST",
            f"/inventory/{product_id}/adjustments",
            body,
            idempotency_key=idempotency_key,
        )


# ------------------------------------------------------------- webhooks --


def sign(secret: str, body: bytes, timestamp: int) -> str:
    digest = hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256)
    return f"t={timestamp},v1={digest.hexdigest()}"


def verify_webhook(
    secret: str,
    raw_body: bytes,
    header: str,
    *,
    tolerance: int = TOLERANCE_SECONDS,
    now: int | None = None,
) -> bool:
    """True only for an untampered body signed within ``tolerance`` seconds.

    Pass the raw request body exactly as received, before any JSON parsing.
    """
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        stamp = int(parts["t"])
        given = parts["v1"]
    except (KeyError, ValueError):
        return False
    if abs((now if now is not None else int(time.time())) - stamp) > tolerance:
        return False
    expected = sign(secret, raw_body, stamp).split("v1=", 1)[1]
    return hmac.compare_digest(expected, given)


class ReplayGuard:
    """Remembers recent event IDs so a redelivered event is processed once.

    In-memory and bounded; use your database for this in production.
    """

    def __init__(self, size: int = 10_000) -> None:
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._size = size

    def first_time(self, event_id: str) -> bool:
        if event_id in self._seen:
            return False
        self._seen[event_id] = None
        if len(self._seen) > self._size:
            self._seen.popitem(last=False)
        return True
