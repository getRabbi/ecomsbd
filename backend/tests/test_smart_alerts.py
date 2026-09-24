"""V2.2 smart alerts: factual, deduplicated, role-targeted, bilingual.

The properties that matter:

* each alert is computed from the definition its own screen uses — overdue COD
  from the Receivables rule, discrepancies from Reconciliation V2 cases, RTO
  from :mod:`app.analytics.rto` (``RETURNED`` *and* courier ``CANCELLED``);
* an alert identity is raised once; an unchanged condition waits out its
  cooldown, material worsening may raise again, a cleared condition resolves;
* the backend picks recipients by role, and a member's own preferences apply
  to their centre and their pushes alike;
* the in-app row is the truth: a push that fails leaves it untouched.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.consignments.models import Consignment, ConsignmentStatus
from app.consignments.service import DeliveryOutcome
from app.core.clock import utc_now
from app.core.context import RequestContext, set_context
from app.notifications import rules
from app.notifications.delivery import DeliveryState, NotificationDispatcher
from app.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationKind,
    NotificationState,
)
from app.notifications.service import NotificationService
from app.notifications.smart import AlertCondition, AlertLifecycle, SmartAlerts
from app.notifications.templates import render
from app.notifications.transport import MockPushTransport, PushMessage, build_sms_transport
from app.reconciliation.models import CaseKind, CaseStatus, ReconciliationCase
from app.tenants.roles import TenantRole
from tests.conftest_commerce import create_product, dispatched_parcel, signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_rto import _Shop
from tests.test_team import _member_session


@pytest.fixture
async def shop(client: AsyncClient, unique_phone: str) -> dict:
    session = await signed_in_shop(client, unique_phone, shop_name="Smart Shop")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
    return session


def _phone() -> str:
    return f"018{uuid.uuid4().int % 100_000_000:08d}"


async def _alerts(db: AsyncSession, kind: NotificationKind | None = None) -> list[Notification]:
    stmt = sa.select(Notification).order_by(Notification.created_at)
    if kind is not None:
        stmt = stmt.where(Notification.kind == str(kind))
    return list((await db.execute(stmt)).scalars().all())


async def _delivered(client: AsyncClient, shop: dict, db: AsyncSession, *, cod: int, days: int):
    parcel = await dispatched_parcel(client, shop, db, cod_paisa=cod)
    await parcel["consignments"].record_outcome(
        parcel["consignment"].id,
        DeliveryOutcome(
            status=ConsignmentStatus.DELIVERED, occurred_at=utc_now() - timedelta(days=days)
        ),
    )
    await db.commit()
    return parcel


def _case(kind: CaseKind, amount: int = 10_000, status: CaseStatus = CaseStatus.OPEN):
    return ReconciliationCase(
        kind=str(kind),
        status=str(status),
        priority="MEDIUM",
        subject_type="consignment",
        subject_id=uuid.uuid4(),
        dedupe_key="",
        amount_paisa=amount,
        summary="test case",
        detail={},
        opened_at=utc_now(),
    )


def _stuck(count: int) -> AlertCondition:
    return AlertCondition(
        kind=NotificationKind.COURIER_STATUS_STUCK,
        subject="shop",
        params={
            "count": count,
            "cod_paisa": 0,
            "oldest_days": 12,
            "by_status": {"IN_TRANSIT": count},
        },
        magnitude=count,
        item_count=count,
    )


def _payout(amount: int = 895_000) -> AlertCondition:
    return AlertCondition(
        kind=NotificationKind.PAYOUT_OVERDUE,
        subject="provider:steadfast",
        params={"provider": "steadfast", "count": 2, "amount_paisa": amount, "days": 7},
        magnitude=amount,
        amount_paisa=amount,
        item_count=2,
        target_params={"provider": "steadfast"},
    )


async def _age(db: AsyncSession, notification: Notification, delta: timedelta) -> None:
    notification.created_at = notification.created_at - delta
    await db.flush()


async def _give_push_tokens(system_db: AsyncSession, *sessions: dict) -> dict[str, str]:
    """A push token on each member's device; returns user_id -> token."""
    from app.auth.models import Device

    tokens: dict[str, str] = {}
    for session in sessions:
        user_id = uuid.UUID(session["user_id"])
        devices = (
            (await system_db.execute(sa.select(Device).where(Device.user_id == user_id)))
            .scalars()
            .all()
        )
        token = f"fcm-{user_id.hex}-" + "t" * 20
        for device in devices:
            device.push_token = token
        tokens[session["user_id"]] = token
    await system_db.commit()
    return tokens


def _dispatcher(system_db: AsyncSession, settings: Any, push: Any) -> NotificationDispatcher:
    return NotificationDispatcher(
        system_db, settings=settings, push=push, sms=build_sms_transport(settings)
    )


async def _list(client: AsyncClient, session: dict, **params: Any) -> list[dict]:
    response = await client.get("/v1/notifications", params=params, headers=auth_header(session))
    assert response.status_code == 200, response.text
    return response.json()["items"]


# --------------------------------------------------------------------------- #
# Detectors
# --------------------------------------------------------------------------- #


class TestPayoutOverdue:
    async def test_overdue_cod_is_one_alert_per_courier_with_the_money_screen_figure(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        from app.money.cashflow import CashflowService

        await _delivered(client, shop, db, cod=895_000, days=12)
        await _delivered(client, shop, db, cod=100_000, days=2)  # not overdue yet

        assert await SmartAlerts(db).run() == 1
        await db.commit()

        [alert] = await _alerts(db, NotificationKind.PAYOUT_OVERDUE)
        [balance] = await CashflowService(db).courier_balances()
        assert alert.amount_paisa == balance.overdue_paisa == 895_000
        assert alert.payload["params"]["count"] == 1
        assert alert.payload["params"]["days"] == 7
        assert "৳8,950" in alert.title
        assert alert.category == str(NotificationCategory.MONEY)
        assert alert.audience == "money.view"
        assert alert.payload["target"]["route"] == "receivables"

    async def test_nothing_overdue_raises_nothing(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await _delivered(client, shop, db, cod=100_000, days=3)
        assert await SmartAlerts(db).run() == 0


class TestReconciliationDiscrepancy:
    async def test_open_cases_are_one_summary_not_one_per_row(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        db.add_all(
            [
                _case(CaseKind.MISSING_COD, 50_000),
                _case(CaseKind.MISSING_COD, 30_000),
                _case(CaseKind.CHARGE_MISMATCH, 2_000),
                # Left to PAYOUT_OVERDUE, and closed cases are not discrepancies.
                _case(CaseKind.DELIVERED_BUT_UNPAID, 99_000),
                _case(CaseKind.MISSING_COD, 70_000, status=CaseStatus.RESOLVED),
            ]
        )
        await db.commit()

        await SmartAlerts(db).run()
        await db.commit()

        [alert] = await _alerts(db, NotificationKind.RECONCILIATION_DISCREPANCY)
        params = alert.payload["params"]
        assert params["count"] == 3
        assert params["by_kind"] == {"MISSING_COD": 2, "CHARGE_MISMATCH": 1}
        assert alert.amount_paisa == 82_000
        assert alert.entity_id is None
        assert alert.payload["target"] == {"route": "reconciliation"}
        assert "2 missing COD" in alert.body

    async def test_a_single_case_links_to_that_case(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        case = _case(CaseKind.UNDERPAID, 42_000)
        db.add(case)
        await db.commit()

        await SmartAlerts(db).run()
        await db.commit()

        [alert] = await _alerts(db, NotificationKind.RECONCILIATION_DISCREPANCY)
        assert alert.entity_type == "reconciliation_case"
        assert alert.entity_id == case.id
        assert alert.payload["target"] == {"route": "reconciliation_case", "id": str(case.id)}


class TestCourierStuck:
    async def test_only_parcels_idle_past_their_status_limit_count(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        stuck = await dispatched_parcel(client, shop, db)
        moving = await dispatched_parcel(client, shop, db)
        manual = await dispatched_parcel(client, shop, db)
        now = utc_now()
        for parcel, provider, idle_days in (
            (stuck, "steadfast", 12),
            (moving, "steadfast", 3),  # a normal delivery time is not a problem
            (manual, "manual", 40),  # no courier feed to be stuck in
        ):
            consignment: Consignment = parcel["consignment"]
            consignment.provider = provider
            consignment.status = str(ConsignmentStatus.IN_TRANSIT)
            consignment.last_status_at = now - timedelta(days=idle_days)
        await db.commit()

        await SmartAlerts(db).run()
        await db.commit()

        [alert] = await _alerts(db, NotificationKind.COURIER_STATUS_STUCK)
        assert alert.payload["params"]["count"] == 1
        assert alert.payload["params"]["by_status"] == {"IN_TRANSIT": 1}
        assert alert.payload["params"]["oldest_days"] == 12
        assert alert.entity_type == "order"
        assert alert.entity_id == stuck["consignment"].order_id
        assert alert.audience == "order.book"


class TestCanonicalRto:
    @pytest.fixture(autouse=True)
    def small_sample(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The real minimum is 20 completed parcels; the rule is the same.
        monkeypatch.setattr(rules, "RTO_THRESHOLDS", rules.RtoThresholds(min_completed=4))

    async def test_courier_cancelled_is_rto_not_only_returned(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        """Regression: the old spike check counted RETURNED alone.

        Steadfast's return-to-origin arrives as CANCELLED, so a shop whose RTO
        is all Steadfast cancellations would have shown 0% and never alerted.
        """
        seller = _Shop(client, shop)
        await seller.parcel("DELIVERED")
        await seller.parcel("DELIVERED")
        await seller.parcel("CANCELLED", provider="steadfast")
        await seller.parcel("CANCELLED", provider="steadfast")
        # None of these is RTO or in the denominator.
        await seller.parcel("LOST")
        await seller.parcel(None)
        await seller.cancel_before_dispatch()

        assert await SmartAlerts(db).run() == 1
        await db.commit()

        [alert] = await _alerts(db, NotificationKind.HIGH_RTO)
        params = alert.payload["params"]
        assert (params["returned"], params["courier_cancelled"], params["rto"]) == (0, 2, 2)
        assert params["completed"] == 4
        assert params["rate_bps"] == 5_000
        assert alert.body == "2 of 4 completed parcels were RTO in the last 30 days."

        # The same numbers the Returns & RTO screen shows.
        screen = await seller.get("/v1/analytics/rto/summary", days=30)
        assert screen["counts"]["rto"] == params["rto"]
        assert screen["counts"]["completed"] == params["completed"]

    async def test_no_alert_below_the_minimum_sample(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        seller = _Shop(client, shop)
        for _ in range(3):
            await seller.parcel("CANCELLED", provider="steadfast")

        await SmartAlerts(db).run()
        assert await _alerts(db, NotificationKind.HIGH_RTO) == []


class TestOtherDetectors:
    async def test_low_stock_uses_only_the_sellers_own_threshold(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        from app.products.models import Product

        low = await create_product(client, shop, name="Silk Scarf", opening_stock=2)
        await create_product(client, shop, name="No Threshold", opening_stock=0)
        product = await db.get(Product, uuid.UUID(low["id"]))
        assert product is not None
        product.low_stock_threshold = 3
        await db.commit()

        await SmartAlerts(db).run()
        await db.commit()

        [alert] = await _alerts(db, NotificationKind.LOW_STOCK)
        assert alert.payload["params"]["count"] == 1
        assert alert.title == "Silk Scarf: 2 left"
        assert alert.entity_type == "product"
        assert alert.audience == "inventory.adjust"

    async def test_courier_account_alert_names_the_provider_and_clears(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        from app.couriers.models import CourierAccount, CourierAccountStatus

        account = CourierAccount(
            provider="steadfast",
            status=str(CourierAccountStatus.NEEDS_RECONNECT),
            masked_identifier="****9876",
            last_validation_message="401 Unauthorized: api key abc123 invalid",
        )
        db.add(account)
        await db.commit()

        await SmartAlerts(db).run()
        await db.commit()
        [alert] = await _alerts(db, NotificationKind.COURIER_ACCOUNT_PROBLEM)
        assert alert.payload["params"] == {"provider": "steadfast"}
        text = alert.title + alert.body + str(alert.payload)
        assert "9876" not in text and "abc123" not in text and "401" not in text
        assert alert.audience == "courier.credential_manage"
        assert alert.payload["target"] == {"route": "courier_account", "id": str(account.id)}

        account.status = str(CourierAccountStatus.CONNECTED)
        await db.commit()
        await SmartAlerts(db).run()
        await db.commit()
        await db.refresh(alert)
        assert alert.state is NotificationState.RESOLVED


# --------------------------------------------------------------------------- #
# Dedupe, cooldown, resolution
# --------------------------------------------------------------------------- #


class TestLifecycle:
    async def test_running_the_scheduler_twice_writes_once(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await _delivered(client, shop, db, cod=140_500, days=12)
        assert await SmartAlerts(db).run() == 1
        await db.commit()
        assert await SmartAlerts(db).run() == 0
        await db.commit()
        assert len(await _alerts(db)) == 1

    async def test_an_unchanged_condition_waits_out_its_cooldown(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        lifecycle = AlertLifecycle(db, NotificationService(db))
        kind = NotificationKind.COURIER_STATUS_STUCK
        assert await lifecycle.sync(kind, [_stuck(3)]) == 1
        [first] = await _alerts(db)

        await _age(db, first, timedelta(days=2))  # cooldown is 3 days
        assert await lifecycle.sync(kind, [_stuck(3)]) == 0

        await _age(db, first, timedelta(days=2))
        assert await lifecycle.sync(kind, [_stuck(3)]) == 1
        reminder = (await _alerts(db))[-1]
        assert reminder.payload["reason"] == "REMINDER"
        assert reminder.dedupe_key == "shop#2"

    async def test_material_worsening_raises_again_but_not_twice_a_morning(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        lifecycle = AlertLifecycle(db, NotificationService(db))
        kind = NotificationKind.COURIER_STATUS_STUCK
        await lifecycle.sync(kind, [_stuck(3)])
        [first] = await _alerts(db)

        assert await lifecycle.sync(kind, [_stuck(4)]) == 0  # not material
        assert await lifecycle.sync(kind, [_stuck(9)]) == 0  # material, too soon

        await _age(db, first, timedelta(hours=21))
        assert await lifecycle.sync(kind, [_stuck(9)]) == 1
        assert (await _alerts(db))[-1].payload["reason"] == "WORSENED"

    async def test_a_cleared_condition_resolves_and_stays_as_history(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        lifecycle = AlertLifecycle(db, NotificationService(db))
        kind = NotificationKind.COURIER_STATUS_STUCK
        await lifecycle.sync(kind, [_stuck(3)])
        assert await lifecycle.sync(kind, []) == 0
        await db.commit()

        [alert] = await _alerts(db)
        assert alert.state is NotificationState.RESOLVED
        [item] = await _list(client, shop)
        assert item["state"] == "RESOLVED"
        assert item["resolved_at"] is not None

        # Nothing further while it stays clear; a recurrence is a new alert.
        assert await lifecycle.sync(kind, []) == 0
        assert await lifecycle.sync(kind, [_stuck(3)]) == 1


# --------------------------------------------------------------------------- #
# Recipients, preferences, tenancy
# --------------------------------------------------------------------------- #


class TestRecipients:
    @pytest.fixture
    async def team(self, client: AsyncClient, shop: dict, db: AsyncSession) -> dict[str, Any]:
        from tests.conftest_commerce import grant_plan

        await grant_plan(shop["tenant_id"], "pro")  # the Free plan has no team
        finance = await _member_session(client, shop, _phone(), TenantRole.FINANCE)
        operator = await _member_session(client, shop, _phone(), TenantRole.ORDER_OPERATOR)
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(shop["tenant_id"])))
        lifecycle = AlertLifecycle(db, NotificationService(db))
        await lifecycle.sync(NotificationKind.PAYOUT_OVERDUE, [_payout()])
        await lifecycle.sync(NotificationKind.COURIER_STATUS_STUCK, [_stuck(3)])
        await db.commit()
        return {"owner": shop, "finance": finance, "operator": operator}

    async def test_each_role_sees_only_its_categories(
        self, client: AsyncClient, team: dict[str, Any]
    ) -> None:
        kinds = {
            name: {item["kind"] for item in await _list(client, session)}
            for name, session in team.items()
        }
        assert kinds["owner"] == {"PAYOUT_OVERDUE", "COURIER_STATUS_STUCK"}
        assert kinds["finance"] == {"PAYOUT_OVERDUE"}
        assert kinds["operator"] == {"COURIER_STATUS_STUCK"}

        badge = await client.get(
            "/v1/notifications/unread-count", headers=auth_header(team["finance"])
        )
        assert badge.json()["unread"] == 1

    async def test_push_goes_only_to_the_audience(
        self,
        client: AsyncClient,
        team: dict[str, Any],
        db: AsyncSession,
        system_db: AsyncSession,
        settings: Any,
    ) -> None:
        tokens = await _give_push_tokens(system_db, *team.values())
        [payout] = await _alerts(db, NotificationKind.PAYOUT_OVERDUE)
        notification = await system_db.get(Notification, payout.id)
        assert notification is not None

        push = MockPushTransport()
        await _dispatcher(system_db, settings, push).dispatch(notification)
        assert {m.token for m in push.sent} == {
            tokens[team["owner"]["user_id"]],
            tokens[team["finance"]["user_id"]],
        }
        # Deep link and route travel with the push; no duplicate on a re-run.
        assert push.sent[0].data["route"] == "receivables"
        assert await _dispatcher(system_db, settings, push).dispatch(notification) == []

    async def test_a_members_muted_category_is_theirs_alone(
        self,
        client: AsyncClient,
        team: dict[str, Any],
        db: AsyncSession,
        system_db: AsyncSession,
        settings: Any,
    ) -> None:
        operator, owner = team["operator"], team["owner"]
        updated = await client.patch(
            "/v1/account/notification-preferences",
            json={"muted_categories": ["COURIER", "NOT_A_CATEGORY"]},
            headers=auth_header(operator),
        )
        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["muted_categories"] == ["COURIER"]
        assert "MONEY" not in body["categories"] and "COURIER" in body["categories"]

        assert await _list(client, operator) == []
        owner_prefs = await client.get(
            "/v1/account/notification-preferences", headers=auth_header(owner)
        )
        assert owner_prefs.json()["muted_categories"] == []
        assert {i["kind"] for i in await _list(client, owner)} == {
            "PAYOUT_OVERDUE",
            "COURIER_STATUS_STUCK",
        }

        tokens = await _give_push_tokens(system_db, owner, operator)
        [stuck] = await _alerts(db, NotificationKind.COURIER_STATUS_STUCK)
        notification = await system_db.get(Notification, stuck.id)
        push = MockPushTransport()
        await _dispatcher(system_db, settings, push).dispatch(notification)
        assert {m.token for m in push.sent} == {tokens[owner["user_id"]]}

    async def test_a_notification_outside_your_audience_cannot_be_marked(
        self, client: AsyncClient, team: dict[str, Any], db: AsyncSession
    ) -> None:
        [payout] = await _alerts(db, NotificationKind.PAYOUT_OVERDUE)
        response = await client.post(
            f"/v1/notifications/{payout.id}/read", headers=auth_header(team["operator"])
        )
        assert response.status_code == 404
        cleared = await client.post(
            "/v1/notifications/read-all", headers=auth_header(team["operator"])
        )
        assert cleared.status_code == 200
        await db.refresh(payout)
        assert payout.read_at is None


class TestTenantIsolation:
    async def test_another_shops_scan_neither_sees_nor_resolves_these_alerts(
        self, client: AsyncClient, shop: dict, db: AsyncSession, system_db: AsyncSession
    ) -> None:
        await AlertLifecycle(db, NotificationService(db)).sync(
            NotificationKind.PAYOUT_OVERDUE, [_payout()]
        )
        await db.commit()
        [mine] = await _alerts(db)

        other = await signed_in_shop(client, _phone(), shop_name="Other Shop")
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(other["tenant_id"])))
        assert await SmartAlerts(db).run() == 0
        await db.commit()
        assert await _alerts(db) == []
        assert await _list(client, other) == []

        row = await system_db.get(Notification, mine.id)
        assert row is not None and row.resolved_at is None


# --------------------------------------------------------------------------- #
# Imports
# --------------------------------------------------------------------------- #


class TestImportAlerts:
    async def _batch(self, db: AsyncSession, shop: dict, **fields: Any):
        from app.imports.models import ImportBatch

        batch = ImportBatch(
            template="ORDERS",
            original_filename="march-orders.csv",
            source_sha256="a" * 64,
            created_by_user_id=uuid.UUID(shop["user_id"]),
            **fields,
        )
        db.add(batch)
        await db.commit()
        return batch

    async def test_many_rejected_rows_are_one_summary_for_the_uploader(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        batch = await self._batch(
            db,
            shop,
            status="COMMITTED",
            row_count=20,
            invalid_count=6,
            created_count=14,
            committed_at=utc_now(),
        )
        alerts = SmartAlerts(db)
        alert = await alerts.import_alert(batch.id)
        assert alert is not None
        assert alert.user_id == uuid.UUID(shop["user_id"])
        assert alert.entity_type == "import" and alert.entity_id == batch.id
        assert alert.title == "Import finished with 6 rejected rows"
        assert "14 of 20 order rows" in alert.body
        assert await alerts.import_alert(batch.id) is None
        assert await alerts.run() == 0
        assert len(await _alerts(db, NotificationKind.IMPORT_FAILURE)) == 1

    async def test_a_couple_of_rejections_in_a_big_file_are_not_noise(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        batch = await self._batch(
            db, shop, status="COMMITTED", row_count=200, invalid_count=2, created_count=198
        )
        assert await SmartAlerts(db).import_alert(batch.id) is None

    async def test_a_stopped_import_clears_when_it_finishes(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        batch = await self._batch(
            db, shop, status="COMMITTING", row_count=50, failure_reason="stopped part-way"
        )
        alerts = SmartAlerts(db)
        stopped = await alerts.import_alert(batch.id)
        assert stopped is not None and stopped.title == "Import did not finish"

        batch.status = "COMMITTED"
        batch.failure_reason = None
        batch.created_count = 50
        await db.commit()
        assert await alerts.import_alert(batch.id) is None
        await db.refresh(stopped)
        assert stopped.state is NotificationState.RESOLVED


# --------------------------------------------------------------------------- #
# Delivery and wording
# --------------------------------------------------------------------------- #


class TestPushAndWording:
    async def test_a_failed_push_leaves_the_in_app_alert(
        self,
        client: AsyncClient,
        shop: dict,
        db: AsyncSession,
        system_db: AsyncSession,
        settings: Any,
    ) -> None:
        class ExplodingTransport:
            name = "exploding"
            is_configured = True

            async def send(self, message: PushMessage) -> Any:
                raise TimeoutError("provider unreachable")

        await AlertLifecycle(db, NotificationService(db)).sync(
            NotificationKind.PAYOUT_OVERDUE, [_payout()]
        )
        await db.commit()
        await _give_push_tokens(system_db, shop)
        [alert] = await _alerts(db)
        notification = await system_db.get(Notification, alert.id)

        deliveries = await _dispatcher(system_db, settings, ExplodingTransport()).dispatch(
            notification
        )
        await system_db.commit()
        assert deliveries[0].state == str(DeliveryState.RETRYING)
        [item] = await _list(client, shop)
        assert item["id"] == str(alert.id) and item["state"] == "NEW"

    async def test_the_same_facts_read_in_english_and_bangla(
        self,
        client: AsyncClient,
        shop: dict,
        db: AsyncSession,
        system_db: AsyncSession,
        settings: Any,
    ) -> None:
        await AlertLifecycle(db, NotificationService(db)).sync(
            NotificationKind.PAYOUT_OVERDUE, [_payout(895_000)]
        )
        await db.commit()

        [en] = await _list(client, shop, lang="en")
        [bn] = await _list(client, shop, lang="bn")
        assert en["title"] == "Steadfast: ৳8,950 overdue"
        assert bn["title"] == "Steadfast: ৳8,950 বকেয়া"
        assert en["category"] == "MONEY"
        assert en["payload"]["target"] == {
            "route": "receivables",
            "params": {"provider": "steadfast"},
        }

        # A push is worded in the recipient's own account language (bn by default).
        await _give_push_tokens(system_db, shop)
        notification = await system_db.get(Notification, uuid.UUID(en["id"]))
        push = MockPushTransport()
        await _dispatcher(system_db, settings, push).dispatch(notification)
        assert push.sent[0].title == bn["title"]

    @pytest.mark.parametrize("locale", ["en", "bn"])
    def test_every_smart_kind_renders_in_both_languages(self, locale: str) -> None:
        samples: dict[NotificationKind, dict[str, Any]] = {
            NotificationKind.PAYOUT_OVERDUE: _payout().params,
            NotificationKind.RECONCILIATION_DISCREPANCY: {
                "count": 2,
                "amount_paisa": 5_000,
                "by_kind": {"MISSING_COD": 1, "CHARGE_MISMATCH": 1},
            },
            NotificationKind.COURIER_STATUS_STUCK: _stuck(2).params,
            NotificationKind.HIGH_RTO: {
                "rto": 8,
                "completed": 25,
                "rate_bps": 3_200,
                "days": 30,
                "prev_rto": 4,
                "prev_completed": 30,
                "prev_rate_bps": 1_333,
            },
            NotificationKind.LOW_STOCK: {"count": 7, "names": ["A", "B"]},
            NotificationKind.NEGATIVE_MARGIN: {"count": 2, "loss_paisa": 4_500, "days": 7},
            NotificationKind.IMPORT_FAILURE: {
                "reason": "FAILED",
                "template": "PRODUCTS",
                "file": "p.csv",
                "rejected": 0,
                "created": 0,
                "row_count": 9,
            },
            NotificationKind.COURIER_ACCOUNT_PROBLEM: {"provider": "pathao"},
            NotificationKind.RETURNED_NOT_RESTOCKED: {"count": 2},
            NotificationKind.FOLLOW_UP_DUE: {"count": 2},
            NotificationKind.PURCHASE_ORDER_OVERDUE: {"count": 2, "numbers": ["PO-00001"]},
            NotificationKind.PARTIAL_RECEIPT_PENDING: {
                "count": 1,
                "days": 3,
                "numbers": ["PO-00002"],
            },
            NotificationKind.SUPPLIER_PAYMENT_OVERDUE: {"count": 1, "amount_paisa": 250_000},
            NotificationKind.STOCKOUT_PREDICTED: {"count": 2, "names": ["Borka (L)", "Hijab"]},
        }
        assert set(samples) == set(rules.ALERT_RULES)
        for kind, params in samples.items():
            rendered = render(kind, params, locale)
            assert rendered is not None, kind
            title, body = rendered
            assert title and body, kind
        assert render(NotificationKind.HIGH_RTO, samples[NotificationKind.HIGH_RTO], "en") == (
            "RTO 32% in the last 30 days",
            "8 of 25 completed parcels were RTO in the last 30 days. "
            "The 30 days before: 4 of 30 (13.3%).",
        )
