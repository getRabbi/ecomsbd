"""Billing: verification, replay, dunning and reconciliation.

Master spec sections 90–93. The tests are grouped by the thing that must not be
allowed to happen:

*   a purchase that was never verified granting a plan;
*   one payment granting two entitlements;
*   a replayed webhook buying a second month;
*   a lapsed subscription taking a seller's own history away.

Everything runs against fixture transports (``tests/conftest_billing.py``): no
network call is made, and none is needed, because the only thing this repository
does not implement is the HTTP call itself.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.models import (
    BillingProviderKind,
    BillingTransaction,
    BillingWebhookEvent,
    DistributionChannel,
    PlayPurchaseToken,
    SubscriptionEvent,
    TransactionState,
    hash_provider_token,
)
from app.billing.providers.base import ProviderBlocker, VerificationResult
from app.billing.providers.registry import build_registry, resolve_channel
from app.billing.service import BillingService
from app.common.audit import AuditAction, AuditLog
from app.core.errors import BillingVerificationError
from app.entitlements.catalog import PlanCode
from app.entitlements.models import Subscription, SubscriptionStatus
from tests.conftest_billing import (
    PLAY_PACKAGE,
    PLAY_PRODUCT_PRO,
    PLAY_PRODUCT_STARTER,
    FakeBkashApi,
    FakePlayApi,
    billing_settings,
    install_registry,
    play_response,
)
from tests.conftest_commerce import signed_in_shop
from tests.test_auth_flow import auth_header

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _service(
    db: AsyncSession, settings: Any, *, play_api: FakePlayApi | None = None, **kwargs: Any
) -> BillingService:
    configured = billing_settings(settings, **kwargs)
    return BillingService(
        db,
        settings=configured,
        registry=build_registry(configured, play_api=play_api),
    )


async def _tenant(system_db: AsyncSession, name: str = "Billing Shop") -> uuid.UUID:
    """A tenant row created directly, for service-level tests."""
    from app.tenants.models import Tenant

    tenant = Tenant(name=name, business_category="CLOTHING")
    system_db.add(tenant)
    await system_db.flush()
    return tenant.id


def _scoped(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    from dataclasses import replace

    from app.core.context import current_context, set_context

    set_context(replace(current_context(), tenant_id=tenant_id))


# --------------------------------------------------------------------------- #
# Provider availability — the honest "not configured" state
# --------------------------------------------------------------------------- #


class TestProviderAvailability:
    def test_play_is_unavailable_without_configuration(self, settings) -> None:
        """The shipped default must refuse, not pretend."""
        registry = build_registry(settings)
        state = registry.availability(BillingProviderKind.PLAY)
        assert state.available is False
        assert state.blocker is ProviderBlocker.PLAY_BILLING_EXTERNAL_CONFIGURATION_REQUIRED

    def test_play_reports_the_package_id_decision_when_the_scaffold_default_is_set(
        self, settings
    ) -> None:
        """com.example is the undecided placeholder, and Play rejects it."""
        registry = build_registry(
            settings.model_copy(update={"play_package_name": "com.example.ecomsbd"})
        )
        state = registry.availability(BillingProviderKind.PLAY)
        assert state.blocker is ProviderBlocker.PACKAGE_ID_DECISION_REQUIRED

    def test_bkash_is_unavailable_without_a_merchant_contract(self, settings) -> None:
        registry = build_registry(settings)
        state = registry.availability(BillingProviderKind.BKASH_WEB)
        assert state.blocker is ProviderBlocker.BKASH_MERCHANT_SETUP_REQUIRED

    def test_configured_but_untransported_is_a_distinct_state(self, settings) -> None:
        """Credentials present, no client wired: that is not 'ready'."""
        registry = build_registry(billing_settings(settings))
        assert (
            registry.availability(BillingProviderKind.PLAY).blocker
            is ProviderBlocker.TRANSPORT_NOT_IMPLEMENTED
        )

    def test_manual_provider_is_never_offerable(self, settings) -> None:
        """Section 92: support-controlled only."""
        state = build_registry(settings).availability(BillingProviderKind.MANUAL_ADMIN)
        assert state.available is True
        assert state.allowed_in_channel is False


class TestDistributionChannel:
    def test_a_play_build_may_not_offer_an_external_payment_link(self, settings) -> None:
        """Master spec section 27.1."""
        registry = build_registry(billing_settings(settings), bkash_api=FakeBkashApi())
        policy = registry.policy(DistributionChannel.PLAY)
        assert policy.allows_external_payment_cta is False
        offered = {state.provider for state in policy.purchasable}
        assert BillingProviderKind.BKASH_WEB not in offered

    def test_a_web_build_may(self, settings) -> None:
        registry = build_registry(billing_settings(settings), bkash_api=FakeBkashApi())
        policy = registry.policy(DistributionChannel.WEB)
        assert policy.allows_external_payment_cta is True
        assert BillingProviderKind.BKASH_WEB in {s.provider for s in policy.purchasable}

    def test_a_client_may_narrow_the_channel_but_never_widen_it(self, settings) -> None:
        """A build claiming to be Play is believed; one claiming to be web is not."""
        play_settings = settings.model_copy(update={"distribution_channel": "PLAY"})
        assert resolve_channel("WEB", play_settings) is DistributionChannel.PLAY
        assert resolve_channel("DIRECT", play_settings) is DistributionChannel.PLAY

        web_settings = settings.model_copy(update={"distribution_channel": "WEB"})
        assert resolve_channel("PLAY", web_settings) is DistributionChannel.PLAY
        assert resolve_channel(None, web_settings) is DistributionChannel.WEB
        assert resolve_channel("nonsense", web_settings) is DistributionChannel.WEB


# --------------------------------------------------------------------------- #
# Play verification
# --------------------------------------------------------------------------- #


class TestPlayVerification:
    async def test_a_verified_purchase_grants_the_mapped_plan(
        self, system_db: AsyncSession, settings
    ) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        service = await _service(system_db, settings, play_api=FakePlayApi())

        outcome = await service.verify_play_purchase(
            tenant_id=tenant_id,
            user_id=None,
            purchase_token="token-aaa",
            product_id=PLAY_PRODUCT_STARTER,
            package_name=PLAY_PACKAGE,
        )
        assert outcome.granted is True
        assert outcome.subscription is not None
        assert outcome.subscription.plan_code == str(PlanCode.STARTER)
        assert outcome.subscription.status == str(SubscriptionStatus.ACTIVE)
        assert outcome.subscription.verified_at is not None

    async def test_the_raw_purchase_token_is_never_stored(
        self, system_db: AsyncSession, settings
    ) -> None:
        """A purchase token is a bearer credential (section 33)."""
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        service = await _service(system_db, settings, play_api=FakePlayApi())
        await service.verify_play_purchase(
            tenant_id=tenant_id,
            user_id=None,
            purchase_token="super-secret-token",
            product_id=PLAY_PRODUCT_STARTER,
        )
        rows = (await system_db.execute(sa.select(PlayPurchaseToken))).scalars().all()
        assert len(rows) == 1
        assert rows[0].token_hash == hash_provider_token("super-secret-token")
        assert "super-secret-token" not in json.dumps(rows[0].metadata_json)

    async def test_an_unknown_product_is_refused_without_calling_the_provider(
        self, system_db: AsyncSession, settings
    ) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        api = FakePlayApi()
        service = await _service(system_db, settings, play_api=api)

        with pytest.raises(BillingVerificationError) as exc:
            await service.verify_play_purchase(
                tenant_id=tenant_id,
                user_id=None,
                purchase_token="token-bbb",
                product_id="some.other.app.product",
            )
        assert exc.value.details is not None
        assert exc.value.details["verification_result"] == str(VerificationResult.UNKNOWN_PRODUCT)
        assert api.calls == [], "an unknown product must not reach the provider"

    async def test_a_purchase_from_another_package_is_refused(
        self, system_db: AsyncSession, settings
    ) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        service = await _service(system_db, settings, play_api=FakePlayApi())

        with pytest.raises(BillingVerificationError) as exc:
            await service.verify_play_purchase(
                tenant_id=tenant_id,
                user_id=None,
                purchase_token="token-ccc",
                product_id=PLAY_PRODUCT_STARTER,
                package_name="com.someone.else",
            )
        assert exc.value.details["verification_result"] == str(VerificationResult.WRONG_PACKAGE)

    async def test_a_purchase_the_provider_rejects_grants_nothing(
        self, system_db: AsyncSession, settings
    ) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        api = FakePlayApi(default=play_response(state="SUBSCRIPTION_STATE_EXPIRED"))
        service = await _service(system_db, settings, play_api=api)

        with pytest.raises(BillingVerificationError):
            await service.verify_play_purchase(
                tenant_id=tenant_id,
                user_id=None,
                purchase_token="token-ddd",
                product_id=PLAY_PRODUCT_STARTER,
            )
        subscription = await service.current_subscription(tenant_id)
        assert subscription is None or subscription.plan_code == str(PlanCode.FREE)

    async def test_an_unconfigured_provider_refuses_rather_than_granting(
        self, system_db: AsyncSession, settings
    ) -> None:
        """The shipped default: no service account, so no grant is possible."""
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        service = BillingService(system_db, settings=settings, registry=build_registry(settings))
        with pytest.raises(BillingVerificationError) as exc:
            await service.verify_play_purchase(
                tenant_id=tenant_id,
                user_id=None,
                purchase_token="token-eee",
                product_id=PLAY_PRODUCT_STARTER,
            )
        assert exc.value.details["verification_result"] == str(VerificationResult.NOT_CONFIGURED)

    async def test_a_provider_outage_never_revokes_existing_access(
        self, system_db: AsyncSession, settings
    ) -> None:
        """UNAVAILABLE is not REJECTED. Google having a bad minute is not a refund."""
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        api = FakePlayApi()
        service = await _service(system_db, settings, play_api=api)
        await service.verify_play_purchase(
            tenant_id=tenant_id,
            user_id=None,
            purchase_token="token-fff",
            product_id=PLAY_PRODUCT_STARTER,
        )

        api.raises = TimeoutError("play unreachable")
        outcome = await service.verify_play_purchase(
            tenant_id=tenant_id,
            user_id=None,
            purchase_token="token-fff",
            product_id=PLAY_PRODUCT_STARTER,
        )
        assert outcome.granted is False
        assert outcome.result is VerificationResult.UNAVAILABLE
        assert outcome.subscription is not None
        assert outcome.subscription.plan_code == str(PlanCode.STARTER)

    async def test_acknowledgement_happens_only_when_the_provider_asks(
        self, system_db: AsyncSession, settings
    ) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        api = FakePlayApi(default=play_response(acknowledged=False))
        service = await _service(system_db, settings, play_api=api)
        await service.verify_play_purchase(
            tenant_id=tenant_id,
            user_id=None,
            purchase_token="token-ack",
            product_id=PLAY_PRODUCT_STARTER,
        )
        assert api.acknowledged == ["token-ack"]

        api.acknowledged.clear()
        api.default = play_response(acknowledged=True)
        await service.verify_play_purchase(
            tenant_id=tenant_id,
            user_id=None,
            purchase_token="token-ack2",
            product_id=PLAY_PRODUCT_STARTER,
        )
        assert api.acknowledged == []


class TestPlayReplayProtection:
    async def test_the_same_token_twice_for_the_same_shop_is_idempotent(
        self, system_db: AsyncSession, settings
    ) -> None:
        """A retried request must not look like a failure, nor grant twice."""
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        service = await _service(system_db, settings, play_api=FakePlayApi())

        first = await service.verify_play_purchase(
            tenant_id=tenant_id,
            user_id=None,
            purchase_token="token-repeat",
            product_id=PLAY_PRODUCT_STARTER,
        )
        second = await service.verify_play_purchase(
            tenant_id=tenant_id,
            user_id=None,
            purchase_token="token-repeat",
            product_id=PLAY_PRODUCT_STARTER,
        )
        assert first.granted and second.granted
        assert second.replayed is True

        tokens = (
            (
                await system_db.execute(
                    sa.select(PlayPurchaseToken).where(
                        PlayPurchaseToken.token_hash == hash_provider_token("token-repeat")
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(tokens) == 1
        assert tokens[0].presentation_count == 2

        subscriptions = (
            (
                await system_db.execute(
                    sa.select(Subscription).where(Subscription.tenant_id == tenant_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(subscriptions) == 1
        # The period end came from the provider both times, so it is unchanged.
        assert subscriptions[0].current_period_end == datetime(2027, 1, 1, tzinfo=UTC)

    async def test_another_shop_cannot_spend_the_same_purchase(
        self, system_db: AsyncSession, settings
    ) -> None:
        """The refusal that stops one payment becoming two subscriptions."""
        tenant_a = await _tenant(system_db, "Shop A")
        tenant_b = await _tenant(system_db, "Shop B")
        service = await _service(system_db, settings, play_api=FakePlayApi())

        _scoped(system_db, tenant_a)
        await service.verify_play_purchase(
            tenant_id=tenant_a,
            user_id=None,
            purchase_token="token-shared",
            product_id=PLAY_PRODUCT_PRO,
        )

        _scoped(system_db, tenant_b)
        with pytest.raises(BillingVerificationError) as exc:
            await service.verify_play_purchase(
                tenant_id=tenant_b,
                user_id=None,
                purchase_token="token-shared",
                product_id=PLAY_PRODUCT_PRO,
            )
        assert exc.value.details["verification_result"] == str(VerificationResult.WRONG_TENANT)

        subs_b = (
            (
                await system_db.execute(
                    sa.select(Subscription).where(Subscription.tenant_id == tenant_b)
                )
            )
            .scalars()
            .all()
        )
        assert all(s.plan_code == str(PlanCode.FREE) for s in subs_b)

    async def test_a_cross_tenant_replay_is_audited(
        self, system_db: AsyncSession, settings
    ) -> None:
        tenant_a = await _tenant(system_db, "Shop A")
        tenant_b = await _tenant(system_db, "Shop B")
        service = await _service(system_db, settings, play_api=FakePlayApi())

        _scoped(system_db, tenant_a)
        await service.verify_play_purchase(
            tenant_id=tenant_a,
            user_id=None,
            purchase_token="token-audited",
            product_id=PLAY_PRODUCT_STARTER,
        )
        _scoped(system_db, tenant_b)
        with pytest.raises(BillingVerificationError):
            await service.verify_play_purchase(
                tenant_id=tenant_b,
                user_id=None,
                purchase_token="token-audited",
                product_id=PLAY_PRODUCT_STARTER,
            )
        await system_db.flush()
        entries = (
            (
                await system_db.execute(
                    sa.select(AuditLog).where(
                        AuditLog.action == str(AuditAction.BILLING_REPLAY_BLOCKED),
                        AuditLog.tenant_id == tenant_b,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(entries) == 1
        assert entries[0].context["reason"] == "token_bound_to_other_tenant"

    async def test_a_refusal_is_recorded_as_a_failed_transaction(
        self, system_db: AsyncSession, settings
    ) -> None:
        """A seller must be able to see the attempt, not an empty history."""
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        service = await _service(
            system_db,
            settings,
            play_api=FakePlayApi(default=play_response(state="SUBSCRIPTION_STATE_EXPIRED")),
        )
        with pytest.raises(BillingVerificationError):
            await service.verify_play_purchase(
                tenant_id=tenant_id,
                user_id=None,
                purchase_token="token-refused",
                product_id=PLAY_PRODUCT_STARTER,
            )
        rows = (
            (
                await system_db.execute(
                    sa.select(BillingTransaction).where(BillingTransaction.tenant_id == tenant_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].state == str(TransactionState.FAILED)
        assert rows[0].verification_result == str(VerificationResult.REJECTED)


class TestRestorePurchases:
    async def test_restore_reverifies_and_grants(self, system_db: AsyncSession, settings) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        service = await _service(system_db, settings, play_api=FakePlayApi())

        outcomes = await service.restore_purchases(
            tenant_id=tenant_id,
            user_id=None,
            purchases=[
                {"purchase_token": "restore-1", "product_id": PLAY_PRODUCT_PRO},
                {"purchase_token": "", "product_id": PLAY_PRODUCT_PRO},  # skipped
            ],
        )
        assert len(outcomes) == 1
        assert outcomes[0].granted is True
        subscription = await service.current_subscription(tenant_id)
        assert subscription is not None and subscription.plan_code == str(PlanCode.PRO)

    async def test_restore_cannot_steal_another_shops_purchase(
        self, system_db: AsyncSession, settings
    ) -> None:
        tenant_a = await _tenant(system_db, "Shop A")
        tenant_b = await _tenant(system_db, "Shop B")
        service = await _service(system_db, settings, play_api=FakePlayApi())

        _scoped(system_db, tenant_a)
        await service.verify_play_purchase(
            tenant_id=tenant_a,
            user_id=None,
            purchase_token="restore-shared",
            product_id=PLAY_PRODUCT_PRO,
        )
        _scoped(system_db, tenant_b)
        outcomes = await service.restore_purchases(
            tenant_id=tenant_b,
            user_id=None,
            purchases=[{"purchase_token": "restore-shared", "product_id": PLAY_PRODUCT_PRO}],
        )
        assert outcomes[0].granted is False
        assert outcomes[0].result is VerificationResult.WRONG_TENANT


# --------------------------------------------------------------------------- #
# Webhooks
# --------------------------------------------------------------------------- #


def _rtdn(notification_type: int, token: str, *, message_id: str = "msg-1") -> bytes:
    inner = {
        "eventTimeMillis": "1789000000000",
        "subscriptionNotification": {
            "notificationType": notification_type,
            "purchaseToken": token,
            "subscriptionId": PLAY_PRODUCT_STARTER,
        },
    }
    import base64

    return json.dumps(
        {
            "message": {
                "messageId": message_id,
                "data": base64.b64encode(json.dumps(inner).encode()).decode(),
            }
        }
    ).encode()


def _signed(body: bytes, secret: str) -> dict[str, str]:
    return {
        "x-ecomsbd-signature": hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    }


class TestBillingWebhooks:
    async def test_an_unsigned_webhook_is_refused(self, system_db: AsyncSession, settings) -> None:
        """An unauthenticated 'grant me a subscription' API is not acceptable."""
        service = await _service(system_db, settings, play_api=FakePlayApi())
        result = await service.handle_webhook(
            provider_kind=BillingProviderKind.PLAY,
            headers={},
            body=_rtdn(2, "token-webhook"),
        )
        assert result["accepted"] is False
        rows = (await system_db.execute(sa.select(BillingWebhookEvent))).scalars().all()
        assert rows[0].state == "REJECTED"

    async def test_a_verifier_with_no_secret_configured_refuses(self, settings) -> None:
        registry = build_registry(settings)
        provider = registry.get(BillingProviderKind.PLAY)
        assert provider.verify_webhook({"x-ecomsbd-signature": "anything"}, b"{}") is False

    async def test_a_renewal_extends_the_period_to_the_providers_expiry(
        self, system_db: AsyncSession, settings
    ) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        configured = billing_settings(settings)
        service = BillingService(
            system_db,
            settings=configured,
            registry=build_registry(configured, play_api=FakePlayApi()),
        )
        await service.verify_play_purchase(
            tenant_id=tenant_id,
            user_id=None,
            purchase_token="token-renew",
            product_id=PLAY_PRODUCT_STARTER,
        )

        body = _rtdn(2, "token-renew")
        result = await service.handle_webhook(
            provider_kind=BillingProviderKind.PLAY,
            headers=_signed(body, "fixture-rtdn-secret"),
            body=body,
        )
        assert result["applied"] == 1
        subscription = await service.current_subscription(tenant_id)
        assert subscription is not None
        assert subscription.status == str(SubscriptionStatus.ACTIVE)

    async def test_a_replayed_webhook_does_not_apply_twice(
        self, system_db: AsyncSession, settings
    ) -> None:
        """The rule that stops one renewal buying two months."""
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        configured = billing_settings(settings)
        service = BillingService(
            system_db,
            settings=configured,
            registry=build_registry(configured, play_api=FakePlayApi()),
        )
        await service.verify_play_purchase(
            tenant_id=tenant_id,
            user_id=None,
            purchase_token="token-replay",
            product_id=PLAY_PRODUCT_STARTER,
        )
        before = len((await system_db.execute(sa.select(SubscriptionEvent))).scalars().all())

        body = _rtdn(2, "token-replay", message_id="msg-dup")
        headers = _signed(body, "fixture-rtdn-secret")
        first = await service.handle_webhook(
            provider_kind=BillingProviderKind.PLAY, headers=headers, body=body
        )
        second = await service.handle_webhook(
            provider_kind=BillingProviderKind.PLAY, headers=headers, body=body
        )

        assert first["applied"] == 1
        assert second["applied"] == 0
        assert second["duplicates"] == 1
        assert second["accepted"] is True  # a provider must not retry forever

        records = (await system_db.execute(sa.select(BillingWebhookEvent))).scalars().all()
        assert len(records) == 1
        assert records[0].delivery_count == 2

        after = len((await system_db.execute(sa.select(SubscriptionEvent))).scalars().all())
        assert after - before == 1, "the replay must not write a second transition"

    async def test_a_revocation_refunds_and_removes_access(
        self, system_db: AsyncSession, settings
    ) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        configured = billing_settings(settings)
        service = BillingService(
            system_db,
            settings=configured,
            registry=build_registry(configured, play_api=FakePlayApi()),
        )
        await service.verify_play_purchase(
            tenant_id=tenant_id,
            user_id=None,
            purchase_token="token-revoke",
            product_id=PLAY_PRODUCT_PRO,
        )
        body = _rtdn(12, "token-revoke", message_id="msg-revoke")  # REVOKED
        await service.handle_webhook(
            provider_kind=BillingProviderKind.PLAY,
            headers=_signed(body, "fixture-rtdn-secret"),
            body=body,
        )
        subscription = await service.current_subscription(tenant_id)
        assert subscription is not None
        assert subscription.status == str(SubscriptionStatus.REFUNDED)
        assert subscription.is_current(at=datetime.now(UTC) + timedelta(seconds=1)) is False

    async def test_an_unattributable_event_is_kept_not_discarded(
        self, system_db: AsyncSession, settings
    ) -> None:
        configured = billing_settings(settings)
        service = BillingService(
            system_db,
            settings=configured,
            registry=build_registry(configured, play_api=FakePlayApi()),
        )
        body = _rtdn(2, "token-nobody", message_id="msg-orphan")
        await service.handle_webhook(
            provider_kind=BillingProviderKind.PLAY,
            headers=_signed(body, "fixture-rtdn-secret"),
            body=body,
        )
        rows = (await system_db.execute(sa.select(BillingWebhookEvent))).scalars().all()
        assert len(rows) == 1
        assert rows[0].error == "no subscription matched this event"


# --------------------------------------------------------------------------- #
# Dunning, grace and expiry
# --------------------------------------------------------------------------- #


class TestDunning:
    async def _active(
        self, db: AsyncSession, settings, tenant_id: uuid.UUID
    ) -> tuple[BillingService, Subscription]:
        service = await _service(db, settings, play_api=FakePlayApi())
        await service.verify_play_purchase(
            tenant_id=tenant_id,
            user_id=None,
            purchase_token=f"token-{uuid.uuid4().hex[:8]}",
            product_id=PLAY_PRODUCT_STARTER,
        )
        subscription = await service.current_subscription(tenant_id)
        assert subscription is not None
        return service, subscription

    async def test_a_failed_payment_opens_grace_and_keeps_access(
        self, system_db: AsyncSession, settings
    ) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        service, subscription = await self._active(system_db, settings, tenant_id)

        await service.record_payment_failure(
            subscription=subscription,
            provider=BillingProviderKind.PLAY,
            failure_code="INSUFFICIENT_FUNDS",
        )
        assert subscription.status == str(SubscriptionStatus.GRACE)
        assert subscription.grace_until is not None
        assert subscription.is_current(at=datetime.now(UTC)) is True

    async def test_repeated_failures_do_not_extend_grace(
        self, system_db: AsyncSession, settings
    ) -> None:
        """Three identical failure notifications must not buy nine days."""
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        service, subscription = await self._active(system_db, settings, tenant_id)

        await service.record_payment_failure(
            subscription=subscription, provider=BillingProviderKind.PLAY, failure_code="X"
        )
        first_grace = subscription.grace_until
        await service.record_payment_failure(
            subscription=subscription, provider=BillingProviderKind.PLAY, failure_code="X"
        )
        assert subscription.grace_until == first_grace

    async def test_exhausted_retries_move_to_past_due_and_end_access(
        self, system_db: AsyncSession, settings
    ) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        service, subscription = await self._active(system_db, settings, tenant_id)

        for _ in range(settings.billing_max_payment_retries):
            await service.record_payment_failure(
                subscription=subscription, provider=BillingProviderKind.PLAY, failure_code="X"
            )
        assert subscription.status == str(SubscriptionStatus.PAST_DUE)
        assert subscription.grace_until is None

    async def test_recovery_closes_the_cycle(self, system_db: AsyncSession, settings) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        service, subscription = await self._active(system_db, settings, tenant_id)

        await service.record_payment_failure(
            subscription=subscription, provider=BillingProviderKind.PLAY, failure_code="X"
        )
        await service.record_payment_recovery(
            subscription=subscription, provider=BillingProviderKind.PLAY
        )
        assert subscription.status == str(SubscriptionStatus.ACTIVE)
        assert subscription.grace_until is None

    async def test_expire_lapsed_leaves_a_subscription_inside_grace_alone(
        self, system_db: AsyncSession, settings
    ) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        service, subscription = await self._active(system_db, settings, tenant_id)

        now = datetime.now(UTC)
        subscription.current_period_end = now - timedelta(hours=1)
        subscription.grace_until = now + timedelta(days=1)
        subscription.status = str(SubscriptionStatus.GRACE)
        await system_db.flush()

        expired = await service.expire_lapsed(now=now)
        assert expired == 0
        assert subscription.status == str(SubscriptionStatus.GRACE)

    async def test_expire_lapsed_closes_one_whose_grace_has_run_out(
        self, system_db: AsyncSession, settings
    ) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        service, subscription = await self._active(system_db, settings, tenant_id)

        now = datetime.now(UTC)
        subscription.current_period_end = now - timedelta(days=5)
        subscription.grace_until = now - timedelta(days=1)
        subscription.status = str(SubscriptionStatus.GRACE)
        await system_db.flush()

        assert await service.expire_lapsed(now=now) == 1
        assert subscription.status == str(SubscriptionStatus.EXPIRED)

    async def test_a_cancelled_subscription_ends_as_cancelled_not_expired(
        self, system_db: AsyncSession, settings
    ) -> None:
        """The distinction support needs: did they leave, or did payment fail?"""
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        service, subscription = await self._active(system_db, settings, tenant_id)

        await service.cancel_subscription(tenant_id=tenant_id, reason="too expensive")
        assert subscription.status == str(SubscriptionStatus.CANCEL_AT_PERIOD_END)
        assert subscription.is_current(at=datetime.now(UTC)) is True

        subscription.current_period_end = datetime.now(UTC) - timedelta(minutes=1)
        await system_db.flush()
        await service.expire_lapsed()
        assert subscription.status == str(SubscriptionStatus.CANCELLED)


class TestReconciliation:
    async def test_provider_active_beats_local_inactive(
        self, system_db: AsyncSession, settings
    ) -> None:
        """Section 90's missed-notification safety net."""
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        bkash = FakeBkashApi(
            query_response={
                "agreementStatus": "Active",
                "plan": "pro",
                "valid_until": "2027-06-01T00:00:00Z",
            }
        )
        configured = billing_settings(settings)
        service = BillingService(
            system_db,
            settings=configured,
            registry=build_registry(configured, bkash_api=bkash),
        )
        subscription = Subscription(
            tenant_id=tenant_id,
            plan_code=str(PlanCode.PRO),
            status=str(SubscriptionStatus.EXPIRED),
            source=str(BillingProviderKind.BKASH_WEB),
            provider_reference="AGR-1",
            current_period_end=datetime.now(UTC) - timedelta(days=2),
        )
        system_db.add(subscription)
        await system_db.flush()

        assert await service.reconcile_subscription(subscription) == "activated"
        assert subscription.status == str(SubscriptionStatus.ACTIVE)
        assert subscription.current_period_end == datetime(2027, 6, 1, tzinfo=UTC)

    async def test_provider_inactive_expires_a_locally_active_subscription(
        self, system_db: AsyncSession, settings
    ) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        bkash = FakeBkashApi(query_response={"agreementStatus": "Cancelled"})
        configured = billing_settings(settings)
        service = BillingService(
            system_db,
            settings=configured,
            registry=build_registry(configured, bkash_api=bkash),
        )
        subscription = Subscription(
            tenant_id=tenant_id,
            plan_code=str(PlanCode.PRO),
            status=str(SubscriptionStatus.ACTIVE),
            source=str(BillingProviderKind.BKASH_WEB),
            provider_reference="AGR-2",
            current_period_end=datetime.now(UTC) + timedelta(days=20),
        )
        system_db.add(subscription)
        await system_db.flush()

        assert await service.reconcile_subscription(subscription) == "expired"
        assert subscription.status == str(SubscriptionStatus.EXPIRED)

    async def test_an_unknown_provider_state_changes_nothing(
        self, system_db: AsyncSession, settings
    ) -> None:
        """A manual grant must not be expired because there is nobody to ask."""
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        service = await _service(system_db, settings)
        subscription = await service.grant_manual_subscription(
            tenant_id=tenant_id,
            plan=PlanCode.PRO,
            days=30,
            reason="pilot seller",
            actor_id=None,
        )
        assert await service.reconcile_subscription(subscription) == "unknown"
        assert subscription.status == str(SubscriptionStatus.ACTIVE)


class TestManualGrants:
    async def test_a_grant_requires_a_reason_and_records_the_actor(
        self, system_db: AsyncSession, settings
    ) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        service = await _service(system_db, settings)
        actor = uuid.uuid4()

        subscription = await service.grant_manual_subscription(
            tenant_id=tenant_id,
            plan=PlanCode.STARTER,
            days=14,
            reason="apology for the payout import bug",
            actor_id=actor,
            actor_label="support:rh",
        )
        assert subscription.granted_by_user_id == actor
        assert subscription.grant_reason.startswith("apology")
        assert subscription.cancel_at_period_end is True  # credit never auto-renews

        with pytest.raises(ValueError, match="reason"):
            await service.grant_manual_subscription(
                tenant_id=tenant_id, plan=PlanCode.PRO, days=1, reason="  ", actor_id=actor
            )

    async def test_a_grant_extends_rather_than_shortens_a_paid_period(
        self, system_db: AsyncSession, settings
    ) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(system_db, tenant_id)
        service = await _service(system_db, settings, play_api=FakePlayApi())
        await service.verify_play_purchase(
            tenant_id=tenant_id,
            user_id=None,
            purchase_token="token-credit",
            product_id=PLAY_PRODUCT_PRO,
        )
        paid_until = (await service.current_subscription(tenant_id)).current_period_end

        subscription = await service.grant_manual_subscription(
            tenant_id=tenant_id,
            plan=PlanCode.PRO,
            days=7,
            reason="goodwill",
            actor_id=None,
        )
        assert subscription.current_period_end == paid_until + timedelta(days=7)

    async def test_the_manual_provider_has_no_checkout(self, settings) -> None:
        """Section 92: it must not be reachable as self-service billing."""
        from app.billing.providers.base import ProviderNotConfiguredError

        provider = build_registry(settings).get(BillingProviderKind.MANUAL_ADMIN)
        with pytest.raises(ProviderNotConfiguredError):
            await provider.create_checkout(tenant_id=uuid.uuid4(), plan=PlanCode.PRO, reference="r")


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #


class TestBillingApi:
    async def test_channel_endpoint_drives_the_client_cta(
        self, client: AsyncClient, unique_phone: str, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        install_registry(monkeypatch, billing_settings(settings), bkash_api=FakeBkashApi())

        response = await client.get(
            "/v1/billing/channel",
            headers={**auth_header(session), "X-Distribution-Channel": "PLAY"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["channel"] == "PLAY"
        assert body["allows_external_payment_link"] is False
        offered = {p["provider"] for p in body["providers"] if p["allowed_in_channel"]}
        assert "bkash_web" not in offered

    async def test_channel_never_leaks_operator_detail_to_a_seller(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """'PLAY_SERVICE_ACCOUNT_JSON is missing' is not seller-facing copy."""
        session = await signed_in_shop(client, unique_phone)
        response = await client.get("/v1/billing/channel", headers=auth_header(session))
        assert all(p["detail"] is None for p in response.json()["providers"])

    async def test_a_web_checkout_is_refused_in_a_play_build(
        self, client: AsyncClient, unique_phone: str, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The server enforces the policy the UI was told about."""
        session = await signed_in_shop(client, unique_phone)
        install_registry(monkeypatch, billing_settings(settings), bkash_api=FakeBkashApi())

        response = await client.post(
            "/v1/billing/web/checkout",
            json={"plan": "starter", "provider": "bkash_web"},
            headers={**auth_header(session), "X-Distribution-Channel": "PLAY"},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"

    async def test_the_manual_provider_cannot_be_named_from_a_seller_route(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        response = await client.post(
            "/v1/billing/web/checkout",
            json={"plan": "pro", "provider": "manual_admin"},
            headers=auth_header(session),
        )
        assert response.status_code == 422

    async def test_play_verify_refuses_when_the_provider_is_unconfigured(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """The shipped default. No credentials, so no grant."""
        session = await signed_in_shop(client, unique_phone)
        response = await client.post(
            "/v1/billing/play/verify",
            json={"purchase_token": "whatever-token", "product_id": "ecomsbd_pro_monthly"},
            headers=auth_header(session),
        )
        assert response.status_code == 402
        assert response.json()["code"] == "BILLING_VERIFICATION_FAILED"

    async def test_play_verify_grants_with_a_configured_provider(
        self, client: AsyncClient, unique_phone: str, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        install_registry(monkeypatch, billing_settings(settings), play_api=FakePlayApi())

        response = await client.post(
            "/v1/billing/play/verify",
            json={
                "purchase_token": "api-token-1",
                "product_id": PLAY_PRODUCT_PRO,
                "package_name": PLAY_PACKAGE,
            },
            headers=auth_header(session),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["granted"] is True
        assert body["subscription"]["plan"] == "pro"

        entitlements = await client.get("/v1/billing/entitlements", headers=auth_header(session))
        assert entitlements.json()["plan"] == "pro"
        assert entitlements.json()["entitlements"]["reconciliation"] is True

    async def test_the_subscription_response_never_carries_a_provider_reference(
        self, client: AsyncClient, unique_phone: str, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        install_registry(monkeypatch, billing_settings(settings), play_api=FakePlayApi())
        await client.post(
            "/v1/billing/play/verify",
            json={"purchase_token": "api-token-2", "product_id": PLAY_PRODUCT_STARTER},
            headers=auth_header(session),
        )
        body = (await client.get("/v1/billing/subscription", headers=auth_header(session))).json()
        assert len(body["provider_reference_suffix"]) == 4
        assert "provider_reference" not in body

    async def test_history_shows_refusals_too(
        self, client: AsyncClient, unique_phone: str, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        install_registry(
            monkeypatch,
            billing_settings(settings),
            play_api=FakePlayApi(default=play_response(state="SUBSCRIPTION_STATE_EXPIRED")),
        )
        await client.post(
            "/v1/billing/play/verify",
            json={"purchase_token": "api-token-3", "product_id": PLAY_PRODUCT_STARTER},
            headers=auth_header(session),
        )
        history = await client.get("/v1/billing/history", headers=auth_header(session))
        assert history.status_code == 200
        rows = history.json()
        assert rows and rows[0]["state"] == "FAILED"
        assert rows[0]["verification_result"] == "REJECTED"

    async def test_an_unknown_webhook_provider_is_a_404_not_a_500(
        self, client: AsyncClient
    ) -> None:
        response = await client.post("/v1/billing/webhooks/stripe", json={})
        assert response.status_code == 404

    async def test_a_webhook_with_no_signature_is_401(self, client: AsyncClient) -> None:
        response = await client.post("/v1/billing/webhooks/play", json={"anything": True})
        assert response.status_code == 401

    async def test_billing_endpoints_require_authentication(self, client: AsyncClient) -> None:
        for path in ("/v1/billing/subscription", "/v1/billing/usage", "/v1/billing/channel"):
            assert (await client.get(path)).status_code == 401


class TestBillingTenantIsolation:
    async def test_one_shop_cannot_see_another_shops_billing_history(
        self, client: AsyncClient, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_registry(monkeypatch, billing_settings(settings), play_api=FakePlayApi())
        first = await signed_in_shop(client, "01711000001", shop_name="Shop A")
        second = await signed_in_shop(client, "01711000002", shop_name="Shop B")

        await client.post(
            "/v1/billing/play/verify",
            json={"purchase_token": "iso-token", "product_id": PLAY_PRODUCT_PRO},
            headers=auth_header(first),
        )
        rows = (await client.get("/v1/billing/history", headers=auth_header(second))).json()
        assert rows == []

        entitlements = (
            await client.get("/v1/billing/entitlements", headers=auth_header(second))
        ).json()
        assert entitlements["plan"] == "free"
