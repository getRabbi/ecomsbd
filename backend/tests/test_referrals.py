"""Shop referrals (V4 WP7.4).

What must hold:

*   off unless ``referrals_enabled`` is on and the shop could pay by TrxID;
*   a shop's code is made once; another shop joins with it before its first
    payment — never its own, a shop with the same owner, the shop it referred,
    or a shop that is not active; the same code again is a 200 repeat;
*   when the referred shop's first TrxID payment is approved, both shops get
    30 days through the admin-manual grant, once (the handler may run twice),
    with the unique business keys ``referral:<id>:referred|referrer``;
*   a referrer on a billing-provider subscription is held, not overwritten;
    a store purchase before the first TrxID payment makes the referral void;
    with the flag off nothing is granted and the referral waits.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.billing.models import BillingPaymentSubmission, BillingTransaction
from app.common.audit import AuditAction, AuditLog
from app.common.feature_flags import _DEFAULTS, FeatureFlag, FlagKey, FlagScope
from app.common.outbox import OutboxEvent, OutboxTopic
from app.core.clock import utc_now
from app.core.context import RequestContext, clear_context, set_context
from app.core.errors import ValidationError
from app.entitlements.models import Subscription, SubscriptionStatus
from app.referrals.models import Referral
from app.referrals.service import CODE_LENGTH, new_code, normalise_code
from app.tenants.models import Tenant, TenantStatus
from app.tenants.roles import TenantRole
from tests.conftest_commerce import grant_plan, signed_in_shop
from tests.test_admin import admin_header
from tests.test_auth_flow import auth_header
from tests.test_manual_payments import (  # noqa: F401  (fixture)
    _another_phone,
    _rows,
    _submitted,
    payments_on,
)
from tests.test_team import _member_session

REFERRALS = "/v1/referrals"


@pytest.fixture
async def referrals_on(settings: Any, payments_on: Any) -> AsyncIterator[Any]:  # noqa: F811
    from tests.conftest import _global_flag_on

    async for _ in _global_flag_on(settings, str(FlagKey.REFERRALS_ENABLED)):
        yield payments_on


async def _system(work: Any) -> Any:
    from app.db.session import system_session

    async with system_session("test: referral fixture") as db:
        return await work(db)


async def _code(client: AsyncClient, shop: dict[str, Any]) -> str:
    made = await client.post(f"{REFERRALS}/code", headers=auth_header(shop))
    assert made.status_code in (200, 201), made.text
    return str(made.json()["code"])


async def _apply(client: AsyncClient, shop: dict[str, Any], code: str) -> Any:
    return await client.post(f"{REFERRALS}/apply", json={"code": code}, headers=auth_header(shop))


async def _pair(client: AsyncClient, unique_phone: str) -> tuple[dict, dict, str]:
    """Shop A's code, applied by shop B."""
    referrer = await signed_in_shop(client, unique_phone, shop_name="Referrer Shop")
    referred = await signed_in_shop(client, _another_phone(), shop_name="Joined Shop Nine")
    code = await _code(client, referrer)
    joined = await _apply(client, referred, code)
    assert joined.status_code == 201, joined.text
    return referrer, referred, code


async def _approve(client: AsyncClient, shop: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    submitted = await _submitted(client, shop, **overrides)
    approved = await client.post(
        f"/v1/admin/payments/{submitted['id']}/approve", json={}, headers=admin_header()
    )
    assert approved.status_code == 200, approved.text
    return approved.json()


async def _run_reward_events(tenant_id: uuid.UUID, *, times: int = 1) -> None:
    import app.referrals.jobs  # noqa: F401  (registers the handler, as the worker does)
    from app.db.session import session_scope
    from app.worker.jobs import HANDLERS

    events = await _rows(
        OutboxEvent,
        OutboxEvent.tenant_id == tenant_id,
        OutboxEvent.topic == str(OutboxTopic.REFERRAL_PAYMENT_APPROVED),
    )
    assert events, "the approve wrote no referral event"
    token = set_context(RequestContext(trace_id="test", tenant_id=tenant_id))
    try:
        for _ in range(times):
            for event in events:
                async with session_scope() as db:
                    await HANDLERS[event.topic](db, event)
    finally:
        clear_context(token)


async def _subscription(tenant_id: uuid.UUID) -> Subscription:
    rows = await _rows(Subscription, Subscription.tenant_id == tenant_id)
    return max(rows, key=lambda row: row.created_at)


async def _referral(referred_tenant_id: uuid.UUID) -> Referral:
    (row,) = await _rows(Referral, Referral.referred_tenant_id == referred_tenant_id)
    return row


# ----------------------------------------------------------------- units ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("abcd2345", "ABCD2345"), (" ABCD-2345 ", "ABCD2345"), ("abcd২৩৪৫", "ABCD2345")],
)
def test_a_code_is_normalised(raw: str, expected: str):
    assert normalise_code(raw) == expected


@pytest.mark.parametrize("raw", ["", "ABC", "ABCD23456", "ABCD0123", "ABCDIL23", None])
def test_a_malformed_code_is_refused(raw: str | None):
    with pytest.raises(ValidationError) as caught:
        normalise_code(raw)
    assert caught.value.details["code"] == "REFERRAL_CODE_INVALID"


def test_new_codes_have_no_lookalike_characters():
    for _ in range(200):
        code = new_code()
        assert len(code) == CODE_LENGTH and normalise_code(code) == code
        assert not set(code) & set("01OIL")


def test_the_flag_is_off_by_default_and_the_topic_has_a_handler():
    import app.referrals.jobs  # noqa: F401
    from app.worker.jobs import HANDLERS

    assert _DEFAULTS[FlagKey.REFERRALS_ENABLED] is False
    assert str(OutboxTopic.REFERRAL_PAYMENT_APPROVED) in HANDLERS


# ---------------------------------------------------------- availability ---


async def test_referrals_are_off_by_default_and_say_nothing(client: AsyncClient, unique_phone: str):
    shop = await signed_in_shop(client, unique_phone)
    seen = await client.get(REFERRALS, headers=auth_header(shop))
    assert seen.status_code == 200
    assert seen.json() == {
        "available": False,
        "unavailable_reason": "FEATURE_OFF",
        "credit_days": 30,
        "code": None,
        "referred_by": None,
        "referrals": [],
    }
    refused = await client.post(f"{REFERRALS}/code", headers=auth_header(shop))
    assert refused.status_code == 403
    assert refused.json()["details"]["code"] == "REFERRALS_UNAVAILABLE"


async def test_referrals_follow_the_trx_id_payment_gate(
    client: AsyncClient, unique_phone: str, settings: Any
):
    from tests.conftest import _global_flag_on

    shop = await signed_in_shop(client, unique_phone)
    async for _ in _global_flag_on(settings, str(FlagKey.REFERRALS_ENABLED)):
        seen = (await client.get(REFERRALS, headers=auth_header(shop))).json()
        assert (seen["available"], seen["unavailable_reason"]) == (False, "PAYMENTS_OFF")


async def test_a_play_build_never_gets_referrals(
    client: AsyncClient, unique_phone: str, referrals_on: Any
):
    shop = await signed_in_shop(client, unique_phone)
    play = {**auth_header(shop), "X-Distribution-Channel": "PLAY"}
    seen = (await client.get(REFERRALS, headers=play)).json()
    assert (seen["available"], seen["unavailable_reason"]) == (False, "CHANNEL")
    refused = await client.post(f"{REFERRALS}/apply", json={"code": "ABCD2345"}, headers=play)
    assert refused.status_code == 403


async def test_only_the_owner_sees_and_uses_referrals(
    client: AsyncClient, unique_phone: str, referrals_on: Any
):
    owner = await signed_in_shop(client, unique_phone, plan="pro")
    staff = await _member_session(client, owner, _another_phone(), TenantRole.MANAGER)
    for response in (
        await client.get(REFERRALS, headers=auth_header(staff)),
        await client.post(f"{REFERRALS}/code", headers=auth_header(staff)),
    ):
        assert response.status_code == 403


# ------------------------------------------------------------------ code ---


async def test_a_shop_gets_one_code(client: AsyncClient, unique_phone: str, referrals_on: Any):
    shop = await signed_in_shop(client, unique_phone)
    first = await client.post(f"{REFERRALS}/code", headers=auth_header(shop))
    again = await client.post(f"{REFERRALS}/code", headers=auth_header(shop))
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json() == again.json() == {"code": first.json()["code"], "credit_days": 30}
    seen = (await client.get(REFERRALS, headers=auth_header(shop))).json()
    assert (seen["available"], seen["code"], seen["referrals"]) == (
        True,
        first.json()["code"],
        [],
    )


# ----------------------------------------------------------------- apply ---


async def test_joining_with_a_code_records_a_pending_referral_once(
    client: AsyncClient, unique_phone: str, referrals_on: Any
):
    referrer, referred, code = await _pair(client, unique_phone)
    again = await _apply(client, referred, code.lower())
    assert again.status_code == 200
    body = again.json()
    assert (body["code"], body["state"], body["credit_state"]) == (code, "PENDING", "PENDING")

    mine = (await client.get(REFERRALS, headers=auth_header(referred))).json()
    assert mine["referred_by"]["code"] == code and mine["referrals"] == []
    theirs = await client.get(REFERRALS, headers=auth_header(referrer))
    (row,) = theirs.json()["referrals"]
    assert (row["state"], row["credit_state"]) == ("PENDING", "PENDING")
    # The other shop is never named to the referrer.
    assert "Joined Shop Nine" not in theirs.text and referred["tenant_id"] not in theirs.text

    referral = await _referral(uuid.UUID(referred["tenant_id"]))
    assert referral.tenant_id == uuid.UUID(referrer["tenant_id"])
    audits = await _rows(
        AuditLog,
        AuditLog.tenant_id == uuid.UUID(referred["tenant_id"]),
        AuditLog.action == str(AuditAction.REFERRAL_APPLIED),
    )
    assert len(audits) == 1


async def test_a_code_cannot_be_used_by_the_wrong_shop(
    client: AsyncClient, unique_phone: str, referrals_on: Any
):
    first = await signed_in_shop(client, unique_phone)
    first_code = await _code(client, first)

    async def refused(shop: dict[str, Any], code: str, status: int, reason: str) -> None:
        response = await _apply(client, shop, code)
        assert response.status_code == status, response.text
        assert response.json()["details"]["code"] == reason

    await refused(first, "not-a-code", 422, "REFERRAL_CODE_INVALID")
    unknown = next(c for c in iter(new_code, None) if c != first_code)
    await refused(first, unknown, 422, "REFERRAL_CODE_UNKNOWN")
    await refused(first, first_code, 422, "REFERRAL_OWN_SHOP")

    # A referred shop cannot then refer its referrer back.
    other = await signed_in_shop(client, _another_phone())
    assert (await _apply(client, other, first_code)).status_code == 201
    await refused(first, await _code(client, other), 422, "REFERRAL_CYCLE")

    # One referral per shop.
    third = await signed_in_shop(client, _another_phone())
    await refused(other, await _code(client, third), 409, "REFERRAL_ALREADY_APPLIED")

    # A suspended shop's code works for nobody.
    async def suspend(db: Any) -> None:
        tenant = await db.get(Tenant, uuid.UUID(third["tenant_id"]))
        tenant.status = str(TenantStatus.SUSPENDED)

    late = await signed_in_shop(client, _another_phone())
    third_code = await _code(client, third)
    await _system(suspend)
    await refused(late, third_code, 422, "REFERRAL_CODE_UNKNOWN")

    # The same owner opens a second shop: no referring yourself. (Last:
    # creating it binds this session to the new shop.)
    second = await client.post(
        "/v1/tenants",
        json={"name": "Owner's Second Shop", "business_category": "CLOTHING"},
        headers=auth_header(first),
    )
    assert second.status_code == 201, second.text
    await refused({**first, **second.json()}, first_code, 422, "REFERRAL_SAME_OWNER")


async def test_a_code_is_only_for_a_shop_that_has_not_paid(
    client: AsyncClient, unique_phone: str, referrals_on: Any
):
    referrer = await signed_in_shop(client, unique_phone)
    code = await _code(client, referrer)
    paid = await signed_in_shop(client, _another_phone())
    await _approve(client, paid)
    response = await _apply(client, paid, code)
    assert response.status_code == 409
    assert response.json()["details"]["code"] == "REFERRAL_AFTER_FIRST_PAYMENT"


async def test_wrong_codes_are_rate_limited(
    client: AsyncClient, unique_phone: str, referrals_on: Any
):
    shop = await signed_in_shop(client, unique_phone)
    statuses = [(await _apply(client, shop, new_code())).status_code for _ in range(11)]
    assert statuses[:10] == [422] * 10 and statuses[10] == 429


# ---------------------------------------------------------------- reward ---


async def test_the_first_payment_credits_both_shops_once(
    client: AsyncClient, unique_phone: str, referrals_on: Any
):
    referrer, referred, code = await _pair(client, unique_phone)
    referrer_id, referred_id = uuid.UUID(referrer["tenant_id"]), uuid.UUID(referred["tenant_id"])
    approved = await _approve(client, referred, plan_code="pro", months=1)

    await _run_reward_events(referred_id, times=2)  # a retried event rewards once

    referral = await _referral(referred_id)
    assert (referral.state, referral.referred_credit_state, referral.referrer_credit_state) == (
        "REWARDED",
        "GRANTED",
        "GRANTED",
    )
    assert str(referral.qualifying_submission_id) == approved["id"]

    mine = await _subscription(referred_id)
    assert mine.plan_code == "pro"
    assert mine.current_period_end == datetime.fromisoformat(approved["period_end"]) + timedelta(
        days=30
    )
    # A Free referrer gets the plan the referred shop paid for.
    theirs = await _subscription(referrer_id)
    assert theirs.plan_code == "pro" and theirs.status == "ACTIVE"
    assert (
        timedelta(days=29, hours=23) < theirs.current_period_end - utc_now() <= timedelta(days=30)
    )

    credits = await _rows(
        BillingTransaction,
        BillingTransaction.provider_event_id.like(f"referral:{referral.id}:%"),
    )
    assert sorted((row.provider_event_id, row.kind, row.amount_paisa) for row in credits) == [
        (f"referral:{referral.id}:referred", "CREDIT", 0),
        (f"referral:{referral.id}:referrer", "CREDIT", 0),
    ]
    rewarded = await _rows(AuditLog, AuditLog.action == str(AuditAction.REFERRAL_REWARDED))
    assert {row.tenant_id for row in rewarded if row.entity_id == str(referral.id)} == {
        referrer_id,
        referred_id,
    }

    seen = (await client.get(REFERRALS, headers=auth_header(referrer))).json()
    (row,) = seen["referrals"]
    assert (row["state"], row["credit_state"]) == ("REWARDED", "GRANTED")
    assert row["rewarded_at"] is not None
    joined = (await client.get(REFERRALS, headers=auth_header(referred))).json()["referred_by"]
    assert (joined["code"], joined["state"], joined["credit_state"]) == (
        code,
        "REWARDED",
        "GRANTED",
    )

    # A second payment rewards nothing more.
    await _approve(client, referred)
    await _run_reward_events(referred_id)
    assert (
        len(
            await _rows(
                BillingTransaction,
                BillingTransaction.provider_event_id.like(f"referral:{referral.id}:%"),
            )
        )
        == 2
    )


async def test_a_paying_referrer_keeps_its_plan_and_gains_thirty_days(
    client: AsyncClient, unique_phone: str, referrals_on: Any
):
    referrer, referred, _ = await _pair(client, unique_phone)
    referrer_id = uuid.UUID(referrer["tenant_id"])
    await grant_plan(referrer["tenant_id"], "pro", days=20)
    before = await _subscription(referrer_id)
    await _approve(client, referred, plan_code="starter", months=1)
    await _run_reward_events(uuid.UUID(referred["tenant_id"]))

    after = await _subscription(referrer_id)
    assert after.plan_code == "pro"
    assert after.current_period_end == before.current_period_end + timedelta(days=30)


async def test_a_referrer_on_a_provider_subscription_is_held_not_overwritten(
    client: AsyncClient, unique_phone: str, referrals_on: Any
):
    referrer, referred, _ = await _pair(client, unique_phone)
    referrer_id, referred_id = uuid.UUID(referrer["tenant_id"]), uuid.UUID(referred["tenant_id"])

    async def on_play(db: Any) -> None:
        db.add(
            Subscription(
                tenant_id=referrer_id,
                plan_code="starter",
                status=str(SubscriptionStatus.ACTIVE),
                source="play",
                current_period_start=utc_now(),
                current_period_end=utc_now() + timedelta(days=10),
                verified_at=utc_now(),
            )
        )

    await _system(on_play)
    before = await _subscription(referrer_id)
    await _approve(client, referred)
    await _run_reward_events(referred_id)

    referral = await _referral(referred_id)
    assert (referral.state, referral.referred_credit_state) == ("REWARDED", "GRANTED")
    assert (referral.referrer_credit_state, referral.referrer_hold_reason) == (
        "HELD",
        "PROVIDER_SUBSCRIPTION",
    )
    after = await _subscription(referrer_id)
    assert (after.source, after.current_period_end) == ("play", before.current_period_end)

    held = await client.get(
        "/v1/admin/referrals", params={"referrer_credit": "HELD"}, headers=admin_header()
    )
    assert held.status_code == 200
    assert str(referral.id) in [row["id"] for row in held.json()]
    seen = (await client.get(REFERRALS, headers=auth_header(referrer))).json()
    assert seen["referrals"][0]["credit_state"] == "HELD"


async def test_a_store_purchase_first_makes_the_referral_void(
    client: AsyncClient, unique_phone: str, referrals_on: Any
):
    _, referred, _ = await _pair(client, unique_phone)
    referred_id = uuid.UUID(referred["tenant_id"])

    async def bought_on_play(db: Any) -> None:
        db.add(
            BillingTransaction(
                tenant_id=referred_id,
                provider="play",
                plan_code="starter",
                kind="PURCHASE",
                state="VERIFIED",
                amount_paisa=19_900,
                verification_result="VERIFIED",
                occurred_at=utc_now() - timedelta(minutes=5),
            )
        )

    await _system(bought_on_play)
    await _approve(client, referred)
    await _run_reward_events(referred_id)

    referral = await _referral(referred_id)
    assert (referral.state, referral.void_reason) == ("VOID", "NOT_FIRST_PAYMENT")
    assert (referral.referred_credit_state, referral.referrer_credit_state) == (
        "PENDING",
        "PENDING",
    )
    assert not await _rows(
        BillingTransaction,
        BillingTransaction.provider_event_id.like("referral:%"),
        BillingTransaction.tenant_id == referred_id,
    )


async def test_with_the_flag_off_nothing_is_granted_and_the_referral_waits(
    client: AsyncClient, unique_phone: str, referrals_on: Any
):
    _, referred, _ = await _pair(client, unique_phone)
    referred_id = uuid.UUID(referred["tenant_id"])

    async def switch(db: Any, enabled: bool) -> None:
        await db.execute(
            sa.delete(FeatureFlag).where(
                FeatureFlag.key == str(FlagKey.REFERRALS_ENABLED),
                FeatureFlag.tenant_id == referred_id,
            )
        )
        if not enabled:
            db.add(
                FeatureFlag(
                    key=str(FlagKey.REFERRALS_ENABLED),
                    scope=str(FlagScope.TENANT),
                    tenant_id=referred_id,
                    enabled=False,
                )
            )

    await _system(lambda db: switch(db, False))
    first = await _approve(client, referred)
    await _run_reward_events(referred_id)
    referral = await _referral(referred_id)
    assert (referral.state, referral.referred_credit_state) == ("PENDING", "PENDING")
    assert not await _rows(
        BillingTransaction, BillingTransaction.provider_event_id.like(f"referral:{referral.id}:%")
    )

    # Back on: the next approved payment rewards, for the first payment.
    await _system(lambda db: switch(db, True))
    await _approve(client, referred)
    await _run_reward_events(referred_id)
    referral = await _referral(referred_id)
    assert referral.state == "REWARDED"
    assert str(referral.qualifying_submission_id) == first["id"]
    assert (
        await _rows(BillingPaymentSubmission, BillingPaymentSubmission.id == uuid.UUID(first["id"]))
    )[0].state == "APPROVED"
