import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa
from tests.conftest_commerce import create_order, signed_in_shop
from tests.test_auth_flow import auth_header

from app.automation import jobs, service
from app.automation.models import AutomationExecution, AutomationTask
from app.core.clock import utc_now
from app.customers.crm_models import CustomerFollowUp, CustomerTagLink
from app.db.session import system_session
from app.messaging import service as messaging
from app.messaging.models import Message
from app.notifications.models import Notification
from app.notifications.service import localized
from app.tenants.models import TenantUser


async def rule(client, headers, action="CREATE_TASK", config=None, **extra):
    response = await client.post(
        "/v1/automation/rules",
        headers=headers,
        json={
            "name": "Order rule",
            "trigger": "order.created",
            "action": action,
            "enabled": True,
            "config": config or {"text_en": "Check order", "text_bn": "অর্ডার দেখুন"},
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def executions(client, headers):
    return (await client.get("/v1/automation/executions", headers=headers)).json()["items"]


async def test_status_trigger_uses_event_transition_and_edit_invalidates_pending(
    client, unique_phone
):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    configured = await rule(
        client,
        headers,
        trigger="order.status_changed",
        conditions=[{"field": "status", "op": "eq", "value": "CONFIRMED"}],
    )
    order = (await create_order(client, shop))["order"]
    assert await executions(client, headers) == []
    changed = await client.patch(
        "/v1/orders/" + order["id"], headers=headers, json={"status": "CONFIRMED"}
    )
    assert changed.status_code == 200, changed.text
    execution = (await executions(client, headers))[0]
    assert execution["status"] == "PENDING"
    update = {k: v for k, v in configured.items() if k not in {"id", "version"}}
    update["config"]["text_en"] = "Changed action"
    response = await client.put(
        "/v1/automation/rules/" + configured["id"], headers=headers, json=update
    )
    assert response.status_code == 200, response.text
    await jobs.execute(uuid.UUID(shop["tenant_id"]), uuid.UUID(execution["id"]))
    assert (await executions(client, headers))[0]["last_error"] == "RULE_DISABLED_OR_CHANGED"


@pytest.mark.parametrize(
    "action",
    [
        "CREATE_TASK",
        "CREATE_FOLLOWUP",
        "ADD_TAG",
        "REMOVE_TAG",
        "SELLER_NOTIFICATION",
        "SEND_TEMPLATE",
    ],
)
async def test_actions_execute_once_through_existing_services(
    client, unique_phone, monkeypatch, action
):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    original = (await create_order(client, shop))["order"]
    config = {"text_en": "Check order", "text_bn": "অর্ডার দেখুন"}
    if action == "CREATE_FOLLOWUP":
        config = {"text": "Customer follow-up", "due_hours": 24}
    if action in {"ADD_TAG", "REMOVE_TAG"}:
        tag = (
            await client.post("/v1/customers/crm/tags", headers=headers, json={"name": "Follow up"})
        ).json()
        config = {"tag_id": tag["id"]}
        if action == "REMOVE_TAG":
            response = await client.post(
                "/v1/customers/crm/bulk-tags",
                headers=headers,
                json={
                    "customer_ids": [original["customer_id"]],
                    "tag_id": tag["id"],
                    "remove": False,
                },
            )
            assert response.status_code == 200, response.text
    if action == "SELLER_NOTIFICATION":
        config.update(title_en="Order needs attention", title_bn="অর্ডার দেখুন")
    if action == "SEND_TEMPLATE":
        monkeypatch.setattr(messaging, "capability", lambda _: (True, None))
        await client.post("/v1/messaging/channels/EMAIL", headers=headers, json={"enabled": True})
        response = await client.post(
            "/v1/messaging/conversations",
            headers=headers,
            json={
                "customer_id": original["customer_id"],
                "recipient": "buyer@example.com",
                "consent": True,
                "evidence": "Buyer requested updates",
            },
        )
        assert response.status_code == 201
        config = {"template_key": "order_update", "locale": "bn"}
    await rule(client, headers, action, config)
    await create_order(client, shop)
    history = await executions(client, headers)
    assert len(history) == 1
    execution_id = uuid.UUID(history[0]["id"])
    await jobs.execute(uuid.UUID(shop["tenant_id"]), execution_id)
    await jobs.execute(uuid.UUID(shop["tenant_id"]), execution_id)
    result = (await executions(client, headers))[0]
    assert result["status"] == "DONE", result
    assert result["attempts"] == 1
    async with system_session("test automation action") as db:
        if action == "CREATE_TASK":
            assert (
                await db.scalar(
                    sa.select(sa.func.count())
                    .select_from(AutomationTask)
                    .where(AutomationTask.execution_id == execution_id)
                )
                == 1
            )
        if action == "CREATE_FOLLOWUP":
            assert (
                await db.scalar(
                    sa.select(sa.func.count())
                    .select_from(CustomerFollowUp)
                    .where(CustomerFollowUp.tenant_id == uuid.UUID(shop["tenant_id"]))
                )
                == 1
            )
        if action in {"ADD_TAG", "REMOVE_TAG"}:
            count = await db.scalar(
                sa.select(sa.func.count())
                .select_from(CustomerTagLink)
                .where(CustomerTagLink.tenant_id == uuid.UUID(shop["tenant_id"]))
            )
            assert count == (1 if action == "ADD_TAG" else 0)
        if action == "SEND_TEMPLATE":
            assert (
                await db.scalar(
                    sa.select(sa.func.count())
                    .select_from(Message)
                    .where(Message.tenant_id == uuid.UUID(shop["tenant_id"]))
                )
                == 1
            )
        if action == "SELLER_NOTIFICATION":
            notification = await db.scalar(
                sa.select(Notification).where(
                    Notification.tenant_id == uuid.UUID(shop["tenant_id"])
                )
            )
            assert localized(notification, "bn") == ("অর্ডার দেখুন", "অর্ডার দেখুন")
            assert localized(notification, "en") == ("Order needs attention", "Check order")


async def test_conditions_and_rule_changes_skip_queued_actions(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    await rule(
        client, headers, conditions=[{"field": "cod_amount_paisa", "op": "gte", "value": 999999}]
    )
    active = await rule(client, headers)
    await create_order(client, shop)
    assert sorted(x["status"] for x in await executions(client, headers)) == ["PENDING", "SKIPPED"]
    await client.patch(
        "/v1/automation/rules/" + active["id"], headers=headers, json={"enabled": False}
    )
    pending = next(x for x in await executions(client, headers) if x["status"] == "PENDING")
    await jobs.execute(uuid.UUID(shop["tenant_id"]), uuid.UUID(pending["id"]))
    assert all(x["status"] == "SKIPPED" for x in await executions(client, headers))


async def test_transient_failure_rolls_back_action_and_retries(client, unique_phone, monkeypatch):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    await rule(client, headers)
    await create_order(client, shop)
    execution_id = uuid.UUID((await executions(client, headers))[0]["id"])
    original = jobs.perform

    async def fail_after_action(db, execution):
        await original(db, execution)
        raise OSError("temporary")

    monkeypatch.setattr(jobs, "perform", fail_after_action)
    await jobs.execute(uuid.UUID(shop["tenant_id"]), execution_id)
    async with system_session("test rollback") as db:
        assert not await db.scalar(
            sa.select(AutomationTask.id).where(AutomationTask.execution_id == execution_id)
        )
        row = await db.get(AutomationExecution, execution_id)
        assert row.status == "RETRY"
        row.next_attempt_at = utc_now() - timedelta(seconds=1)
    monkeypatch.setattr(jobs, "perform", original)
    await jobs.execute(uuid.UUID(shop["tenant_id"]), execution_id)
    assert (await executions(client, headers))[0]["status"] == "DONE"


async def test_opt_out_prevents_automation_message(client, unique_phone, monkeypatch):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    monkeypatch.setattr(messaging, "capability", lambda _: (True, None))
    await client.post("/v1/messaging/channels/EMAIL", headers=headers, json={"enabled": True})
    await rule(client, headers, "SEND_TEMPLATE", {"template_key": "order_update"})
    await create_order(client, shop)
    execution = (await executions(client, headers))[0]
    await jobs.execute(uuid.UUID(shop["tenant_id"]), uuid.UUID(execution["id"]))
    result = (await executions(client, headers))[0]
    assert result["status"] == "SKIPPED"
    assert result["last_error"] == "CONSENT_REQUIRED"


async def test_loop_guard_and_unapproved_actions(client, unique_phone, monkeypatch):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    await rule(client, headers)
    token = service.automation_depth.set(1)
    try:
        await create_order(client, shop)
    finally:
        service.automation_depth.reset(token)
    assert await executions(client, headers) == []
    for action in ["UPDATE_LEDGER", "RECONCILE", "CHANGE_STATUS", "HTTP_REQUEST"]:
        response = await client.post(
            "/v1/automation/rules",
            headers=headers,
            json={"name": "Forbidden", "trigger": "order.created", "action": action, "config": {}},
        )
        assert response.status_code == 422


async def test_tenant_rbac_and_revoked_rule_authority(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    owned = await rule(client, headers)
    await create_order(client, shop)
    other = await signed_in_shop(client, "018" + unique_phone[3:])
    assert (
        await client.patch(
            "/v1/automation/rules/" + owned["id"],
            headers=auth_header(other),
            json={"enabled": False},
        )
    ).status_code == 404
    assert (await client.get("/v1/automation/executions", headers=auth_header(other))).json()[
        "items"
    ] == []
    async with system_session("test owner permission") as db:
        member = await db.scalar(
            sa.select(TenantUser).where(TenantUser.tenant_id == uuid.UUID(shop["tenant_id"]))
        )
        member.role = "VIEWER"
    assert (
        await client.patch(
            "/v1/automation/rules/" + owned["id"], headers=headers, json={"enabled": False}
        )
    ).status_code == 403
    execution = (await executions(client, headers))[0]
    await jobs.execute(uuid.UUID(shop["tenant_id"]), uuid.UUID(execution["id"]))
    assert (await executions(client, headers))[0]["last_error"] == "RULE_OWNER_PERMISSION_REVOKED"
