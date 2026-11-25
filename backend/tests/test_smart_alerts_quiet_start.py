"""First-scan quiet mode (``SMART_ALERTS_PUSH_FROM``, launch run 14).

The first full smart-alert scan finds every condition that has been true for
weeks at once. Alerts it raises before ``SMART_ALERTS_PUSH_FROM`` land in the
notification centre only; per shop, one CRITICAL courier-account or
overdue-payout alert may still push. Alerts raised by an event (an account
entering ``NEEDS_RECONNECT`` now) and alerts after the moment push as usual.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.config import Settings, get_settings
from app.notifications.delivery import QUIET_START_REASON, DeliveryState, NotificationDelivery
from app.notifications.models import Notification, NotificationKind
from app.notifications.service import NotificationService
from app.notifications.smart import AlertLifecycle, courier_account_status_changed
from app.notifications.transport import MockPushTransport
from tests.test_smart_alerts import (
    _alerts,
    _dispatcher,
    _give_push_tokens,
    _list,
    _payout,
    _stuck,
    shop,  # noqa: F401 - fixture
)

_LATER = utc_now() + timedelta(hours=3)
_EARLIER = utc_now() - timedelta(hours=3)


async def _dispatch_all(
    system_db: AsyncSession, settings: Any, push: MockPushTransport, owner: dict
) -> dict:
    """Dispatch the shop's alerts in creation order; returns kind -> delivery states."""
    states: dict[str, list[str]] = {}
    rows = (
        await system_db.execute(
            sa.select(Notification)
            .where(Notification.tenant_id == uuid.UUID(owner["tenant_id"]))
            .order_by(Notification.created_at)
        )
    ).scalars()
    for notification in list(rows):
        kind = str(notification.kind)
        for delivery in await _dispatcher(system_db, settings, push).dispatch(notification):
            states.setdefault(kind, []).append(delivery.state)
    await system_db.commit()
    return states


async def _reasons(system_db: AsyncSession, owner: dict) -> set[str | None]:
    return set(
        (
            await system_db.execute(
                sa.select(NotificationDelivery.suppression_reason).where(
                    NotificationDelivery.tenant_id == uuid.UUID(owner["tenant_id"])
                )
            )
        ).scalars()
    )


def test_the_setting_reads_an_offset_and_refuses_a_naive_time(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMART_ALERTS_PUSH_FROM", "2026-11-26T12:00:00+06:00")
    assert Settings().smart_alerts_push_from == datetime(2026, 11, 26, 6, tzinfo=UTC)
    monkeypatch.setenv("SMART_ALERTS_PUSH_FROM", "2026-11-26T12:00:00")
    with pytest.raises(ValueError):
        Settings()
    monkeypatch.delenv("SMART_ALERTS_PUSH_FROM")
    assert Settings().smart_alerts_push_from is None


class TestQuietStart:
    async def test_quiet_scan_keeps_alerts_in_the_centre_and_pushes_one_critical(
        self,
        client: AsyncClient,
        shop: dict,  # noqa: F811
        db: AsyncSession,
        system_db: AsyncSession,
        settings: Any,
    ) -> None:
        lifecycle = AlertLifecycle(db, NotificationService(db), quiet_until=_LATER)
        await lifecycle.sync(NotificationKind.PAYOUT_OVERDUE, [_payout()])
        await lifecycle.sync(NotificationKind.COURIER_STATUS_STUCK, [_stuck(3)])
        await db.commit()
        tokens = await _give_push_tokens(system_db, shop)

        push = MockPushTransport()
        states = await _dispatch_all(system_db, settings, push, shop)

        # The overdue payout (CRITICAL) is the shop's one push; the stuck
        # parcels (WARNING) wait in the centre.
        assert [m.token for m in push.sent] == [tokens[shop["user_id"]]]
        assert states["PAYOUT_OVERDUE"] == [str(DeliveryState.SENT)]
        assert states["COURIER_STATUS_STUCK"] == [str(DeliveryState.SUPPRESSED)]
        assert QUIET_START_REASON in await _reasons(system_db, shop)
        assert {item["kind"] for item in await _list(client, shop)} == {
            "PAYOUT_OVERDUE",
            "COURIER_STATUS_STUCK",
        }
        for alert in await _alerts(db):
            assert alert.payload["quiet_start"] is True

    async def test_a_second_critical_alert_in_quiet_mode_is_not_pushed(
        self,
        client: AsyncClient,
        shop: dict,  # noqa: F811
        db: AsyncSession,
        system_db: AsyncSession,
        settings: Any,
    ) -> None:
        from app.couriers.models import CourierAccount, CourierAccountStatus
        from app.notifications.smart import SmartAlerts

        db.add(
            CourierAccount(
                provider="steadfast",
                status=str(CourierAccountStatus.NEEDS_RECONNECT),
                masked_identifier="****0001",
            )
        )
        await db.commit()
        smart = SmartAlerts(db, quiet_until=_LATER)
        await smart.lifecycle.sync(NotificationKind.PAYOUT_OVERDUE, [_payout()])
        await smart.run()
        await db.commit()
        await _give_push_tokens(system_db, shop)

        push = MockPushTransport()
        states = await _dispatch_all(system_db, settings, push, shop)

        assert len(push.sent) == 1
        sent = [kind for kind, s in states.items() if str(DeliveryState.SENT) in s]
        assert sent == ["PAYOUT_OVERDUE"]
        assert states["COURIER_ACCOUNT_PROBLEM"] == [str(DeliveryState.SUPPRESSED)]

    async def test_no_device_does_not_use_up_the_shops_one_push(
        self,
        client: AsyncClient,
        shop: dict,  # noqa: F811
        db: AsyncSession,
        system_db: AsyncSession,
        settings: Any,
    ) -> None:
        lifecycle = AlertLifecycle(db, NotificationService(db), quiet_until=_LATER)
        await lifecycle.sync(NotificationKind.PAYOUT_OVERDUE, [_payout()])
        await db.commit()
        push = MockPushTransport()
        assert (await _dispatch_all(system_db, settings, push, shop))["PAYOUT_OVERDUE"] == [
            str(DeliveryState.SUPPRESSED)
        ]

        # The device registers later; an event-raised reconnect alert is
        # outside quiet mode and pushes as usual.
        await _give_push_tokens(system_db, shop)
        await courier_account_status_changed(
            db, account_id=uuid.uuid4(), provider="pathao", needs_reconnect=True
        )
        await db.commit()
        [event] = await _alerts(db, NotificationKind.COURIER_ACCOUNT_PROBLEM)
        assert "quiet_start" not in event.payload
        notification = await system_db.get(Notification, event.id)
        deliveries = await _dispatcher(system_db, settings, push).dispatch(notification)
        assert [d.state for d in deliveries] == [str(DeliveryState.SENT)]

    async def test_after_the_moment_alerts_push_as_before(
        self,
        client: AsyncClient,
        shop: dict,  # noqa: F811
        db: AsyncSession,
        system_db: AsyncSession,
        settings: Any,
    ) -> None:
        lifecycle = AlertLifecycle(db, NotificationService(db), quiet_until=_EARLIER)
        await lifecycle.sync(NotificationKind.COURIER_STATUS_STUCK, [_stuck(3)])
        await db.commit()
        await _give_push_tokens(system_db, shop)

        push = MockPushTransport()
        states = await _dispatch_all(system_db, settings, push, shop)
        assert states["COURIER_STATUS_STUCK"] == [str(DeliveryState.SENT)]
        [alert] = await _alerts(db)
        assert "quiet_start" not in alert.payload

    async def test_the_daily_scan_reads_the_setting(
        self,
        client: AsyncClient,
        shop: dict,  # noqa: F811
        db: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.notifications.alerts import AlertService
        from app.reconciliation.models import CaseKind
        from tests.test_smart_alerts import _case

        db.add(_case(CaseKind.MISSING_COD, 50_000))
        await db.commit()
        monkeypatch.setattr(get_settings(), "smart_alerts_push_from", _LATER)
        assert await AlertService(db).raise_daily_alerts() == 1
        await db.commit()
        [alert] = await _alerts(db, NotificationKind.RECONCILIATION_DISCREPANCY)
        assert alert.payload["quiet_start"] is True
