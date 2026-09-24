"""V3.4 Automation Pro: multi-step workflows on the V2 engine.

Each test defends one promise from the V3.4 brief: steps run once, waits are
durable rows, a run finishes on its own version, loops stop, compliance checks
still apply, and nothing a workflow does can double-book, double-send or move
stock or money.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa

from app.automation import actions, jobs, service
from app.automation.models import AutomationExecution, AutomationStepRun
from app.common.audit import AuditLog
from app.common.outbox import OutboxEvent
from app.consignments.models import Consignment
from app.core.clock import DHAKA, utc_now
from app.core.context import RequestContext, clear_context, set_context
from app.customers.crm_models import CustomerFollowUp, CustomerTagLink
from app.db.session import session_scope, system_session
from app.integrations.models import IntegrationConnection, IntegrationEvent
from app.notifications.models import Notification
from app.products.models import StockMovement
from app.tenants.models import TenantUser
from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.test_auth_flow import auth_header

# ----------------------------------------------------------------- helpers --


def action(step_id: str, name: str, **config: Any) -> dict[str, Any]:
    return {"type": "action", "id": step_id, "action": name, "config": config}


def followup(step_id: str = "fu", text: str = "Call the buyer") -> dict[str, Any]:
    return action(step_id, "CREATE_FOLLOWUP", text=text, due_hours=4)


def notice(step_id: str = "tell") -> dict[str, Any]:
    return action(
        step_id,
        "SELLER_NOTIFICATION",
        title_en="Heads up",
        title_bn="খেয়াল করুন",
        text_en="Something happened",
        text_bn="কিছু একটা হয়েছে",
    )


async def tag(client, headers, name: str) -> str:
    response = await client.post("/v1/customers/crm/tags", headers=headers, json={"name": name})
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


async def workflow(
    client,
    headers,
    trigger: str,
    steps: list[dict],
    *,
    conditions: dict | None = None,
    publish: bool = True,
    name: str = "Workflow",
) -> dict[str, Any]:
    definition = {"trigger": trigger, "steps": steps}
    if conditions:
        definition["conditions"] = conditions
    created = await client.post(
        "/v1/automation/workflows", headers=headers, json={"name": name, "definition": definition}
    )
    assert created.status_code == 201, created.text
    row = created.json()
    if publish:
        published = await client.post(
            f"/v1/automation/workflows/{row['id']}/publish", headers=headers, json={"enable": True}
        )
        assert published.status_code == 200, published.text
        row = published.json()
    return row


async def runs(client, headers, **params: Any) -> list[dict[str, Any]]:
    response = await client.get("/v1/automation/executions", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def detail(client, headers, run_id: str) -> dict[str, Any]:
    response = await client.get(f"/v1/automation/executions/{run_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def run(shop: dict, run_id: str) -> None:
    await jobs.execute(uuid.UUID(shop["tenant_id"]), uuid.UUID(run_id))


async def make_due(run_id: str, *, receipt_age: timedelta | None = None) -> None:
    """Move time forward for one run: its wake-up (and wait start) are now past."""
    async with system_session("test: time passes") as db:
        row = await db.get(AutomationExecution, uuid.UUID(run_id))
        row.next_attempt_at = utc_now() - timedelta(seconds=1)
        if receipt_age is not None:
            for receipt in (
                await db.scalars(
                    sa.select(AutomationStepRun).where(AutomationStepRun.execution_id == row.id)
                )
            ).all():
                receipt.created_at = utc_now() - receipt_age


async def count(model: Any, *where: Any) -> int:
    async with system_session("test: count") as db:
        return int(
            await db.scalar(sa.select(sa.func.count()).select_from(model).where(*where)) or 0
        )


async def confirm(client, headers, order_id: str) -> None:
    response = await client.patch(
        f"/v1/orders/{order_id}", headers=headers, json={"status": "CONFIRMED"}
    )
    assert response.status_code == 200, response.text


@pytest.fixture
async def shop(client, unique_phone):
    return await signed_in_shop(client, unique_phone)


@pytest.fixture
def headers(shop):
    return auth_header(shop)


def tenant(shop) -> uuid.UUID:
    return uuid.UUID(shop["tenant_id"])


# -------------------------------------------------------- multi-step flows --


async def test_multi_step_workflow_runs_every_step_once(client, shop, headers):
    vip = await tag(client, headers, "Online buyer")
    await workflow(
        client,
        headers,
        "order.created",
        [action("t", "ADD_TAG", tag_id=vip), followup(), notice()],
    )
    order = (await create_order(client, shop))["order"]
    [queued] = await runs(client, headers)
    assert queued["status"] == "QUEUED" and queued["order_id"] == order["id"]
    await run(shop, queued["id"])
    await run(shop, queued["id"])  # a duplicate dispatcher tick does nothing
    done = await detail(client, headers, queued["id"])
    assert done["status"] == "SUCCEEDED"
    assert [(s["step_id"], s["status"]) for s in done["steps"]] == [
        ("t", "SUCCEEDED"),
        ("fu", "SUCCEEDED"),
        ("tell", "SUCCEEDED"),
    ]
    assert done["started_at"] and done["finished_at"] and done["duration_seconds"] is not None
    tid = tenant(shop)
    assert await count(CustomerTagLink, CustomerTagLink.tenant_id == tid) == 1
    assert await count(CustomerFollowUp, CustomerFollowUp.tenant_id == tid) == 1
    assert (
        await count(Notification, Notification.tenant_id == tid, Notification.kind == "AUTOMATION")
        == 1
    )


async def test_branch_takes_then_or_else_with_and_or_groups(client, shop, headers):
    big, small = await tag(client, headers, "Big"), await tag(client, headers, "Small")
    await workflow(
        client,
        headers,
        "order.created",
        [
            {
                "type": "branch",
                "id": "size",
                "conditions": {
                    "match": "any",
                    "conditions": [{"field": "cod_amount_paisa", "op": "gte", "value": 500_000}],
                    "groups": [
                        {
                            "match": "all",
                            "conditions": [
                                {"field": "payment_method", "op": "eq", "value": "PREPAID"},
                                {"field": "channel", "op": "eq", "value": "MANUAL"},
                            ],
                        }
                    ],
                },
                "then": [action("big", "ADD_TAG", tag_id=big)],
                "else": [action("small", "ADD_TAG", tag_id=small)],
            },
            notice("after"),
        ],
    )
    await create_order(client, shop, cod_amount_paisa=900_000, phone="01711111111")
    await create_order(client, shop, cod_amount_paisa=10_000, phone="01722222222")
    outcomes = {}
    for row in await runs(client, headers):
        await run(shop, row["id"])
        result = await detail(client, headers, row["id"])
        assert result["status"] == "SUCCEEDED"
        steps = {s["step_id"]: s for s in result["steps"]}
        # Both arms rejoin the step after the branch.
        assert steps["after"]["status"] == "SUCCEEDED"
        outcomes[steps["size"]["outcome"]] = [s for s in steps if s in {"big", "small"}]
    assert outcomes == {"THEN": ["big"], "ELSE": ["small"]}


# ------------------------------------------------------------------- waits --


async def test_durable_delay_waits_once_and_survives_a_restart(client, shop, headers):
    await workflow(
        client,
        headers,
        "order.created",
        [{"type": "delay", "id": "later", "mode": "duration", "minutes": 30}, followup()],
    )
    await create_order(client, shop)
    [row] = await runs(client, headers)
    await run(shop, row["id"])
    waiting = await detail(client, headers, row["id"])
    assert waiting["status"] == "WAITING" and waiting["current_step"] == "fu"
    wake = waiting["next_attempt_at"]
    # A restarted worker (a fresh call, nothing in memory) finds nothing due.
    await run(shop, row["id"])
    assert (await detail(client, headers, row["id"]))["status"] == "WAITING"
    assert await count(CustomerFollowUp, CustomerFollowUp.tenant_id == tenant(shop)) == 0
    assert wake is not None
    await make_due(row["id"])
    # The regular dispatcher, not a scheduler of its own, picks it up.
    await jobs.dispatch_automation()
    await jobs.dispatch_automation()
    assert (await detail(client, headers, row["id"]))["status"] == "SUCCEEDED"
    assert await count(CustomerFollowUp, CustomerFollowUp.tenant_id == tenant(shop)) == 1


async def test_time_of_day_delay_uses_the_shop_timezone(client, shop, headers):
    token = set_context(RequestContext(trace_id="test", tenant_id=tenant(shop)))
    try:
        async with session_scope() as db:
            execution = AutomationExecution(
                tenant_id=tenant(shop),
                rule_id=uuid.uuid4(),
                event_id=uuid.uuid4(),
                rule_version=1,
                snapshot={},
                next_attempt_at=utc_now(),
            )
            wake = await jobs._wake_at(
                db, execution, {"mode": "until_time", "time": "09:00", "day_offset": 1}
            )
    finally:
        clear_context(token)
    local = wake.astimezone(DHAKA)
    assert (local.hour, local.minute) == (9, 0)
    assert wake > utc_now() and wake - utc_now() <= timedelta(days=2)
    assert local.date() > utc_now().astimezone(DHAKA).date()


async def test_event_wait_resumes_when_the_order_is_confirmed(client, shop, headers):
    done = await tag(client, headers, "Confirmed")
    await workflow(
        client,
        headers,
        "order.created",
        [
            {"type": "wait_event", "id": "w", "event": "order.confirmed", "timeout_minutes": 1440},
            action("t", "ADD_TAG", tag_id=done),
        ],
    )
    order = (await create_order(client, shop))["order"]
    [row] = await runs(client, headers)
    await run(shop, row["id"])
    waiting = await detail(client, headers, row["id"])
    assert waiting["status"] == "WAITING" and waiting["waiting_for"] == "order.confirmed"
    await confirm(client, headers, order["id"])  # wakes it in the same transaction
    woken = await detail(client, headers, row["id"])
    assert woken["status"] == "WAITING" and woken["waiting_for"] is None
    await run(shop, row["id"])
    finished = await detail(client, headers, row["id"])
    assert finished["status"] == "SUCCEEDED"
    assert {s["step_id"]: s["outcome"] for s in finished["steps"]}["w"] == "EVENT"
    assert await count(CustomerTagLink, CustomerTagLink.tenant_id == tenant(shop)) == 1


async def test_event_wait_timeout_takes_the_else_path(client, shop, headers):
    shipped = await tag(client, headers, "Shipped")
    await workflow(
        client,
        headers,
        "order.created",
        [
            {"type": "wait_event", "id": "w", "event": "order.confirmed", "timeout_minutes": 60},
            {
                "type": "branch",
                "id": "ok",
                "conditions": {
                    "conditions": [{"field": "status", "op": "eq", "value": "CONFIRMED"}]
                },
                "then": [action("t", "ADD_TAG", tag_id=shipped)],
                "else": [followup("chase")],
            },
        ],
    )
    await create_order(client, shop)
    [row] = await runs(client, headers)
    await run(shop, row["id"])
    await make_due(row["id"], receipt_age=timedelta(minutes=61))
    await run(shop, row["id"])
    result = await detail(client, headers, row["id"])
    assert result["status"] == "SUCCEEDED"
    steps = {s["step_id"]: s for s in result["steps"]}
    assert steps["w"]["status"] == "TIMED_OUT" and steps["ok"]["outcome"] == "ELSE"
    assert "t" not in steps and steps["chase"]["status"] == "SUCCEEDED"


async def test_wait_for_a_followup_completed_by_the_seller(client, shop, headers):
    await workflow(
        client,
        headers,
        "order.created",
        [
            followup("fu"),
            {
                "type": "wait_event",
                "id": "w",
                "event": "followup.completed",
                "timeout_minutes": 600,
            },
            notice("thanks"),
        ],
    )
    order = (await create_order(client, shop))["order"]
    [row] = await runs(client, headers)
    await run(shop, row["id"])
    assert (await detail(client, headers, row["id"]))["status"] == "WAITING"
    async with system_session("test: follow-up id") as db:
        fu = await db.scalar(
            sa.select(CustomerFollowUp).where(CustomerFollowUp.tenant_id == tenant(shop))
        )
    response = await client.patch(
        f"/v1/customers/{order['customer_id']}/follow-ups/{fu.id}",
        headers=headers,
        json={"completed": True},
    )
    assert response.status_code == 200, response.text
    await run(shop, row["id"])
    assert (await detail(client, headers, row["id"]))["status"] == "SUCCEEDED"


# ------------------------------------------------------------- idempotency --


async def test_a_replayed_event_starts_one_run(client, shop, headers):
    await workflow(client, headers, "order.created", [notice()])
    await create_order(client, shop)
    async with system_session("test: replay") as db:
        event = await db.scalar(
            sa.select(OutboxEvent).where(
                OutboxEvent.tenant_id == tenant(shop), OutboxEvent.topic == "order.created"
            )
        )
    token = set_context(RequestContext(trace_id="test", tenant_id=tenant(shop)))
    try:
        for _ in range(2):
            async with session_scope() as db:
                await service.schedule_event(db, event)
    finally:
        clear_context(token)
    assert len(await runs(client, headers)) == 1


async def test_retry_resumes_at_the_failed_step_without_repeating_effects(
    client, shop, headers, monkeypatch
):
    await workflow(client, headers, "order.created", [followup("first"), notice("second")])
    await create_order(client, shop)
    [row] = await runs(client, headers)
    original = actions.perform

    async def flaky(db, execution, step):
        if step["id"] == "second":
            raise OSError("temporary provider-secret-text")
        return await original(db, execution, step)

    monkeypatch.setattr(jobs, "perform", flaky)
    for _ in range(jobs.MAX_ATTEMPTS):
        await make_due(row["id"])
        await run(shop, row["id"])
    failed = await detail(client, headers, row["id"])
    assert failed["status"] == "FAILED" and failed["retryable"] is True
    assert failed["current_step"] == "second"
    assert failed["last_error"] == "TRANSIENT:OSError"
    assert "provider-secret-text" not in str(failed)
    monkeypatch.setattr(jobs, "perform", original)
    retried = await client.post(f"/v1/automation/executions/{row['id']}/retry", headers=headers)
    assert retried.status_code == 200, retried.text
    await run(shop, row["id"])
    assert (await detail(client, headers, row["id"]))["status"] == "SUCCEEDED"
    # The first step's follow-up exists once: the retry did not rerun it.
    assert await count(CustomerFollowUp, CustomerFollowUp.tenant_id == tenant(shop)) == 1
    assert (
        await count(
            AuditLog,
            AuditLog.tenant_id == tenant(shop),
            AuditLog.action == "automation.execution_retried",
        )
        == 1
    )


# ----------------------------------------------------------------- loops --


async def _schedule_as(shop, event: OutboxEvent, cause: service.Cause | None) -> None:
    token = set_context(RequestContext(trace_id="test", tenant_id=tenant(shop)))
    marker = service.automation_cause.set(cause)
    try:
        async with session_scope() as db:
            await service.schedule_event(db, event)
    finally:
        service.automation_cause.reset(marker)
        clear_context(token)


async def test_loops_depth_and_repeats_are_stopped_with_a_reason(client, shop, headers):
    wf = await workflow(client, headers, "order.created", [notice()])
    other = uuid.uuid4()
    order = (await create_order(client, shop))["order"]

    def event() -> OutboxEvent:
        return OutboxEvent(
            id=uuid.uuid4(),
            tenant_id=tenant(shop),
            topic="order.created",
            payload={"order_id": order["id"]},
        )

    rule = uuid.UUID(wf["id"])
    # Self-trigger: the workflow is already in this chain.
    await _schedule_as(shop, event(), service.Cause(uuid.uuid4(), other, 1, (str(rule),)))
    await _schedule_as(shop, event(), service.Cause(uuid.uuid4(), rule, 0, ()))
    # Too deep a chain of workflow-caused runs.
    await _schedule_as(shop, event(), service.Cause(uuid.uuid4(), other, service.MAX_DEPTH, ()))
    # A chain within limits is allowed and remembers its ancestry.
    await _schedule_as(shop, event(), service.Cause(uuid.uuid4(), other, 0, ()))
    reasons = [(r["status"], r["last_error"], r["depth"]) for r in await runs(client, headers)]
    assert ("SKIPPED", "LOOP_DETECTED", 2) in reasons
    assert ("SKIPPED", "LOOP_DETECTED", 1) in reasons
    assert ("SKIPPED", "DEPTH_LIMIT", service.MAX_DEPTH + 1) in reasons
    assert ("QUEUED", None, 1) in reasons
    # Repeat guard: the same workflow for the same order, too often in a day.
    for _ in range(service.REPEAT_LIMIT):
        await _schedule_as(shop, event(), None)
    assert (await runs(client, headers))[0]["last_error"] == "REPEAT_LIMIT"


# ------------------------------------------------------------- versioning --


async def test_a_run_finishes_on_its_version_and_new_events_use_the_new_one(client, shop, headers):
    one, two = await tag(client, headers, "V1"), await tag(client, headers, "V2")
    wf = await workflow(client, headers, "order.created", [action("t", "ADD_TAG", tag_id=one)])
    await create_order(client, shop, phone="01733333333")
    [first] = await runs(client, headers)
    edited = await client.put(
        f"/v1/automation/workflows/{wf['id']}",
        headers=headers,
        json={
            "name": "Workflow v2",
            "definition": {
                "trigger": "order.created",
                "steps": [action("t", "ADD_TAG", tag_id=two)],
            },
        },
    )
    assert edited.status_code == 200 and edited.json()["has_unpublished_changes"] is True
    published = await client.post(
        f"/v1/automation/workflows/{wf['id']}/publish", headers=headers, json={"enable": True}
    )
    assert published.json()["published_version"] == 2
    await create_order(client, shop, phone="01744444444")
    await run(shop, first["id"])
    second = next(r for r in await runs(client, headers) if r["id"] != first["id"])
    await run(shop, second["id"])
    async with system_session("test: tags per version") as db:
        links = (
            (
                await db.execute(
                    sa.select(CustomerTagLink.tag_id).where(
                        CustomerTagLink.tenant_id == tenant(shop)
                    )
                )
            )
            .scalars()
            .all()
        )
    assert sorted(map(str, links)) == sorted([one, two])
    assert (await detail(client, headers, first["id"]))["version"] == 1
    assert (await detail(client, headers, second["id"]))["version"] == 2
    versions = (await client.get(f"/v1/automation/workflows/{wf['id']}", headers=headers)).json()
    assert [v["number"] for v in versions["versions"]] == [2, 1]


# ---------------------------------------------------------- compliance ---


async def test_messages_respect_consent_template_purpose_and_frequency(
    client, shop, headers, monkeypatch
):
    from app.messaging import service as messaging

    monkeypatch.setattr(messaging, "capability", lambda _: (True, None))
    await client.post("/v1/messaging/channels/EMAIL", headers=headers, json={"enabled": True})
    send = {"template_key": "order_update", "locale": "bn", "channel": "EMAIL"}
    # Only transactional templates: an unknown or marketing key is refused.
    refused = await client.post(
        "/v1/automation/workflows",
        headers=headers,
        json={
            "name": "Promo",
            "definition": {
                "trigger": "order.created",
                "steps": [action("m", "SEND_TEMPLATE", **{**send, "template_key": "big_sale"})],
            },
        },
    )
    wf = refused.json()
    publish = await client.post(
        f"/v1/automation/workflows/{wf['id']}/publish", headers=headers, json={"enable": True}
    )
    assert publish.status_code == 422
    assert "TEMPLATE_NOT_TRANSACTIONAL" in publish.json()["details"]["problems"]

    await workflow(
        client,
        headers,
        "order.created",
        [action("m1", "SEND_TEMPLATE", **send), action("m2", "SEND_TEMPLATE", **send), notice()],
    )
    order = (await create_order(client, shop))["order"]
    [row] = await runs(client, headers, status="QUEUED")
    await run(shop, row["id"])
    no_consent = await detail(client, headers, row["id"])
    # No consent: nothing is sent, the rest of the workflow still runs.
    assert no_consent["status"] == "SUCCEEDED"
    assert [(s["step_id"], s["status"], s["outcome"]) for s in no_consent["steps"]][:2] == [
        ("m1", "SKIPPED", "CONSENT_REQUIRED"),
        ("m2", "SKIPPED", "CONSENT_REQUIRED"),
    ]
    consent = await client.post(
        "/v1/messaging/conversations",
        headers=headers,
        json={
            "customer_id": order["customer_id"],
            "recipient": "buyer@example.com",
            "consent": True,
            "evidence": "Buyer asked for updates",
        },
    )
    assert consent.status_code == 201, consent.text
    monkeypatch.setattr(actions, "MESSAGE_LIMIT_PER_DAY", 1)
    await create_order(client, shop)
    [second] = await runs(client, headers, status="QUEUED")
    await run(shop, second["id"])
    result = await detail(client, headers, second["id"])
    assert [(s["step_id"], s["status"], s["outcome"]) for s in result["steps"]][:2] == [
        ("m1", "SUCCEEDED", None),
        ("m2", "SKIPPED", "FREQUENCY_LIMIT"),
    ]


# --------------------------------------------------------- CRM / segments --


async def test_repeat_buyer_recipe_tags_on_the_second_order(client, shop, headers):
    installed = await client.post(
        "/v1/automation/recipes/repeat_buyer_tag/install", headers=headers, json={"locale": "bn"}
    )
    assert installed.status_code == 201, installed.text
    body = installed.json()
    assert body["blocker"] is None and body["enabled"] and body["published_version"] == 1
    catalog = (await client.get("/v1/automation/recipes", headers=headers)).json()["items"]
    assert next(r for r in catalog if r["key"] == "repeat_buyer_tag")["installed"] is True
    await create_order(client, shop)
    assert await runs(client, headers) == []  # the first order is not a repeat
    await create_order(client, shop)
    [row] = await runs(client, headers)
    assert row["trigger"] == "customer.segment_entered"
    await run(shop, row["id"])
    assert (await detail(client, headers, row["id"]))["status"] == "SUCCEEDED"
    assert await count(CustomerTagLink, CustomerTagLink.tenant_id == tenant(shop)) == 1
    # Still an ordinary, editable workflow.
    editable = await client.get(f"/v1/automation/workflows/{body['id']}", headers=headers)
    assert editable.json()["draft"]["trigger"] == "customer.segment_entered"


async def test_recipe_with_missing_prerequisites_stays_a_draft(client, shop, headers):
    no_provider = await client.post(
        "/v1/automation/recipes/confirmed_book_track/install", headers=headers, json={}
    )
    assert no_provider.status_code == 422
    body = (
        await client.post(
            "/v1/automation/recipes/confirmed_book_track/install",
            headers=headers,
            json={"provider": "steadfast", "locale": "en"},
        )
    ).json()
    assert body["blocker"] in {"COURIER_NOT_CONNECTED", "CHANNEL_DISABLED"}
    assert body["enabled"] is False and body["published_version"] is None


# ---------------------------------------------------------- inventory ----


async def test_low_stock_crossing_notifies_once_and_never_moves_stock(client, shop, headers):
    await client.post("/v1/automation/recipes/low_stock_notify/install", headers=headers, json={})
    product = await create_product(client, shop, opening_stock=10, low_stock_threshold=5)
    movements = await count(StockMovement, StockMovement.tenant_id == tenant(shop))

    async def adjust(delta: int) -> None:
        response = await client.post(
            f"/v1/products/{product['id']}/stock-adjustments",
            headers=headers,
            json={"quantity_delta": delta, "note": "Counted the shelf"},
        )
        assert response.status_code == 201, response.text

    await adjust(-3)  # 7: above the level
    await adjust(-3)  # 4: crossed
    await adjust(-1)  # 3: still low, not a new crossing
    [row] = await runs(client, headers)
    assert row["trigger"] == "inventory.low"
    await run(shop, row["id"])
    assert (await detail(client, headers, row["id"]))["status"] == "SUCCEEDED"
    # The workflow recorded no stock movement of its own.
    assert await count(StockMovement, StockMovement.tenant_id == tenant(shop)) == movements + 3
    for forbidden in (
        "ADJUST_STOCK",
        "SET_STOCK",
        "LEDGER_ENTRY",
        "RECONCILE_PAYOUT",
        "HTTP_REQUEST",
    ):
        response = await client.post(
            "/v1/automation/workflows",
            headers=headers,
            json={
                "name": "No",
                "definition": {"trigger": "order.created", "steps": [action("x", forbidden)]},
            },
        )
        assert response.status_code == 422, forbidden


# ------------------------------------------------------------ integrations --


async def test_integration_failure_is_retried_once_through_the_integration_queue(
    client, shop, headers
):
    from app.integrations import service as integrations

    await workflow(
        client,
        headers,
        "integration.sync_failed",
        [action("retry", "RETRY_INTEGRATION_SYNC"), notice()],
    )
    token = set_context(RequestContext(trace_id="test", tenant_id=tenant(shop), user_id=None))
    try:
        async with session_scope() as db:
            conn = IntegrationConnection(
                tenant_id=tenant(shop),
                provider="SHOPIFY",
                name="Store",
                state="CONNECTED",
                created_by=uuid.UUID(shop["user"]["id"]) if "user" in shop else uuid.uuid4(),
                credentials_enc="enc-not-a-real-secret",
            )
            db.add(conn)
            await db.flush()
            problem = await integrations.record_issue(
                db,
                conn,
                kind="WEBHOOK",
                topic="orders/create",
                code="PROVIDER_TIMEOUT",
                external_ref="1001",
            )
            problem_id = problem.id
    finally:
        clear_context(token)
    [row] = await runs(client, headers)
    await run(shop, row["id"])
    result = await detail(client, headers, row["id"])
    assert result["status"] == "SUCCEEDED"
    async with system_session("test: problem state") as db:
        assert (await db.get(IntegrationEvent, problem_id)).status == "QUEUED"
    # The retried import fails again: same problem, so no second run.
    token = set_context(RequestContext(trace_id="test", tenant_id=tenant(shop)))
    try:
        async with session_scope() as db:
            again = await db.get(IntegrationEvent, problem_id)
            conn = await db.get(IntegrationConnection, again.connection_id)
            await integrations.record_issue(
                db,
                conn,
                kind="WEBHOOK",
                topic="orders/create",
                code="PROVIDER_TIMEOUT",
                external_ref="1001",
                event=again,
            )
    finally:
        clear_context(token)
    assert len(await runs(client, headers)) == 1
    # No credential or token in anything the retry centre shows.
    text = str(result) + str((await client.get("/v1/automation/workflows", headers=headers)).json())
    assert "enc-not-a-real-secret" not in text and "credentials" not in text


# ------------------------------------------------------------------ couriers --


@pytest.fixture
async def steadfast(db, settings, shop, monkeypatch):
    from app.consignments.service import ConsignmentService
    from app.core.security import CredentialVault
    from app.couriers.accounts import ConnectRequest, CourierAccountService
    from app.couriers.booking import CourierBookingService
    from app.couriers.registry import CourierAdapterRegistry
    from app.couriers.steadfast.adapter import SteadfastAdapter
    from app.couriers.steadfast.client import SteadfastClient, SteadfastConfig
    from app.couriers.steadfast.transport import FakeSteadfastTransport
    from app.money.service import ReceivableService
    from tests.fixtures.steadfast import bodies

    transport = FakeSteadfastTransport()

    async def no_sleep(_: float) -> None:
        return None

    registry = CourierAdapterRegistry(
        {
            "steadfast": lambda: SteadfastAdapter(
                SteadfastClient(
                    transport, config=SteadfastConfig(max_read_retries=0), sleep=no_sleep
                )
            )
        }
    )
    set_context(RequestContext(trace_id="test", tenant_id=tenant(shop)))
    accounts = CourierAccountService(db, vault=CredentialVault(settings), registry=registry)
    transport.enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    await accounts.connect(
        ConnectRequest(
            provider="steadfast", api_key="sfk-wf-key-abcd", secret_key="sfs-wf-secret-wxyz"
        )
    )
    await db.commit()

    def booking_service(session):
        vault = CredentialVault(settings)
        return CourierBookingService(
            session,
            accounts=CourierAccountService(session, vault=vault, registry=registry),
            consignments=ConsignmentService(session, receivables=ReceivableService(session)),
            vault=vault,
            settings=settings,
        )

    monkeypatch.setattr(jobs, "_booking_service", booking_service)
    return transport


async def _bookable(client, shop) -> dict:
    product = await create_product(client, shop, sku=f"WF-{uuid.uuid4().hex[:6]}")
    return (
        await create_order(
            client,
            shop,
            items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": 125_000}],
            cod_amount_paisa=125_000,
            address="House 12, Road 3, Mirpur 10, Dhaka",
        )
    )["order"]


async def test_courier_booking_happens_once_and_chains_with_causation(
    client, shop, headers, steadfast
):
    from tests.fixtures.steadfast import bodies

    booker = await workflow(
        client,
        headers,
        "order.confirmed",
        [action("book", "BOOK_COURIER", provider="steadfast")],
        name="Book",
    )
    tracker = await workflow(client, headers, "courier.booked", [notice("tracked")], name="Track")
    order = await _bookable(client, shop)
    steadfast.enqueue("POST", "/create_order", body=bodies.create_ok(invoice=order["order_number"]))
    await confirm(client, headers, order["id"])
    [row] = await runs(client, headers, workflow_id=booker["id"])
    await run(shop, row["id"])
    await run(shop, row["id"])
    assert (await detail(client, headers, row["id"]))["status"] == "SUCCEEDED"
    assert len(steadfast.calls_to("POST", "/create_order")) == 1
    # The booking's own event started the tracking workflow, one level deeper.
    [chained] = await runs(client, headers, workflow_id=tracker["id"])
    assert chained["depth"] == 1 and chained["status"] == "QUEUED"

    # A manual test run for the same, already booked order books nothing.
    tested = await client.post(
        f"/v1/automation/workflows/{booker['id']}/run",
        headers=headers,
        json={"order_id": order["id"]},
    )
    assert tested.status_code == 422  # confirmation is required
    tested = await client.post(
        f"/v1/automation/workflows/{booker['id']}/run",
        headers=headers,
        json={"order_id": order["id"], "confirm": True},
    )
    assert tested.status_code == 202, tested.text
    await run(shop, tested.json()["id"])
    again = await detail(client, headers, tested.json()["id"])
    assert again["status"] == "SUCCEEDED" and again["source"] == "TEST"
    assert again["steps"][0]["outcome"] == "ALREADY_BOOKED"
    assert len(steadfast.calls_to("POST", "/create_order")) == 1


async def test_an_ambiguous_booking_stops_and_is_never_retried(client, shop, headers, steadfast):
    from app.couriers.steadfast.errors import SteadfastAmbiguousError

    await workflow(
        client, headers, "order.confirmed", [action("book", "BOOK_COURIER", provider="steadfast")]
    )
    order = await _bookable(client, shop)
    steadfast.enqueue_error("POST", "/create_order", SteadfastAmbiguousError("lost"))
    await confirm(client, headers, order["id"])
    [row] = await runs(client, headers)
    await run(shop, row["id"])
    result = await detail(client, headers, row["id"])
    assert (result["status"], result["last_error"], result["retryable"]) == (
        "FAILED",
        "BOOKING_UNKNOWN",
        False,
    )
    refused = await client.post(f"/v1/automation/executions/{row['id']}/retry", headers=headers)
    assert refused.status_code == 409
    # Even forced back into the queue, the parcel check refuses to book again.
    async with system_session("test: force") as db:
        forced = await db.get(AutomationExecution, uuid.UUID(row["id"]))
        forced.status, forced.next_attempt_at = "QUEUED", utc_now() - timedelta(seconds=1)
    await run(shop, row["id"])
    assert (await detail(client, headers, row["id"]))["last_error"] == "BOOKING_UNKNOWN"
    assert len(steadfast.calls_to("POST", "/create_order")) == 1
    assert await count(Consignment, Consignment.order_id == uuid.UUID(order["id"])) == 1
    # A worker that died mid-call leaves the run RUNNING; recovery requeues it
    # and the parcel check still refuses to book.
    async with system_session("test: crashed worker") as db:
        crashed = await db.get(AutomationExecution, uuid.UUID(row["id"]))
        crashed.status = "RUNNING"
        crashed.updated_at = utc_now() - timedelta(minutes=30)
    await jobs.dispatch_automation()
    assert (await detail(client, headers, row["id"]))["last_error"] == "BOOKING_UNKNOWN"
    assert len(steadfast.calls_to("POST", "/create_order")) == 1


# --------------------------------------------------------- preview / test --


async def test_preview_shows_the_path_without_side_effects(client, shop, headers):
    wf = await workflow(
        client,
        headers,
        "order.created",
        [
            {
                "type": "branch",
                "id": "cod",
                "conditions": {
                    "conditions": [{"field": "payment_method", "op": "eq", "value": "COD"}]
                },
                "then": [followup("cod_call")],
                "else": [notice("prepaid")],
            },
            {"type": "wait_event", "id": "w", "event": "order.confirmed", "timeout_minutes": 60},
            action(
                "book", "SEND_TEMPLATE", template_key="order_update", locale="en", channel="EMAIL"
            ),
        ],
        publish=False,
    )
    order = (await create_order(client, shop, cod_amount_paisa=50_000))["order"]
    before = await count(CustomerFollowUp, CustomerFollowUp.tenant_id == tenant(shop))
    response = await client.post(
        f"/v1/automation/workflows/{wf['id']}/preview",
        headers=headers,
        json={"order_id": order["id"]},
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["side_effects"] is False and preview["entry_conditions"]["matched"] is True
    assert [(s["id"], s["would"]) for s in preview["steps"]] == [
        ("cod", "THEN"),
        ("cod_call", "RUN"),
        ("w", "WAIT_FOR"),
        ("book", "SKIP"),
    ]
    assert preview["steps"][-1]["reason"] == "CONSENT_REQUIRED"
    assert await count(CustomerFollowUp, CustomerFollowUp.tenant_id == tenant(shop)) == before
    assert await runs(client, headers) == []


# ------------------------------------------------------------- validation --


@pytest.mark.parametrize(
    ("definition", "problem"),
    [
        (
            {"trigger": "order.created", "steps": [followup("a"), followup("a")]},
            "DUPLICATE_STEP_ID",
        ),
        (
            {
                "trigger": "inventory.low",
                "steps": [action("m", "SEND_TEMPLATE", template_key="order_update")],
            },
            "ACTION_NOT_AVAILABLE:SEND_TEMPLATE",
        ),
        (
            {
                "trigger": "payout.overdue",
                "conditions": {
                    "conditions": [{"field": "status", "op": "eq", "value": "CONFIRMED"}]
                },
                "steps": [notice()],
            },
            "CONDITION_NOT_AVAILABLE:status",
        ),
        (
            {
                "trigger": "order.created",
                "steps": [
                    {"type": "delay", "id": "d", "mode": "followup_due", "step": "fu"},
                    followup("fu"),
                ],
            },
            "FOLLOWUP_STEP_NOT_BEFORE_DELAY",
        ),
        (
            {
                "trigger": "order.created",
                "conditions": {
                    "conditions": [{"field": "status", "op": "eq", "value": "SHIPPED_BY_MAGIC"}]
                },
                "steps": [notice()],
            },
            "INVALID_VALUE:status",
        ),
    ],
)
async def test_invalid_workflows_cannot_be_published(client, headers, definition, problem):
    created = await client.post(
        "/v1/automation/workflows", headers=headers, json={"name": "Bad", "definition": definition}
    )
    if created.status_code == 422:
        assert problem in str(created.json())
        return
    published = await client.post(
        f"/v1/automation/workflows/{created.json()['id']}/publish", headers=headers, json={}
    )
    assert published.status_code == 422
    assert problem in published.json()["details"]["problems"]


# ------------------------------------------------------ tenancy and RBAC --


async def _role(shop, role: str) -> None:
    async with system_session("test: role") as db:
        member = await db.scalar(sa.select(TenantUser).where(TenantUser.tenant_id == tenant(shop)))
        member.role = role


async def test_roles_and_tenants_are_enforced_on_the_server(client, shop, headers, unique_phone):
    wf = await workflow(
        client,
        headers,
        "order.created",
        [{"type": "delay", "id": "d", "mode": "duration", "minutes": 60}, notice()],
    )
    await create_order(client, shop)
    [row] = await runs(client, headers)
    await run(shop, row["id"])

    other = auth_header(await signed_in_shop(client, "018" + unique_phone[3:]))
    assert (
        await client.get(f"/v1/automation/workflows/{wf['id']}", headers=other)
    ).status_code == 404
    assert (
        await client.get(f"/v1/automation/executions/{row['id']}", headers=other)
    ).status_code == 404
    assert (await client.get("/v1/automation/executions", headers=other)).json()["items"] == []
    assert (
        await client.post(f"/v1/automation/executions/{row['id']}/cancel", headers=other)
    ).status_code == 404

    await _role(shop, "VIEWER")
    assert (await client.get("/v1/automation/workflows", headers=headers)).status_code == 200
    assert (
        await client.patch(
            f"/v1/automation/workflows/{wf['id']}", headers=headers, json={"enabled": False}
        )
    ).status_code == 403
    assert (
        await client.post(f"/v1/automation/executions/{row['id']}/cancel", headers=headers)
    ).status_code == 403

    await _role(shop, "ORDER_OPERATOR")
    assert (
        await client.post(f"/v1/automation/workflows/{wf['id']}/publish", headers=headers, json={})
    ).status_code == 403
    cancelled = await client.post(f"/v1/automation/executions/{row['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"

    await _role(shop, "MANAGER")
    toggled = await client.patch(
        f"/v1/automation/workflows/{wf['id']}", headers=headers, json={"enabled": False}
    )
    assert toggled.status_code == 200 and toggled.json()["enabled"] is False
    actions_seen = {a for (a,) in (await _audit_rows(shop))}
    assert {
        "automation.workflow_created",
        "automation.workflow_published",
        "automation.workflow_disabled",
        "automation.execution_cancelled",
    } <= actions_seen


async def _audit_rows(shop) -> list[tuple[str]]:
    async with system_session("test: audit") as db:
        return list(
            (
                await db.execute(
                    sa.select(AuditLog.action).where(
                        AuditLog.tenant_id == tenant(shop), AuditLog.action.like("automation.%")
                    )
                )
            ).tuples()
        )


async def test_metrics_report_operational_facts(client, shop, headers):
    await workflow(client, headers, "order.created", [notice()])
    await create_order(client, shop)
    [row] = await runs(client, headers)
    await run(shop, row["id"])
    metrics = (await client.get("/v1/automation/metrics", headers=headers)).json()
    assert metrics["executions"] == 1 and metrics["succeeded"] == 1 and metrics["failed"] == 0
    assert metrics["average_duration_seconds"] is not None
    listing = (await client.get("/v1/automation/workflows", headers=headers)).json()["items"]
    assert listing[0]["runs_7d"] == {"SUCCEEDED": 1}
