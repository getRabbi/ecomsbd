"""A stand-in for the provider side of the Integrations Hub.

Replaces ``app.integrations.http.send`` and answers with the shapes Shopify's
GraphQL Admin API, WooCommerce REST v3 and the Graph API document. Every call
is recorded so tests can assert what was, and was not, sent.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from typing import Any
from urllib.parse import urlencode

from pydantic import SecretStr

from app.integrations import http
from app.integrations.http import Reply
from tests.test_auth_flow import auth_header

SHOPIFY_SECRET = "shpss_test_secret"


def shopify_node(number: int, **overrides: Any) -> dict[str, Any]:
    node = {
        "id": f"gid://shopify/Order/{number}",
        "legacyResourceId": str(number),
        "name": f"#{1000 + number}",
        "createdAt": "2026-09-20T10:00:00Z",
        "updatedAt": "2026-09-20T10:00:00Z",
        "cancelledAt": None,
        "test": False,
        "displayFinancialStatus": "PENDING",
        "note": None,
        "phone": "+8801712345678",
        "currencyCode": "BDT",
        "shippingAddress": {
            "name": "Rahim Uddin",
            "firstName": "Rahim",
            "lastName": "Uddin",
            "address1": "House 1, Road 2",
            "address2": None,
            "city": "Dhaka",
            "province": None,
            "zip": "1207",
            "phone": None,
        },
        "billingAddress": None,
        "totalOutstandingSet": {"shopMoney": {"amount": "1060.00", "currencyCode": "BDT"}},
        "totalShippingPriceSet": {"shopMoney": {"amount": "60.00"}},
        "lineItems": {
            "nodes": [
                {
                    "title": "Panjabi",
                    "variantTitle": "L",
                    "sku": "P-L",
                    "currentQuantity": 2,
                    "discountedUnitPriceAfterAllDiscountsSet": {"shopMoney": {"amount": "500.00"}},
                }
            ]
        },
    }
    return {**node, **overrides}


def woo_order(number: int, **overrides: Any) -> dict[str, Any]:
    order = {
        "id": number,
        "number": str(number),
        "status": "processing",
        "currency": "BDT",
        "total": "1060.00",
        "shipping_total": "60.00",
        "payment_method": "cod",
        "date_paid": "2026-09-20T10:00:00",
        "customer_note": "",
        "billing": {
            "first_name": "Karim",
            "last_name": "Mia",
            "phone": "01812345678",
            "address_1": "Road 5",
            "address_2": "",
            "city": "Chattogram",
            "state": "BD-10",
            "postcode": "4000",
        },
        "shipping": {"first_name": "", "last_name": "", "address_1": "", "city": ""},
        "line_items": [{"name": "Saree", "quantity": 3, "total": "1000.00"}],
    }
    return {**order, **overrides}


class FakeProviders:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.shopify_orders: dict[str, dict[str, Any]] = {}
        self.shopify_pages: list[list[dict[str, Any]]] = []
        self.woo_orders: dict[int, dict[str, Any]] = {}
        self.woo_webhook_status = "active"
        self.fail: dict[str, int] = {}  # substring of URL/query -> HTTP status
        self.subscriptions: list[dict[str, Any]] = []
        self.currency = "BDT"
        self.token_counter = 0

    def _failure(self, key: str) -> Reply | None:
        for needle, status in self.fail.items():
            if needle in key:
                return Reply(status, {"errors": "nope"}, {})
        return None

    async def send(self, method: str, url: str, **kw: Any) -> Reply:
        self.calls.append({"method": method, "url": url, **kw})
        body = kw.get("json_body") or {}
        key = url + " " + json.dumps(body, default=str) + " " + json.dumps(kw.get("params") or {})
        failed = self._failure(key)
        if failed:
            return failed
        if url.endswith("/admin/oauth/access_token"):
            self.token_counter += 1
            return Reply(
                200,
                {
                    "access_token": f"shpat_{self.token_counter}",
                    "scope": "read_orders",
                    "expires_in": 3600,
                    "refresh_token": f"shprt_{self.token_counter}",
                    "refresh_token_expires_in": 7776000,
                },
                {},
            )
        if "/graphql.json" in url:
            return Reply(200, self._graphql(url, body), {})
        if "/wp-json/wc/v3" in url:
            return self._woo(method, url, kw.get("params") or {}, body)
        if "graph.facebook.com" in url:
            return Reply(400, {"error": {"code": 190}}, {})
        return Reply(404, None, {})

    def _graphql(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        query, variables = body["query"], body.get("variables") or {}
        shop = url.split("/")[2]
        if "shop {" in query:
            return {
                "data": {
                    "shop": {
                        "name": "Test Store",
                        "myshopifyDomain": shop,
                        "currencyCode": self.currency,
                    }
                }
            }
        if "webhookSubscriptionCreate" in query:
            sub = {
                "id": f"gid://shopify/WebhookSubscription/{len(self.subscriptions) + 1}",
                "topic": variables["topic"],
                "uri": variables["sub"]["uri"],
            }
            self.subscriptions.append(sub)
            return {
                "data": {
                    "webhookSubscriptionCreate": {
                        "webhookSubscription": {"id": sub["id"]},
                        "userErrors": [],
                    }
                }
            }
        if "webhookSubscriptionDelete" in query:
            self.subscriptions = [s for s in self.subscriptions if s["id"] != variables["id"]]
            return {
                "data": {
                    "webhookSubscriptionDelete": {
                        "deletedWebhookSubscriptionId": variables["id"],
                        "userErrors": [],
                    }
                }
            }
        if "webhookSubscriptions" in query:
            return {"data": {"webhookSubscriptions": {"nodes": self.subscriptions}}}
        if "query Orders" in query:
            index = int(variables.get("after") or 0)
            page = self.shopify_pages[index] if index < len(self.shopify_pages) else []
            more = index + 1 < len(self.shopify_pages)
            return {
                "data": {
                    "orders": {
                        "nodes": page,
                        "pageInfo": {
                            "hasNextPage": more,
                            "endCursor": str(index + 1) if more else None,
                        },
                    }
                }
            }
        if "query Order(" in query:
            number = variables["id"].rsplit("/", 1)[-1]
            return {"data": {"order": self.shopify_orders.get(number)}}
        return {"errors": [{"message": "unknown"}]}

    def _woo(self, method: str, url: str, params: dict[str, Any], body: dict[str, Any]) -> Reply:
        path = url.split("/wp-json/wc/v3", 1)[1]
        if method == "GET" and path == "/orders":
            ordered = sorted(self.woo_orders.values(), key=lambda o: o["id"])
            per_page, page = int(params.get("per_page", 10)), int(params.get("page", 1))
            chunk = ordered[(page - 1) * per_page : page * per_page]
            pages = max(1, -(-len(ordered) // per_page))
            return Reply(
                200, chunk, {"x-wp-total": str(len(ordered)), "x-wp-totalpages": str(pages)}
            )
        if method == "GET" and path.startswith("/orders/"):
            order = self.woo_orders.get(int(path.rsplit("/", 1)[-1]))
            return (
                Reply(200, order, {})
                if order
                else Reply(404, {"code": "woocommerce_rest_shop_order_invalid_id"}, {})
            )
        if method == "POST" and path == "/webhooks":
            return Reply(201, {"id": 100 + len(self.calls), "status": "active", **body}, {})
        if method == "GET" and path.startswith("/webhooks/"):
            return Reply(
                200, {"id": int(path.rsplit("/", 1)[-1]), "status": self.woo_webhook_status}, {}
            )
        if method == "DELETE":
            return Reply(200, {}, {})
        return Reply(404, None, {})


def live_settings():
    """The object the app reads now. Another suite may have reset the cache,
    so the session ``settings`` fixture is not necessarily it."""
    from app.core.config import get_settings

    return get_settings()


def enable_shopify(monkeypatch, _settings=None, *, https: bool = True) -> None:
    current = live_settings()
    monkeypatch.setattr(current, "shopify_client_id", "client-123")
    monkeypatch.setattr(current, "shopify_client_secret", SecretStr(SHOPIFY_SECRET))
    if https:
        monkeypatch.setattr(current, "public_base_url", "https://api.ecomsbd.test")


def install(monkeypatch) -> FakeProviders:
    fake = FakeProviders()
    monkeypatch.setattr(http, "send", fake.send)
    return fake


def shopify_hmac(body: bytes) -> str:
    return base64.b64encode(
        hmac.new(SHOPIFY_SECRET.encode(), body, hashlib.sha256).digest()
    ).decode()


def woo_hmac(secret: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def signed_callback(params: dict[str, str]) -> str:
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    digest = hmac.new(SHOPIFY_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hmac": digest})


def unique_shop() -> str:
    return f"store-{uuid.uuid4().hex[:10]}.myshopify.com"


async def connect_shopify(client, shop_session, shop_domain: str) -> dict[str, Any]:
    """Create a connection and complete OAuth through the real callback route."""
    headers = auth_header(shop_session)
    created = await client.post(
        "/v1/integrations", headers=headers, json={"provider": "SHOPIFY", "name": "My Shopify"}
    )
    assert created.status_code == 201, created.text
    connection_id = created.json()["connection"]["id"]
    started = await client.post(
        f"/v1/integrations/{connection_id}/connect", headers=headers, json={"shop": shop_domain}
    )
    assert started.status_code == 200, started.text
    url = started.json()["authorize_url"]
    assert url.startswith(f"https://{shop_domain}/admin/oauth/authorize?")
    state = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))["state"]
    query = signed_callback(
        {"code": "auth-code", "shop": shop_domain, "state": state, "timestamp": "1790000000"}
    )
    callback = await client.get(f"/v1/integration-callbacks/shopify?{query}")
    assert callback.status_code == 200, callback.text
    assert callback.json()["result"] == "CONNECTED", callback.text
    return {"id": connection_id, "state": state, "query": query}


async def webhook_token(connection_id: str) -> str:
    from app.db.session import system_session
    from app.integrations.models import IntegrationConnection

    async with system_session("test: webhook token") as db:
        row = await db.get(IntegrationConnection, uuid.UUID(connection_id))
        return row.webhook_token
