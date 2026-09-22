"""Targeted V2.3 CRM contracts, tenant boundaries and source-of-truth checks."""

import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa

from app.core.clock import utc_now
from app.tenants.roles import TenantRole
from tests.conftest_commerce import signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_customers import create_customer
from tests.test_rto import _Shop
from tests.test_team import _member_session


def phone():
    return f"017{uuid.uuid4().int % 100_000_000:08d}"


@pytest.fixture
async def crm(client, unique_phone):
    session = await signed_in_shop(client, unique_phone, plan="pro")
    customer = await create_customer(
        client, session, phone="01712345678", name="Nusrat", address="Dhaka"
    )
    return session, customer, auth_header(session)


async def test_profile_note_timeline_and_pagination(client, crm):
    _, customer, headers = crm
    path = f"/v1/customers/{customer['id']}"
    for value in ("Prefers evening delivery", "Call first", "Repeat customer"):
        response = await client.post(f"{path}/notes", json={"text": value}, headers=headers)
        assert response.status_code == 201, response.text
        assert response.json()["actor_id"]
    response = await client.get(f"{path}/crm", headers=headers)
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["addresses"][0]["raw_address"] == "Dhaka"
    assert detail["value"]["measured_profit_paisa"] is None
    assert detail["risk"]["state"] == "INSUFFICIENT_DATA"
    seen = []
    cursor = None
    for _ in range(3):
        page = await client.get(
            f"{path}/timeline",
            params={"limit": 1, **({"cursor": cursor} if cursor else {})},
            headers=headers,
        )
        assert page.status_code == 200, page.text
        body = page.json()
        seen.extend(row["id"] for row in body["items"])
        cursor = body["next_cursor"]
    assert len(set(seen)) == 3
    assert cursor is None
    assert (
        await client.post(
            f"{path}/notes", json={"text": "  ", "actor_id": str(uuid.uuid4())}, headers=headers
        )
    ).status_code == 422


async def test_tags_bulk_filters_and_archive(client, crm):
    session, customer, headers = crm
    second = await create_customer(client, session, phone="01811225678", name="Rafi")
    tag = await client.post("/v1/customers/crm/tags", json={"name": "VIP"}, headers=headers)
    assert tag.status_code == 201, tag.text
    tid = tag.json()["id"]
    payload = {"customer_ids": [customer["id"], second["id"]], "tag_id": tid}
    for _ in range(2):
        result = await client.post("/v1/customers/crm/bulk-tags", json=payload, headers=headers)
        assert result.status_code == 200, result.text
    result = await client.get(
        "/v1/customers/crm", params={"tag_id": tid, "limit": 1}, headers=headers
    )
    assert result.status_code == 200, result.text
    assert result.json()["has_more"]
    assert result.json()["items"][0]["tags"] == [{"id": tid, "name": "VIP"}]
    exact = await client.get("/v1/customers/crm", params={"search": "01712345678"}, headers=headers)
    assert [row["id"] for row in exact.json()["items"]] == [customer["id"]]
    assert "01712345678" not in exact.text
    assert "01811225678" not in exact.text
    await client.post(
        "/v1/customers/crm/bulk-tags", json={**payload, "remove": True}, headers=headers
    )
    assert not (
        await client.get("/v1/customers/crm", params={"tag_id": tid}, headers=headers)
    ).json()["items"]
    await client.delete(f"/v1/customers/crm/tags/{tid}", headers=headers)
    assert not (await client.get("/v1/customers/crm/tags", headers=headers)).json()["items"]


async def test_followup_due_complete_reopen_and_assignee(client, crm):
    _, customer, headers = crm
    path = f"/v1/customers/{customer['id']}/follow-ups"
    payload = {"text": "Confirm bulk order", "due_at": (utc_now() - timedelta(hours=1)).isoformat()}
    invalid = await client.post(
        path, json={**payload, "assignee_id": str(uuid.uuid4())}, headers=headers
    )
    assert invalid.status_code == 422, invalid.text
    response = await client.post(path, json=payload, headers=headers)
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["state"] == "OVERDUE"
    due = await client.get(
        "/v1/customers/crm", params={"segment": "FOLLOW_UP_DUE"}, headers=headers
    )
    assert [row["id"] for row in due.json()["items"]] == [customer["id"]]
    done = await client.patch(f"{path}/{task['id']}", json={"completed": True}, headers=headers)
    assert done.status_code == 200, done.text
    assert done.json()["completed_by"] == task["created_by"]
    assert not (
        await client.get("/v1/customers/crm", params={"segment": "FOLLOW_UP_DUE"}, headers=headers)
    ).json()["items"]
    reopened = await client.patch(
        f"{path}/{task['id']}", json={"completed": False}, headers=headers
    )
    assert reopened.json()["completed_at"] is None
    listing = await client.get(path, headers=headers)
    assert listing.status_code == 200, listing.text


@pytest.mark.parametrize(
    "role,can_view,can_write,money",
    [
        (TenantRole.MANAGER, True, True, True),
        (TenantRole.ORDER_OPERATOR, True, True, False),
        (TenantRole.VIEWER, True, False, False),
        (TenantRole.FINANCE, False, False, False),
    ],
)
async def test_crm_uses_existing_roles(client, crm, role, can_view, can_write, money):
    session, customer, _ = crm
    member = await _member_session(client, session, phone(), role)
    headers = auth_header(member)
    profile = await client.get(f"/v1/customers/{customer['id']}/crm", headers=headers)
    assert profile.status_code == (200 if can_view else 403), profile.text
    if can_view:
        body = profile.json()
        assert body["can_write"] is can_write
        assert (body["value"] is not None) is money
        if not money:
            assert body["realized_revenue_paisa"] is None
            assert "HIGH_VALUE" not in body["segments"]
            assert (
                await client.get(
                    "/v1/customers/crm", params={"segment": "HIGH_VALUE"}, headers=headers
                )
            ).status_code == 403
        assert (body["risk"] is not None) is (role != TenantRole.VIEWER)
    for path, payload in [
        (f"/v1/customers/{customer['id']}/notes", {"text": "Private note"}),
        ("/v1/customers/crm/tags", {"name": "Wholesale"}),
        (
            f"/v1/customers/{customer['id']}/follow-ups",
            {"text": "Call", "due_at": utc_now().isoformat()},
        ),
    ]:
        response = await client.post(path, json=payload, headers=headers)
        assert response.status_code == (201 if can_write else 403), response.text


async def test_every_crm_resource_is_shop_private_and_bulk_is_atomic(client, crm):
    _, customer, headers = crm
    other = await signed_in_shop(client, phone(), plan="pro")
    theirs = await create_customer(client, other, phone="01712345678")
    other_headers = auth_header(other)
    tag = (
        await client.post("/v1/customers/crm/tags", json={"name": "Private VIP"}, headers=headers)
    ).json()
    task = (
        await client.post(
            f"/v1/customers/{customer['id']}/follow-ups",
            json={"text": "Private task", "due_at": utc_now().isoformat()},
            headers=headers,
        )
    ).json()
    for suffix in ("crm", "timeline", "follow-ups"):
        assert (
            await client.get(f"/v1/customers/{customer['id']}/{suffix}", headers=other_headers)
        ).status_code == 404
    assert (
        await client.post(
            f"/v1/customers/{customer['id']}/notes",
            json={"text": "intrusion"},
            headers=other_headers,
        )
    ).status_code == 404
    assert (
        await client.patch(
            f"/v1/customers/{customer['id']}/follow-ups/{task['id']}",
            json={"completed": True},
            headers=other_headers,
        )
    ).status_code == 404
    assert (
        await client.delete(f"/v1/customers/crm/tags/{tag['id']}", headers=other_headers)
    ).status_code == 404
    assert (
        await client.post(
            "/v1/customers/crm/bulk-tags",
            json={"customer_ids": [theirs["id"]], "tag_id": tag["id"]},
            headers=other_headers,
        )
    ).status_code == 404
    mixed = await client.post(
        "/v1/customers/crm/bulk-tags",
        json={"customer_ids": [customer["id"], theirs["id"]], "tag_id": tag["id"]},
        headers=headers,
    )
    assert mixed.status_code == 404
    assert not (
        await client.get("/v1/customers/crm", params={"tag_id": tag["id"]}, headers=headers)
    ).json()["items"]
    assert not (await client.get("/v1/customers/crm/tags", headers=other_headers)).json()["items"]
    assert (await client.get("/v1/customers/crm")).status_code == 401


async def test_repeat_new_inactive_active_and_date_filters(client, crm, system_db):
    from app.orders.models import Order

    session, customer, headers = crm
    shop = _Shop(client, session)
    first = await shop.order()
    second = await shop.order()
    body = await shop.get("/v1/customers/crm", segment="REPEAT", min_orders=2, max_orders=2)
    assert [row["id"] for row in body["items"]] == [customer["id"]]
    assert set(body["items"][0]["segments"]) >= {"NEW", "REPEAT", "ACTIVE_ORDERS"}
    assert body["items"][0]["order_count"] == 2
    old = utc_now() - timedelta(days=100)
    await system_db.execute(
        sa.update(Order)
        .where(Order.id.in_([uuid.UUID(first["id"]), uuid.UUID(second["id"])]))
        .values(created_at=old)
    )
    await system_db.commit()
    inactive = await shop.get("/v1/customers/crm", segment="INACTIVE")
    assert [row["id"] for row in inactive["items"]] == [customer["id"]]
    assert "NEW" not in inactive["items"][0]["segments"]
    assert not (
        await shop.get("/v1/customers/crm", last_from=(old + timedelta(days=1)).isoformat())
    )["items"]
    assert (await shop.get("/v1/customers/crm", last_until=(old + timedelta(days=1)).isoformat()))[
        "items"
    ]
    assert not (await shop.get("/v1/customers/crm", min_orders=3))["items"]
    assert (
        await client.get("/v1/customers/crm", params={"cursor": "bad"}, headers=headers)
    ).status_code == 422
    assert (
        await client.get("/v1/customers/crm", params={"limit": 101}, headers=headers)
    ).status_code == 422


async def test_rto_counts_risk_and_timeline_use_canonical_outcomes(client, crm):
    from app.analytics.rto import MIN_RATE_SAMPLE

    session, customer, _ = crm
    shop = _Shop(client, session)
    await shop.parcel("RETURNED")
    await shop.parcel("CANCELLED", provider="steadfast")
    before = await shop.get(f"/v1/customers/{customer['id']}/crm")
    assert before["returned_count"] == 2
    assert "REPEATED_RTO" not in before["segments"]
    for _ in range(MIN_RATE_SAMPLE - 2):
        await shop.parcel("DELIVERED")
    await shop.cancel_before_dispatch()
    after = await shop.get(f"/v1/customers/{customer['id']}/crm")
    canonical = await shop.get("/v1/customers/risk-check", phone="01712345678")
    assert after["returned_count"] == canonical["returned_count"] == 2
    assert after["delivered_count"] == canonical["delivered_count"] == MIN_RATE_SAMPLE - 2
    assert after["cancelled_count"] == canonical["cancelled_count"] == 1
    assert after["risk"]["state"] == canonical["state"]
    assert set(after["segments"]) >= {"REPEATED_RTO", "SUCCESSFUL_REPEAT"}
    assert after["active_orders"] == 0
    timeline = await shop.get(f"/v1/customers/{customer['id']}/timeline", limit=100)
    kinds = [row["kind"] for row in timeline["items"]]
    assert kinds.count("ORDER_RTO") == 2
    assert kinds.count("ORDER_DELIVERED") == MIN_RATE_SAMPLE - 2
    assert kinds.count("ORDER_CANCELLED") == 1


async def test_shop_relative_value_measured_coverage_and_revision(client, crm, system_db):
    from app.profit.models import ProfitSnapshot

    session, _, _ = crm
    shop = _Shop(client, session)
    customer_ids = []
    for n in range(1, 6):
        parcel = await shop.parcel("DELIVERED", phone=phone())
        customer_ids.append(parcel["order"]["customer_id"])
        await system_db.execute(
            sa.update(ProfitSnapshot)
            .where(ProfitSnapshot.order_id == uuid.UUID(parcel["order"]["id"]))
            .values(
                quality="ACTUAL", realized_revenue_paisa=n * 101, contribution_profit_paisa=n * 10
            )
        )
        await system_db.commit()
    await system_db.commit()
    listed = await shop.get("/v1/customers/crm", segment="HIGH_VALUE")
    assert listed["definitions"]["high_value_threshold_paisa"] == 303
    assert {r["id"] for r in listed["items"]} == set(customer_ids[2:])
    detail = await shop.get(f"/v1/customers/{customer_ids[-1]}/crm")
    assert detail["value"]["delivered_revenue_paisa"] == 505
    assert detail["value"]["measured_profit_paisa"] == 50
    assert detail["value"]["average_delivered_order_paisa"] == 505
    assert detail["value"]["measured_orders"] == 1
    assert type(detail["value"]["total_order_value_paisa"]) is int
    assert detail["realized_revenue_paisa"] == 505
    # A missing/estimated snapshot contributes no factual money, and a superseded
    # snapshot never contributes a second time.
    await system_db.execute(
        sa.update(ProfitSnapshot)
        .where(
            ProfitSnapshot.tenant_id == uuid.UUID(session["tenant_id"]),
            ProfitSnapshot.realized_revenue_paisa == 505,
        )
        .values(quality="ESTIMATED")
    )
    await system_db.commit()
    detail = await shop.get(f"/v1/customers/{customer_ids[-1]}/crm")
    assert detail["value"]["measured_profit_paisa"] is None
    assert detail["value"]["measured_parcels"] == 0
    assert detail["value"]["completed_parcels"] == 1
    assert not (await shop.get("/v1/customers/crm", segment="HIGH_VALUE"))["items"]
    await system_db.execute(
        sa.update(ProfitSnapshot)
        .where(ProfitSnapshot.tenant_id == uuid.UUID(session["tenant_id"]))
        .values(is_current=False)
    )
    await system_db.commit()
    assert (await shop.get(f"/v1/customers/{customer_ids[0]}/crm"))["value"][
        "delivered_revenue_paisa"
    ] is None


async def test_notes_immutable_legacy_history_privacy_cleanup_and_log_safety(
    client, crm, system_db, caplog
):
    from app.customers.crm_models import CustomerActivity, CustomerFollowUp, CustomerTag
    from app.privacy.service import PrivacyService

    session, customer, headers = crm
    path = f"/v1/customers/{customer['id']}"
    note = (
        await client.post(f"{path}/notes", json={"text": "Private observation"}, headers=headers)
    ).json()
    assert (
        await client.patch(
            f"{path}/notes/{note['id']}", json={"text": "overwrite"}, headers=headers
        )
    ).status_code in (404, 405)
    assert (await client.delete(f"{path}/notes/{note['id']}", headers=headers)).status_code in (
        404,
        405,
    )
    await client.patch(path, json={"notes": "Legacy first"}, headers=headers)
    await client.patch(path, json={"notes": "Legacy replacement"}, headers=headers)
    timeline = (await client.get(f"{path}/timeline", headers=headers)).json()
    assert {"Legacy first", "Legacy replacement", "Private observation"} <= {
        r["text"] for r in timeline["items"]
    }
    await client.get("/v1/customers/crm", params={"search": "01712345678"}, headers=headers)
    assert "01712345678" not in caplog.text
    await client.post("/v1/customers/crm/tags", json={"name": "Private name"}, headers=headers)
    await client.post(
        f"{path}/follow-ups",
        json={"text": "Private follow-up", "due_at": utc_now().isoformat()},
        headers=headers,
    )
    tenant = uuid.UUID(session["tenant_id"])
    await PrivacyService(system_db)._anonymise_customers(tenant)
    await system_db.flush()
    assert not any(
        (
            await system_db.scalars(
                sa.select(CustomerActivity.text).where(CustomerActivity.tenant_id == tenant)
            )
        ).all()
    )
    assert not any(
        (
            await system_db.scalars(
                sa.select(CustomerFollowUp.text).where(CustomerFollowUp.tenant_id == tenant)
            )
        ).all()
    )
    tags = (
        await system_db.scalars(sa.select(CustomerTag).where(CustomerTag.tenant_id == tenant))
    ).all()
    assert all(row.name == "" and row.name_key.startswith("deleted:") for row in tags)


async def test_followup_alert_dedupes_resolves_and_contains_no_customer_pii(client, crm, db):
    from app.core.context import RequestContext, set_context
    from app.notifications.models import NotificationKind
    from app.notifications.smart import SmartAlerts
    from app.notifications.templates import render

    session, customer, headers = crm
    path = f"/v1/customers/{customer['id']}/follow-ups"
    task = (
        await client.post(
            path,
            json={
                "text": "Call 01712345678",
                "due_at": (utc_now() - timedelta(hours=1)).isoformat(),
            },
            headers=headers,
        )
    ).json()
    set_context(RequestContext(trace_id="crm-test", tenant_id=uuid.UUID(session["tenant_id"])))
    alerts = SmartAlerts(db)
    conditions = await alerts.followups_due()
    assert len(conditions) == 1
    assert conditions[0].params == {"count": 1}
    assert "01712345678" not in str(conditions)
    assert await alerts.lifecycle.sync(NotificationKind.FOLLOW_UP_DUE, conditions) == 1
    assert await alerts.lifecycle.sync(NotificationKind.FOLLOW_UP_DUE, conditions) == 0
    await db.commit()
    await client.patch(f"{path}/{task['id']}", json={"completed": True}, headers=headers)
    set_context(RequestContext(trace_id="crm-test", tenant_id=uuid.UUID(session["tenant_id"])))
    assert await alerts.followups_due() == []
    assert await alerts.lifecycle.sync(NotificationKind.FOLLOW_UP_DUE, []) == 0
    assert render(NotificationKind.FOLLOW_UP_DUE, {"count": 1}, "en") != render(
        NotificationKind.FOLLOW_UP_DUE, {"count": 1}, "bn"
    )


async def test_timeline_does_not_skip_same_order_events_at_same_time(client, crm, system_db):
    from app.orders.models import Order

    session, customer, headers = crm
    order = await _Shop(client, session).order()
    moment = utc_now()
    await system_db.execute(
        sa.update(Order)
        .where(Order.id == uuid.UUID(order["id"]))
        .values(created_at=moment, cancelled_at=moment, status="CANCELLED")
    )
    await system_db.commit()
    path = f"/v1/customers/{customer['id']}/timeline"
    first = (await client.get(path, params={"limit": 1}, headers=headers)).json()
    second = (
        await client.get(path, params={"limit": 1, "cursor": first["next_cursor"]}, headers=headers)
    ).json()
    assert {first["items"][0]["kind"], second["items"][0]["kind"]} == {
        "ORDER_CREATED",
        "ORDER_CANCELLED",
    }
    assert second["has_more"] is False


async def test_followup_assignee_must_belong_to_this_shop_and_authorship_is_fixed(client, crm):
    session, customer, headers = crm
    member = await _member_session(client, session, phone(), TenantRole.ORDER_OPERATOR)
    roster = (await client.get("/v1/customers/crm/members", headers=headers)).json()
    assert len(roster["items"]) == 2
    path = f"/v1/customers/{customer['id']}/follow-ups"
    owner_id = (
        await client.post(
            f"/v1/customers/{customer['id']}/notes", json={"text": "owner note"}, headers=headers
        )
    ).json()["actor_id"]
    member_id = next(row["id"] for row in roster["items"] if row["id"] != owner_id)
    task = await client.post(
        path,
        json={
            "text": "Call tomorrow",
            "due_at": (utc_now() + timedelta(days=1)).isoformat(),
            "assignee_id": member_id,
        },
        headers=headers,
    )
    assert task.status_code == 201
    assert task.json()["state"] == "UPCOMING"
    assert task.json()["created_by"] == owner_id
    bad = await client.post(
        path,
        json={"text": "Spoof", "due_at": utc_now().isoformat(), "created_by": member_id},
        headers=headers,
    )
    assert bad.status_code == 422
    done = await client.patch(
        f"{path}/{task.json()['id']}", json={"completed": True}, headers=auth_header(member)
    )
    assert done.json()["completed_by"] == member_id
    assert done.json()["created_by"] == owner_id
    other = await signed_in_shop(client, phone())
    foreign = (await client.get("/v1/customers/crm/members", headers=auth_header(other))).json()[
        "items"
    ][0]["id"]
    assert (
        await client.post(
            path,
            json={
                "text": "Foreign assignee",
                "due_at": utc_now().isoformat(),
                "assignee_id": foreign,
            },
            headers=headers,
        )
    ).status_code == 422


async def test_high_value_shop_population_and_plan_gate_do_not_leak(client, crm, system_db):
    from app.entitlements.models import Subscription
    from app.profit.models import ProfitSnapshot

    session, customer, headers = crm
    shop = _Shop(client, session)
    await shop.parcel("DELIVERED")
    tenant = uuid.UUID(session["tenant_id"])
    await system_db.execute(
        sa.update(ProfitSnapshot).where(ProfitSnapshot.tenant_id == tenant).values(quality="ACTUAL")
    )
    await system_db.commit()
    # Four paying customers in another shop must not supply the minimum sample.
    other = await signed_in_shop(client, phone(), plan="pro")
    for _ in range(4):
        await _Shop(client, other).parcel("DELIVERED", phone=phone())
    await system_db.execute(
        sa.update(ProfitSnapshot)
        .where(ProfitSnapshot.tenant_id == uuid.UUID(other["tenant_id"]))
        .values(quality="ACTUAL")
    )
    await system_db.commit()
    assert not (await shop.get("/v1/customers/crm", segment="HIGH_VALUE"))["items"]
    await system_db.execute(
        sa.update(ProfitSnapshot)
        .where(ProfitSnapshot.tenant_id == tenant)
        .values(business_date=(utc_now() - timedelta(days=1000)).date())
    )
    await system_db.execute(
        sa.update(Subscription).where(Subscription.tenant_id == tenant).values(plan_code="free")
    )
    await system_db.commit()
    detail = await shop.get(f"/v1/customers/{customer['id']}/crm")
    assert detail["money_locked"] == "PLAN"
    assert detail["value"] is None
    assert (
        await client.get("/v1/customers/crm", params={"segment": "HIGH_VALUE"}, headers=headers)
    ).status_code == 403


async def test_measured_profit_includes_return_and_loss_and_uses_money_rounding(
    client, crm, system_db
):
    from app.profit.models import ProfitSnapshot

    session, customer, _ = crm
    shop = _Shop(client, session)
    for outcome, revenue, profit in [
        ("DELIVERED", 101, 20),
        ("DELIVERED", 102, 30),
        ("RETURNED", 0, -40),
        ("LOST", 0, -20),
    ]:
        parcel = await shop.parcel(outcome)
        await system_db.execute(
            sa.update(ProfitSnapshot)
            .where(ProfitSnapshot.order_id == uuid.UUID(parcel["order"]["id"]))
            .values(
                quality="ACTUAL", realized_revenue_paisa=revenue, contribution_profit_paisa=profit
            )
        )
        await system_db.commit()
    body = await shop.get(f"/v1/customers/{customer['id']}/crm")
    assert body["value"]["measured_profit_paisa"] == -10
    assert body["value"]["average_delivered_order_paisa"] == 102
    assert body["value"]["measured_parcels"] == body["value"]["completed_parcels"] == 4
    assert body["active_orders"] == 0


@pytest.mark.postgres
async def test_crm_tables_enable_postgres_row_security(system_db):
    if system_db.bind.dialect.name != "postgresql":
        pytest.skip("PostgreSQL only")
    rows = (
        await system_db.execute(
            sa.text(
                "SELECT relname, relrowsecurity FROM pg_class WHERE relname IN ('customer_activities', 'customer_followups', 'customer_tags', 'customer_tag_links')"
            )
        )
    ).all()
    assert len(rows) == 4
    assert all(enabled for _, enabled in rows)
