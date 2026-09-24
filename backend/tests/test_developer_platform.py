"""V3.8: developer portal, key lifecycle, webhook tools, single health model, SDK contract."""

from __future__ import annotations

import importlib.util
import json
import re
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.common import cache
from app.common.audit import AuditLog
from app.core.clock import utc_now
from app.db.session import system_session
from app.main import app as fastapi_app
from app.orders.models import Order
from app.public_api import webhooks
from app.public_api.models import ApiKey, WebhookEndpoint
from app.tenants.models import TenantUser
from tests.conftest_commerce import signed_in_shop
from tests.test_auth_flow import auth_header

SDK = Path(__file__).resolve().parents[2] / "sdk"


def _sdk():
    spec = importlib.util.spec_from_file_location("ecomsbd_sdk", SDK / "python" / "ecomsbd.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _key(client, shop, scopes, **extra) -> dict:
    response = await client.post(
        "/v1/developers/keys",
        headers=auth_header(shop),
        json={"name": "Site", "scopes": scopes, **extra},
    )
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def _bearer(key: dict) -> dict:
    return {"Authorization": "Bearer " + key["key"], "Idempotency-Key": str(uuid.uuid4())}


async def _audits(shop, prefix: str) -> list[AuditLog]:
    async with system_session("test: audits") as db:
        return list(
            (
                await db.scalars(
                    sa.select(AuditLog).where(
                        AuditLog.tenant_id == uuid.UUID(shop["tenant_id"]),
                        AuditLog.action.like(prefix + "%"),
                    )
                )
            ).all()
        )


async def _role(shop, role: str) -> None:
    async with system_session("test: role") as db:
        member = await db.scalar(
            sa.select(TenantUser).where(TenantUser.tenant_id == uuid.UUID(shop["tenant_id"]))
        )
        member.role = role


# ------------------------------------------------------------------- keys ---


async def test_key_is_shown_once_and_me_needs_no_scope(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    key = await _key(client, shop, ["orders:read"])
    listing = await client.get("/v1/developers/keys", headers=auth_header(shop))
    assert key["key"] not in listing.text
    [row] = listing.json()["items"]
    assert row["state"] == "ACTIVE" and row["last_used_at"] is None
    me = await client.get("/public/v1/me", headers=_bearer(key))
    assert me.status_code == 200
    assert me.json() == {
        "key_id": key["id"],
        "shop_id": shop["tenant_id"],
        "scopes": ["orders:read"],
        "rate_limit_per_minute": 60,
        "expires_at": None,
    }
    # Scopes are enforced by the API, not the portal.
    assert (
        await client.post(
            "/public/v1/customers", headers=_bearer(key), json={"phone": "01712345678"}
        )
    ).status_code == 403
    after = (await client.get("/v1/developers/keys", headers=auth_header(shop))).json()
    assert after["items"][0]["last_used_at"] is not None


async def test_rotation_revocation_and_expiry_are_enforced_and_audited(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    key = await _key(client, shop, ["orders:read"], expires_in_days=30)
    assert key["expires_at"] is not None
    rotated = await client.post(
        f"/v1/developers/keys/{key['id']}/rotate", headers=auth_header(shop)
    )
    assert rotated.status_code == 201 and rotated.headers["cache-control"] == "no-store"
    new = rotated.json()
    assert new["key"] != key["key"] and new["replaces"] == key["id"]
    assert new["scopes"] == key["scopes"] and new["expires_at"] == key["expires_at"]
    assert (await client.get("/public/v1/me", headers=_bearer(key))).status_code == 401
    assert (await client.get("/public/v1/me", headers=_bearer(new))).status_code == 200
    again = await client.post(f"/v1/developers/keys/{key['id']}/rotate", headers=auth_header(shop))
    assert again.status_code == 409

    async with system_session("test: expire key") as db:
        row = await db.get(ApiKey, uuid.UUID(new["id"]))
        row.expires_at = utc_now() - timedelta(minutes=1)
    expired = await client.get("/public/v1/me", headers=_bearer(new))
    assert expired.status_code == 401
    states = {
        k["id"]: k["state"]
        for k in (await client.get("/v1/developers/keys", headers=auth_header(shop))).json()[
            "items"
        ]
    }
    assert states == {key["id"]: "REVOKED", new["id"]: "EXPIRED"}

    third = await _key(client, shop, ["orders:read"])
    assert (
        await client.delete(f"/v1/developers/keys/{third['id']}", headers=auth_header(shop))
    ).json()["state"] == "REVOKED"
    assert (await client.get("/public/v1/me", headers=_bearer(third))).status_code == 401

    actions = [a.action for a in await _audits(shop, "developer.api_key")]
    assert actions.count("developer.api_key_created") == 3
    assert "developer.api_key_rotated" in actions
    assert actions.count("developer.api_key_revoked") == 2
    for audit in await _audits(shop, "developer."):
        for token in (key["key"], new["key"], third["key"]):
            assert token not in json.dumps(audit.context)


async def test_rate_limit_and_idempotency(client, unique_phone, monkeypatch):
    # One fixed window for the whole test: the limiter's windows are wall-clock
    # minutes, and a request pair straddling a minute boundary resets the count.
    monkeypatch.setattr(
        cache.RateLimiter,
        "_key",
        staticmethod(lambda scope, identity, window_seconds: f"rl:{scope}:{identity}:test"),
    )
    shop = await signed_in_shop(client, unique_phone)
    key = await _key(client, shop, ["customers:write"], rate_limit=2)
    headers = _bearer(key)
    first = await client.post(
        "/public/v1/customers", headers=headers, json={"phone": "01712345678"}
    )
    same = await client.post("/public/v1/customers", headers=headers, json={"phone": "01712345678"})
    assert first.status_code == 201 and same.json() == first.json()
    limited = await client.post(
        "/public/v1/customers", headers=_bearer(key), json={"phone": "01712345679"}
    )
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 1

    other = await _key(client, shop, ["customers:write"])
    body_changed = _bearer(other)
    await client.post("/public/v1/customers", headers=body_changed, json={"phone": "01812345678"})
    conflict = await client.post(
        "/public/v1/customers", headers=body_changed, json={"phone": "01812345679"}
    )
    assert conflict.status_code == 409
    history = (
        await client.get(
            "/v1/developers/requests", headers=auth_header(shop), params={"key_id": other["id"]}
        )
    ).json()["items"]
    assert [h["endpoint"] for h in history] == ["customers.create"]
    assert "01812345678" not in json.dumps(history)


async def test_key_check_never_echoes_and_is_shop_scoped(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    key = await _key(client, shop, ["orders:read"])
    other = await signed_in_shop(client, "018" + unique_phone[3:])
    theirs = await _key(client, other, ["orders:read"])
    headers = auth_header(shop)
    ok = await client.post("/v1/developers/keys/check", headers=headers, json={"key": key["key"]})
    assert ok.json()["valid"] is True and ok.json()["key_id"] == key["id"]
    assert key["key"] not in ok.text
    foreign = await client.post(
        "/v1/developers/keys/check", headers=headers, json={"key": theirs["key"]}
    )
    assert foreign.json() == {"valid": False, "state": "UNKNOWN"}
    junk = await client.post(
        "/v1/developers/keys/check", headers=headers, json={"key": "not-a-key-at-all"}
    )
    assert junk.json() == {"valid": False, "state": "UNKNOWN"}
    # Other shops cannot touch this shop's keys.
    assert (
        await client.post(f"/v1/developers/keys/{key['id']}/rotate", headers=auth_header(other))
    ).status_code == 404
    assert (
        await client.delete(f"/v1/developers/keys/{key['id']}", headers=auth_header(other))
    ).status_code == 404


async def test_developer_portal_is_owner_only(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    await _role(shop, "MANAGER")
    for method, path in (
        ("GET", "/v1/developers/keys"),
        ("GET", "/v1/developers/health"),
        ("GET", "/v1/developers/samples"),
        ("GET", "/v1/developers/requests"),
    ):
        assert (await client.request(method, path, headers=auth_header(shop))).status_code == 403


# --------------------------------------------------------------- webhooks ---


async def test_webhook_topics_secret_rotation_and_audit(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    created = await client.post(
        "/v1/developers/webhooks",
        headers=headers,
        json={"url": "https://hooks.example.com/ecomsbd", "topics": ["order.created"]},
    )
    assert created.status_code == 201
    hook = created.json()
    bad = await client.patch(
        f"/v1/developers/webhooks/{hook['id']}", headers=headers, json={"topics": ["nope"]}
    )
    assert bad.status_code == 422
    updated = await client.patch(
        f"/v1/developers/webhooks/{hook['id']}",
        headers=headers,
        json={"topics": ["order.delivered", "tracking.assigned"]},
    )
    assert updated.json()["topics"] == ["order.delivered", "tracking.assigned"]
    rotated = await client.post(
        f"/v1/developers/webhooks/{hook['id']}/rotate-secret", headers=headers
    )
    assert rotated.status_code == 201 and rotated.headers["cache-control"] == "no-store"
    secret = rotated.json()["signing_secret"]
    assert secret != hook["signing_secret"]
    listing = await client.get("/v1/developers/webhooks", headers=headers)
    assert secret not in listing.text and hook["signing_secret"] not in listing.text
    async with system_session("test: stored secret") as db:
        row = await db.get(WebhookEndpoint, uuid.UUID(hook["id"]))
        assert secret not in row.secret_enc
    test = await client.post(f"/v1/developers/webhooks/{hook['id']}/test", headers=headers)
    assert test.json()["creates_data"] is False
    actions = {a.action for a in await _audits(shop, "developer.webhook")}
    assert actions == {
        "developer.webhook_created",
        "developer.webhook_updated",
        "developer.webhook_secret_rotated",
    }
    for audit in await _audits(shop, "developer."):
        assert secret not in json.dumps(audit.context)
    pending = (
        await client.get("/v1/developers/deliveries", headers=headers, params={"status": "PENDING"})
    ).json()["items"]
    assert [d["topic"] for d in pending] == ["webhook.test"]
    assert (
        await client.get("/v1/developers/deliveries", headers=headers, params={"status": "FAILED"})
    ).json()["items"] == []


def test_signature_rejects_tampering_and_replay():
    body = b'{"id":"e1","type":"order.delivered"}'
    header = webhooks.signature("placeholder-signing-secret", body, 1000)
    assert header == "t=1000,v1=af66d2bcbfc2077710d11feb3d1614439fdc6e2bd0b3d3cc75e568991a37d6bf"
    assert webhooks.verify_signature("placeholder-signing-secret", body, header, now=1000)
    assert not webhooks.verify_signature(
        "placeholder-signing-secret", body + b" ", header, now=1000
    )
    assert not webhooks.verify_signature("placeholder-signing-secret", body, header, now=1301)


async def test_samples_are_placeholders_and_verifiable(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    samples = (await client.get("/v1/developers/samples", headers=auth_header(shop))).json()
    example = samples["signature_example"]
    assert webhooks.verify_signature(
        example["secret"], example["body"].encode(), example["header"], now=example["timestamp"]
    )
    assert set(samples["events"]) == set(webhooks.TOPICS)
    for event in samples["events"].values():
        assert set(event["data"]) <= webhooks.DATA_FIELDS
        assert "phone" not in json.dumps(event)


# ------------------------------------------------ health and custom website ---


async def test_health_reuses_the_integrations_hub_truth(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    website = (
        await client.post(
            "/v1/integrations",
            headers=headers,
            json={"provider": "CUSTOM_WEBSITE", "name": "My website"},
        )
    ).json()
    health = (await client.get("/v1/developers/health", headers=headers)).json()
    assert health["api"]["state"] == "ACTIVE" and health["api"]["active_keys"] == 1
    hub = (await client.get("/v1/integrations", headers=headers)).json()["items"]
    assert health["connections"] == [
        item for item in hub if item["id"] == website["connection"]["id"]
    ]
    # Rotating the website's key from the portal keeps the connection healthy.
    key_id = website["connection"]["id"]
    keys = (await client.get("/v1/developers/keys", headers=headers)).json()["items"]
    [site_key] = [k for k in keys if k["connection_id"] == key_id]
    await client.post(f"/v1/developers/keys/{site_key['id']}/rotate", headers=headers)
    hub_after = (await client.get("/v1/integrations", headers=headers)).json()["items"]
    [conn] = [item for item in hub_after if item["id"] == key_id]
    assert conn["health"] != "AUTH_EXPIRED"


async def test_custom_website_test_order_creates_nothing(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    website = (
        await client.post(
            "/v1/integrations",
            headers=headers,
            json={"provider": "CUSTOM_WEBSITE", "name": "My website"},
        )
    ).json()
    path = f"/v1/integrations/{website['connection']['id']}/test-order"

    async def orders() -> int:
        async with system_session("test: count orders") as db:
            return int(
                await db.scalar(
                    sa.select(sa.func.count(Order.id)).where(
                        Order.tenant_id == uuid.UUID(shop["tenant_id"])
                    )
                )
            )

    sample = (await client.post(path, headers=headers, json={})).json()
    assert sample["valid"] is True and sample["creates_data"] is False
    assert sample["normalized"]["phone_masked"] == "01700****00"
    bad = (
        await client.post(
            path,
            headers=headers,
            json={
                "payload": {
                    "phone": "12345678",
                    "items": [{"product_id": str(uuid.uuid4()), "quantity": 1}],
                }
            },
        )
    ).json()
    assert bad["valid"] is False
    assert {p["code"] for p in bad["problems"]} == {"INVALID_PHONE", "UNKNOWN_PRODUCT"}
    invalid = await client.post(path, headers=headers, json={"payload": {"items": []}})
    assert invalid.status_code == 422
    assert await orders() == 0


# ------------------------------------------------------------ SDK contract ---


def test_python_sdk_signs_like_the_api_and_rejects_replays():
    sdk = _sdk()
    body = b'{"id":"e1","type":"order.delivered"}'
    header = webhooks.signature("s3cret-placeholder", body, 5000)
    assert sdk.sign("s3cret-placeholder", body, 5000) == header
    assert sdk.verify_webhook("s3cret-placeholder", body, header, now=5000)
    assert not sdk.verify_webhook("s3cret-placeholder", body, header, now=5301)
    assert not sdk.verify_webhook("s3cret-placeholder", b"{}", header, now=5000)
    assert not sdk.verify_webhook("s3cret-placeholder", body, "garbage", now=5000)
    guard = sdk.ReplayGuard()
    assert guard.first_time("e1") and not guard.first_time("e1")


def _routes_in(text: str) -> set[tuple[str, str]]:
    return set(re.findall(r"\[\s*'(GET|POST)',\s*'(/[^']*)'\s*\]", text))


@pytest.mark.parametrize("language", ["python", "js", "php"])
def test_every_sdk_route_exists_in_the_public_api(language):
    api = {
        (method.upper(), path.removeprefix("/public/v1"))
        for path, operations in fastapi_app.openapi()["paths"].items()
        if path.startswith("/public/v1")
        for method in operations
    }
    if language == "python":
        wanted = set(_sdk().ROUTES)
    elif language == "js":
        wanted = _routes_in((SDK / "js" / "ecomsbd.mjs").read_text(encoding="utf-8"))
    else:
        wanted = _routes_in((SDK / "php" / "Ecomsbd.php").read_text(encoding="utf-8"))
    assert len(wanted) == 11
    assert wanted <= api, wanted - api
