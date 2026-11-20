"""Official Meta path on (V4 Phase 6).

A delivering Messenger / WhatsApp connection covers the matching phone apps,
so linked devices stop reading them; losing the connection uncovers them.
Messenger / WhatsApp leave "Coming soon" only with the App Review attestation,
the shop's flag and a passing health check. A Meta webhook draft runs through
the same confirmation + Autopilot pipeline as a phone capture.
"""

from __future__ import annotations

import time
import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from pydantic import SecretStr

from app.autopilot.models import AutopilotDecision
from app.chat_orders import jobs as chat_jobs
from app.chat_orders.models import ChatOrderDraft
from app.common.feature_flags import FeatureFlag, FlagKey, FlagScope
from app.confirmations.models import OrderConfirmation
from app.core.clock import utc_now
from app.core.context import RequestContext, clear_context, set_context
from app.db.session import session_scope, system_session
from app.intake import coverage
from app.intake.models import ShopIntakeSettings
from app.integrations import meta, service
from app.integrations.http import ProviderError
from app.integrations.models import IntegrationConnection
from app.orders.models import Order
from tests.conftest_commerce import signed_in_shop
from tests.integrations_fakes import live_settings
from tests.test_auth_flow import auth_header
from tests.test_automation_workflows import detail, run, runs
from tests.test_autopilot import (
    E164,
    _edit_draft,
    _good,
    _status,
    autopilot_on,
    engines_on,
    live_verified,
    ready,
)
from tests.test_chat_orders import _messenger, _post
from tests.test_device_links import LINK, _device, _key_header
from tests.test_intake_capture import CHAT, intake_on

__all__ = ["autopilot_on", "engines_on", "intake_on", "live_verified", "ready"]

RULES = "/v1/intake/rules"
HUB = "/v1/integrations"
BUSINESS_SUITE = "com.facebook.pages.app"
WA_BUSINESS = "com.whatsapp.w4b"
WHATSAPP = "com.whatsapp"
MESSENGER_APP = "com.facebook.orca"


@pytest.fixture(autouse=True)
def _meta_configured(monkeypatch: pytest.MonkeyPatch) -> Any:
    settings = live_settings()
    monkeypatch.setattr(settings, "meta_app_id", "app")
    monkeypatch.setattr(settings, "meta_app_secret", SecretStr("meta-secret"))
    monkeypatch.setattr(settings, "meta_webhook_verify_token", SecretStr("verify-me"))
    return settings


def _tenant(shop: dict[str, Any]) -> uuid.UUID:
    return uuid.UUID(shop["tenant_id"])


def _account() -> str:
    return str(uuid.uuid4().int)[:12]


async def _flag(shop: dict[str, Any], enabled: bool = True) -> None:
    """The shop's own `meta_official_path_enabled` row (no global row is touched)."""
    key = str(FlagKey.META_OFFICIAL_PATH_ENABLED)
    async with system_session("test: shop flag") as db:
        await db.execute(
            sa.delete(FeatureFlag).where(
                FeatureFlag.key == key, FeatureFlag.tenant_id == _tenant(shop)
            )
        )
        db.add(
            FeatureFlag(key=key, scope=FlagScope.TENANT, tenant_id=_tenant(shop), enabled=enabled)
        )


async def _connection(
    shop: dict[str, Any], provider: str, *, state: str = "PENDING", **values: Any
) -> uuid.UUID:
    account = _account()
    async with system_session("test: meta connection") as db:
        conn = IntegrationConnection(
            tenant_id=_tenant(shop),
            provider=provider,
            name=provider.title(),
            state=state,
            account_id=account,
            account_key=account,
            account_name="Test",
            config={},
            created_by=uuid.uuid4(),
            **values,
        )
        db.add(conn)
        await db.flush()
        return conn.id


async def _in_shop(shop: dict[str, Any], action: Any) -> Any:
    """Run ``action(db)`` in the shop's own tenant session, as a request would."""
    token = set_context(RequestContext(trace_id="test", tenant_id=_tenant(shop)))
    try:
        async with session_scope() as db:
            return await action(db)
    finally:
        clear_context(token)


async def _go_live(shop: dict[str, Any], connection_id: uuid.UUID) -> None:
    async def act(db: Any) -> None:
        conn = await service.get(db, connection_id, lock=True)
        await service.went_live(db, conn)

    await _in_shop(shop, act)


async def _covered(shop: dict[str, Any]) -> list[str] | None:
    async with system_session("test: covered") as db:
        row = await db.scalar(
            sa.select(ShopIntakeSettings).where(ShopIntakeSettings.tenant_id == _tenant(shop))
        )
        return None if row is None else list(row.covered_packages)


async def _linked_shop(client: AsyncClient, phone: str) -> tuple[dict[str, Any], str]:
    shop = await signed_in_shop(client, phone)
    linked = await client.post(LINK, json=_device(), headers=auth_header(shop))
    assert linked.status_code == 200, linked.text
    return shop, linked.json()["ingest_key"]


async def _rules(client: AsyncClient, key: str) -> tuple[dict[str, Any], str]:
    response = await client.get(RULES, headers=_key_header(key))
    assert response.status_code == 200, response.text
    return response.json(), response.headers["etag"]


async def _set_columns(connection_id: uuid.UUID, **values: Any) -> None:
    async with system_session("test: connection columns") as db:
        conn = await db.get(IntegrationConnection, connection_id)
        assert conn is not None
        for name, value in values.items():
            setattr(conn, name, value)


# --------------------------------------------------------------- coverage ---


async def test_a_connected_page_and_number_cover_their_apps_until_disconnected(
    client: AsyncClient, unique_phone: str, intake_on: None
):
    shop, key = await _linked_shop(client, unique_phone)
    await _flag(shop)
    before, etag = await _rules(client, key)
    assert before["enabled_packages"] == [BUSINESS_SUITE, WA_BUSINESS]
    assert before["covered_packages"] == []

    page = await _connection(shop, "MESSENGER")
    await _go_live(shop, page)
    assert await _covered(shop) == [BUSINESS_SUITE]
    refreshed = await client.get(RULES, headers={**_key_header(key), "If-None-Match": etag})
    assert refreshed.status_code == 200, "the next rules refresh sees the change"
    assert refreshed.json()["covered_packages"] == [BUSINESS_SUITE]
    assert refreshed.json()["enabled_packages"] == [WA_BUSINESS]

    number = await _connection(shop, "WHATSAPP")
    await _go_live(shop, number)
    body, _ = await _rules(client, key)
    assert body["covered_packages"] == [BUSINESS_SUITE, WA_BUSINESS]
    assert body["enabled_packages"] == []

    # Personal Messenger and WhatsApp are never covered: the webhook never sees them.
    patched = await client.patch(
        "/v1/intake/settings",
        json={"package_toggles": {WHATSAPP: True, MESSENGER_APP: True}},
        headers=auth_header(shop),
    )
    assert patched.status_code == 200, patched.text
    body, _ = await _rules(client, key)
    assert body["enabled_packages"] == [WHATSAPP, MESSENGER_APP]

    # Disconnecting the Page gives Business Suite back to the phone.
    gone = await client.post(f"{HUB}/{page}/disconnect", headers=auth_header(shop))
    assert gone.status_code == 200, gone.text
    assert await _covered(shop) == [WA_BUSINESS]
    body, _ = await _rules(client, key)
    assert body["enabled_packages"] == [BUSINESS_SUITE, WHATSAPP, MESSENGER_APP]


async def test_pausing_a_connection_uncovers_and_resuming_covers_again(
    client: AsyncClient, unique_phone: str
):
    shop = await signed_in_shop(client, unique_phone)
    await _flag(shop)
    page = await _connection(shop, "MESSENGER")
    await _go_live(shop, page)
    assert await _covered(shop) == [BUSINESS_SUITE]

    paused = await client.patch(f"{HUB}/{page}", json={"enabled": False}, headers=auth_header(shop))
    assert paused.status_code == 200, paused.text
    assert await _covered(shop) == []
    resumed = await client.patch(f"{HUB}/{page}", json={"enabled": True}, headers=auth_header(shop))
    assert resumed.status_code == 200, resumed.text
    assert await _covered(shop) == [BUSINESS_SUITE]


async def test_with_the_flag_off_nothing_is_covered_and_the_sweep_follows_the_flag(
    client: AsyncClient, unique_phone: str
):
    shop = await signed_in_shop(client, unique_phone)
    page = await _connection(shop, "MESSENGER")
    await _go_live(shop, page)
    assert await _covered(shop) is None, "flag off: no settings row is even created"

    await _flag(shop)
    await coverage.sweep_coverage()
    assert await _covered(shop) == [BUSINESS_SUITE]

    await _flag(shop, enabled=False)
    await coverage.sweep_coverage()
    assert await _covered(shop) == [], "switching the flag off gives every app back"


async def test_a_token_found_expired_uncovers_the_app(
    client: AsyncClient, unique_phone: str, monkeypatch: pytest.MonkeyPatch
):
    shop = await signed_in_shop(client, unique_phone)
    await _flag(shop)
    page = await _connection(shop, "MESSENGER")

    async def seal(db: Any) -> None:
        conn = await service.get(db, page, lock=True)
        service.seal(conn, {"page_token": "page-token"})

    await _in_shop(shop, seal)
    await _go_live(shop, page)
    assert await _covered(shop) == [BUSINESS_SUITE]

    async def expired(page_id: str, page_token: str) -> dict[str, Any]:
        raise ProviderError("AUTH_EXPIRED")

    monkeypatch.setattr(meta, "check", expired)
    checked = await client.post(f"{HUB}/{page}/test", headers=auth_header(shop))
    assert checked.status_code == 200, checked.text
    assert checked.json()["ok"] is False
    assert await _covered(shop) == [], "the health check that saw it uncovers at once"

    async def fine(page_id: str, page_token: str) -> dict[str, Any]:
        return {"id": page_id}

    monkeypatch.setattr(meta, "check", fine)
    again = await client.post(f"{HUB}/{page}/test", headers=auth_header(shop))
    assert again.json()["ok"] is True
    assert await _covered(shop) == [BUSINESS_SUITE], "a passing check reconnects and covers"


async def test_the_hourly_sweep_catches_expiry_and_a_failing_webhook_seen_elsewhere(
    client: AsyncClient, unique_phone: str
):
    shop = await signed_in_shop(client, unique_phone)
    await _flag(shop)
    page = await _connection(shop, "MESSENGER")
    number = await _connection(shop, "WHATSAPP")
    await _go_live(shop, page)
    await _go_live(shop, number)
    assert await _covered(shop) == [BUSINESS_SUITE, WA_BUSINESS]

    # A webhook or a message send found the token expired (mark_error, no hook).
    await _set_columns(page, state="AUTH_EXPIRED")
    # The number's webhook subscription failed: chats no longer arrive.
    await _set_columns(number, webhook_state="FAILING")
    from app.intake.jobs import scan_device_health

    counts = await scan_device_health()
    assert counts["coverage_failed"] == 0 and counts["coverage_shops"] >= 1
    assert await _covered(shop) == []


# ------------------------------------------------------------ launch gate ---


async def test_a_health_check_passes_only_for_a_delivering_connection_checked_lately(
    client: AsyncClient, unique_phone: str, monkeypatch: pytest.MonkeyPatch
):
    # Far enough ahead that every other test's connections are stale.
    future = utc_now() + timedelta(days=400)
    monkeypatch.setattr(coverage, "utc_now", lambda: future)
    assert await coverage.check_passed("MESSENGER") is False

    shop = await signed_in_shop(client, unique_phone)
    page = await _connection(
        shop, "MESSENGER", state="CONNECTED", last_success_at=future - timedelta(hours=1)
    )
    assert await coverage.check_passed("MESSENGER") is True
    assert await coverage.check_passed("WHATSAPP") is False, "per provider"

    await _set_columns(page, last_error_at=future - timedelta(minutes=5))
    assert await coverage.check_passed("MESSENGER") is False, "an error after the success"
    await _set_columns(page, last_error_at=None, last_success_at=future - timedelta(hours=25))
    assert await coverage.check_passed("MESSENGER") is False, "older than the window"
    await _set_columns(page, last_success_at=future - timedelta(hours=1), webhook_state="FAILING")
    assert await coverage.check_passed("MESSENGER") is False, "a failing webhook"
    await _set_columns(page, webhook_state="ACTIVE", state="AUTH_EXPIRED")
    assert await coverage.check_passed("MESSENGER") is False, "not connected"


def _providers(body: dict[str, Any]) -> dict[str, bool]:
    return {row["provider"]: row["coming_soon"] for row in body["providers"]}


async def test_messenger_leaves_coming_soon_only_when_every_condition_holds(
    client: AsyncClient, unique_phone: str, monkeypatch: pytest.MonkeyPatch, _meta_configured: Any
):
    settings = _meta_configured
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)

    async def hub() -> dict[str, bool]:
        response = await client.get(HUB, headers=headers)
        assert response.status_code == 200, response.text
        return _providers(response.json())

    passing = {"value": True}

    async def check(provider: str) -> bool:
        return passing["value"]

    monkeypatch.setattr(coverage, "check_passed", check)
    providers = ("SHOPIFY", "WOOCOMMERCE", "CUSTOM_WEBSITE", "MESSENGER", "WHATSAPP")
    assert await hub() == dict.fromkeys(providers, True), "default: everything Coming soon"

    monkeypatch.setattr(settings, "meta_app_review_approved", True)
    assert (await hub())["MESSENGER"] is True, "approval alone is not enough: flag off"
    await _flag(shop)
    assert (await hub())["MESSENGER"] is False, "approved + Meta app + flag + check"
    assert (await hub())["WHATSAPP"] is True, "WhatsApp signup is still not set up"
    shown = await hub()
    assert all(shown[p] for p in ("SHOPIFY", "WOOCOMMERCE", "CUSTOM_WEBSITE"))

    passing["value"] = False
    assert (await hub())["MESSENGER"] is True, "no passing health check"
    passing["value"] = True
    monkeypatch.setattr(settings, "meta_app_review_approved", False)
    assert (await hub())["MESSENGER"] is True, "no App Review attestation"
    monkeypatch.setattr(settings, "meta_app_review_approved", True)
    monkeypatch.setattr(settings, "meta_webhook_verify_token", None)
    assert (await hub())["MESSENGER"] is True, "the Meta app is not set up"
    monkeypatch.setattr(settings, "meta_webhook_verify_token", SecretStr("verify-me"))

    # WhatsApp opens the same way once its signup is configured and public.
    monkeypatch.setattr(settings, "meta_whatsapp_embedded_signup_config_id", "config")
    monkeypatch.setattr(settings, "public_web_url", "https://app.example.test")
    monkeypatch.setattr(settings, "meta_whatsapp_public_signup_enabled", True)
    assert (await hub())["WHATSAPP"] is False

    # The connection page answers the same question for its provider.
    page = await _connection(shop, "MESSENGER", state="CONNECTED")
    shown = await client.get(f"{HUB}/{page}", headers=headers)
    assert shown.status_code == 200, shown.text
    assert shown.json()["availability"]["coming_soon"] is False
    await _flag(shop, enabled=False)
    shown = await client.get(f"{HUB}/{page}", headers=headers)
    assert shown.json()["availability"]["coming_soon"] is True


# ------------------------------------------------------------- end to end ---


async def test_a_messenger_webhook_draft_runs_through_confirmation_and_autopilot(
    client: AsyncClient, ready: dict[str, Any]
):
    """Phase 6 adds no second pipeline: the Meta draft is the same draft."""
    shop, product = ready["shop"], ready["product"]
    headers = auth_header(shop)
    await _flag(shop)
    installed = await client.post(
        "/v1/automation/recipes/autopilot/install",
        headers=headers,
        json={"locale": "en", "enable": True},
    )
    assert installed.status_code == 201, installed.text
    workflow_id = installed.json()["id"]
    toggled = await client.patch(
        f"/v1/automation/workflows/{workflow_id}", headers=headers, json={"enabled": True}
    )
    assert toggled.status_code == 200, toggled.text

    page = _account()
    async with system_session("test: page connection") as db:
        conn = IntegrationConnection(
            tenant_id=_tenant(shop),
            provider="MESSENGER",
            name="Page",
            state="PENDING",
            account_id=page,
            account_key=page,
            account_name="Test Page",
            config={},
            created_by=uuid.uuid4(),
        )
        db.add(conn)
        await db.flush()
        connection_id = conn.id
    await _go_live(shop, connection_id)
    assert await _covered(shop) == [BUSINESS_SUITE]

    start = time.time()
    for index, line in enumerate(CHAT):
        payload = _messenger(
            line, mid=f"e2e.{uuid.uuid4().hex[:8]}.{index}", page=page, at=start + index * 30
        )
        assert await _post(client, payload) == 200
    await chat_jobs.process_chat_messages()

    async with system_session("test: the webhook draft") as db:
        draft = await db.scalar(
            sa.select(ChatOrderDraft).where(
                ChatOrderDraft.tenant_id == _tenant(shop),
                ChatOrderDraft.connection_id == connection_id,
            )
        )
        assert draft is not None
        assert (draft.provider, draft.capture_source) == ("MESSENGER", "META_WEBHOOK")
        assert draft.status == "READY_FOR_REVIEW"
        draft_id = draft.id

    # The draft-ready event started the shop's Autopilot recipe.
    [row] = await runs(client, headers, workflow_id=workflow_id)
    # Same known-good fields as the phone path's test, so only the source differs.
    await _edit_draft(draft_id, **_good(product, E164))
    await run(shop, row["id"])
    done = await detail(client, headers, row["id"])
    assert done["status"] == "SUCCEEDED", done
    steps = [(s["step_id"], s["status"], s["outcome"]) for s in done["steps"]]
    assert steps[0] == ("convert", "SUCCEEDED", "CONVERTED")
    assert steps[1][:2] == ("confirm", "SUCCEEDED")

    async with system_session("test: the order") as db:
        kept = await db.get(ChatOrderDraft, draft_id)
        assert kept is not None and kept.status == "CONFIRMED"
        order = await db.get(Order, kept.confirmed_order_id)
        assert order is not None and order.status == "DRAFT"
        assert order.channel == "MESSENGER"
        confirmation = await db.scalar(
            sa.select(OrderConfirmation).where(OrderConfirmation.order_id == order.id)
        )
        assert confirmation is not None and confirmation.state == "CALL_QUEUED"
        decision = await db.scalar(
            sa.select(AutopilotDecision).where(AutopilotDecision.draft_id == draft_id)
        )
        assert decision is not None and decision.outcome == "CONVERTED"
    assert (await _status(client, shop))["workflow"] == {"installed": True, "enabled": True}
