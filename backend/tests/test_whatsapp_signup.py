"""Embedded Signup over HTTP, real tenancy/vault and deterministic Meta responses."""

import json
import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import SecretStr

from app.core.clock import utc_now
from app.core.config import AppEnv
from app.core.redaction import redact_text, redact_value
from app.db.session import system_session
from app.integrations import http, service
from app.integrations.models import IntegrationConnection
from app.tenants.models import TenantUser
from tests.conftest_commerce import signed_in_shop
from tests.integrations_fakes import live_settings
from tests.test_auth_flow import auth_header

ORIGIN = "https://scalemyprints.com"
CALLBACK = "/v1/integration-callbacks/whatsapp-signup"
TOKEN = "EAAG" + "private-provider-token" * 3
CODE = "one-time-authorization-code"
WABA = "9876543210"
PHONE = "1234567890"


class Graph:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.waba = WABA
        self.phone = PHONE
        self.app = "123456"
        self.status = "CONNECTED"
        self.verified = "VERIFIED"
        self.scopes = ["whatsapp_business_management", "whatsapp_business_messaging"]

    async def send(self, method, url, *, params=None, headers=None, json_body=None, **_):
        path = urlsplit(url).path.removeprefix("/v26.0")
        self.calls.append((method, path))
        if self.failure == path:
            return http.Reply(400, {"error": {"code": 190, "message": TOKEN + CODE}}, {})
        if path == "/oauth/access_token":
            assert params["code"] == CODE
            assert "redirect_uri" not in params and "grant_type" not in params
            return http.Reply(200, {"access_token": TOKEN}, {})
        if path == "/debug_token":
            return http.Reply(
                200,
                {
                    "data": {
                        "is_valid": True,
                        "app_id": self.app,
                        "scopes": self.scopes,
                        "granular_scopes": [
                            {"scope": s, "target_ids": [self.waba]} for s in self.scopes
                        ],
                    }
                },
                {},
            )
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        if path == f"/{WABA}":
            data = {"id": WABA, "name": "Shop"}
        elif path == f"/{WABA}/phone_numbers":
            data = {"data": [{"id": self.phone}]}
        elif path == f"/{PHONE}":
            data = {
                "id": PHONE,
                "status": self.status,
                "code_verification_status": self.verified,
                "display_phone_number": "+8801712345678",
                "verified_name": "Shop",
            }
        elif path == f"/{PHONE}/register":
            assert json_body["messaging_product"] == "whatsapp"
            assert len(json_body["pin"]) == 6
            self.status = "CONNECTED"
            data = {"success": True}
        elif path == f"/{WABA}/subscribed_apps":
            data = {"success": True}
        else:
            raise AssertionError(path)
        return http.Reply(200, data, {})


@pytest.fixture
async def setup(client, unique_phone, monkeypatch):
    monkeypatch.setitem(globals(), "PHONE", str(uuid.uuid4().int)[:12])
    monkeypatch.setitem(globals(), "WABA", str(uuid.uuid4().int)[:12])
    settings = live_settings()
    for key, value in {
        "meta_app_id": "123456",
        "meta_app_secret": SecretStr("private-app-secret"),
        "meta_webhook_verify_token": SecretStr("verify"),
        "public_web_url": ORIGIN,
        "meta_whatsapp_embedded_signup_config_id": "654321",
        "meta_whatsapp_public_signup_enabled": True,
    }.items():
        monkeypatch.setattr(settings, key, value)
    graph = Graph()
    monkeypatch.setattr(http, "send", graph.send)
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    created = await client.post(
        "/v1/integrations", headers=headers, json={"provider": "WHATSAPP", "name": "WhatsApp"}
    )
    assert created.status_code == 201, created.text
    return shop, headers, created.json()["connection"]["id"], graph


async def start(client, setup):
    _, headers, cid, _ = setup
    response = await client.post(
        f"/v1/integrations/{cid}/connect", headers=headers, json={"return_to": "app"}
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    url = urlsplit(response.json()["authorize_url"])
    assert url.query == "" and url.path == "/ecomsbd/whatsapp-connect"
    return parse_qs(url.fragment)["state"][0]


async def boot(client, setup):
    ticket = await start(client, setup)
    response = await client.post(
        f"{CALLBACK}/bootstrap", headers={"Origin": ORIGIN}, json={"signup_state": ticket}
    )
    assert response.status_code == 200, response.text
    return response.json(), ticket


def body(bootstrap):
    return {
        "browser_session": bootstrap["browser_session"],
        "connection_id": bootstrap["connection_id"],
        "authorization_code": CODE,
        "waba_id": WABA,
        "phone_number_id": PHONE,
    }


async def complete(client, bootstrap, **changes):
    return await client.post(
        f"{CALLBACK}/complete", headers={"Origin": ORIGIN}, json={**body(bootstrap), **changes}
    )


async def test_success_vault_health_replay_reconnect_disconnect(client, setup):
    _, headers, cid, graph = setup
    bootstrap, ticket = await boot(client, setup)
    assert bootstrap["config_id"] == "654321"
    first = await complete(client, bootstrap)
    assert first.status_code == 200, first.text
    assert first.json()["result"] == "CONNECTED"
    again = await complete(client, bootstrap)
    assert again.json() == first.json()
    assert graph.calls.count(("GET", "/oauth/access_token")) == 1
    assert (
        await complete(client, bootstrap, authorization_code="different-code")
    ).status_code == 409
    replay = await client.post(
        f"{CALLBACK}/bootstrap", headers={"Origin": ORIGIN}, json={"signup_state": ticket}
    )
    assert replay.status_code == 409
    detail = await client.get(f"/v1/integrations/{cid}", headers=headers)
    assert detail.json()["connection"]["state"] == "CONNECTED"
    assert detail.json()["connection"]["webhook_state"] == "ACTIVE"
    for secret in (TOKEN, CODE, ticket, bootstrap["browser_session"], "private-app-secret"):
        assert secret not in detail.text + first.text
    async with system_session("test signup vault") as db:
        conn = await db.get(IntegrationConnection, uuid.UUID(cid))
        assert TOKEN not in conn.credentials_enc
        assert service.unseal(conn)["access_token"] == TOKEN
        assert service.unseal(conn)["waba_id"] == WABA
    checked = await client.post(f"/v1/integrations/{cid}/test", headers=headers)
    assert checked.json()["ok"] is True
    graph.failure = f"/{PHONE}"
    expired = await client.post(f"/v1/integrations/{cid}/test", headers=headers)
    assert expired.json()["connection"]["state"] == "AUTH_EXPIRED"
    graph.failure = None
    renewed, _ = await boot(client, setup)
    assert (await complete(client, renewed)).json()["result"] == "CONNECTED"
    disconnected = await client.post(f"/v1/integrations/{cid}/disconnect", headers=headers)
    assert disconnected.status_code == 200
    assert (await complete(client, renewed)).status_code == 409
    assert not any(path.endswith("/deregister") for _, path in graph.calls)
    async with system_session("test disconnected vault") as db:
        conn = await db.get(IntegrationConnection, uuid.UUID(cid))
        assert conn.credentials_enc is None and conn.account_key is None


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("waba", "9999999999", "WABA_MISMATCH"),
        ("phone", "9999999999", "PHONE_NUMBER_MISMATCH"),
        ("app", "999999", "AUTH_EXPIRED"),
        ("verified", "NOT_VERIFIED", "PHONE_NOT_VERIFIED"),
        ("scopes", [], "PERMISSION_MISSING"),
        ("failure", "/oauth/access_token", "AUTH_EXPIRED"),
        ("failure", "SUBSCRIBE", "AUTH_EXPIRED"),
    ],
)
async def test_meta_failures_never_connect(client, setup, field, value, expected):
    bootstrap, _ = await boot(client, setup)
    setattr(setup[3], field, f"/{WABA}/subscribed_apps" if value == "SUBSCRIBE" else value)
    response = await complete(client, bootstrap)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == expected
    assert TOKEN not in response.text and CODE not in response.text
    detail = await client.get(f"/v1/integrations/{setup[2]}", headers=setup[1])
    assert detail.json()["connection"]["state"] == "PENDING"


async def test_register_new_number_and_resolve_without_browser_ids(client, setup):
    setup[3].status = "PENDING"
    bootstrap, _ = await boot(client, setup)
    response = await complete(client, bootstrap, waba_id=None, phone_number_id=None)
    assert response.json()["result"] == "CONNECTED", response.text
    assert ("POST", f"/{PHONE}/register") in setup[3].calls


@pytest.mark.parametrize("phase", ["bootstrap", "complete"])
async def test_expiry(client, setup, phase):
    if phase == "bootstrap":
        payload = {"signup_state": await start(client, setup)}
    else:
        bootstrap, _ = await boot(client, setup)
        payload = body(bootstrap)
    async with system_session("test expiry") as db:
        conn = await db.get(IntegrationConnection, uuid.UUID(setup[2]))
        conn.state_expires_at = utc_now() - timedelta(seconds=1)
    response = await client.post(f"{CALLBACK}/{phase}", headers={"Origin": ORIGIN}, json=payload)
    assert response.status_code == 409
    assert not setup[3].calls


async def test_csrf_wrong_tenant_revoked_user_and_restart(client, setup):
    bootstrap, _ = await boot(client, setup)
    cross = await client.post(
        f"{CALLBACK}/complete", headers={"Origin": "https://evil.test"}, json=body(bootstrap)
    )
    assert cross.status_code == 403
    wrong = await complete(client, bootstrap, connection_id=str(uuid.uuid4()))
    assert wrong.status_code == 409
    injected = await complete(
        client, bootstrap, user_id=str(uuid.uuid4()), tenant_id=str(uuid.uuid4())
    )
    assert injected.status_code == 422
    # A fresh start invalidates the previous browser capability.
    fresh, _ = await boot(client, setup)
    assert (await complete(client, bootstrap)).status_code == 409
    async with system_session("test revoked initiating seller") as db:
        import sqlalchemy as sa

        member = await db.scalar(
            sa.select(TenantUser).where(TenantUser.tenant_id == uuid.UUID(setup[0]["tenant_id"]))
        )
        member.is_active = False
    assert (await complete(client, fresh)).status_code == 409
    assert not setup[3].calls


async def test_cancel_and_wrong_shop_cannot_start(client, setup, unique_phone):
    other_phone = unique_phone[:-1] + str((int(unique_phone[-1]) + 1) % 10)
    other = await signed_in_shop(client, other_phone)
    denied = await client.post(
        f"/v1/integrations/{setup[2]}/connect", headers=auth_header(other), json={}
    )
    assert denied.status_code == 404
    bootstrap, _ = await boot(client, setup)
    cancel = await complete(client, bootstrap, authorization_code=None, cancelled=True)
    assert cancel.json()["result"] == "ACCESS_DENIED"
    assert (await complete(client, bootstrap)).status_code == 409
    assert not setup[3].calls


async def test_technical_and_approval_blockers_and_test_allowlist(client, setup, monkeypatch):
    settings = live_settings()
    monkeypatch.setattr(settings, "meta_whatsapp_embedded_signup_config_id", None)
    response = await client.post(f"/v1/integrations/{setup[2]}/connect", headers=setup[1], json={})
    assert response.json()["details"]["blocker"] == "META_APP_SETUP_REQUIRED"
    monkeypatch.setattr(settings, "meta_whatsapp_embedded_signup_config_id", "654321")
    monkeypatch.setattr(settings, "meta_whatsapp_public_signup_enabled", False)
    response = await client.post(f"/v1/integrations/{setup[2]}/connect", headers=setup[1], json={})
    assert response.json()["details"]["blocker"] == "META_APPROVAL_REQUIRED"
    async with system_session("test allowlist") as db:
        conn = await db.get(IntegrationConnection, uuid.UUID(setup[2]))
        monkeypatch.setattr(settings, "meta_whatsapp_test_user_ids", [conn.created_by])
    await start(client, setup)


async def test_manual_fallback_requires_server_gate_or_admin(client, setup, monkeypatch):
    settings = live_settings()
    payload = {"phone_number_id": PHONE, "waba_id": WABA, "access_token": TOKEN}
    url = f"/v1/integrations/{setup[2]}/whatsapp"
    denied = await client.post(url, headers=setup[1], json=payload)
    assert denied.status_code == 401
    monkeypatch.setattr(settings, "meta_whatsapp_manual_enabled", True)
    assert (await client.post(url, headers=setup[1], json=payload)).status_code == 200
    monkeypatch.setattr(settings, "app_env", AppEnv.PRODUCTION)
    # Keep the fixture's local seller JWT authority while testing the real
    # production manual gate; deployed seller auth is separately covered.
    monkeypatch.setattr(type(settings), "supabase_auth_active", property(lambda _: False))
    assert (await client.post(url, headers=setup[1], json=payload)).status_code == 401
    monkeypatch.setattr(settings, "admin_api_tokens", ["independent-platform-admin-token"])
    allowed = await client.post(
        url, headers={**setup[1], "X-Admin-Token": "independent-platform-admin-token"}, json=payload
    )
    assert allowed.status_code == 200, allowed.text


def test_secret_redaction():
    from app.core.observability import _scrub

    data = {
        "authorization_code": CODE,
        "browser_session": CODE,
        "signup_state": CODE,
        "registration_pin": CODE,
    }
    assert CODE not in json.dumps(redact_value(data))
    assert CODE not in redact_text(
        f"https://graph.facebook.com/oauth/access_token?code={CODE}&client_secret={CODE}&input_token={CODE}"
    )
    for path in (f"{CALLBACK}/complete", "/v1/integrations/abc/whatsapp"):
        event = {"request": {"url": f"https://api.test{path}", "data": json.dumps(data)}}
        assert CODE not in json.dumps(_scrub(event, {}))


async def test_embedded_signup_feeds_existing_whatsapp_draft_buffer(client, setup):
    from app.chat_orders import jobs
    from app.chat_orders.models import ChatMessage
    from tests.test_chat_orders import CHAT, _count, _drafts, _signed, _wa_text, _whatsapp

    bootstrap, _ = await boot(client, setup)
    assert (await complete(client, bootstrap)).json()["result"] == "CONNECTED"
    for index, line in enumerate([*CHAT, CHAT[0], "STOP"]):
        message_id = f"wamid.signup.{index if index != 4 else 0}"
        payload = _whatsapp(_wa_text(line, wamid=message_id), number=PHONE)
        raw, signature = _signed(payload, secret=b"private-app-secret")
        posted = await client.post("/v1/webhooks/integrations/meta", content=raw, headers=signature)
        assert posted.status_code == 200
    assert await _count(ChatMessage, setup[0]) == 4
    await jobs.process_chat_messages()
    drafts = await _drafts(setup[0])
    assert len(drafts) == 1
    assert drafts[0].provider == "WHATSAPP" and drafts[0].status == "READY_FOR_REVIEW"
