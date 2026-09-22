"""Alerts, the notification centre and the Friday summary.

Master spec sections 23, 24, 94 and 95. The properties that matter:

* the same warning, on the same day, is one row — a centre that repeats itself
  is a centre nobody reads, and then the important alert goes unread with the
  rest;
* an alert with nothing in it is never raised, because section 23 forbids
  notifications for non-actionable changes;
* a ranking is withheld, with a visible reason, until the sample supports it
  (section 24);
* the Friday summary's figures agree with the screens they came from.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.consignments.models import ConsignmentStatus
from app.consignments.service import DeliveryOutcome
from app.core.clock import FRIDAY, business_date, tenant_now, utc_now
from app.core.context import RequestContext, set_context
from app.core.errors import NotFoundError
from app.notifications.alerts import MIN_RANKING_SAMPLE, AlertService
from app.notifications.jobs import SUMMARY_HOUR
from app.notifications.models import Notification, NotificationKind, Severity
from app.notifications.service import NotificationService
from app.profit.service import ProfitService
from app.reconciliation.models import CaseKind, CaseStatus
from app.reconciliation.service import ReconciliationService
from tests.conftest_commerce import (
    create_order,
    create_product,
    dispatched_parcel,
    signed_in_shop,
)


@pytest.fixture
async def shop(client: AsyncClient, unique_phone: str) -> dict:
    session = await signed_in_shop(client, unique_phone, shop_name="Alert Shop")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
    return session


async def settled_parcel(
    client: AsyncClient,
    shop: dict,
    db: AsyncSession,
    *,
    cod: int = 140_500,
    status: ConsignmentStatus = ConsignmentStatus.DELIVERED,
    days_ago: int = 0,
    product_name: str = "Cotton Abaya",
    provider: str | None = None,
) -> dict:
    """A parcel taken all the way to a profit snapshot."""
    from app.consignments.service import ConsignmentService
    from app.ledger.service import LedgerService
    from app.money.service import ReceivableService

    product = await create_product(
        client,
        shop,
        name=product_name,
        sku=f"sku-{uuid.uuid4().hex[:8]}",
        opening_stock=50,
    )
    order = await create_order(
        client,
        shop,
        items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": cod}],
        cod_amount_paisa=cod,
    )

    ledger = LedgerService(db)
    receivables = ReceivableService(db, ledger=ledger)
    consignments = ConsignmentService(db, receivables=receivables)
    consignment = await consignments.dispatch_manual(uuid.UUID(order["order"]["id"]))
    if provider is not None:
        consignment.provider = provider
    await db.commit()

    await consignments.record_outcome(
        consignment.id,
        DeliveryOutcome(status=status, occurred_at=utc_now() - timedelta(days=days_ago)),
    )
    await db.commit()
    snapshot = await ProfitService(db).snapshot(consignment.id)
    await db.commit()

    return {
        "product": product,
        "order": order["order"],
        "consignment": consignment,
        "snapshot": snapshot,
        "receivables": receivables,
    }


async def unpaid_case(client: AsyncClient, shop: dict, db: AsyncSession, *, cod: int) -> None:
    """One delivered-but-unpaid case, old enough for the scan to see it."""
    parcel = await dispatched_parcel(client, shop, db, cod_paisa=cod)
    await parcel["consignments"].record_outcome(
        parcel["consignment"].id,
        DeliveryOutcome(
            status=ConsignmentStatus.DELIVERED,
            occurred_at=utc_now() - timedelta(days=12),
        ),
    )
    await db.commit()
    await ReconciliationService(db, receivables=parcel["receivables"]).scan_for_cases()
    await db.commit()


class TestDeduplication:
    async def test_the_same_warning_twice_in_a_day_is_one_row(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        service = NotificationService(db)
        first = await service.notify(
            kind=NotificationKind.DELIVERED_BUT_UNPAID,
            severity=Severity.CRITICAL,
            title="Delivered but not paid",
            body="7 parcels reached the customer and the money has not arrived.",
        )
        second = await service.notify(
            kind=NotificationKind.DELIVERED_BUT_UNPAID,
            severity=Severity.CRITICAL,
            title="Delivered but not paid",
            body="8 parcels reached the customer and the money has not arrived.",
        )
        await db.commit()

        assert first is not None
        # Section 94: deduplicate repetitive warnings. The second run of the
        # day writes nothing rather than a near-identical row.
        assert second is None
        assert await service.unread_count() == 1

    async def test_a_different_dedupe_key_is_a_different_notification(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        service = NotificationService(db)
        await service.notify(
            kind=NotificationKind.UNDERPAID,
            severity=Severity.WARNING,
            title="Paid short",
            body="One payment came in below what the parcel was owed.",
            dedupe_key="line:1",
        )
        second = await service.notify(
            kind=NotificationKind.UNDERPAID,
            severity=Severity.WARNING,
            title="Paid short",
            body="Another payment came in below what the parcel was owed.",
            dedupe_key="line:2",
        )
        await db.commit()

        assert second is not None
        assert await service.unread_count() == 2

    async def test_tomorrow_is_a_new_notification(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        service = NotificationService(db)
        yesterday = utc_now() - timedelta(days=1)
        await service.notify(
            kind=NotificationKind.STALE_IN_TRANSIT,
            severity=Severity.WARNING,
            title="Stuck in transit",
            body="4 parcels have been with a courier far longer than usual.",
            occurred_at=yesterday,
        )
        today = await service.notify(
            kind=NotificationKind.STALE_IN_TRANSIT,
            severity=Severity.WARNING,
            title="Stuck in transit",
            body="5 parcels have been with a courier far longer than usual.",
        )
        await db.commit()

        # Still a problem tomorrow is still worth saying tomorrow.
        assert today is not None
        assert await service.unread_count() == 2


class TestReading:
    async def test_marking_one_read_leaves_the_others(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        service = NotificationService(db)
        first = await service.notify(
            kind=NotificationKind.UNDERPAID,
            severity=Severity.WARNING,
            title="Paid short",
            body="A payment came up short.",
            dedupe_key="a",
        )
        await service.notify(
            kind=NotificationKind.UNDERPAID,
            severity=Severity.WARNING,
            title="Paid short",
            body="Another payment came up short.",
            dedupe_key="b",
        )
        await db.commit()

        assert first is not None
        await service.mark_read(first.id)
        await db.commit()
        assert await service.unread_count() == 1

    async def test_marking_all_read_says_how_many(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        service = NotificationService(db)
        for index in range(3):
            await service.notify(
                kind=NotificationKind.UNDERPAID,
                severity=Severity.WARNING,
                title="Paid short",
                body="A payment came up short.",
                dedupe_key=f"line:{index}",
            )
        await db.commit()

        assert await service.mark_all_read() == 3
        await db.commit()
        assert await service.unread_count() == 0

    async def test_an_unknown_notification_is_not_found(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        with pytest.raises(NotFoundError):
            await NotificationService(db).mark_read(uuid.uuid4())

    async def test_unread_only_filters(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        service = NotificationService(db)
        read = await service.notify(
            kind=NotificationKind.UNDERPAID,
            severity=Severity.WARNING,
            title="Paid short",
            body="A payment came up short.",
            dedupe_key="a",
        )
        await service.notify(
            kind=NotificationKind.RETURN_SPIKE,
            severity=Severity.WARNING,
            title="More returns than usual",
            body="Returns are up.",
        )
        await db.commit()
        assert read is not None
        await service.mark_read(read.id)
        await db.commit()

        unread = await service.list_notifications(unread_only=True)
        assert [item.notification_kind for item in unread] == [NotificationKind.RETURN_SPIKE]


class TestSeverity:
    def test_info_never_justifies_a_push(self) -> None:
        # Section 95: push only for action, material money, or a threshold.
        assert Severity.INFO.deserves_push is False
        assert Severity.ACTION.deserves_push is True
        assert Severity.WARNING.deserves_push is True
        assert Severity.CRITICAL.deserves_push is True

    def test_severity_orders_by_how_much_is_at_stake(self) -> None:
        assert Severity.CRITICAL.rank > Severity.WARNING.rank > Severity.ACTION.rank
        assert Severity.ACTION.rank > Severity.INFO.rank


class TestBundling:
    async def test_an_empty_bundle_writes_nothing(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        result = await NotificationService(db).bundle([], title="Nothing", body="Nothing")
        assert result is None

    async def test_a_bundle_carries_the_deep_links(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        items = [("consignment", uuid.uuid4()) for _ in range(5)]
        bundle = await NotificationService(db).bundle(
            items,
            title="5 parcels need attention",
            body="Five parcels are waiting on you.",
        )
        await db.commit()

        assert bundle is not None
        assert bundle.item_count == 5
        assert len(bundle.payload["items"]) == 5


class TestAlerts:
    async def test_the_four_alerts_are_always_reported(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        alerts = await AlertService(db).current_alerts()
        # Section 23's four lines, in its order, even when every one is empty:
        # the Money screen shows the row set, not whichever happen to be full.
        assert [alert.kind for alert in alerts] == [
            NotificationKind.DELIVERED_BUT_UNPAID,
            NotificationKind.UNDERPAID,
            NotificationKind.STALE_IN_TRANSIT,
            NotificationKind.RETURNED_NOT_RESTOCKED,
        ]
        assert all(alert.is_empty for alert in alerts)

    async def test_an_empty_alert_raises_no_notification(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        created = await AlertService(db).raise_daily_alerts()
        await db.commit()

        # Section 23: no noisy notifications for non-actionable changes.
        # "0 parcels unpaid" is the purest form of that.
        assert created == 0
        assert await NotificationService(db).unread_count() == 0

    async def test_delivered_but_unpaid_is_counted_and_priced(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await unpaid_case(client, shop, db, cod=140_500)
        await unpaid_case(client, shop, db, cod=89_500)

        alerts = {alert.kind: alert for alert in await AlertService(db).current_alerts()}
        unpaid = alerts[NotificationKind.DELIVERED_BUT_UNPAID]
        assert unpaid.count == 2
        assert unpaid.amount_paisa == 230_000
        # The most expensive thing the product finds, so it is the loudest.
        assert unpaid.severity is Severity.CRITICAL

    async def test_the_alert_says_the_money_not_just_the_count(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await unpaid_case(client, shop, db, cod=895_000)
        created = await AlertService(db).raise_daily_alerts()
        await db.commit()

        assert created == 1
        [notification] = await NotificationService(db).list_notifications()
        assert "৳8,950" in notification.title
        assert notification.amount_paisa == 895_000
        assert notification.item_count == 1

    async def test_running_the_scan_twice_leaves_one_alert(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await unpaid_case(client, shop, db, cod=140_500)
        service = AlertService(db)
        assert await service.raise_daily_alerts() == 1
        await db.commit()
        assert await service.raise_daily_alerts() == 0
        await db.commit()

        assert await NotificationService(db).unread_count() == 1

    async def test_a_resolved_case_stops_being_an_alert(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await unpaid_case(client, shop, db, cod=140_500)
        engine = ReconciliationService(db)
        [case] = await engine.list_cases(kind=CaseKind.DELIVERED_BUT_UNPAID)
        await engine.update_case(
            case.id, status=CaseStatus.RESOLVED, resolution="Courier paid in cash"
        )
        await db.commit()

        alerts = {alert.kind: alert for alert in await AlertService(db).current_alerts()}
        assert alerts[NotificationKind.DELIVERED_BUT_UNPAID].is_empty

    async def test_a_stale_parcel_reports_the_exposure(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=520_000)
        parcel["consignment"].booked_at = utc_now() - timedelta(days=20)
        await db.commit()
        await ReconciliationService(db, receivables=parcel["receivables"]).scan_for_cases()
        await db.commit()

        alerts = {alert.kind: alert for alert in await AlertService(db).current_alerts()}
        stale = alerts[NotificationKind.STALE_IN_TRANSIT]
        assert stale.count == 1
        assert stale.amount_paisa == 520_000


class TestWeeklySummary:
    async def test_the_week_is_seven_days_to_the_dhaka_date(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        summary = await AlertService(db).weekly_summary()
        assert summary.week_end == business_date(at=utc_now())
        assert (summary.week_end - summary.week_start).days == 6

    async def test_the_figures_match_the_parcels(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await settled_parcel(client, shop, db, cod=140_500)
        await settled_parcel(client, shop, db, cod=89_500)
        await settled_parcel(client, shop, db, cod=60_000, status=ConsignmentStatus.RETURNED)

        summary = await AlertService(db).weekly_summary()
        assert summary.order_count == 3
        assert summary.delivered_count == 2
        assert summary.return_count == 1
        # A returned parcel collects nothing, so it adds nothing to sales.
        assert summary.sales_paisa == 230_000

        totals = await ProfitService(db).totals(since=summary.week_start, until=summary.week_end)
        # The summary and the Insights screen must not disagree.
        assert summary.contribution_profit_paisa == totals["contribution_profit_paisa"]

    async def test_cod_outstanding_matches_the_aging_report(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await settled_parcel(client, shop, db, cod=140_500)
        summary = await AlertService(db).weekly_summary()

        aging = await parcel["receivables"].aging()
        assert summary.cod_outstanding_paisa == sum(amount for _, _, amount in aging)
        assert summary.cod_outstanding_paisa == 140_500

    async def test_a_ranking_is_withheld_until_the_sample_supports_it(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await settled_parcel(client, shop, db, product_name="Cotton Abaya")
        await settled_parcel(client, shop, db, product_name="Silk Hijab")

        summary = await AlertService(db).weekly_summary()
        # Section 24: do not show unreliable rankings before enough sample
        # exists — and say why, rather than leaving a blank tile.
        assert summary.best_product is None
        assert summary.worst_product is None
        assert summary.ranking_note is not None
        assert str(MIN_RANKING_SAMPLE) in summary.ranking_note

    async def test_a_ranking_appears_once_the_sample_is_enough(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        for _ in range(MIN_RANKING_SAMPLE):
            await settled_parcel(client, shop, db, cod=200_000, product_name="Cotton Abaya")
        for _ in range(MIN_RANKING_SAMPLE):
            await settled_parcel(
                client,
                shop,
                db,
                cod=200_000,
                product_name="Silk Hijab",
                status=ConsignmentStatus.RETURNED,
            )

        summary = await AlertService(db).weekly_summary()
        assert summary.ranking_note is None
        assert summary.best_product is not None
        assert summary.worst_product is not None
        # Delivered beats returned: the returns collected nothing and still
        # cost the goods.
        assert summary.best_product[0] == "Cotton Abaya"
        assert summary.worst_product[0] == "Silk Hijab"
        assert summary.best_product[1] > summary.worst_product[1]

    async def test_one_courier_is_never_ranked_against_itself(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        for _ in range(MIN_RANKING_SAMPLE):
            await settled_parcel(client, shop, db)

        summary = await AlertService(db).weekly_summary()
        assert summary.best_courier is None
        assert summary.worst_courier is None
        assert summary.courier_note is not None

    async def test_couriers_are_ranked_by_delivery_success(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        for index in range(MIN_RANKING_SAMPLE):
            await settled_parcel(client, shop, db, provider="alpha")
            await settled_parcel(
                client,
                shop,
                db,
                provider="beta",
                status=(ConsignmentStatus.RETURNED if index < 3 else ConsignmentStatus.DELIVERED),
            )

        summary = await AlertService(db).weekly_summary()
        assert summary.courier_note is None
        assert summary.best_courier == ("alpha", 10_000)
        assert summary.worst_courier is not None
        assert summary.worst_courier[0] == "beta"
        assert summary.worst_courier[1] == 4_000

    async def test_the_summary_is_information_not_an_interruption(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await AlertService(db).send_weekly_summary()
        await db.commit()

        [notification] = await NotificationService(db).list_notifications()
        assert notification.notification_kind is NotificationKind.WEEKLY_SUMMARY
        # Nobody's Friday evening should be interrupted by a summary.
        assert notification.notification_severity is Severity.INFO
        assert notification.notification_severity.deserves_push is False

    async def test_sending_it_twice_in_a_week_writes_one(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        service = AlertService(db)
        await service.send_weekly_summary()
        await db.commit()
        await service.send_weekly_summary()
        await db.commit()

        assert await NotificationService(db).unread_count() == 1

    async def test_the_payload_carries_every_figure_the_screen_shows(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await settled_parcel(client, shop, db, cod=140_500)
        await AlertService(db).send_weekly_summary()
        await db.commit()

        [notification] = await NotificationService(db).list_notifications()
        # Section 23's weekly list, so opening an old summary shows what it
        # said at the time rather than recomputing it from today's data.
        for field in (
            "order_count",
            "delivered_count",
            "return_count",
            "return_loss_paisa",
            "sales_paisa",
            "contribution_profit_paisa",
            "cod_outstanding_paisa",
            "overdue_paisa",
            "mismatch_count",
            "best_product",
            "worst_product",
            "best_courier",
            "worst_courier",
        ):
            assert field in notification.payload, field


class TestScheduling:
    def test_the_summary_hour_is_friday_evening_in_dhaka(self) -> None:
        # Section 42: Fri 18:00 Asia/Dhaka. Section 69: Friday means Friday in
        # the tenant timezone, not wherever the worker happens to run.
        assert SUMMARY_HOUR == 18
        assert FRIDAY == 4

    def test_a_late_utc_thursday_is_already_friday_in_dhaka(self) -> None:
        from datetime import UTC, datetime

        moment = datetime(2026, 9, 10, 19, 0, tzinfo=UTC)  # Thursday, UTC
        local = tenant_now("Asia/Dhaka", at=moment)
        assert local.weekday() == FRIDAY
        assert local.hour == 1


class TestTenantIsolation:
    async def test_one_shop_never_sees_another_shops_alerts(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await NotificationService(db).notify(
            kind=NotificationKind.UNDERPAID,
            severity=Severity.WARNING,
            title="Paid short",
            body="A payment came up short.",
        )
        await db.commit()

        other = await signed_in_shop(client, f"0171{uuid.uuid4().int % 10_000_000:07d}")
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(other["tenant_id"])))

        assert await NotificationService(db).unread_count() == 0
        assert await NotificationService(db).list_notifications() == []

    async def test_the_same_dedupe_key_is_free_in_another_shop(
        self,
        client: AsyncClient,
        shop: dict,
        db: AsyncSession,
        system_db: AsyncSession,
    ) -> None:
        await NotificationService(db).notify(
            kind=NotificationKind.UNDERPAID,
            severity=Severity.WARNING,
            title="Paid short",
            body="A payment came up short.",
            dedupe_key="shared",
        )
        await db.commit()

        other = await signed_in_shop(client, f"0171{uuid.uuid4().int % 10_000_000:07d}")
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(other["tenant_id"])))
        second = await NotificationService(db).notify(
            kind=NotificationKind.UNDERPAID,
            severity=Severity.WARNING,
            title="Paid short",
            body="A payment came up short.",
            dedupe_key="shared",
        )
        await db.commit()

        # The uniqueness is per shop: one seller's alert must never suppress
        # another's.
        assert second is not None
        total = await system_db.execute(
            sa.select(sa.func.count())
            .select_from(Notification)
            .where(
                Notification.dedupe_key == "shared",
                Notification.tenant_id.in_(
                    [uuid.UUID(shop["tenant_id"]), uuid.UUID(other["tenant_id"])]
                ),
            )
        )
        assert int(total.scalar_one()) == 2

    async def test_marking_all_read_clears_only_this_shops_badge(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        """A bulk UPDATE goes through none of the session's read guards.

        Written because the first version of ``mark_all_read`` did not filter
        by tenant, so one seller clearing their badge cleared everybody's.
        """
        await NotificationService(db).notify(
            kind=NotificationKind.UNDERPAID,
            severity=Severity.WARNING,
            title="Paid short",
            body="A payment came up short.",
        )
        await db.commit()

        other = await signed_in_shop(client, f"0171{uuid.uuid4().int % 10_000_000:07d}")
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(other["tenant_id"])))
        await NotificationService(db).notify(
            kind=NotificationKind.UNDERPAID,
            severity=Severity.WARNING,
            title="Paid short",
            body="A payment came up short.",
        )
        await db.commit()
        assert await NotificationService(db).mark_all_read() == 1
        await db.commit()

        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(shop["tenant_id"])))
        assert await NotificationService(db).unread_count() == 1
