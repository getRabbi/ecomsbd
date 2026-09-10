"""Notification transports, delivery and alert fatigue.

Master spec sections 94 and 95, and the Phase F brief's sections 33–35.

The claim under test is section 94's: **push is a second copy, never the only
one.** So the tests check that a missing transport never loses information, that
a configured one is idempotent, and that section 95's fatigue rules suppress
loudly enough to be visible in a support query.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest_commerce import signed_in_shop
from tests.test_auth_flow import auth_header

from app.core.clock import utc_now
from app.notifications.delivery import (
    MAX_PUSHES_PER_TENANT_HOUR,
    Channel,
    DeliveryState,
    NotificationDelivery,
    NotificationDispatcher,
)
from app.notifications.models import Notification, NotificationKind, Severity
from app.notifications.transport import (
    DeliveryOutcome,
    DisabledPushTransport,
    DisabledSmsTransport,
    FcmPushTransport,
    MockPushTransport,
    MockSmsTransport,
    PushMessage,
    SmsGatewayTransport,
    SmsMessage,
    build_push_transport,
    build_sms_transport,
    count_sms_segments,
)

# --------------------------------------------------------------------------- #
# Segment counting
# --------------------------------------------------------------------------- #


class TestSmsSegments:
    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ("", 0),
            ("Order confirmed", 1),
            ("a" * 160, 1),
            ("a" * 161, 2),
            ("a" * 306, 2),
            ("a" * 307, 3),
        ],
    )
    def test_gsm_bodies(self, body: str, expected: int) -> None:
        assert count_sms_segments(body) == expected

    def test_one_bangla_character_makes_the_whole_message_ucs2(self) -> None:
        """The reason the templates are written in Banglish."""
        banglish = "Apnar order confirm hoyeche. Dhonnobad!"
        bangla = "আপনার অর্ডার কনফার্ম হয়েছে। ধন্যবাদ!"
        assert count_sms_segments(banglish) == 1
        assert count_sms_segments(bangla) == 1  # short enough for one UCS-2 part

        long_banglish = "Apnar order confirm hoyeche. " * 4
        assert len(long_banglish) < 160
        assert count_sms_segments(long_banglish) == 1
        assert count_sms_segments(long_banglish + "ধ") > 1

    def test_extended_gsm_characters_cost_two(self) -> None:
        assert count_sms_segments("a" * 159 + "{") == 2


# --------------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------------- #


class TestTransports:
    async def test_the_shipped_default_reports_not_configured(self, settings) -> None:
        """It records the attempt and sends nothing. It does not claim success."""
        push = build_push_transport(settings)
        assert isinstance(push, DisabledPushTransport)
        result = await push.send(
            PushMessage(token="x" * 40, title="Money arrived", body="৳8,950 settled")
        )
        assert result.outcome is DeliveryOutcome.NOT_CONFIGURED
        assert result.delivered is False
        assert "FCM_CREDENTIALS_REQUIRED" in (result.detail or "")

        sms = build_sms_transport(settings)
        assert isinstance(sms, DisabledSmsTransport)
        sms_result = await sms.send(SmsMessage(phone_e164="+8801712345678", body="Hi"))
        assert "SMS_PROVIDER_REQUIRED" in (sms_result.detail or "")

    async def test_fcm_validates_the_payload_even_when_unconfigured(self, settings) -> None:
        """A malformed payload is our bug and must fail the same way regardless."""
        transport = FcmPushTransport(settings)
        assert transport.is_configured is False

        bad_token = await transport.send(PushMessage(token="short", title="Hi", body="x"))
        assert bad_token.outcome is DeliveryOutcome.REJECTED
        assert "token" in (bad_token.detail or "")

        no_title = await transport.send(PushMessage(token="x" * 40, title="  ", body="x"))
        assert no_title.outcome is DeliveryOutcome.REJECTED

        long_title = await transport.send(PushMessage(token="x" * 40, title="t" * 200, body="x"))
        assert long_title.outcome is DeliveryOutcome.REJECTED

        valid = await transport.send(PushMessage(token="x" * 40, title="Hi", body="x"))
        assert valid.outcome is DeliveryOutcome.NOT_CONFIGURED

    async def test_the_sms_gateway_refuses_an_invalid_number(self, settings) -> None:
        transport = SmsGatewayTransport(settings)
        result = await transport.send(SmsMessage(phone_e164="12345", body="Hi"))
        assert result.outcome is DeliveryOutcome.REJECTED

    async def test_an_absurdly_long_sms_is_treated_as_a_bug(self, settings) -> None:
        """Four segments of alert is a templating bug, not an alert."""
        transport = SmsGatewayTransport(settings)
        result = await transport.send(SmsMessage(phone_e164="+8801712345678", body="a" * 900))
        assert result.outcome is DeliveryOutcome.REJECTED
        assert "segments" in (result.detail or "")

    async def test_the_mock_transport_is_idempotent(self) -> None:
        transport = MockPushTransport()
        message = PushMessage(
            token="x" * 40, title="Hi", body="x", idempotency_key="notification-1"
        )
        first = await transport.send(message)
        second = await transport.send(message)
        assert first.outcome is DeliveryOutcome.SENT
        assert second.outcome is DeliveryOutcome.DUPLICATE
        assert len(transport.sent) == 1

    def test_a_push_message_never_logs_its_token(self) -> None:
        message = PushMessage(token="secret-token-value-that-is-long", title="Hi", body="x")
        logged = message.redacted()
        assert "secret-token-value" not in str(logged)
        assert logged["token_suffix"] == "s-long"

    def test_an_sms_message_never_logs_the_number(self) -> None:
        message = SmsMessage(phone_e164="+8801712345678", body="Hi")
        assert "01712345678" not in str(message.redacted())

    async def test_a_mock_sms_reports_its_own_segment_count(self) -> None:
        transport = MockSmsTransport()
        result = await transport.send(SmsMessage(phone_e164="+8801712345678", body="a" * 200))
        assert result.provider_segments == 2


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


async def _tenant_with_device(
    client: AsyncClient, phone: str, *, plan: str | None = None
) -> dict[str, Any]:
    """A signed-in shop whose device has a push token registered."""
    from app.auth.models import Device
    from app.db.session import system_session

    session = await signed_in_shop(client, phone, shop_name="Push Shop", plan=plan)
    async with system_session("test fixture: register push token") as db:
        device = (
            (
                await db.execute(
                    sa.select(Device).where(Device.user_id == uuid.UUID(session["user_id"]))
                )
            )
            .scalars()
            .first()
        )
        assert device is not None
        device.push_token = "fcm-token-" + "y" * 40
    return session


async def _notify(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    severity: Severity = Severity.WARNING,
    kind: NotificationKind = NotificationKind.DELIVERED_BUT_UNPAID,
    dedupe: str | None = None,
) -> Notification:
    from app.core.clock import business_date

    notification = Notification(
        tenant_id=tenant_id,
        kind=str(kind),
        severity=str(severity),
        title="7 parcels delivered but unpaid",
        body="৳8,950 has not arrived from the courier.",
        dedupe_key=dedupe or uuid.uuid4().hex,
        business_date=business_date(),
        # The model sets no Python-side default for these: the service supplies
        # them, and a test that builds the row directly must too.
        created_at=utc_now(),
    )
    db.add(notification)
    await db.flush()
    return notification


def _dispatcher(db: AsyncSession, settings: Any, push: Any = None, sms: Any = None):
    return NotificationDispatcher(
        db,
        settings=settings,
        push=push or build_push_transport(settings),
        sms=sms or build_sms_transport(settings),
    )


class TestDispatch:
    async def test_a_notification_with_no_transport_is_recorded_not_lost(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession, settings
    ) -> None:
        session = await _tenant_with_device(client, unique_phone)
        tenant_id = uuid.UUID(session["tenant_id"])
        notification = await _notify(system_db, tenant_id)

        deliveries = await _dispatcher(system_db, settings).dispatch(notification)
        assert len(deliveries) == 1
        assert deliveries[0].state == str(DeliveryState.NOT_CONFIGURED)
        # The seller still has it in the centre. That is the point.
        assert notification.read_at is None

    async def test_a_configured_transport_sends_once(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession, settings
    ) -> None:
        session = await _tenant_with_device(client, unique_phone)
        tenant_id = uuid.UUID(session["tenant_id"])
        notification = await _notify(system_db, tenant_id)

        push = MockPushTransport()
        dispatcher = _dispatcher(system_db, settings, push=push)

        first = await dispatcher.dispatch(notification)
        assert first[0].state == str(DeliveryState.SENT)
        assert len(push.sent) == 1

        # Running the dispatcher again must not send a second time.
        second = await dispatcher.dispatch(notification)
        assert second == []
        assert len(push.sent) == 1

    async def test_the_push_carries_a_deep_link(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession, settings
    ) -> None:
        """Section 94: opening it lands on the exact record."""
        session = await _tenant_with_device(client, unique_phone)
        tenant_id = uuid.UUID(session["tenant_id"])
        notification = await _notify(system_db, tenant_id)
        notification.entity_type = "order"
        notification.entity_id = uuid.uuid4()

        push = MockPushTransport()
        await _dispatcher(system_db, settings, push=push).dispatch(notification)
        assert push.sent[0].deep_link == f"ecomsbd://order/{notification.entity_id}"

    async def test_info_is_never_pushed(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession, settings
    ) -> None:
        """Section 95. The Friday summary belongs in the centre."""
        session = await _tenant_with_device(client, unique_phone)
        tenant_id = uuid.UUID(session["tenant_id"])
        notification = await _notify(
            system_db, tenant_id, severity=Severity.INFO, kind=NotificationKind.WEEKLY_SUMMARY
        )

        push = MockPushTransport()
        deliveries = await _dispatcher(system_db, settings, push=push).dispatch(notification)
        assert deliveries[0].state == str(DeliveryState.SUPPRESSED)
        assert "INFO" in (deliveries[0].suppression_reason or "")
        assert push.sent == []

    async def test_a_muted_kind_is_suppressed_visibly(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession, settings
    ) -> None:
        session = await _tenant_with_device(client, unique_phone)
        tenant_id = uuid.UUID(session["tenant_id"])
        push = MockPushTransport()
        dispatcher = _dispatcher(system_db, settings, push=push)

        preference = await dispatcher.preferences(tenant_id)
        preference.muted_kinds = [str(NotificationKind.DELIVERED_BUT_UNPAID)]
        await system_db.flush()

        notification = await _notify(system_db, tenant_id)
        deliveries = await dispatcher.dispatch(notification)
        assert deliveries[0].state == str(DeliveryState.SUPPRESSED)
        assert "muted" in (deliveries[0].suppression_reason or "")
        assert push.sent == []

    async def test_push_off_suppresses_everything(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession, settings
    ) -> None:
        session = await _tenant_with_device(client, unique_phone)
        tenant_id = uuid.UUID(session["tenant_id"])
        dispatcher = _dispatcher(system_db, settings, push=MockPushTransport())

        preference = await dispatcher.preferences(tenant_id)
        preference.push_enabled = False
        await system_db.flush()

        deliveries = await dispatcher.dispatch(await _notify(system_db, tenant_id))
        assert deliveries[0].state == str(DeliveryState.SUPPRESSED)

    async def test_the_hourly_cap_stops_a_runaway_job(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession, settings
    ) -> None:
        """Section 95. The notifications still land in the centre."""
        session = await _tenant_with_device(client, unique_phone)
        tenant_id = uuid.UUID(session["tenant_id"])
        push = MockPushTransport()
        dispatcher = _dispatcher(system_db, settings, push=push)

        for index in range(MAX_PUSHES_PER_TENANT_HOUR + 3):
            notification = await _notify(system_db, tenant_id, dedupe=f"burst-{index}")
            await dispatcher.dispatch(notification)

        assert len(push.sent) == MAX_PUSHES_PER_TENANT_HOUR
        suppressed = (
            (
                await system_db.execute(
                    sa.select(NotificationDelivery).where(
                        NotificationDelivery.tenant_id == tenant_id,
                        NotificationDelivery.state == str(DeliveryState.SUPPRESSED),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(suppressed) == 3
        assert "notification centre" in (suppressed[0].suppression_reason or "")

    async def test_a_shop_with_no_push_token_is_recorded_not_skipped(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession, settings
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="No Token Shop")
        tenant_id = uuid.UUID(session["tenant_id"])
        notification = await _notify(system_db, tenant_id)

        deliveries = await _dispatcher(system_db, settings).dispatch(notification)
        assert deliveries[0].state == str(DeliveryState.SUPPRESSED)
        assert "push token" in (deliveries[0].suppression_reason or "")

    async def test_a_rejected_token_is_dropped_from_the_device(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession, settings
    ) -> None:
        """A dead token never comes back; keeping it accrues failures forever."""
        from app.auth.models import Device
        from app.notifications.transport import TransportResult

        session = await _tenant_with_device(client, unique_phone)
        tenant_id = uuid.UUID(session["tenant_id"])

        class RejectingTransport:
            name = "rejecting"
            is_configured = True

            async def send(self, message: PushMessage) -> TransportResult:
                return TransportResult(
                    DeliveryOutcome.REJECTED, self.name, detail="unregistered token"
                )

        notification = await _notify(system_db, tenant_id)
        deliveries = await _dispatcher(system_db, settings, push=RejectingTransport()).dispatch(
            notification
        )
        assert deliveries[0].state == str(DeliveryState.FAILED)

        device = (
            (
                await system_db.execute(
                    sa.select(Device).where(Device.user_id == uuid.UUID(session["user_id"]))
                )
            )
            .scalars()
            .first()
        )
        assert device is not None
        assert device.push_token is None

    async def test_a_transport_that_raises_does_not_break_the_job(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession, settings
    ) -> None:
        session = await _tenant_with_device(client, unique_phone)
        tenant_id = uuid.UUID(session["tenant_id"])

        class ExplodingTransport:
            name = "exploding"
            is_configured = True

            async def send(self, message: PushMessage) -> Any:
                raise TimeoutError("provider unreachable")

        notification = await _notify(system_db, tenant_id)
        deliveries = await _dispatcher(system_db, settings, push=ExplodingTransport()).dispatch(
            notification
        )
        assert deliveries[0].state == str(DeliveryState.RETRYING)
        assert deliveries[0].next_attempt_at is not None
        assert deliveries[0].next_attempt_at > utc_now()

    async def test_the_delivery_row_never_holds_the_token(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession, settings
    ) -> None:
        session = await _tenant_with_device(client, unique_phone)
        tenant_id = uuid.UUID(session["tenant_id"])
        notification = await _notify(system_db, tenant_id)

        deliveries = await _dispatcher(system_db, settings, push=MockPushTransport()).dispatch(
            notification
        )
        row = deliveries[0]
        assert len(row.target_hash) == 64
        assert "fcm-token-" not in row.target_hash


class TestSmsMetering:
    async def test_an_sms_is_metered_before_it_is_sent(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession, settings
    ) -> None:
        """A quota checked after the message has gone is not a quota."""
        from app.entitlements.catalog import Entitlement
        from app.entitlements.service import EntitlementService

        session = await _tenant_with_device(client, unique_phone, plan="pro")
        tenant_id = uuid.UUID(session["tenant_id"])
        notification = await _notify(system_db, tenant_id)

        dispatcher = _dispatcher(system_db, settings, sms=MockSmsTransport())
        delivery = await dispatcher.send_sms(
            tenant_id=tenant_id,
            notification_id=notification.id,
            phone_e164="+8801712345678",
            body="a" * 200,
        )
        assert delivery is not None
        assert delivery.state == str(DeliveryState.SENT)
        assert delivery.estimated_segments == 2
        assert delivery.provider_segments == 2

        usage = await EntitlementService(system_db).usage(tenant_id)
        segments = next(
            view for view in usage if view.entitlement == str(Entitlement.SMS_SEGMENTS_MONTHLY)
        )
        assert segments.used == 2

    async def test_a_free_shop_cannot_send_sms_at_all(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession, settings
    ) -> None:
        from app.core.errors import EntitlementRequiredError

        session = await _tenant_with_device(client, unique_phone)
        tenant_id = uuid.UUID(session["tenant_id"])
        notification = await _notify(system_db, tenant_id)

        with pytest.raises(EntitlementRequiredError):
            await _dispatcher(system_db, settings, sms=MockSmsTransport()).send_sms(
                tenant_id=tenant_id,
                notification_id=notification.id,
                phone_e164="+8801712345678",
                body="Hi",
            )


class TestPreferencesApi:
    async def test_the_client_is_told_no_transport_exists(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """Better than a switch that changes nothing."""
        session = await signed_in_shop(client, unique_phone, shop_name="Prefs Shop")
        body = (
            await client.get("/v1/account/notification-preferences", headers=auth_header(session))
        ).json()
        assert body["push_transport_available"] is False
        assert body["sms_transport_available"] is False
        assert body["push_enabled"] is True
        assert body["routine_tracking_push"] is False, "section 95: opt-in"

    async def test_muting_a_kind_is_stored(self, client: AsyncClient, unique_phone: str) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Prefs Shop")
        updated = await client.patch(
            "/v1/account/notification-preferences",
            json={"muted_kinds": ["STALE_IN_TRANSIT", "NOT_A_REAL_KIND"], "sms_enabled": True},
            headers=auth_header(session),
        )
        assert updated.status_code == 200
        body = updated.json()
        assert body["muted_kinds"] == ["STALE_IN_TRANSIT"], "unknown kinds are dropped"
        assert body["sms_enabled"] is True

    async def test_quiet_hours_are_validated(self, client: AsyncClient, unique_phone: str) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Prefs Shop")
        response = await client.patch(
            "/v1/account/notification-preferences",
            json={"quiet_hours_start": 25},
            headers=auth_header(session),
        )
        assert response.status_code == 422


class TestMaintenanceJobs:
    async def test_the_billing_reconciler_leaves_manual_grants_alone(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession
    ) -> None:
        """No provider to ask means nothing changes — never an expiry."""
        from app.entitlements.models import Subscription, SubscriptionStatus
        from app.worker.maintenance import reconcile_billing

        session = await signed_in_shop(client, unique_phone, shop_name="Grant Shop", plan="pro")
        tenant_id = uuid.UUID(session["tenant_id"])

        subscription = (
            (
                await system_db.execute(
                    sa.select(Subscription).where(Subscription.tenant_id == tenant_id)
                )
            )
            .scalars()
            .first()
        )
        assert subscription is not None
        subscription.last_synced_at = utc_now() - timedelta(days=3)
        await system_db.commit()

        counts = await reconcile_billing()
        assert counts["checked"] >= 1
        assert counts["expired"] == 0

        await system_db.refresh(subscription)
        assert subscription.status == str(SubscriptionStatus.ACTIVE)

    async def test_the_dispatcher_job_records_every_pending_notification(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession
    ) -> None:
        from app.worker.maintenance import dispatch_notifications

        session = await _tenant_with_device(client, unique_phone)
        tenant_id = uuid.UUID(session["tenant_id"])
        await _notify(system_db, tenant_id, dedupe="job-test-1")
        await system_db.commit()

        counts = await dispatch_notifications()
        assert counts["dispatched"] >= 1

        rows = (
            (
                await system_db.execute(
                    sa.select(NotificationDelivery).where(
                        NotificationDelivery.tenant_id == tenant_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows
        assert all(row.channel == str(Channel.PUSH) for row in rows)
        # Nothing is configured, so nothing was sent — and that is recorded.
        assert all(row.state == str(DeliveryState.NOT_CONFIGURED) for row in rows)

    async def test_the_dispatcher_job_does_not_re_attempt_forever(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession
    ) -> None:
        """An unread notification must not be tried again every two minutes."""
        from app.worker.maintenance import dispatch_notifications

        session = await _tenant_with_device(client, unique_phone)
        tenant_id = uuid.UUID(session["tenant_id"])
        await _notify(system_db, tenant_id, dedupe="job-test-2")
        await system_db.commit()

        await dispatch_notifications()
        second = await dispatch_notifications()
        assert second["dispatched"] == 0
