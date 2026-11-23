"""Autopilot (V4 Phase 4, L11).

A ready chat draft becomes an order only when the shop gate (the Settings
checklist, re-checked every run) and the order gate both pass; any failure
leaves the draft with the seller and records the reason. The order goes to
customer confirmation; Autopilot itself never books.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.autopilot import service as autopilot
from app.autopilot.models import AutopilotDecision
from app.chat_orders.models import ChatOrderDraft
from app.common.audit import AuditLog
from app.confirmations.models import OrderConfirmation
from app.core.clock import utc_now
from app.core.context import RequestContext, clear_context, set_context
from app.couriers.models import CourierAccount
from app.db.session import session_scope, system_session
from app.delivery_rules.models import ShopDeliveryRules
from app.orders.models import Order
from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.test_ai_parse import ai_parse_on
from tests.test_auth_flow import auth_header
from tests.test_automation_workflows import action, count, detail, run, runs, workflow
from tests.test_intake_capture import intake_on

__all__ = ["ai_parse_on", "intake_on"]  # fixtures used by name below

E164 = "+8801700000011"
ADDRESS = "House 12, Road 3, Mirpur 10, Dhaka"
PRICE = 125_000
INSIDE = 6_000
RULES = {
    "inside_dhaka_charge_paisa": INSIDE,
    "dhaka_suburb_charge_paisa": 10_000,
    "outside_dhaka_charge_paisa": 13_000,
    "default_courier": "steadfast",
}


async def _flag(settings: Any, key: str) -> AsyncIterator[None]:
    from tests.conftest import _global_flag_on

    async for _ in _global_flag_on(settings, key):
        yield


@pytest.fixture
async def autopilot_on(settings: Any) -> AsyncIterator[None]:
    async for _ in _flag(settings, "autopilot_enabled"):
        yield


@pytest.fixture
async def engines_on(settings: Any) -> AsyncIterator[None]:
    async for _ in _flag(settings, "confirmation_engine_enabled"):
        async for _ in _flag(settings, "delivery_rules_enabled"):
            async for _ in _flag(settings, "intake_device_enabled"):
                yield


@pytest.fixture
def live_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every courier manifest reads as live-verified (none ships so today)."""
    from app.couriers import capabilities

    real = capabilities.load_manifest

    def verified(provider: str, **kwargs: Any) -> Any:
        manifest = real(provider, **kwargs)
        return dataclasses.replace(
            manifest, live_verified_at=date(2026, 11, 20), live_evidence="test smoke run"
        )

    monkeypatch.setattr(capabilities, "load_manifest", verified)


def _tenant(shop: dict) -> uuid.UUID:
    return uuid.UUID(shop["tenant_id"])


async def _connect(shop: dict, provider: str = "steadfast") -> None:
    async with system_session("test: courier account") as db:
        db.add(
            CourierAccount(
                tenant_id=_tenant(shop),
                provider=provider,
                status="CONNECTED",
                api_key_encrypted="enc-key",
                secret_key_encrypted="enc-secret",
            )
        )


async def _confirmed(client: AsyncClient, shop: dict, n: int) -> None:
    order = (await create_order(client, shop, phone="01700000099"))["order"]
    async with system_session("test: confirmed history") as db:
        for _ in range(n):
            db.add(
                OrderConfirmation(
                    tenant_id=_tenant(shop),
                    order_id=uuid.UUID(order["id"]),
                    state="CONFIRMED",
                    order_version=1,
                    cod_paisa_snapshot=0,
                )
            )


async def _rules(client: AsyncClient, shop: dict, **changes: Any) -> None:
    response = await client.patch(
        "/v1/shop/delivery-rules", json={**RULES, **changes}, headers=auth_header(shop)
    )
    assert response.status_code == 200, response.text


async def _set(shop: dict, **values: Any) -> None:
    async with system_session("test: shop rules") as db:
        row = await db.scalar(
            sa.select(ShopDeliveryRules).where(ShopDeliveryRules.tenant_id == _tenant(shop))
        )
        assert row is not None
        for key, value in values.items():
            setattr(row, key, value)


async def _status(client: AsyncClient, shop: dict) -> dict[str, Any]:
    response = await client.get("/v1/autopilot", headers=auth_header(shop))
    assert response.status_code == 200, response.text
    return response.json()


def _failing(status: dict[str, Any]) -> list[str]:
    return [line["reason"] for line in status["checklist"] if not line["ok"]]


@pytest.fixture
async def ready(
    client: AsyncClient,
    unique_phone: str,
    autopilot_on: None,
    engines_on: None,
    courier_flags_on: None,
    live_verified: None,
) -> dict[str, Any]:
    """A Pro shop with every checklist line met and Autopilot switched on."""
    shop = await signed_in_shop(client, unique_phone, plan="pro")
    product = await create_product(
        client, shop, name="Kalo Panjabi", sku=f"AP-{uuid.uuid4().hex[:6]}", price_paisa=PRICE
    )
    await _rules(client, shop)
    await _connect(shop)
    await _confirmed(client, shop, autopilot.MIN_CONFIRMED_ORDERS)
    response = await client.put(
        "/v1/autopilot",
        json={"consent": True, "manual_queue": True, "on": True},
        headers=auth_header(shop),
    )
    assert response.status_code == 200, response.text
    return {"shop": shop, "product": product}


def _item(product: dict, **match: Any) -> dict[str, Any]:
    return {
        "name": "kalo panjabi",
        "quantity": 1,
        "match": {
            "status": "MATCHED",
            "product_id": product["id"],
            "product_name": product["name"],
            "variant_status": "NONE",
            "unit_price_paisa": PRICE,
            **match,
        },
    }


def _good(product: dict, phone: str) -> dict[str, Any]:
    return {
        "status": "READY_FOR_REVIEW",
        "phones": [phone],
        "selected_phone": phone,
        "address": ADDRESS,
        "items": [_item(product)],
        "cod_amount_paisa": PRICE + INSIDE,
        "cod_source": "CATALOG",
        "missing_fields": [],
        "uncertain_fields": [],
        "warnings": [],
        "duplicate_of_order_id": None,
    }


async def _capture(client: AsyncClient, shop: dict) -> dict[str, Any]:
    from tests.test_intake_capture import CHAT, MANUAL

    captured = await client.post(
        MANUAL,
        json={"source": "SHARE", "messages": [{"text": "\n".join(CHAT)}]},
        headers=auth_header(shop),
    )
    assert captured.status_code == 200, captured.text
    return captured.json()["draft"]


async def _edit_draft(draft_id: uuid.UUID, **fields: Any) -> None:
    async with system_session("test: draft fields") as db:
        draft = await db.get(ChatOrderDraft, draft_id)
        assert draft is not None
        for key, value in fields.items():
            setattr(draft, key, value)


async def _new_draft(client: AsyncClient, ready: dict, phone: str, **fields: Any) -> uuid.UUID:
    """A ready draft with the given phone, then ``fields`` on top."""
    if "template" not in ready:
        ready["template"] = uuid.UUID((await _capture(client, ready["shop"]))["id"])
    async with system_session("test: draft copy") as db:
        base = await db.get(ChatOrderDraft, ready["template"])
        assert base is not None
        now = utc_now()
        draft = ChatOrderDraft(
            tenant_id=base.tenant_id,
            connection_id=base.connection_id,
            identity_id=base.identity_id,
            provider=base.provider,
            capture_source=base.capture_source,
            first_message_at=now,
            last_message_at=now,
            **{**_good(ready["product"], phone), **fields},
        )
        db.add(draft)
        # The template must not look like an open duplicate of anything.
        base.status = "IGNORED"
        await db.flush()
        return draft.id


async def _convert(shop: dict, draft_id: uuid.UUID) -> autopilot.Converted:
    token = set_context(RequestContext(trace_id="test", tenant_id=_tenant(shop)))
    try:
        async with session_scope() as db:
            return await autopilot.convert_draft(db, draft_id, execution_id=None)
    finally:
        clear_context(token)


# ------------------------------------------------------------- the switch ---


async def test_every_route_is_off_without_the_flag(client: AsyncClient, unique_phone: str):
    shop = await signed_in_shop(client, unique_phone, plan="pro")
    headers = auth_header(shop)
    calls = [
        client.get("/v1/autopilot", headers=headers),
        client.put("/v1/autopilot", json={"on": True}, headers=headers),
        client.post("/v1/autopilot/pause", headers=headers),
        client.post("/v1/autopilot/resume", headers=headers),
        client.get("/v1/autopilot/today", headers=headers),
    ]
    for call in calls:
        response = await call
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "FEATURE_DISABLED"


async def test_the_checklist_shows_each_condition_and_turning_on_needs_all_of_them(
    client: AsyncClient,
    unique_phone: str,
    autopilot_on: None,
    engines_on: None,
    courier_flags_on: None,
    monkeypatch: pytest.MonkeyPatch,
):
    shop = await signed_in_shop(client, unique_phone)  # Free plan
    headers = auth_header(shop)
    status = await _status(client, shop)
    assert [line["key"] for line in status["checklist"]] == [
        "PLAN",
        "CONFIRMATIONS",
        "COURIER",
        "DELIVERY_RULES",
        "VOICE",
        "HISTORY",
        "CONSENT",
    ]
    assert _failing(status) == [
        "NOT_IN_PLAN",
        "COURIER_NOT_READY",
        "DELIVERY_RULES_INCOMPLETE",
        "VOICE_UNAVAILABLE",
        "NOT_ENOUGH_CONFIRMED",
        "CONSENT_MISSING",
    ]
    assert status["on"] is False and status["reason"] == "AUTOPILOT_OFF"
    assert status["workflow"] == {"installed": False, "enabled": False}
    # No delivery rules row: nothing is created, nothing saved.
    refused = await client.put("/v1/autopilot", json={"on": True, "consent": True}, headers=headers)
    assert refused.status_code == 409
    assert refused.json()["details"]["failing"] == ["DELIVERY_RULES_INCOMPLETE"]
    assert await count(ShopDeliveryRules, ShopDeliveryRules.tenant_id == _tenant(shop)) == 0

    from tests.conftest_commerce import grant_plan

    await grant_plan(shop["tenant_id"], "pro")
    await _rules(client, shop, inside_dhaka_charge_paisa=0)
    courier = lambda s: next(x for x in s["checklist"] if x["key"] == "COURIER")  # noqa: E731
    status = await _status(client, shop)
    assert courier(status)["detail"]["problem"] == "NOT_LIVE_VERIFIED"
    assert "DELIVERY_RULES_INCOMPLETE" in _failing(status)  # one zone charge is 0
    await _rules(client, shop)

    from app.couriers import capabilities

    real = capabilities.load_manifest
    monkeypatch.setattr(
        capabilities,
        "load_manifest",
        lambda p, **k: dataclasses.replace(
            real(p, **k), live_verified_at=date(2026, 11, 20), live_evidence="smoke"
        ),
    )
    assert courier(await _status(client, shop))["detail"]["problem"] == "NOT_CONNECTED"
    await _connect(shop)
    from app.common.provider_health import ProviderHealth

    async with system_session("test: courier down") as db:
        down = ProviderHealth(
            kind="COURIER", provider="steadfast", capability="*", scope_key="*", state="DOWN"
        )
        db.add(down)
    assert courier(await _status(client, shop))["detail"]["problem"] == "UNHEALTHY"
    async with system_session("test: courier back") as db:
        await db.execute(sa.delete(ProviderHealth).where(ProviderHealth.provider == "steadfast"))

    await _confirmed(client, shop, autopilot.MIN_CONFIRMED_ORDERS - 1)
    status = await _status(client, shop)
    history = next(x for x in status["checklist"] if x["key"] == "HISTORY")
    assert history["detail"] == {"confirmed": 19, "needed": 20} and not history["ok"]
    refused = await client.put(
        "/v1/autopilot", json={"on": True, "consent": True, "manual_queue": True}, headers=headers
    )
    assert refused.status_code == 409
    assert refused.json()["details"]["failing"] == ["NOT_ENOUGH_CONFIRMED"]
    # The refusal saved nothing: consent and manual-queue mode were rolled back.
    status = await _status(client, shop)
    assert status["consent_at"] is None and status["manual_queue"] is False

    await _confirmed(client, shop, 1)
    turned = await client.put(
        "/v1/autopilot", json={"on": True, "consent": True, "manual_queue": True}, headers=headers
    )
    assert turned.status_code == 200, turned.text
    body = turned.json()
    assert body["on"] is True and body["ready"] is True and body["reason"] is None
    consent_at = body["consent_at"]
    again = await client.put("/v1/autopilot", json={"consent": True}, headers=headers)
    assert again.json()["consent_at"] == consent_at  # recorded once
    async with system_session("test: audit") as db:
        rows = (
            await db.scalars(
                sa.select(AuditLog.context).where(
                    AuditLog.tenant_id == _tenant(shop), AuditLog.action == "autopilot.updated"
                )
            )
        ).all()
    assert [row["changed"] for row in rows] == [["manual_queue", "consent", "on"]]


async def test_pause_holds_conversions_and_queued_calls_and_the_switches_stop_it(
    client: AsyncClient, ready: dict, settings: Any
):
    shop = ready["shop"]
    headers = auth_header(shop)
    paused = await client.post("/v1/autopilot/pause", headers=headers)
    assert paused.status_code == 200, paused.text
    assert paused.json()["paused_until"] is not None
    assert paused.json()["reason"] == "AUTOPILOT_PAUSED"
    held = await _convert(shop, await _new_draft(client, ready, "+8801700000031"))
    assert (held.outcome, held.reason) == ("HELD", "AUTOPILOT_PAUSED")

    from app.confirmations.calls import voice_gate

    async for _ in _flag(settings, "voice_confirmation_enabled"):
        token = set_context(RequestContext(trace_id="test", tenant_id=_tenant(shop)))
        try:
            async with session_scope() as db:
                gate = await voice_gate(db, _tenant(shop))
                assert (gate.on, gate.reason) == (False, "SHOP_PAUSED")
                await autopilot.resume(db, _tenant(shop))
            async with session_scope() as db:
                assert (await voice_gate(db, _tenant(shop))).reason != "SHOP_PAUSED"
        finally:
            clear_context(token)

    # Turning off also clears a pause; pausing an Autopilot that is off is refused.
    await client.post("/v1/autopilot/pause", headers=headers)
    off = await client.put("/v1/autopilot", json={"on": False}, headers=headers)
    assert off.json()["on"] is False and off.json()["paused_until"] is None
    assert (await client.post("/v1/autopilot/pause", headers=headers)).status_code == 409
    held = await _convert(shop, await _new_draft(client, ready, "+8801700000032"))
    assert held.reason == "AUTOPILOT_OFF"

    await client.put("/v1/autopilot", json={"on": True}, headers=headers)
    async with system_session("test: kill switch") as db:
        from app.common.feature_flags import FeatureFlag

        db.add(
            FeatureFlag(
                key="autopilot_enabled", scope="TENANT", tenant_id=_tenant(shop), enabled=False
            )
        )
    held = await _convert(shop, await _new_draft(client, ready, "+8801700000033"))
    assert held.reason == "AUTOPILOT_DISABLED"
    async with system_session("test: kill switch off") as db:
        await db.execute(
            sa.delete(FeatureFlag).where(
                FeatureFlag.key == "autopilot_enabled", FeatureFlag.tenant_id == _tenant(shop)
            )
        )


async def test_the_heartbeat_tells_phones_to_upload_only_with_consent_and_no_pause(
    client: AsyncClient, ready: dict
):
    from tests.test_device_links import BEAT, LINK, _device, _key_header

    shop = ready["shop"]
    linked = (await client.post(LINK, json=_device(), headers=auth_header(shop))).json()

    async def beat() -> bool:
        response = await client.post(
            BEAT, json={"listener_state": "ON"}, headers=_key_header(linked["ingest_key"])
        )
        assert response.status_code == 200, response.text
        return bool(response.json()["autopilot_on"])

    assert await beat() is True
    await client.post("/v1/autopilot/pause", headers=auth_header(shop))
    assert await beat() is False
    await client.post("/v1/autopilot/resume", headers=auth_header(shop))
    assert await beat() is True
    await client.put("/v1/autopilot", json={"on": False}, headers=auth_header(shop))
    assert await beat() is False


async def test_an_autopilot_shops_automatic_upload_is_read_with_the_model(
    client: AsyncClient,
    ready: dict,
    intake_on: None,
    ai_parse_on: None,
    unique_phone: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """Operator decision 2026-11-20 (1): automatic uploads of an Autopilot shop
    go through the LLM merge path, once per batch, metered by
    ``ai_parse_monthly``; other shops (and a paused one) stay deterministic."""
    from app.chat_orders.models import ChatMessage
    from app.core.errors import EntitlementRequiredError
    from app.entitlements.service import EntitlementService
    from app.orders.parser.ai_client import FakeAiParseClient
    from tests.test_ai_parse import LOOSE_TEXT, answer
    from tests.test_device_links import LINK, _device
    from tests.test_intake_capture import _batch, _upload

    fake = FakeAiParseClient(answer())
    monkeypatch.setattr("app.api.v1.intake.build_ai_parse_client", lambda settings: fake)
    shop = ready["shop"]
    key = (await client.post(LINK, json=_device(), headers=auth_header(shop))).json()["ingest_key"]

    async def drafts(tenant: uuid.UUID) -> list[ChatOrderDraft]:
        async with system_session("test: drafts") as db:
            rows = await db.scalars(
                sa.select(ChatOrderDraft).where(
                    ChatOrderDraft.tenant_id == tenant, ChatOrderDraft.status != "IGNORED"
                )
            )
            return list(rows)

    async def received(tenant: uuid.UUID) -> int:
        return await count(
            ChatMessage,
            ChatMessage.tenant_id == tenant,
            ChatMessage.processing_status == "RECEIVED",
        )

    sent = await _upload(client, key, _batch([LOOSE_TEXT, "taratari diben"], chat="Sakib"))
    assert sent.status_code == 200, sent.text
    assert len(fake.calls) == 1  # one read for the whole batch
    [draft] = await drafts(_tenant(shop))
    assert draft.customer_name == "sakib" and draft.address == "chandpur sadar"
    assert await received(_tenant(shop)) == 0  # read now, not left for the worker

    # Over the monthly limit: the deterministic read stands, nothing is called.
    async def exhausted(_self: Any, _tenant_id: Any, entitlement: Any, **_: Any) -> None:
        raise EntitlementRequiredError(str(entitlement))

    monkeypatch.setattr(EntitlementService, "consume", exhausted)
    other_text = LOOSE_TEXT.replace("01700000012", "01700000013")  # not merged with Sakib's
    sent = await _upload(client, key, _batch([other_text], chat="Karim"))
    assert sent.status_code == 200, sent.text
    assert len(fake.calls) == 1
    karim = [d for d in await drafts(_tenant(shop)) if d.id != draft.id]
    assert len(karim) == 1 and karim[0].customer_name != "sakib"
    monkeypatch.undo()
    monkeypatch.setattr("app.api.v1.intake.build_ai_parse_client", lambda settings: fake)

    # Paused: uploads are left to the worker's deterministic read.
    await client.post("/v1/autopilot/pause", headers=auth_header(shop))
    sent = await _upload(client, key, _batch([LOOSE_TEXT], chat="Rafiq"))
    assert sent.status_code == 200, sent.text
    assert len(fake.calls) == 1 and await received(_tenant(shop)) == 1

    # A shop without Autopilot: deterministic only.
    other = await signed_in_shop(client, unique_phone[:-2] + "77", plan="pro")
    other_key = (await client.post(LINK, json=_device(), headers=auth_header(other))).json()
    sent = await _upload(client, other_key["ingest_key"], _batch([LOOSE_TEXT], chat="Sakib"))
    assert sent.status_code == 200, sent.text
    assert len(fake.calls) == 1 and await received(_tenant(other)) == 1
    from app.chat_orders import jobs as chat_jobs

    for _ in range(5):  # leave no RECEIVED rows for other tests' worker runs
        if (await chat_jobs.process_chat_messages())["processed"] == 0:
            break


# ------------------------------------------------------------- conversion ---


async def test_the_recipe_converts_a_ready_draft_and_asks_the_customer(
    client: AsyncClient, ready: dict
):
    shop, product = ready["shop"], ready["product"]
    headers = auth_header(shop)
    installed = await client.post(
        "/v1/automation/recipes/autopilot/install",
        headers=headers,
        json={"locale": "en", "enable": True},
    )
    assert installed.status_code == 201, installed.text
    assert installed.json()["enabled"] is False  # always installed switched off
    assert (await _status(client, shop))["workflow"] == {"installed": True, "enabled": False}
    toggled = await client.patch(
        f"/v1/automation/workflows/{installed.json()['id']}",
        headers=headers,
        json={"enabled": True},
    )
    assert toggled.status_code == 200, toggled.text

    draft = await _capture(client, shop)
    draft_id = uuid.UUID(draft["id"])
    await _edit_draft(draft_id, **_good(product, E164))
    [row] = await runs(client, headers, workflow_id=installed.json()["id"])
    await run(shop, row["id"])
    done = await detail(client, headers, row["id"])
    assert done["status"] == "SUCCEEDED"
    steps = [(s["step_id"], s["status"], s["outcome"]) for s in done["steps"]]
    assert steps[0][:3] == ("convert", "SUCCEEDED", "CONVERTED")
    assert steps[1][:2] == ("confirm", "SUCCEEDED")

    async with system_session("test: the order") as db:
        kept = await db.get(ChatOrderDraft, draft_id)
        assert kept is not None and kept.status == "CONFIRMED" and kept.closed_by is None
        order = await db.get(Order, kept.confirmed_order_id)
        assert order is not None
        assert order.status == "DRAFT"
        assert order.cod_amount_paisa == PRICE + INSIDE
        confirmation = await db.scalar(
            sa.select(OrderConfirmation).where(OrderConfirmation.order_id == order.id)
        )
        assert confirmation is not None and confirmation.state == "CALL_QUEUED"
        decision = await db.scalar(
            sa.select(AutopilotDecision).where(AutopilotDecision.draft_id == draft_id)
        )
        assert decision is not None and decision.outcome == "CONVERTED"
        assert decision.order_id == order.id and decision.reason_code is None

    # Asked again (a retry, a second workflow): the same order, never a second.
    again = await _convert(shop, draft_id)
    assert (again.outcome, again.order_id) == ("CONVERTED", order.id)
    assert await count(Order, Order.tenant_id == _tenant(shop), Order.id == order.id) == 1

    today = (await client.get("/v1/autopilot/today", headers=headers)).json()
    assert (today["converted"], today["confirmed"], today["cancelled"], today["waiting"]) == (
        1,
        0,
        0,
        1,
    )
    assert today["recent"][0]["outcome"] == "CONVERTED"
    assert today["recent"][0]["order_number"] == order.order_number


async def test_a_held_draft_ends_its_run_with_the_reason_and_no_order(
    client: AsyncClient, ready: dict
):
    shop = ready["shop"]
    headers = auth_header(shop)
    wf = await workflow(
        client,
        headers,
        "intake.draft_ready",
        [action("convert", "CONVERT_DRAFT_TO_ORDER"), action("ask", "REQUEST_CONFIRMATION")],
    )
    draft = await _capture(client, shop)
    await _edit_draft(
        uuid.UUID(draft["id"]), **{**_good(ready["product"], E164), "uncertain_fields": ["items"]}
    )
    [row] = await runs(client, headers, workflow_id=wf["id"])
    await run(shop, row["id"])
    done = await detail(client, headers, row["id"])
    assert [(s["step_id"], s["status"], s["outcome"]) for s in done["steps"]] == [
        ("convert", "SKIPPED", "FIELDS_UNCERTAIN"),
        ("ask", "SKIPPED", "DRAFT_NOT_CONVERTED"),
    ]
    async with system_session("test: kept") as db:
        kept = await db.get(ChatOrderDraft, uuid.UUID(draft["id"]))
        assert kept is not None and kept.status == "READY_FOR_REVIEW"
    today = (await client.get("/v1/autopilot/today", headers=headers)).json()
    assert today["held"] == 1 and today["converted"] == 0
    assert today["recent"][0] == {
        **today["recent"][0],
        "outcome": "HELD",
        "reason": "FIELDS_UNCERTAIN",
    }


async def test_each_order_gate_failure_keeps_the_draft_with_its_reason(
    client: AsyncClient, ready: dict, monkeypatch: pytest.MonkeyPatch
):
    shop, product = ready["shop"], ready["product"]
    out_of_stock = await create_product(
        client, shop, name="Sold Out", sku=f"AP-{uuid.uuid4().hex[:6]}", opening_stock=0
    )
    recent = (await create_order(client, shop, phone="01700000049"))["order"]
    cases: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
        ("DRAFT_NOT_READY", {"status": "NEEDS_INFO"}, {}),
        ("DRAFT_NOT_READY", {"missing_fields": ["address"]}, {}),
        ("FIELDS_UNCERTAIN", {"uncertain_fields": ["address"]}, {}),
        ("PHONE_NOT_SINGLE", {"phones": ["+8801700000041", "+8801700000042"]}, {}),
        ("ITEM_NOT_MATCHED", {"items": [_item(product, status="AMBIGUOUS")]}, {}),
        ("ITEM_NOT_MATCHED", {"items": [_item(product, variant_status="AMBIGUOUS")]}, {}),
        ("ITEM_NOT_MATCHED", {"items": []}, {}),
        ("COD_NOT_CALCULATED", {"items": [_item(product, unit_price_paisa=None)]}, {}),
        ("ZONE_UNRESOLVED", {"address": "Somewhere near the big tree"}, {}),
        ("COD_MISMATCH", {"cod_source": "CHAT_MISMATCH"}, {}),
        ("COD_MISMATCH", {"cod_amount_paisa": PRICE + INSIDE - 1}, {}),
        ("OUT_OF_STOCK", {"items": [_item(out_of_stock)]}, {}),
        ("DUPLICATE_RECENT", {"duplicate_of_order_id": uuid.UUID(recent["id"])}, {}),
        ("POLICY_SELLER_REVIEW", {}, {"call_nobody": True}),
        ("DAILY_CAP_REACHED", {}, {"autopilot_daily_cap": 0}),
    ]
    orders_before = await count(Order, Order.tenant_id == _tenant(shop))
    for index, (reason, fields, rules) in enumerate(cases):
        phone = f"+88017000000{20 + index:02d}"
        fields = {**fields}
        if "phones" not in fields:
            fields.setdefault("selected_phone", phone)
        else:
            fields["selected_phone"] = fields["phones"][0]
        draft_id = await _new_draft(client, ready, fields.get("phones", [phone])[0], **fields)
        if rules:
            await _set(shop, **rules)
        held = await _convert(shop, draft_id)
        if rules:
            await _set(shop, call_nobody=False, autopilot_daily_cap=30)
        assert (held.outcome, held.reason) == ("HELD", reason), (index, held)
        async with system_session("test: kept") as db:
            kept = await db.get(ChatOrderDraft, draft_id)
            assert kept is not None and kept.confirmed_order_id is None
            assert kept.status in ("READY_FOR_REVIEW", "NEEDS_INFO")
    # A recent order for the phone (not linked on the draft) also holds it.
    draft_id = await _new_draft(client, ready, "+8801700000049")
    assert (await _convert(shop, draft_id)).reason == "DUPLICATE_RECENT"
    assert await count(Order, Order.tenant_id == _tenant(shop)) == orders_before

    # The shop's default courier is always used (operator decision 2), but a
    # Pathao / RedX order whose location ids are not resolved is held.
    resolved: list[bool] = [False]

    async def fake_pathao(_db: Any, _tenant_id: uuid.UUID, address: str) -> bool:
        assert address == ADDRESS
        return resolved[0]

    monkeypatch.setattr(autopilot, "_resolve_pathao", fake_pathao)
    await _connect(shop, "pathao")
    await _connect(shop, "redx")
    await _set(shop, default_courier="pathao")
    held = await _convert(shop, await _new_draft(client, ready, "+8801700000050"))
    assert (held.outcome, held.reason) == ("HELD", "LOCATION_UNRESOLVED")
    resolved[0] = True
    made = await _convert(shop, await _new_draft(client, ready, "+8801700000053"))
    assert made.outcome == "CONVERTED"
    await _set(shop, default_courier="redx")
    held = await _convert(shop, await _new_draft(client, ready, "+8801700000054"))
    assert (held.outcome, held.reason) == ("HELD", "LOCATION_UNRESOLVED")
    await _set(shop, default_courier="steadfast")
    # The cap counts today's conversions: two made, a cap of 2 holds the next.
    made = await _convert(shop, await _new_draft(client, ready, "+8801700000051"))
    assert made.outcome == "CONVERTED"
    await _set(shop, autopilot_daily_cap=2)
    held = await _convert(shop, await _new_draft(client, ready, "+8801700000052"))
    assert held.reason == "DAILY_CAP_REACHED"


async def test_the_pathao_location_check_uses_the_shops_own_pathao_lists(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.couriers.accounts import CourierAccountService
    from app.couriers.registry import CourierAdapterRegistry, set_courier_registry
    from tests.test_pathao_locations import FakePathao
    from tests.test_quick_order import _tenant as new_tenant

    tenant = await new_tenant(db)
    # No Pathao account: not resolved.
    assert not await autopilot._resolve_pathao(db, tenant.id, ADDRESS)
    db.add(
        CourierAccount(
            tenant_id=tenant.id,
            provider="pathao",
            status="CONNECTED",
            api_key_encrypted="enc-key",
            secret_key_encrypted="enc-secret",
        )
    )
    await db.flush()
    monkeypatch.setattr(CourierAccountService, "credentials_for", lambda _self, _account: "creds")
    fake = FakePathao()
    adapter = type("FakeAdapter", (), {"client": fake})()
    set_courier_registry(CourierAdapterRegistry({"pathao": lambda: adapter}))
    assert await autopilot._resolve_pathao(db, tenant.id, ADDRESS)  # Dhaka / Mirpur 10
    assert not await autopilot._resolve_pathao(db, tenant.id, "Near the big tree, Dhaka")

    async def broken(_creds: Any) -> Any:
        raise RuntimeError("pathao down")

    other = await new_tenant(db)  # a fresh cache key, so the lists are fetched
    db.add(
        CourierAccount(
            tenant_id=other.id,
            provider="pathao",
            status="CONNECTED",
            api_key_encrypted="enc-key",
            secret_key_encrypted="enc-secret",
        )
    )
    await db.flush()
    fake.list_cities = broken  # type: ignore[method-assign]
    assert not await autopilot._resolve_pathao(db, other.id, ADDRESS)


async def test_a_refused_order_create_keeps_the_draft(
    client: AsyncClient, ready: dict, monkeypatch: pytest.MonkeyPatch
):
    from app.core.errors import ConflictError
    from app.orders.service import OrderService

    async def refuse(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise ConflictError("limit", details={"code": "ENTITLEMENT_LIMIT"})

    monkeypatch.setattr(OrderService, "create", refuse)
    shop = ready["shop"]
    draft_id = await _new_draft(client, ready, "+8801700000061")
    held = await _convert(shop, draft_id)
    assert (held.outcome, held.reason) == ("HELD", "ORDER_REFUSED")
    async with system_session("test: kept") as db:
        kept = await db.get(ChatOrderDraft, draft_id)
        assert kept is not None and kept.status == "READY_FOR_REVIEW"


# --------------------------------------------------------------- workflows ---


async def test_order_steps_may_follow_a_conversion_but_never_a_booking(
    client: AsyncClient, unique_phone: str
):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)

    async def problems(steps: list[dict]) -> list[str]:
        created = await workflow(client, headers, "intake.draft_ready", steps, publish=False)
        response = await client.post(
            f"/v1/automation/workflows/{created['id']}/publish",
            headers=headers,
            json={"enable": False},
        )
        if response.status_code == 200:
            return []
        assert response.status_code == 422, response.text
        return response.json()["details"]["problems"]

    convert = action("convert", "CONVERT_DRAFT_TO_ORDER")
    ask = action("ask", "REQUEST_CONFIRMATION")
    book = action("book", "BOOK_COURIER", provider="steadfast")
    assert await problems([convert, ask]) == []
    assert await problems([ask, convert]) == ["ACTION_NOT_AVAILABLE:REQUEST_CONFIRMATION"]
    assert await problems([convert, book]) == ["ACTION_NOT_AVAILABLE:BOOK_COURIER"]
    # On one branch arm only: not before the step on every path.
    branch = {
        "type": "branch",
        "id": "maybe",
        "conditions": {"match": "all", "conditions": [], "groups": []},
        "then": [action("convert2", "CONVERT_DRAFT_TO_ORDER")],
        "else": [],
    }
    assert await problems([branch, ask]) == ["ACTION_NOT_AVAILABLE:REQUEST_CONFIRMATION"]


async def test_the_preview_shows_the_shop_gate_reason(client: AsyncClient, ready: dict):
    shop = ready["shop"]
    headers = auth_header(shop)
    wf = await workflow(
        client, headers, "intake.draft_ready", [action("convert", "CONVERT_DRAFT_TO_ORDER")]
    )
    preview = await client.post(
        f"/v1/automation/workflows/{wf['id']}/preview", headers=headers, json={}
    )
    assert preview.status_code == 200, preview.text
    assert "RUN" in preview.text
    await client.post("/v1/autopilot/pause", headers=headers)
    preview = await client.post(
        f"/v1/automation/workflows/{wf['id']}/preview", headers=headers, json={}
    )
    assert "AUTOPILOT_PAUSED" in preview.text


async def test_the_status_compares_the_three_best_couriers_below_the_default(db: Any) -> None:
    from app.consignments.models import ConsignmentStatus
    from tests.test_quick_order import _parcel, _tenant

    tenant = await _tenant(db)
    delivered, returned = ConsignmentStatus.DELIVERED, ConsignmentStatus.RETURNED
    for status in (delivered, delivered, delivered, returned):  # pathao 3/4, inside Dhaka
        await _parcel(db, provider="pathao", status=status)
    for _ in range(3):  # steadfast 3/3, outside Dhaka: every zone counts here
        await _parcel(db, provider="steadfast", status=delivered, district="Chandpur", area=None)
    for status in (delivered, returned, returned):  # redx 1/3
        await _parcel(db, provider="redx", status=status)
    for _ in range(2):  # carrybee 2/2: under the sample floor, not ranked
        await _parcel(db, provider="carrybee", status=delivered)
    for _ in range(3):  # paperfly 0/3: a fourth courier, cut by the limit of three
        await _parcel(db, provider="paperfly", status=returned)
    await db.flush()
    db.add(ShopDeliveryRules(tenant_id=tenant.id, default_courier="redx"))
    await db.flush()
    view = (await autopilot.status_view(db, tenant.id))["courier_comparison"]
    assert view["default"] == "redx" and view["days"] == 180
    assert [
        (o["courier"], o["delivered"], o["parcels"], o["is_default"]) for o in view["options"]
    ] == [
        ("steadfast", 3, 3, False),
        ("pathao", 3, 4, False),
        ("redx", 1, 3, True),
    ]


def test_no_shipped_courier_manifest_is_live_verified():
    from app.couriers.capabilities import load_all_manifests

    manifests = load_all_manifests()
    assert manifests and not any(m.is_live_verified for m in manifests.values())


def test_a_live_verification_needs_both_a_date_and_evidence(tmp_path: Any):
    from app.couriers.capabilities import load_manifest

    for name, block, verified in (
        ("a", "live_verification:\n  verified_at: 2026-11-20\n  evidence: smoke run 42\n", True),
        ("b", "live_verification:\n  verified_at: 2026-11-20\n", False),
        ("c", "live_verification:\n  evidence: smoke run\n", False),
        ("d", "live_verification: true\n", False),
    ):
        (tmp_path / f"{name}.yaml").write_text(f"provider: {name}\n{block}", encoding="utf-8")
        manifest = load_manifest(name, directory=tmp_path)
        assert manifest is not None and manifest.is_live_verified is verified, name


def test_pause_lasts_one_hour():
    assert timedelta(hours=1) == autopilot.PAUSE_FOR
