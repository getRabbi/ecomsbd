"""WooCommerce, from the WooCommerce REST API documentation only.

- Keys come from the store's own ``/wc-auth/v1/authorize`` endpoint (one click
  for the seller) or are pasted from WooCommerce > Settings > Advanced > REST
  API. Either way they are REST API keys used over HTTPS Basic auth.
- ``/wp-json/wc/v3`` for orders and webhooks.
- Webhooks carry ``X-WC-Webhook-Signature``: base64 HMAC-SHA256 of the raw
  body under the secret we set when creating the webhook.
  ``X-WC-Webhook-Delivery-ID`` is the dedupe key.

Plain-HTTP stores are refused: without TLS the API needs OAuth 1.0a request
signing, and a seller's keys would cross the network in the clear.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

from app.core.config import get_settings
from app.core.errors import ValidationError
from app.integrations import http
from app.integrations.http import ProviderError
from app.integrations.normalize import Reject, Skip, clip, paisa
from app.public_api.webhooks import valid_url

WEBHOOK_TOPICS = ("order.created", "order.updated")
#: Orders a seller would ship. pending/failed are unpaid checkouts, the rest
#: are finished or withdrawn.
IMPORTABLE = frozenset({"processing", "on-hold", "completed"})
PAGE_SIZE = 50


def normalize_store(value: str) -> str:
    url = value.strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.query or parts.fragment:
        raise ValidationError(
            "Enter the store's HTTPS address",
            details={"field": "store_url", "code": "STORE_URL_INVALID"},
        )
    store = f"https://{(parts.hostname or '').lower()}{parts.path.rstrip('/')}"
    try:
        valid_url(store)
    except ValidationError as exc:
        raise ValidationError(
            "Enter the store's public HTTPS address",
            details={"field": "store_url", "code": "STORE_URL_INVALID"},
        ) from exc
    return store


def callback_url() -> str:
    return get_settings().public_base_url.rstrip("/") + "/v1/integration-callbacks/woocommerce"


def auth_endpoint_available() -> bool:
    settings = get_settings()
    return settings.public_base_url.startswith("https://") and bool(settings.public_web_url)


def authorize_url(store: str, state: str, return_url: str) -> str:
    query = urlencode(
        {
            "app_name": "ecomsbd",
            "scope": "read_write",
            "user_id": state,
            "return_url": return_url,
            "callback_url": callback_url(),
        }
    )
    return f"{store}/wc-auth/v1/authorize?{query}"


def valid_webhook(secret: str | None, body: bytes, header: str | None) -> bool:
    if not secret or not header:
        return False
    digest = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest())
    return hmac.compare_digest(digest.decode(), header.strip())


def is_ping(body: bytes) -> bool:
    """WooCommerce's unsigned activation request: ``webhook_id=<n>``."""
    head, _, value = body.decode(errors="replace").partition("=")
    return head == "webhook_id" and value.strip().isdigit()


async def call(
    store: str,
    credentials: dict[str, Any],
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: Any = None,
) -> http.Reply:
    reply = await http.send(
        method,
        f"{store}/wp-json/wc/v3{path}",
        params=params,
        json_body=body,
        auth=(credentials["consumer_key"], credentials["consumer_secret"]),
    )
    return http.raise_for_status(reply)


async def verify(store: str, credentials: dict[str, Any]) -> None:
    reply = await call(store, credentials, "GET", "/orders", params={"per_page": 1})
    if not isinstance(reply.data, list):
        # A 200 that is not the orders list is a caching or security plugin
        # answering for WooCommerce; nothing after this would work either.
        raise ProviderError("MALFORMED_RESPONSE")


async def register_webhooks(
    store: str, credentials: dict[str, Any], delivery_url: str, secret: str
) -> list[int]:
    ids = []
    for topic in WEBHOOK_TOPICS:
        reply = await call(
            store,
            credentials,
            "POST",
            "/webhooks",
            body={
                "name": f"ecomsbd {topic}",
                "topic": topic,
                "delivery_url": delivery_url,
                "secret": secret,
                "status": "active",
            },
        )
        webhook_id = (reply.data or {}).get("id") if isinstance(reply.data, dict) else None
        if not isinstance(webhook_id, int):
            raise ProviderError("WEBHOOK_REGISTRATION_FAILED")
        ids.append(webhook_id)
    return ids


async def webhooks_active(store: str, credentials: dict[str, Any], ids: list[int]) -> bool:
    for webhook_id in ids:
        reply = await call(store, credentials, "GET", f"/webhooks/{int(webhook_id)}")
        if not isinstance(reply.data, dict) or reply.data.get("status") != "active":
            return False
    return bool(ids)


async def unregister_webhooks(store: str, credentials: dict[str, Any], ids: list[int]) -> None:
    for webhook_id in ids:
        try:
            await call(
                store,
                credentials,
                "DELETE",
                f"/webhooks/{int(webhook_id)}",
                params={"force": "true"},
            )
        except ProviderError as exc:
            if exc.code != "NOT_FOUND_AT_PROVIDER":
                raise


def _gmt(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S")


async def orders_page(
    store: str,
    credentials: dict[str, Any],
    *,
    since: datetime,
    until: datetime,
    updated: bool,
    created_from: datetime,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None, int | None]:
    page = int(cursor or "1")
    params: dict[str, Any] = {
        "per_page": PAGE_SIZE,
        "page": page,
        "order": "asc",
        "dates_are_gmt": "true",
    }
    if updated:
        params |= {
            "modified_after": _gmt(since),
            "modified_before": _gmt(until),
            "after": _gmt(created_from),
            "orderby": "modified",
        }
    else:
        params |= {"after": _gmt(since), "before": _gmt(until), "orderby": "date"}
    reply = await call(store, credentials, "GET", "/orders", params=params)
    if not isinstance(reply.data, list):
        raise ProviderError("MALFORMED_RESPONSE")
    try:
        pages = int(reply.headers.get("x-wp-totalpages") or 0)
        total = int(reply.headers["x-wp-total"]) if "x-wp-total" in reply.headers else None
    except ValueError:
        pages, total = 0, None
    next_cursor = str(page + 1) if page < pages and reply.data else None
    return reply.data, next_cursor, total


async def fetch_order(store: str, credentials: dict[str, Any], order_id: str) -> dict[str, Any]:
    if not order_id.isdigit():
        raise ProviderError("MALFORMED_PAYLOAD")
    reply = await call(store, credentials, "GET", f"/orders/{order_id}")
    if not isinstance(reply.data, dict):
        raise ProviderError("MALFORMED_RESPONSE")
    return reply.data


def webhook_order(body: dict[str, Any]) -> tuple[str, str | None]:
    order_id = body.get("id")
    if not isinstance(order_id, int) or order_id <= 0:
        raise ProviderError("MALFORMED_PAYLOAD")
    status = body.get("status")
    return str(order_id), "cancelled" if status in {"cancelled", "refunded", "trash"} else None


def external_id(order: dict[str, Any]) -> str:
    value = order.get("id")
    if not isinstance(value, int) or value <= 0:
        raise ProviderError("MALFORMED_PAYLOAD")
    return str(value)


def is_cancelled(order: dict[str, Any]) -> bool:
    return order.get("status") in {"cancelled", "refunded", "trash"}


def to_native(order: dict[str, Any]) -> dict[str, Any]:
    status = order.get("status")
    if status in {"cancelled", "refunded", "trash"}:
        raise Skip("ORDER_CANCELLED_AT_PROVIDER")
    if status not in IMPORTABLE:
        raise Skip("ORDER_NOT_READY")
    if order.get("currency") != "BDT":
        raise Reject("CURRENCY_NOT_BDT")
    ship = order.get("shipping") or {}
    bill = order.get("billing") or {}
    phone = ship.get("phone") or bill.get("phone")
    if not phone:
        raise Reject("MISSING_PHONE")
    items = []
    for line in order.get("line_items") or []:
        quantity = int(line.get("quantity") or 0)
        if quantity <= 0:
            continue
        total = paisa(line.get("total"))
        # Whole-paisa unit price plus a sub-unit line discount reproduces the
        # store's line total exactly.
        unit = -(-total // quantity)
        items.append(
            {
                "name": clip(line.get("name"), 200) or "WooCommerce item",
                "quantity": min(quantity, 9999),
                "unit_price_paisa": unit,
                "discount_paisa": unit * quantity - total,
                **(
                    {"_link": f"{line['product_id']}:{line.get('variation_id') or ''}"}
                    if line.get("product_id")
                    else {}
                ),
            }
        )
    if not items:
        raise Reject("NO_ITEMS")
    person = ship if (ship.get("first_name") or ship.get("last_name")) else bill
    place = ship if ship.get("address_1") else bill
    address = ", ".join(
        filter(
            None, (place.get(k) for k in ("address_1", "address_2", "city", "state", "postcode"))
        )
    )
    total = paisa(order.get("total"))
    # WooCommerce marks cash-on-delivery orders paid as they reach processing,
    # so the payment method, not date_paid, says what the courier collects.
    cod = total if order.get("payment_method") == "cod" or not order.get("date_paid") else 0
    note = f"WooCommerce #{order.get('number') or order.get('id')}"
    if order.get("customer_note"):
        note += "\n" + str(order["customer_note"])
    return {
        "phone": str(phone)[:24],
        "customer_name": clip(
            " ".join(filter(None, [person.get("first_name"), person.get("last_name")])), 160
        ),
        "address": clip(address, 1000),
        "district": clip(place.get("city"), 80),
        "note": note[:4000],
        "items": items,
        "delivery_fee_paisa": paisa(order.get("shipping_total")),
        "cod_amount_paisa": cod,
    }
