"""Automation hooks for customer confirmation and intake (V4 WP2.4).

The confirmation and intake facts become workflow triggers, a workflow can ask
for a customer confirmation (the WP2.2 policy still decides), and turning a
chat draft into an order waits for Autopilot (Phase 4). Booking after a
confirmation is the V3.4 ``BOOK_COURIER`` action, once (L10).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient

from app.automation import recipes
from app.automation.triggers import read_event
from app.chat_orders.models import ChatOrderDraft
from app.common.outbox import OutboxEvent, OutboxTopic, enqueue
from app.confirmations import manual
from app.confirmations.models import OrderConfirmation
from app.core.context import RequestContext, clear_context, set_context
from app.db.session import session_scope
from app.notifications.models import Notification
from app.orders.models import Order
from tests.test_automation_workflows import (
    _bookable,
    action,
    count,
    detail,
    notice,
    run,
    runs,
    steadfast,  # noqa: F401 - fixture
    tenant,
    workflow,
)
from tests.test_confirmation_queue import NOON

NEW_TRIGGERS = {
    "confirmation.confirmed": "order",
    "confirmation.cancelled": "order",
    "confirmation.no_answer": "order",
    "confirmation.callback_requested": "order",
    "intake.draft_ready": "draft",
}


@pytest.fixture
async def confirmation_on(settings: Any) -> AsyncIterator[None]:
    from app.common.feature_flags import FlagKey
    from tests.conftest import _global_flag_on

    async for _ in _global_flag_on(settings, str(FlagKey.CONFIRMATION_ENGINE_ENABLED)):
        yield


@pytest.fixture
async def intake_on(settings: Any) -> AsyncIterator[None]:
    from app.common.feature_flags import FlagKey
    from tests.conftest import _global_flag_on

    async for _ in _global_flag_on(settings, str(FlagKey.INTAKE_DEVICE_ENABLED)):
        yield


@pytest.fixture
async def shop(client, unique_phone):
    from tests.conftest_commerce import signed_in_shop

    return await signed_in_shop(client, unique_phone)


@pytest.fixture
def headers(shop):
    from tests.test_auth_flow import auth_header

    return auth_header(shop)


async def _order(client: AsyncClient, shop: dict, phone: str) -> dict[str, Any]:
    from tests.conftest_commerce import create_order

    return (await create_order(client, shop, phone=phone))["order"]


async def _runs_for(client: AsyncClient, headers: dict, order: dict) -> list[dict]:
    return [row for row in await runs(client, headers) if row["order_id"] == order["id"]]


async def _install(client: AsyncClient, headers: dict, key: str, **body: Any) -> dict[str, Any]:
    response = await client.post(
        f"/v1/automation/recipes/{key}/install", headers=headers, json={"locale": "en", **body}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _confirmations(order_id: str) -> list[OrderConfirmation]:
    from tests.test_confirmation_queue import _rows

    return await _rows(OrderConfirmation, OrderConfirmation.order_id == uuid.UUID(order_id))


async def _read(shop: dict, topic: OutboxTopic, payload: dict[str, Any]) -> list[tuple[str, dict]]:
    """What the automation reader makes of one event, without writing it."""
    event = OutboxEvent(topic=str(topic), payload=payload, tenant_id=tenant(shop))
    token = set_context(RequestContext(trace_id="test", tenant_id=tenant(shop)))
    try:
        async with session_scope() as db:
            reading = await read_event(db, event)
    finally:
        clear_context(token)
    return [(match.trigger, match.subject) for match in reading.matches]


def _payload(order: dict, *, outcome: str | None, state: str = "CONFIRMED") -> dict[str, Any]:
    # The shape of ConfirmationService.payload: ids and codes only.
    return {
        "confirmation_id": str(uuid.uuid4()),
        "order_id": order["id"],
        "state": state,
        "cause": None,
        "method": None,
        "decision": "CALL",
        "reason_code": "NEW_BUYER",
        "order_outcome": outcome,
        "attempt_count": 1,
    }


# ------------------------------------------------------------- catalog ---


async def test_the_builder_lists_the_new_triggers_actions_and_recipes(client, headers):
    catalog = (await client.get("/v1/automation/catalog", headers=headers)).json()
    triggers = {row["key"]: row["subject"] for row in catalog["workflow_triggers"]}
    assert {key: triggers.get(key) for key in NEW_TRIGGERS} == NEW_TRIGGERS
    needs = {row["key"]: row["needs"] for row in catalog["workflow_actions"]}
    assert needs["REQUEST_CONFIRMATION"] == "order"
    assert needs["CONVERT_DRAFT_TO_ORDER"] == "draft"
    assert catalog["subjects"]["draft"] == ["draft"]

    listed = {
        row["key"]: row
        for row in (await client.get("/v1/automation/recipes", headers=headers)).json()["items"]
    }
    confirm, book = listed["order_confirm_customer"], listed["confirmation_book_track"]
    assert (confirm["trigger"], confirm["needs"]) == ("order.created", [])
    assert (book["trigger"], book["needs"]) == ("confirmation.confirmed", ["provider", "channel"])
    for row in (confirm, book):
        assert row["name"]["en"] and row["name"]["bn"] != row["name"]["en"]
    # The recipes are ordinary definitions: the request, then booking + update.
    steps = recipes.build(
        "confirmation_book_track", {"locale": "bn", "provider": "pathao", "channel": "WHATSAPP"}
    )["steps"]
    assert [(s["action"], s["config"]) for s in steps] == [
        ("BOOK_COURIER", {"provider": "pathao"}),
        ("SEND_TEMPLATE", {"template_key": "order_update", "locale": "bn", "channel": "WHATSAPP"}),
    ]
    [ask] = recipes.build("order_confirm_customer", {"locale": "en"})["steps"]
    assert ask["action"] == "REQUEST_CONFIRMATION"


async def test_each_action_fits_only_its_subject(client, headers):
    async def problems(trigger: str, steps: list[dict], **extra: Any) -> list[str]:
        created = await workflow(client, headers, trigger, steps, publish=False, **extra)
        response = await client.post(
            f"/v1/automation/workflows/{created['id']}/publish",
            headers=headers,
            json={"enable": True},
        )
        if response.status_code == 200:
            return []
        assert response.status_code == 422, response.text
        return response.json()["details"]["problems"]

    convert = action("convert", "CONVERT_DRAFT_TO_ORDER")
    ask = action("ask", "REQUEST_CONFIRMATION")
    assert await problems("intake.draft_ready", [ask]) == [
        "ACTION_NOT_AVAILABLE:REQUEST_CONFIRMATION"
    ]
    assert await problems("order.created", [convert]) == [
        "ACTION_NOT_AVAILABLE:CONVERT_DRAFT_TO_ORDER"
    ]
    # A draft has no order or customer yet: their conditions do not fit.
    assert await problems(
        "intake.draft_ready",
        [convert],
        conditions={"conditions": [{"field": "status", "op": "eq", "value": "DRAFT"}]},
    ) == ["CONDITION_NOT_AVAILABLE:status"]
    assert await problems("intake.draft_ready", [convert, notice()]) == []
    assert await problems("confirmation.no_answer", [notice()]) == []


async def test_requesting_confirmation_cannot_be_switched_on_while_confirmations_are_off(
    client, headers
):
    draft = await workflow(
        client, headers, "order.created", [action("ask", "REQUEST_CONFIRMATION")], publish=False
    )
    refused = await client.post(
        f"/v1/automation/workflows/{draft['id']}/publish", headers=headers, json={"enable": True}
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["details"]["blocker"] == "CONFIRMATION_ENGINE_DISABLED"
    # Saved switched off it publishes; the recipe stays a draft with the reason.
    kept = await client.post(
        f"/v1/automation/workflows/{draft['id']}/publish", headers=headers, json={"enable": False}
    )
    assert kept.status_code == 200, kept.text
    installed = await _install(client, headers, "order_confirm_customer")
    assert installed["blocker"] == "CONFIRMATION_ENGINE_DISABLED"
    assert installed["enabled"] is False


# ------------------------------------------------- REQUEST_CONFIRMATION ---


async def test_a_new_order_is_sent_to_confirmation_once_and_the_policy_decides(
    client, shop, headers, confirmation_on
):
    installed = await _install(client, headers, "order_confirm_customer")
    assert installed["blocker"] is None and installed["enabled"] is True
    order = await _order(client, shop, "01700000061")
    [queued] = await runs(client, headers)
    assert queued["trigger"] == "order.created" and queued["order_id"] == order["id"]

    preview = await client.post(
        f"/v1/automation/workflows/{installed['id']}/preview",
        headers=headers,
        json={"order_id": order["id"]},
    )
    assert [(s["action"], s["would"]) for s in preview.json()["steps"]] == [
        ("REQUEST_CONFIRMATION", "RUN")
    ]
    assert await _confirmations(order["id"]) == []  # a preview writes nothing

    await run(shop, queued["id"])
    await run(shop, queued["id"])  # a duplicate dispatcher tick does nothing
    done = await detail(client, headers, queued["id"])
    [row] = await _confirmations(order["id"])
    assert done["status"] == "SUCCEEDED"
    [step] = done["steps"]
    # A new buyer: the policy queues a call. Nobody is called from the step.
    assert (step["status"], step["outcome"]) == ("SUCCEEDED", "CALL")
    assert (row.state, row.decision, row.requested_by) == ("CALL_QUEUED", "CALL", None)

    again = await client.post(
        f"/v1/automation/workflows/{installed['id']}/run",
        headers=headers,
        json={"order_id": order["id"], "confirm": True},
    )
    assert again.status_code == 202, again.text
    await run(shop, again.json()["id"])
    repeat = await detail(client, headers, again.json()["id"])
    assert repeat["steps"][0]["outcome"] == "ALREADY_REQUESTED"
    assert len(await _confirmations(order["id"])) == 1
    preview = await client.post(
        f"/v1/automation/workflows/{installed['id']}/preview",
        headers=headers,
        json={"order_id": order["id"]},
    )
    assert preview.json()["steps"][0]["reason"] == "ALREADY_REQUESTED"


async def test_a_confirmed_order_or_a_switched_off_engine_is_skipped_with_a_reason(
    client, shop, headers, settings
):
    from app.common.feature_flags import FlagKey
    from tests.conftest import _global_flag_on

    switch = _global_flag_on(settings, str(FlagKey.CONFIRMATION_ENGINE_ENABLED))
    await switch.__anext__()
    try:
        await workflow(client, headers, "order.created", [action("ask", "REQUEST_CONFIRMATION")])
        moved = await _order(client, shop, "01700000062")
        updated = await client.patch(
            f"/v1/orders/{moved['id']}", headers=headers, json={"status": "CONFIRMED"}
        )
        assert updated.status_code == 200, updated.text
        [row] = await _runs_for(client, headers, moved)
        await run(shop, row["id"])
        step = (await detail(client, headers, row["id"]))["steps"][0]
        assert (step["status"], step["outcome"]) == ("SKIPPED", "ORDER_NOT_DRAFT")
        assert await _confirmations(moved["id"]) == []
        later = await _order(client, shop, "01700000063")
    finally:
        await switch.aclose()  # the operator switches confirmations off
    [row] = await _runs_for(client, headers, later)
    await run(shop, row["id"])
    step = (await detail(client, headers, row["id"]))["steps"][0]
    assert (step["status"], step["outcome"]) == ("SKIPPED", "CONFIRMATION_ENGINE_DISABLED")
    assert await _confirmations(later["id"]) == []


# ----------------------------------------------------- confirmation.* ---


async def test_confirmed_and_cancelled_start_only_when_the_answer_moved_the_order(client, shop):
    order = await _order(client, shop, "01700000064")
    subject = {"order_id": order["id"], "customer_id": order["customer_id"]}
    yes, no = OutboxTopic.CONFIRMATION_CONFIRMED, OutboxTopic.CONFIRMATION_CANCELLED

    assert await _read(shop, yes, _payload(order, outcome="APPLIED")) == [
        ("confirmation.confirmed", subject)
    ]
    assert await _read(shop, no, _payload(order, outcome="APPLIED", state="CANCELLED")) == [
        ("confirmation.cancelled", subject)
    ]
    # Edited after the call, or no longer a draft: the seller decides, nothing runs.
    for outcome in ("ORDER_CHANGED", "ORDER_NOT_DRAFT"):
        assert await _read(shop, yes, _payload(order, outcome=outcome)) == []
        assert await _read(shop, no, _payload(order, outcome=outcome, state="CANCELLED")) == []
    # Ended because the order itself was cancelled: that is ``order.cancelled``.
    assert await _read(shop, no, _payload(order, outcome=None, state="CANCELLED")) == []
    for topic, trigger in (
        (OutboxTopic.CONFIRMATION_NO_ANSWER, "confirmation.no_answer"),
        (OutboxTopic.CONFIRMATION_CALLBACK_REQUESTED, "confirmation.callback_requested"),
    ):
        assert await _read(shop, topic, _payload(order, outcome=None, state="NO_ANSWER")) == [
            (trigger, subject)
        ]
    # An order that is not this shop's (or not there) starts nothing.
    stranger = {**_payload(order, outcome="APPLIED"), "order_id": str(uuid.uuid4())}
    assert await _read(shop, yes, stranger) == []
    # The internal "send the link" topic is not a trigger.
    assert (
        await _read(shop, OutboxTopic.CONFIRMATION_LINK_REQUESTED, {"order_id": order["id"]}) == []
    )


async def test_an_unanswered_call_runs_its_workflow(client, shop, headers):
    wf = await workflow(client, headers, "confirmation.no_answer", [notice("tell")])
    order = await _order(client, shop, "01700000065")
    token = set_context(RequestContext(trace_id="test", tenant_id=tenant(shop)))
    try:
        async with session_scope() as db:
            # As the state machine writes it after an unanswered IVR call (Phase 3).
            await enqueue(
                db,
                OutboxTopic.CONFIRMATION_NO_ANSWER,
                _payload(order, outcome=None, state="NO_ANSWER"),
            )
    finally:
        clear_context(token)
    [row] = await runs(client, headers, workflow_id=wf["id"])
    assert row["trigger"] == "confirmation.no_answer" and row["order_id"] == order["id"]
    await run(shop, row["id"])
    assert (await detail(client, headers, row["id"]))["status"] == "SUCCEEDED"
    automation = Notification.kind == "AUTOMATION"
    assert await count(Notification, Notification.tenant_id == tenant(shop), automation) == 1


@pytest.mark.usefixtures("courier_flags_on")
async def test_the_customers_yes_books_the_courier_once(
    client,
    shop,
    headers,
    confirmation_on,
    steadfast,  # noqa: F811 - the imported fixture
    monkeypatch,
):
    from tests.fixtures.steadfast import bodies

    monkeypatch.setattr(manual, "utc_now", lambda: NOON)
    await _install(client, headers, "order_confirm_customer")
    booker = await workflow(
        client,
        headers,
        "confirmation.confirmed",
        [action("book", "BOOK_COURIER", provider="steadfast")],
        name="Book on yes",
    )
    # A V3.4 "order confirmed → book" workflow on the same shop as well.
    legacy = await workflow(
        client,
        headers,
        "order.confirmed",
        [action("book", "BOOK_COURIER", provider="steadfast")],
        name="Book on confirmed",
    )
    order = await _bookable(client, shop)
    [ask] = await runs(client, headers)
    await run(shop, ask["id"])
    assert [row.state for row in await _confirmations(order["id"])] == ["CALL_QUEUED"]
    assert await runs(client, headers, workflow_id=booker["id"]) == []  # no answer yet

    answered = await client.post(
        f"/v1/orders/{order['id']}/confirmation/mark_confirmed", headers=headers
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["confirmation"]["order_outcome"] == "APPLIED"
    steadfast.enqueue("POST", "/create_order", body=bodies.create_ok(invoice=order["order_number"]))
    [on_yes] = await runs(client, headers, workflow_id=booker["id"])
    [on_confirmed] = await runs(client, headers, workflow_id=legacy["id"])
    assert on_yes["trigger"] == "confirmation.confirmed" and on_yes["order_id"] == order["id"]
    for row in (on_yes, on_confirmed):
        await run(shop, row["id"])
        await run(shop, row["id"])
    outcomes = [
        (await detail(client, headers, row["id"]))["steps"][0]["outcome"]
        for row in (on_yes, on_confirmed)
    ]
    # Two workflows asked; the booking service booked once.
    assert outcomes[0] != "ALREADY_BOOKED" and outcomes[1] == "ALREADY_BOOKED"
    assert len(steadfast.calls_to("POST", "/create_order")) == 1

    with_tracking = await _install(
        client, headers, "confirmation_book_track", provider="steadfast", channel="EMAIL"
    )
    assert with_tracking["blocker"] == "CHANNEL_DISABLED"  # no email channel set up here
    assert with_tracking["draft"]["trigger"] == "confirmation.confirmed"


# ----------------------------------------------------- intake.draft_ready ---


async def test_a_ready_draft_runs_its_workflow_but_is_not_converted_before_autopilot(
    client, shop, headers, intake_on
):
    from tests.test_intake_capture import CHAT, MANUAL

    wf = await workflow(
        client,
        headers,
        "intake.draft_ready",
        [action("convert", "CONVERT_DRAFT_TO_ORDER"), notice("tell")],
    )
    orders_before = await count(Order, Order.tenant_id == tenant(shop))
    captured = await client.post(
        MANUAL, json={"source": "SHARE", "messages": [{"text": "\n".join(CHAT)}]}, headers=headers
    )
    assert captured.status_code == 200, captured.text
    draft = captured.json()["draft"]
    [row] = await runs(client, headers, workflow_id=wf["id"])
    assert row["trigger"] == "intake.draft_ready"
    await run(shop, row["id"])
    done = await detail(client, headers, row["id"])
    assert done["status"] == "SUCCEEDED"
    assert [(s["step_id"], s["status"], s["outcome"]) for s in done["steps"]] == [
        ("convert", "SKIPPED", "AUTOPILOT_DISABLED"),
        ("tell", "SUCCEEDED", None),
    ]
    assert await count(Order, Order.tenant_id == tenant(shop)) == orders_before
    from tests.test_confirmation_queue import _rows

    [kept] = await _rows(ChatOrderDraft, ChatOrderDraft.id == uuid.UUID(draft["id"]))
    assert kept.status == "READY_FOR_REVIEW" and kept.confirmed_order_id is None
    # A draft id that is not this shop's starts nothing.
    payload = {"draft_id": str(uuid.uuid4()), "capture_source": "SHARE"}
    assert await _read(shop, OutboxTopic.INTAKE_DRAFT_READY, payload) == []
    stored = await count(
        OutboxEvent,
        OutboxEvent.tenant_id == tenant(shop),
        OutboxEvent.topic == str(OutboxTopic.INTAKE_DRAFT_READY),
    )
    assert stored == 1
