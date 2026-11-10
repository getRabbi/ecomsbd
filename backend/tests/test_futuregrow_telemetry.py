"""Fixture-only telemetry boundaries; no live relay or provider calls."""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from starlette.requests import Request

from app.api.v1.telemetry import Consent, bound_payload, get_consent, relay, set_consent
from app.common.cache import InMemoryBackend


def test_install_binding_and_identity_validation():
    body = {
        "kind": "install",
        "appId": "com.ecomsbd.app",
        "platform": "android",
        "installId": "installation-123",
        "accountBinding": "user-a:shop-a",
        "userId": "untrusted",
        "consent": {"analytics": True},
    }
    path, first = bound_payload(body, "user-a:shop-a")
    _, second = bound_payload({**body, "accountBinding": "user-b:shop-a"}, "user-b:shop-a")
    assert path == "app-installs"
    assert "userId" not in first
    assert first["installId"] != second["installId"]
    assert bound_payload(body, "user-a:shop-a")[1] == first
    for changes in (
        {"appId": "another.app"},
        {"consent": {"analytics": False}},
        {"platform": "web"},
        {"kind": "arbitrary"},
        {"accountBinding": "wrong-account"},
    ):
        with pytest.raises(HTTPException):
            bound_payload({**body, **changes}, "user-a:shop-a")


@pytest.mark.asyncio
async def test_backend_consent_defaults_denied_and_is_scoped(monkeypatch):
    cache = InMemoryBackend()
    monkeypatch.setattr("app.api.v1.telemetry.get_cache", lambda: cache)
    shop = uuid.uuid4()
    first = SimpleNamespace(user_id=uuid.uuid4(), require_tenant=lambda: shop)
    second = SimpleNamespace(user_id=uuid.uuid4(), require_tenant=lambda: shop)
    assert await get_consent(first) == {"analytics": False}
    await set_consent(Consent(analytics=True), first)
    assert await get_consent(first) == {"analytics": True}
    assert await get_consent(second) == {"analytics": False}
    await set_consent(Consent(analytics=False), first)
    assert await get_consent(first) == {"analytics": False}


@pytest.mark.asyncio
async def test_relay_requires_server_consent_and_preserves_retry_acknowledgements(monkeypatch):
    cache = InMemoryBackend()
    monkeypatch.setattr("app.api.v1.telemetry.get_cache", lambda: cache)
    monkeypatch.setattr("app.common.cache.get_cache", lambda: cache)
    principal = SimpleNamespace(
        user_id=uuid.uuid4(), require_tenant=lambda: uuid.UUID(int=1), role="OWNER"
    )
    settings = SimpleNamespace(
        futuregrow_base_url="https://futuregrow.example",
        futuregrow_server_key=SecretStr("fgk_sk_fixture"),
    )
    payload = {
        "kind": "install",
        "appId": "com.ecomsbd.app",
        "platform": "android",
        "installId": "install-12345678",
        "accountBinding": f"{principal.user_id}:{principal.require_tenant()}",
        "installedAt": "2026-11-10T00:00:00Z",
        "consent": {"analytics": True},
    }

    def request(body):
        async def receive():
            return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}

        return Request(
            {"type": "http", "method": "POST", "path": "/telemetry", "headers": []}, receive
        )

    with pytest.raises(HTTPException) as denied:
        await relay(request(payload), principal, settings, None)
    assert denied.value.status_code == 403
    await set_consent(Consent(analytics=True), principal)
    seen = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        @asynccontextmanager
        async def stream(self, method, url, **kwargs):
            seen.append((method, url, kwargs))

            async def chunks():
                yield b'{"recorded":true}'

            yield SimpleNamespace(status_code=202, aiter_bytes=chunks)

    monkeypatch.setattr("app.api.v1.telemetry.httpx.AsyncClient", Client)
    response = await relay(request(payload), principal, settings, None)
    assert response.status_code == 202
    assert seen[0][1] == "https://futuregrow.example/ingest/v1/app-installs"
    assert seen[0][2]["headers"]["authorization"] == "Bearer fgk_sk_fixture"
    assert seen[0][2]["json"]["installId"] != payload["installId"]
    transaction = SimpleNamespace(
        id=uuid.uuid4(),
        state="VERIFIED",
        kind="PURCHASE",
        amount_paisa=12345,
        currency="BDT",
        metadata_json={},
        occurred_at=datetime(2026, 11, 10, 1, tzinfo=UTC),
    )

    class Database:
        async def scalar(self, _):
            return transaction

        async def commit(self):
            pass

    conversion = {
        **payload,
        "kind": "event",
        "event": "conversion",
        "eventId": f"conversion_{str(transaction.id).upper()}",
        "occurredAt": "2026-11-10T02:00:00Z",
        "conversion": {"name": "subscription_paid", "valueMicros": 123450000, "currency": "BDT"},
    }
    conversion.pop("installedAt")
    await relay(request(conversion), principal, settings, Database())
    normalized = seen[-1][2]["json"]
    assert normalized["eventId"] == f"conversion_{transaction.id}"
    assert normalized["occurredAt"] == "2026-11-10T01:00:00+00:00"
    assert transaction.metadata_json["futuregrow_install"] == normalized["installId"]
    await relay(
        request({**conversion, "occurredAt": "2026-11-10T03:00:00Z"}),
        principal,
        settings,
        Database(),
    )
    assert seen[-1][2]["json"] == normalized
    transaction.state = "PENDING"
    with pytest.raises(HTTPException) as unverified:
        await relay(request(conversion), principal, settings, Database())
    assert unverified.value.status_code == 422
    settings.futuregrow_server_key = None
    with pytest.raises(HTTPException) as missing:
        await relay(request(payload), principal, settings, None)
    assert missing.value.status_code == 503
