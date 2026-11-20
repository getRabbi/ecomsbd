"""Voice call credits (V4 WP3.4).

The plan allowance (Free 0 / Starter 50 / Pro 200 a month) is spent first, then
the oldest unexpired pack; Free Launch Mode spends neither but stops at the
per-shop and global daily ceilings. The dispatcher spends a call's credit in
the same transaction as the attempt; only a call the provider certainly never
placed gets it back. Packs are granted by the operator (idempotent, 90 days,
optionally against one approved payment), and the admin report gives calls,
answer and confirm rates and cost per shop per day.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.models import (
    BillingPaymentSubmission,
    BillingProviderKind,
    ManualPaymentState,
)
from app.common.audit import AuditAction, AuditLog
from app.common.outbox import OutboxTopic
from app.confirmations import jobs
from app.confirmations.models import ConfirmationAttempt
from app.core.clock import business_date, utc_now
from app.core.config import Settings
from app.core.errors import (
    AppError,
    ErrorCode,
    IdempotencyConflictError,
    NotFoundError,
    ValidationError,
)
from app.db.session import system_session
from app.entitlements.catalog import PLANS, Entitlement, PlanCode
from app.entitlements.models import Subscription, SubscriptionStatus
from app.entitlements.usage import METERED_ENTITLEMENTS, UsageCounter, UsagePeriod
from app.voice.credits import SHOP_DAILY_KEY, VoiceCredits, call_cost, voice_report
from app.voice.models import (
    ChargeSource,
    VoiceCallCharge,
    VoiceCreditPack,
    VoiceDailyUsage,
)
from app.voice.transport import VoiceCallOutcome, VoiceEventKind
from tests.conftest_commerce import grant_plan
from tests.test_admin import _provision_admin, admin_client, admin_header
from tests.test_entitlements import _tenant
from tests.test_voice_calls import (
    NOON,
    Voice,
    _attempts,
    _callback,
    _confirmation,
    _event,
    _outbox,
    _queued,
    _rows,
    _shop,
    engine_on,
    voice,
)

__all__ = ["admin_client", "engine_on", "voice"]  # fixtures, imported for pytest

_VOICE = Entitlement.VOICE_CALLS_MONTHLY
_DAY = business_date(None, at=NOON).isoformat()


# ------------------------------------------------------------- helpers ---


async def _starter(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    db.add(
        Subscription(
            tenant_id=tenant_id,
            plan_code=str(PlanCode.STARTER),
            status=str(SubscriptionStatus.ACTIVE),
            source=str(BillingProviderKind.MANUAL_ADMIN),
            current_period_end=datetime.now(UTC) + timedelta(days=30),
        )
    )
    await db.flush()


async def _pack(
    db: AsyncSession, tenant_id: uuid.UUID, calls: int, *, age_days: int = 0
) -> VoiceCreditPack:
    """A pack granted ``age_days`` ago (91 = expired yesterday)."""
    granted = utc_now() - timedelta(days=age_days)
    pack, _ = await VoiceCredits(db).grant_pack(
        tenant_id,
        calls=calls,
        grant_key=uuid.uuid4().hex,
        reason="test pack",
        granted_by="test",
        now=granted,
    )
    pack.created_at = granted
    await db.flush()
    return pack


async def _packs(db: AsyncSession, tenant_id: uuid.UUID) -> dict[uuid.UUID, int]:
    rows = await db.scalars(
        sa.select(VoiceCreditPack)
        .where(VoiceCreditPack.tenant_id == tenant_id)
        .execution_options(populate_existing=True)
    )
    return {row.id: row.remaining for row in rows}


async def _used(db: AsyncSession, tenant_id: uuid.UUID, key: str) -> int:
    used = await db.scalar(
        sa.select(UsageCounter.used)
        .where(UsageCounter.tenant_id == tenant_id, UsageCounter.entitlement_key == key)
        .execution_options(populate_existing=True)
    )
    return int(used or 0)


async def _global(db: AsyncSession, day: str) -> int:
    used = await db.scalar(
        sa.select(VoiceDailyUsage.used)
        .where(VoiceDailyUsage.day == day)
        .execution_options(populate_existing=True)
    )
    return int(used or 0)


async def _payment(
    db: AsyncSession, tenant_id: uuid.UUID, state: ManualPaymentState
) -> BillingPaymentSubmission:
    row = BillingPaymentSubmission(
        tenant_id=tenant_id,
        plan_code="starter",
        months=1,
        amount_paisa=19_900,
        method="BKASH",
        payer_number_masked="017••••••01",
        trx_id="TX" + uuid.uuid4().hex[:10].upper(),
        state=str(state),
    )
    db.add(row)
    await db.flush()
    return row


# ------------------------------------------------------------- catalog ---


def test_plans_carry_monthly_voice_calls_and_free_launch_caps_default() -> None:
    limits = [PLANS[code].limit(_VOICE) for code in (PlanCode.FREE, PlanCode.STARTER, PlanCode.PRO)]
    assert limits == [0, 50, 200]
    assert METERED_ENTITLEMENTS[_VOICE] is UsagePeriod.MONTHLY
    assert Settings.model_fields["voice_daily_global_cap"].default == 300
    assert Settings.model_fields["voice_daily_shop_cap"].default == 30


def test_a_call_costs_what_the_provider_reported_else_the_manifest_price_marked_estimated():
    def cost(reported: int | None, seconds: int | None, call: int | None, minute: int | None):
        return call_cost(
            reported_paisa=reported,
            duration_seconds=seconds,
            price_per_call_paisa=call,
            price_per_minute_paisa=minute,
        )

    assert cost(120, None, 150, None) == (120, False)
    assert cost(None, None, 150, None) == (150, True)
    assert cost(None, 61, None, 100) == (200, True)  # per started minute
    assert cost(None, 60, None, 100) == (100, True)
    assert cost(None, 0, None, 100) == (100, True)
    assert cost(None, None, None, 100) == (None, True)  # length unknown
    assert cost(None, 30, None, None) == (None, True)


# --------------------------------------------------------------- spend ---


async def test_the_allowance_goes_first_then_the_oldest_unexpired_pack(
    system_db: AsyncSession, settings: Any
) -> None:
    tenant = await _tenant(system_db, "Voice Allowance Shop")
    await _starter(system_db, tenant)
    expired = await _pack(system_db, tenant, 5, age_days=91)
    older = await _pack(system_db, tenant, 2, age_days=10)
    newer = await _pack(system_db, tenant, 1, age_days=1)
    credits = VoiceCredits(system_db, settings=settings)
    now = utc_now()

    spent = [await credits.reserve(tenant, now=now) for _ in range(54)]

    assert all(r is not None and r.source is ChargeSource.PLAN_ALLOWANCE for r in spent[:50])
    assert [(r.source, r.pack_id) for r in spent[50:53] if r is not None] == [
        (ChargeSource.PACK, older.id),
        (ChargeSource.PACK, older.id),
        (ChargeSource.PACK, newer.id),
    ]
    assert spent[53] is None
    assert await _packs(system_db, tenant) == {expired.id: 5, older.id: 0, newer.id: 0}
    assert await _used(system_db, tenant, str(_VOICE)) == 50
    balance = await credits.balance(tenant, now=now)
    assert (balance.allowance_limit, balance.allowance_used, balance.allowance_remaining) == (
        50,
        50,
        0,
    )
    assert (balance.has_credit, balance.packs, balance.pack_calls_remaining) == (False, (), 0)


async def test_a_free_shop_calls_only_on_a_pack(system_db: AsyncSession, settings: Any) -> None:
    tenant = await _tenant(system_db, "Voice Free Shop")
    credits = VoiceCredits(system_db, settings=settings)
    now = utc_now()
    assert await credits.reserve(tenant, now=now) is None
    assert (await credits.balance(tenant, now=now)).has_credit is False
    pack = await _pack(system_db, tenant, 1)
    assert (await credits.balance(tenant, now=now)).has_credit is True
    reserved = await credits.reserve(tenant, now=now)
    assert reserved is not None and (reserved.source, reserved.pack_id) == (
        ChargeSource.PACK,
        pack.id,
    )
    assert await _used(system_db, tenant, str(_VOICE)) == 0, "no allowance counter spent"


async def test_undo_gives_each_credit_back_once_and_never_below_zero(
    system_db: AsyncSession, settings: Any
) -> None:
    now = utc_now()
    starter = await _tenant(system_db, "Voice Undo Starter")
    await _starter(system_db, starter)
    credits = VoiceCredits(system_db, settings=settings)
    allowance = await credits.reserve(starter, now=now)
    assert allowance is not None and await _used(system_db, starter, str(_VOICE)) == 1
    await credits.undo(allowance)
    await credits.undo(allowance)
    assert await _used(system_db, starter, str(_VOICE)) == 0

    free = await _tenant(system_db, "Voice Undo Free")
    pack = await _pack(system_db, free, 1)
    reserved = await credits.reserve(free, now=now)
    assert reserved is not None and await _packs(system_db, free) == {pack.id: 0}
    await credits.undo(reserved)
    await credits.undo(reserved)
    assert await _packs(system_db, free) == {pack.id: 1}, "never above the pack's size"


async def test_free_launch_spends_no_pack_and_stops_at_the_shop_and_global_daily_caps(
    system_db: AsyncSession, settings: Any
) -> None:
    now = utc_now()
    day = business_date(None, at=now).isoformat()
    before = await _global(system_db, day)
    free = settings.model_copy(
        update={
            "free_launch_mode": True,
            "voice_daily_shop_cap": 2,
            "voice_daily_global_cap": before + 3,
        }
    )
    a = await _tenant(system_db, "Voice Launch A")
    b = await _tenant(system_db, "Voice Launch B")
    pack = await _pack(system_db, a, 10)
    credits = VoiceCredits(system_db, settings=free)

    first = [await credits.reserve(a, now=now) for _ in range(3)]
    assert [r.source if r else None for r in first] == [
        ChargeSource.FREE_LAUNCH,
        ChargeSource.FREE_LAUNCH,
        None,  # the shop's daily cap
    ]
    assert first[0] is not None and first[0].period_key == day
    assert await _packs(system_db, a) == {pack.id: 10}, "Free Launch spends no pack"
    assert await credits.reserve(b, now=now) is not None
    assert await credits.reserve(b, now=now) is None  # every shop together: the global cap
    assert await _used(system_db, b, SHOP_DAILY_KEY) == 1, (
        "the refused call gave its shop count back"
    )
    assert await _global(system_db, day) == before + 3

    balance = await credits.balance(b, now=now)
    assert (balance.free_launch, balance.has_credit, balance.shop_today, balance.shop_cap) == (
        True,
        False,
        1,
        2,
    )
    assert (balance.global_today, balance.global_cap) == (before + 3, before + 3)
    assert balance.allowance_remaining is None, "the plan itself is unlimited"

    await credits.undo(first[0])
    assert await _used(system_db, a, SHOP_DAILY_KEY) == 1
    assert await _global(system_db, day) == before + 2

    none_left = free.model_copy(update={"voice_daily_shop_cap": 0})
    assert await VoiceCredits(system_db, settings=none_left).reserve(b, now=now) is None


# --------------------------------------------------------------- grant ---


async def test_a_pack_grant_is_idempotent_and_lasts_90_days(
    system_db: AsyncSession, settings: Any
) -> None:
    tenant = await _tenant(system_db, "Voice Grant Shop")
    credits = VoiceCredits(system_db, settings=settings)
    now = utc_now()

    def grant(calls: int = 25, key: str = "grant-a", reason: str = "pilot credit") -> Any:
        return credits.grant_pack(
            tenant, calls=calls, grant_key=key, reason=reason, granted_by="ops", now=now
        )

    pack, created = await grant()
    assert created and (pack.calls, pack.remaining, pack.source) == (25, 25, "ADMIN_GRANT")
    assert pack.expires_at == now + timedelta(days=90)
    again, created_again = await grant()
    assert (again.id, created_again) == (pack.id, False)
    with pytest.raises(IdempotencyConflictError):
        await grant(calls=26)
    for calls in (0, 10_001):
        with pytest.raises(ValidationError):
            await grant(calls=calls, key=f"grant-{calls}")
    with pytest.raises(ValidationError):
        await grant(key="grant-b", reason="   ")
    assert await _packs(system_db, tenant) == {pack.id: 25}


async def test_a_payment_pack_needs_an_approved_payment_of_the_shop_and_only_one(
    system_db: AsyncSession, settings: Any
) -> None:
    tenant = await _tenant(system_db, "Voice Payment Shop")
    other = await _tenant(system_db, "Voice Payment Other")
    submitted = await _payment(system_db, tenant, ManualPaymentState.SUBMITTED)
    approved = await _payment(system_db, tenant, ManualPaymentState.APPROVED)
    foreign = await _payment(system_db, other, ManualPaymentState.APPROVED)
    credits = VoiceCredits(system_db, settings=settings)

    def grant(payment: uuid.UUID, key: str) -> Any:
        return credits.grant_pack(
            tenant,
            calls=50,
            grant_key=key,
            reason="paid by bKash",
            granted_by="ops",
            payment_submission_id=payment,
        )

    with pytest.raises(AppError) as refused:
        await grant(submitted.id, "pay-submitted")
    assert refused.value.code is ErrorCode.VOICE_PACK_PAYMENT_INVALID
    assert refused.value.details == {"reason": "NOT_APPROVED"}
    with pytest.raises(NotFoundError):
        await grant(foreign.id, "pay-foreign")
    pack, created = await grant(approved.id, "pay-1")
    assert created and (pack.source, pack.payment_submission_id) == (
        "PAYMENT_SUBMISSION",
        approved.id,
    )
    with pytest.raises(AppError) as twice:
        await grant(approved.id, "pay-2")
    assert twice.value.details == {"reason": "ALREADY_USED"}


# ---------------------------------------------------------- dispatcher ---


async def _charges(shop: dict[str, Any]) -> list[VoiceCallCharge]:
    return await _rows(shop, VoiceCallCharge)


async def _shop_packs(shop: dict[str, Any]) -> list[VoiceCreditPack]:
    return await _rows(shop, VoiceCreditPack)


async def test_a_shop_without_credit_goes_to_the_seller_and_nobody_is_called(
    client: AsyncClient, voice: Voice
):
    shop = await _shop(client, "01700000501")
    async with system_session("test: spend the pack") as db:
        await db.execute(
            sa.update(VoiceCreditPack)
            .where(VoiceCreditPack.tenant_id == uuid.UUID(shop["tenant_id"]))
            .values(remaining=0)
        )
    order = await _queued(client, shop, "01700000502")

    counts = await jobs.dispatch_confirmation_calls()

    assert counts.get("NO_CREDIT") == 1, counts
    assert voice.mock.calls == []
    row = await _confirmation(shop, order)
    assert row.state == "SELLER_REVIEW"
    assert await _attempts(shop, row) == []
    assert await _charges(shop) == []
    (moved,) = await _outbox(shop, OutboxTopic.CONFIRMATION_REVIEW_NEEDED, row)
    assert moved.payload["cause"] == "NO_VOICE_CREDIT"


@pytest.mark.parametrize(
    ("n", "outcome", "raises", "kept"),
    [
        (0, None, False, True),  # PLACED
        (1, VoiceCallOutcome.DUPLICATE, False, True),
        (2, VoiceCallOutcome.UNKNOWN, False, True),
        (3, None, True, True),  # the transport raised mid-request: may have rung
        (4, VoiceCallOutcome.FAILED, False, False),
        (5, VoiceCallOutcome.REJECTED, False, False),
        (6, VoiceCallOutcome.NOT_CONFIGURED, False, False),
    ],
)
async def test_a_call_that_reached_the_provider_stays_charged_and_one_never_placed_is_given_back(
    client: AsyncClient,
    voice: Voice,
    n: int,
    outcome: VoiceCallOutcome | None,
    raises: bool,
    kept: bool,
):
    # One owner per case: a user owns at most five shops.
    shop = await _shop(client, f"0170000053{n}")
    order = await _queued(client, shop, f"0170000054{n}")
    voice.transport.raises = raises
    if outcome is not None:
        voice.mock.script(outcome)

    await jobs.dispatch_confirmation_calls()

    row = await _confirmation(shop, order)
    (attempt,) = await _attempts(shop, row)
    (charge,) = await _charges(shop)
    (pack,) = await _shop_packs(shop)
    assert (charge.attempt_id, charge.source, charge.pack_id, charge.provider) == (
        attempt.id,
        "PACK",
        pack.id,
        "mock",
    )
    assert charge.day == _DAY
    if kept:
        assert (charge.state, charge.released_at, pack.remaining) == ("CHARGED", None, 99)
    else:
        assert (charge.state, pack.remaining) == ("RELEASED", 100)
        assert charge.released_at is not None


async def test_a_stuck_call_reconciled_to_the_seller_keeps_its_charge(
    client: AsyncClient, voice: Voice
):
    shop = await _shop(client, "01700000505")
    order = await _queued(client, shop, "01700000506")
    await jobs.dispatch_confirmation_calls()
    voice.clock.at = NOON + timedelta(minutes=11)
    counts = await jobs.dispatch_confirmation_calls()
    assert counts.get("reconciled", 0) >= 1, counts
    row = await _confirmation(shop, order)
    assert row.state == "SELLER_REVIEW"
    (moved,) = await _outbox(shop, OutboxTopic.CONFIRMATION_REVIEW_NEEDED, row)
    assert moved.payload["cause"] == "CALL_UNKNOWN"
    (charge,) = await _charges(shop)
    (pack,) = await _shop_packs(shop)
    assert (charge.state, pack.remaining) == ("CHARGED", 99)


async def test_an_open_breaker_spends_nothing_on_the_call_it_holds(
    client: AsyncClient, voice: Voice
):
    shop = await _shop(client, "01700000519")
    orders = [await _queued(client, shop, f"0170000052{n}") for n in range(6)]
    voice.mock.script(*[VoiceCallOutcome.FAILED] * 5)
    await jobs.dispatch_confirmation_calls()
    states = [(await _confirmation(shop, order)).state for order in orders]
    assert states.count("SELLER_REVIEW") == 5 and states.count("CALL_QUEUED") == 1
    charges = await _charges(shop)
    assert [charge.state for charge in charges] == ["RELEASED"] * 5
    (pack,) = await _shop_packs(shop)
    assert pack.remaining == 100, "five given back, the held call never spent"


async def test_the_dispatcher_spends_the_plan_allowance_before_a_pack(
    client: AsyncClient, voice: Voice
):
    shop = await _shop(client, "01700000507")
    await grant_plan(shop["tenant_id"], str(PlanCode.STARTER))
    await _queued(client, shop, "01700000508")
    await jobs.dispatch_confirmation_calls()
    (charge,) = await _charges(shop)
    (counter,) = await _rows(shop, UsageCounter, UsageCounter.entitlement_key == str(_VOICE))
    (pack,) = await _shop_packs(shop)
    assert (charge.source, charge.period_key, charge.pack_id) == (
        "PLAN_ALLOWANCE",
        counter.period_key,
        None,
    )
    assert counter.period_key == business_date(None, at=NOON).strftime("%Y-%m")
    assert (counter.used, counter.limit_value, pack.remaining) == (1, 50, 100)


# --------------------------------------------------------------- admin ---


async def test_the_report_counts_calls_answers_confirms_and_cost_per_shop_and_day(
    client: AsyncClient, voice: Voice, admin_client: Any
):
    shop = await _shop(client, "01700000509")
    tenant = uuid.UUID(shop["tenant_id"])
    confirmed = await _queued(client, shop, "01700000510")
    unanswered = await _queued(client, shop, "01700000511")
    failed = await _queued(client, shop, "01700000512")
    voice.mock.script(VoiceCallOutcome.PLACED, VoiceCallOutcome.PLACED, VoiceCallOutcome.FAILED)
    await jobs.dispatch_confirmation_calls()
    yes = await _confirmation(shop, confirmed)
    quiet = await _confirmation(shop, unanswered)
    for response in (
        await _callback(
            client,
            voice,
            _event(voice, yes, 1, VoiceEventKind.ANSWERED),
            _event(voice, yes, 1, VoiceEventKind.DTMF, "1"),
        ),
        await _callback(client, voice, _event(voice, quiet, 1, VoiceEventKind.NO_ANSWER)),
    ):
        assert response.status_code == 200, response.text
    assert (await _confirmation(shop, failed)).state == "SELLER_REVIEW"
    (reported,) = await _attempts(shop, yes)
    async with system_session("test: provider cost") as db:
        await db.execute(
            sa.update(ConfirmationAttempt)
            .where(ConfirmationAttempt.id == reported.id)
            .values(cost_paisa=120, cost_quality="EXACT")
        )

    day = datetime.fromisoformat(_DAY).date()
    async with system_session("test: voice report") as db:
        (row,) = await voice_report(
            db, day_from=day, day_to=day, tenant_id=tenant, prices={"mock": (150, None)}
        )
    assert row.as_dict() == {
        "tenant_id": str(tenant),
        "shop_name": "Test Shop",
        "day": _DAY,
        "calls": 2,  # the FAILED call was given back
        "answered": 1,
        "confirmed": 1,
        "answer_rate_bps": 5000,
        "confirm_rate_bps": 5000,
        "cost_paisa": 270,  # 120 reported + 150 from the price
        "cost_per_call_paisa": 135,
        "cost_estimated": True,
        "calls_without_cost": 0,
        "providers": ["mock"],
    }

    # Through the console: no manifest offers a price here, so only the
    # provider's own figure is known.
    response = await client.get(
        "/v1/admin/voice/report",
        params={"from": _DAY, "to": _DAY, "tenant_id": str(tenant)},
        headers=admin_header(),
    )
    assert response.status_code == 200, response.text
    (listed,) = response.json()["rows"]
    assert (listed["calls"], listed["answered"], listed["confirmed"]) == (2, 1, 1)
    assert (listed["cost_paisa"], listed["calls_without_cost"], listed["cost_estimated"]) == (
        120,
        1,
        False,
    )

    later = (day + timedelta(days=31)).isoformat()
    for params in ({"from": _DAY, "to": later}, {"from": later, "to": _DAY}):
        refused = await client.get("/v1/admin/voice/report", params=params, headers=admin_header())
        assert refused.status_code == 422, refused.text


async def test_the_console_grants_a_pack_once_and_shows_the_balance(
    client: AsyncClient, voice: Voice, admin_client: Any, settings: Any
):
    from app.admin.models import PlatformAdminRole

    shop = await _shop(client, "01700000513")
    tenant = shop["tenant_id"]
    url = f"/v1/admin/voice/credits/{tenant}"
    body = {"calls": 40, "reason": "pilot shop credit", "idempotency_key": "grant-0001"}

    first = await client.post(f"{url}/packs", json=body, headers=admin_header())
    assert first.status_code == 200, first.text
    granted = first.json()
    assert granted["created"] is True
    assert (granted["pack"]["calls"], granted["pack"]["remaining"]) == (40, 40)
    assert granted["pack"]["source"] == "ADMIN_GRANT"
    again = await client.post(f"{url}/packs", json=body, headers=admin_header())
    assert again.status_code == 200 and again.json()["created"] is False
    assert again.json()["pack"]["id"] == granted["pack"]["id"]
    changed = await client.post(f"{url}/packs", json={**body, "calls": 41}, headers=admin_header())
    assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    async with system_session("test: audit") as db:
        audits = (
            await db.scalars(
                sa.select(AuditLog).where(
                    AuditLog.action == str(AuditAction.ADMIN_VOICE_PACK_GRANTED),
                    AuditLog.entity_id == granted["pack"]["id"],
                )
            )
        ).all()
    assert len(audits) == 1

    balance = (await client.get(url, headers=admin_header())).json()
    assert (balance["free_launch"], balance["has_credit"], balance["allowance_limit"]) == (
        False,
        True,
        0,
    )
    assert balance["pack_calls_remaining"] == 140  # the test shop's own 100 + 40
    assert [pack["remaining"] for pack in balance["packs"]] == [100, 40]

    ops = "ops-" + "v" * 44
    await _provision_admin(PlatformAdminRole.OPS, ops, settings)
    assert (await client.get(url, headers=admin_header(ops))).status_code == 200
    refused = await client.post(
        f"{url}/packs", json={**body, "idempotency_key": "grant-0002"}, headers=admin_header(ops)
    )
    assert refused.status_code == 403
    missing = await client.get(f"/v1/admin/voice/credits/{uuid.uuid4()}", headers=admin_header())
    assert missing.status_code == 404
