"""Shopify, from shopify.dev only.

- Authorization code grant with expiring offline tokens (``expiring=1``):
  a one-hour access token and a rotating 90-day refresh token.
- GraphQL Admin API, pinned by ``SHOPIFY_API_VERSION``. New public apps may not
  use REST.
- Webhooks registered with ``webhookSubscriptionCreate`` and verified with a
  base64 HMAC-SHA256 of the raw body under the app's client secret.
  ``X-Shopify-Webhook-Id`` is the dedupe key.

Webhook bodies are REST-shaped; they are used only for the order id and a
cancellation hint. The order itself is always read back through GraphQL, so
there is one normalizer and the stored event carries no customer data.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.errors import ValidationError
from app.integrations import http
from app.integrations.http import ProviderError
from app.integrations.normalize import Reject, Skip, clip, paisa

SHOP_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]*\.myshopify\.com$")
WEBHOOK_TOPICS = ("ORDERS_CREATE", "ORDERS_UPDATED", "APP_UNINSTALLED")
PAGE_SIZE = 50

ORDER_FIELDS = """
fragment OrderFields on Order {
  id legacyResourceId name createdAt updatedAt cancelledAt test
  displayFinancialStatus note phone currencyCode
  shippingAddress { name firstName lastName address1 address2 city province zip phone }
  billingAddress { name firstName lastName phone }
  totalOutstandingSet { shopMoney { amount currencyCode } }
  totalShippingPriceSet { shopMoney { amount } }
  lineItems(first: 100) {
    nodes {
      title variantTitle sku currentQuantity
      discountedUnitPriceAfterAllDiscountsSet { shopMoney { amount } }
    }
  }
}
"""
ORDERS_QUERY = (
    ORDER_FIELDS
    + """
query Orders($first: Int!, $after: String, $query: String) {
  orders(first: $first, after: $after, query: $query, sortKey: CREATED_AT) {
    nodes { ...OrderFields }
    pageInfo { hasNextPage endCursor }
  }
}
"""
)
ORDER_QUERY = (
    ORDER_FIELDS
    + """
query Order($id: ID!) { order(id: $id) { ...OrderFields } }
"""
)
SHOP_QUERY = "query { shop { name myshopifyDomain currencyCode } }"
SUBSCRIBE = """
mutation Subscribe($topic: WebhookSubscriptionTopic!, $sub: WebhookSubscriptionInput!) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $sub) {
    webhookSubscription { id }
    userErrors { field message }
  }
}
"""
UNSUBSCRIBE = """
mutation Unsubscribe($id: ID!) {
  webhookSubscriptionDelete(id: $id) { deletedWebhookSubscriptionId userErrors { message } }
}
"""
SUBSCRIPTIONS = "query { webhookSubscriptions(first: 20) { nodes { id topic uri } } }"


def configured() -> bool:
    settings = get_settings()
    return bool(settings.shopify_client_id and settings.shopify_client_secret)


def _secret() -> str:
    secret = get_settings().shopify_client_secret
    if secret is None:
        raise ProviderError("SHOPIFY_APP_SETUP_REQUIRED")
    return secret.get_secret_value()


def normalize_shop(value: str) -> str:
    shop = value.strip().lower().removeprefix("https://").removeprefix("http://")
    shop = shop.split("/", 1)[0]
    if shop and "." not in shop:
        shop += ".myshopify.com"
    if not SHOP_RE.fullmatch(shop):
        raise ValidationError(
            "Enter your store's myshopify.com address",
            details={"field": "shop", "code": "SHOPIFY_DOMAIN_INVALID"},
        )
    return shop


def redirect_uri() -> str:
    return get_settings().public_base_url.rstrip("/") + "/v1/integration-callbacks/shopify"


def authorize_url(shop: str, state: str) -> str:
    settings = get_settings()
    query = urlencode(
        {
            "client_id": settings.shopify_client_id,
            "scope": settings.shopify_scopes,
            "redirect_uri": redirect_uri(),
            "state": state,
        }
    )
    return f"https://{shop}/admin/oauth/authorize?{query}"


def valid_callback(params: list[tuple[str, str]]) -> bool:
    """Shopify's callback HMAC: every parameter but ``hmac``, sorted, keyed by the secret.

    Shopify's own libraries differ on whether values are re-encoded before
    signing, so both forms are accepted; each is still keyed by our secret.
    """
    given = [value for key, value in params if key == "hmac"]
    if len(given) != 1:
        return False
    rest = sorted((key, value) for key, value in params if key not in {"hmac", "signature"})
    secret = _secret().encode()
    for message in ("&".join(f"{k}={v}" for k, v in rest), urlencode(rest)):
        digest = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(digest, given[0].lower()):
            return True
    return False


def valid_webhook(body: bytes, header: str | None) -> bool:
    if not header or not configured():
        return False
    digest = base64.b64encode(hmac.new(_secret().encode(), body, hashlib.sha256).digest())
    return hmac.compare_digest(digest.decode(), header.strip())


def _tokens(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or not data.get("access_token"):
        raise ProviderError("MALFORMED_RESPONSE")
    now = utc_now()
    result = {
        "access_token": data["access_token"],
        "scope": data.get("scope") or "",
        "refresh_token": data.get("refresh_token"),
        "expires_at": None,
    }
    if data.get("expires_in"):
        result["expires_at"] = (now + timedelta(seconds=int(data["expires_in"]))).isoformat()
    return result


async def _token_request(shop: str, body: dict[str, Any]) -> dict[str, Any]:
    reply = await http.send(
        "POST",
        f"https://{shop}/admin/oauth/access_token",
        json_body={
            "client_id": get_settings().shopify_client_id,
            "client_secret": _secret(),
            **body,
        },
    )
    if reply.status in (400, 401):
        raise ProviderError("AUTH_EXPIRED", status=reply.status)
    return _tokens(http.raise_for_status(reply).data)


async def exchange_code(shop: str, code: str) -> dict[str, Any]:
    return await _token_request(shop, {"code": code, "expiring": "1"})


async def refresh(shop: str, refresh_token: str) -> dict[str, Any]:
    return await _token_request(
        shop, {"grant_type": "refresh_token", "refresh_token": refresh_token}
    )


def needs_refresh(credentials: dict[str, Any]) -> bool:
    expires = credentials.get("expires_at")
    if not expires or not credentials.get("refresh_token"):
        return False
    return datetime.fromisoformat(expires) - utc_now() < timedelta(minutes=2)


async def graphql(
    shop: str, credentials: dict[str, Any], query: str, variables: dict[str, Any] | None = None
) -> dict[str, Any]:
    reply = await http.send(
        "POST",
        f"https://{shop}/admin/api/{get_settings().shopify_api_version}/graphql.json",
        headers={"X-Shopify-Access-Token": credentials["access_token"]},
        json_body={"query": query, "variables": variables or {}},
    )
    data = http.raise_for_status(reply).data
    if not isinstance(data, dict):
        raise ProviderError("MALFORMED_RESPONSE")
    for error in data.get("errors") or []:
        code = ((error or {}).get("extensions") or {}).get("code")
        if code == "THROTTLED":
            raise ProviderError("PROVIDER_RATE_LIMITED")
        if code == "ACCESS_DENIED":
            raise ProviderError("PERMISSION_MISSING")
        raise ProviderError("PROVIDER_REJECTED")
    if not isinstance(data.get("data"), dict):
        raise ProviderError("MALFORMED_RESPONSE")
    return data["data"]


async def shop_info(shop: str, credentials: dict[str, Any]) -> dict[str, Any]:
    data = (await graphql(shop, credentials, SHOP_QUERY)).get("shop") or {}
    if (data.get("myshopifyDomain") or "").lower() != shop:
        raise ProviderError("ACCOUNT_MISMATCH")
    return data


async def register_webhooks(shop: str, credentials: dict[str, Any], uri: str) -> list[str]:
    existing = (await graphql(shop, credentials, SUBSCRIPTIONS)).get("webhookSubscriptions") or {}
    have = {
        node["topic"]: node["id"]
        for node in existing.get("nodes") or []
        if node.get("uri") == uri and node.get("topic")
    }
    ids = []
    for topic in WEBHOOK_TOPICS:
        if topic in have:
            ids.append(have[topic])
            continue
        result = (
            await graphql(shop, credentials, SUBSCRIBE, {"topic": topic, "sub": {"uri": uri}})
        ).get("webhookSubscriptionCreate") or {}
        created = result.get("webhookSubscription") or {}
        if result.get("userErrors") or not created.get("id"):
            raise ProviderError("WEBHOOK_REGISTRATION_FAILED")
        ids.append(created["id"])
    return ids


async def webhooks_active(shop: str, credentials: dict[str, Any], uri: str) -> bool:
    existing = (await graphql(shop, credentials, SUBSCRIPTIONS)).get("webhookSubscriptions") or {}
    topics = {node.get("topic") for node in existing.get("nodes") or [] if node.get("uri") == uri}
    return set(WEBHOOK_TOPICS) <= topics


async def unregister_webhooks(shop: str, credentials: dict[str, Any], ids: list[str]) -> None:
    for subscription_id in ids:
        await graphql(shop, credentials, UNSUBSCRIBE, {"id": subscription_id})


def _date(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def search(since: datetime, until: datetime, *, updated: bool, created_from: datetime) -> str:
    field = "updated_at" if updated else "created_at"
    terms = [f"{field}:>='{_date(since)}'", f"{field}:<='{_date(until)}'"]
    if updated:
        terms.append(f"created_at:>='{_date(created_from)}'")
    return " AND ".join(terms)


async def orders_page(
    shop: str, credentials: dict[str, Any], query: str, cursor: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    data = (
        await graphql(
            shop, credentials, ORDERS_QUERY, {"first": PAGE_SIZE, "after": cursor, "query": query}
        )
    ).get("orders") or {}
    info = data.get("pageInfo") or {}
    return list(data.get("nodes") or []), (
        info.get("endCursor") if info.get("hasNextPage") else None
    )


async def fetch_order(shop: str, credentials: dict[str, Any], order_id: str) -> dict[str, Any]:
    if not order_id.isdigit():
        raise ProviderError("MALFORMED_PAYLOAD")
    node = (
        await graphql(shop, credentials, ORDER_QUERY, {"id": f"gid://shopify/Order/{order_id}"})
    ).get("order")
    if not node:
        raise ProviderError("NOT_FOUND_AT_PROVIDER")
    return node


def webhook_order(body: dict[str, Any]) -> tuple[str, str | None]:
    """(order id, hint) from a verified REST-shaped webhook body."""
    order_id = body.get("id")
    if not isinstance(order_id, int) or order_id <= 0:
        raise ProviderError("MALFORMED_PAYLOAD")
    return str(order_id), "cancelled" if body.get("cancelled_at") else None


def _money(node: dict[str, Any] | None) -> Any:
    return ((node or {}).get("shopMoney") or {}).get("amount")


def external_id(node: dict[str, Any]) -> str:
    value = str(node.get("legacyResourceId") or "")
    if not value.isdigit():
        raise ProviderError("MALFORMED_PAYLOAD")
    return value


def is_cancelled(node: dict[str, Any]) -> bool:
    return bool(node.get("cancelledAt"))


def to_native(node: dict[str, Any]) -> dict[str, Any]:
    """A GraphQL order as the V2 native order payload."""
    if node.get("cancelledAt"):
        raise Skip("ORDER_CANCELLED_AT_PROVIDER")
    if node.get("currencyCode") != "BDT":
        raise Reject("CURRENCY_NOT_BDT")
    ship = node.get("shippingAddress") or {}
    bill = node.get("billingAddress") or {}
    phone = node.get("phone") or ship.get("phone") or bill.get("phone")
    if not phone:
        raise Reject("MISSING_PHONE")
    items = []
    for line in (node.get("lineItems") or {}).get("nodes") or []:
        quantity = int(line.get("currentQuantity") or 0)
        if quantity <= 0:
            continue
        variant = line.get("variantTitle")
        items.append(
            {
                "name": clip(line.get("title"), 200) or "Shopify item",
                "quantity": min(quantity, 9999),
                "unit_price_paisa": paisa(
                    _money(line.get("discountedUnitPriceAfterAllDiscountsSet"))
                ),
                "variant_label": clip(variant, 120) if variant != "Default Title" else None,
            }
        )
    if not items:
        raise Reject("NO_ITEMS")
    name = (
        ship.get("name")
        or bill.get("name")
        or " ".join(filter(None, [ship.get("firstName"), ship.get("lastName")]))
    )
    address = ", ".join(
        filter(None, (ship.get(k) for k in ("address1", "address2", "city", "province", "zip")))
    )
    note = f"Shopify {node.get('name') or ''}".strip()
    if node.get("note"):
        note += "\n" + str(node["note"])
    return {
        "phone": str(phone)[:24],
        "customer_name": clip(name, 160),
        "address": clip(address, 1000),
        "district": clip(ship.get("city"), 80),
        "note": note[:4000],
        "items": items,
        "delivery_fee_paisa": paisa(_money(node.get("totalShippingPriceSet"))),
        # What the courier must still collect: zero for a prepaid order.
        "cod_amount_paisa": paisa(_money(node.get("totalOutstandingSet"))),
    }
