"""IVR confirmation calls (V4 WP3.2).

The dispatcher, the provider callback route and the reconciler, against the
mock transport: what is recorded before the provider is asked, what each key
does, what an ambiguous answer does (never a second call), quiet hours, the
per-customer daily cap, and the callback's storage, signature and dedupe.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient, Response

from app.common.outbox import OutboxEvent, OutboxTopic
from app.common.provider_health import ProviderHealth
from app.confirmations import calls, jobs
from app.confirmations.calls import CALLS_PER_CUSTOMER_PER_DAY, IvrStatus
from app.confirmations.models import (
    AttemptChannel,
    ConfirmationAttempt,
    ConfirmationState,
    OrderConfirmation,
)
from app.core.clock import DHAKA, utc_now
from app.core.context import RequestContext, clear_context, set_context
from app.db.session import session_scope, system_session
from app.notifications.models import Notification, NotificationKind
from app.notifications.templates import render
from app.orders.models import Order, OrderStatus
from app.voice.models import (
    VoiceCallbackDelivery,
    VoiceCallEvent,
    VoiceProviderConnection,
)
from app.voice.transport import (
    MockVoiceTransport,
    VoiceCall,
    VoiceCallOutcome,
    VoiceCallResult,
    VoiceEvent,
    VoiceEventKind,
)
from tests.conftest_commerce import create_order, signed_in_shop
from tests.test_auth_flow import auth_header

S = ConfirmationState

_TOMORROW = (utc_now() + timedelta(days=1)).astimezone(DHAKA)
#: Tomorrow 12:00 and 23:00 in Dhaka: after every row's first-call delay,
#: outside / inside the default 21:00–09:00 quiet hours.
NOON = _TOMORROW.replace(hour=12, minute=0, second=0, microsecond=0).astimezone(UTC)
NIGHT = _TOMORROW.replace(hour=23, minute=0, second=0, microsecond=0).astimezone(UTC)
AUDIO = "voice/test/intro-v1"


# ------------------------------------------------------------- fixtures ---


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


@dataclass
class Hooked:
    """The mock, with a hook that runs inside ``place_call`` (as a provider
    would be mid-request) and an optional exception."""

    mock: MockVoiceTransport = field(default_factory=MockVoiceTransport)
    before: Callable[[VoiceCall], Awaitable[None]] | None = None
    raises: bool = False

    @property
    def name(self) -> str:
        return self.mock.name

    def availability(self) -> Any:
        return self.mock.availability()

    async def place_call(self, call: VoiceCall) -> VoiceCallResult:
        if self.before is not None:
            await self.before(call)
        if self.raises:
            raise TimeoutError("provider read timed out")
        return await self.mock.place_call(call)

    async def verify_callback(self, headers: Mapping[str, str], body: bytes) -> bool:
        return await self.mock.verify_callback(headers, body)

    def parse_callback(self, body: bytes) -> list[VoiceEvent]:
        return self.mock.parse_callback(body)


@dataclass
class Voice:
    transport: Hooked
    clock: Clock

    @property
    def mock(self) -> MockVoiceTransport:
        return self.transport.mock


async def _reset_voice() -> None:
    from app.common.feature_flags import FeatureFlag, FlagKey

    async with system_session("test: reset voice") as db:
        await db.execute(
            sa.delete(FeatureFlag).where(
                FeatureFlag.key == str(FlagKey.VOICE_CONFIRMATION_ENABLED),
                FeatureFlag.tenant_id.is_not(None),
            )
        )
        for model in (VoiceCallEvent, VoiceCallbackDelivery):
            await db.execute(sa.delete(model))
        await db.execute(
            sa.delete(VoiceProviderConnection).where(VoiceProviderConnection.provider == "mock")
        )
        await db.execute(sa.delete(ProviderHealth).where(ProviderHealth.provider == "mock"))


@pytest.fixture
async def engine_on(settings: Any) -> AsyncIterator[None]:
    from app.common.feature_flags import FlagKey
    from tests.conftest import _global_flag_on

    async for _ in _global_flag_on(settings, str(FlagKey.CONFIRMATION_ENGINE_ENABLED)):
        yield


async def _shop(client: AsyncClient, phone: str) -> dict[str, Any]:
    """A shop with ``voice_confirmation_enabled`` on for it alone: the test
    database is shared, and other suites' queued rows must never be dialled."""
    from app.common.feature_flags import FeatureFlag, FlagKey, FlagScope

    shop = await signed_in_shop(client, phone)
    async with system_session("test: voice flag") as db:
        db.add(
            FeatureFlag(
                key=str(FlagKey.VOICE_CONFIRMATION_ENABLED),
                scope=str(FlagScope.TENANT),
                tenant_id=uuid.UUID(shop["tenant_id"]),
                enabled=True,
                rollout_percentage=100,
            )
        )
    return shop


@pytest.fixture
async def voice(engine_on: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Voice]:
    transport = Hooked()
    monkeypatch.setattr(calls, "voice_transport", lambda: transport)

    async def audio(_db: Any, _tenant_id: uuid.UUID, **_kw: Any) -> str:
        return AUDIO

    monkeypatch.setattr(calls, "intro_audio_ref", audio)
    clock = Clock(NOON)
    monkeypatch.setattr(jobs, "utc_now", clock)
    monkeypatch.setattr(calls, "utc_now", clock)
    await _reset_voice()
    async with system_session("test: mock voice account") as db:
        db.add(
            VoiceProviderConnection(
                provider="mock", enabled=True, created_by="test", updated_by="test", config={}
            )
        )
    yield Voice(transport, clock)
    await _reset_voice()


class _Tenant:
    def __init__(self, shop: dict[str, Any]) -> None:
        self.id = uuid.UUID(shop["tenant_id"])

    def __enter__(self) -> None:
        self._token = set_context(RequestContext(trace_id="test", tenant_id=self.id))

    def __exit__(self, *_: object) -> None:
        clear_context(self._token)


async def _rows(shop: dict[str, Any], model: Any, *where: Any) -> list[Any]:
    with _Tenant(shop):
        async with session_scope() as db:
            return list((await db.scalars(sa.select(model).where(*where))).all())


async def _confirmation(shop: dict[str, Any], order: dict[str, Any]) -> OrderConfirmation:
    (row,) = await _rows(
        shop,
        OrderConfirmation,
        OrderConfirmation.order_id == uuid.UUID(order["id"]),
        OrderConfirmation.state != str(S.SUPERSEDED),
    )
    return row


async def _attempts(shop: dict[str, Any], row: OrderConfirmation) -> list[ConfirmationAttempt]:
    found = await _rows(shop, ConfirmationAttempt, ConfirmationAttempt.confirmation_id == row.id)
    return sorted(found, key=lambda attempt: attempt.attempt_no)


async def _order(shop: dict[str, Any], order: dict[str, Any]) -> Order:
    (row,) = await _rows(shop, Order, Order.id == uuid.UUID(order["id"]))
    return row


async def _queued(client: AsyncClient, shop: dict[str, Any], phone: str) -> dict[str, Any]:
    """A new customer's order, confirmation requested: the policy queues a call."""
    order = (await create_order(client, shop, phone=phone))["order"]
    started = await client.post(
        f"/v1/orders/{order['id']}/confirmation",
        json={"mode": "REQUEST"},
        headers=auth_header(shop),
    )
    assert started.status_code == 200, started.text
    assert started.json()["confirmation"]["state"] == "CALL_QUEUED"
    return order


async def _callback(client: AsyncClient, voice: Voice, *events: VoiceEvent, **kw: Any) -> Response:
    headers, body = voice.mock.callback(*events, **kw)
    return await client.post(
        "/v1/webhooks/voice/mock",
        content=body,
        headers={**headers, "content-type": "application/json"},
    )


def _event(
    voice: Voice,
    row: OrderConfirmation,
    attempt_no: int,
    kind: VoiceEventKind,
    digit: str | None = None,
    *,
    echo_ref: bool = True,
    duration: int | None = None,
) -> VoiceEvent:
    ref = calls.client_ref(row.id, attempt_no)
    provider_ref = voice.mock.provider_ref_for(ref)
    assert provider_ref is not None
    return VoiceEvent(
        kind=kind,
        provider_ref=provider_ref,
        client_ref=ref if echo_ref else None,
        digit=digit,
        duration_seconds=duration,
    )


async def _outbox(shop: dict[str, Any], topic: OutboxTopic, row: OrderConfirmation) -> list[Any]:
    found = await _rows(shop, OutboxEvent, OutboxEvent.topic == str(topic))
    return [event for event in found if event.payload.get("confirmation_id") == str(row.id)]


async def _run_handlers(shop: dict[str, Any], row: OrderConfirmation) -> None:
    from app.worker.jobs import HANDLERS

    with _Tenant(shop):
        for topic in (
            OutboxTopic.CONFIRMATION_CALLBACK_REQUESTED,
            OutboxTopic.CONFIRMATION_REVIEW_NEEDED,
        ):
            for event in await _outbox(shop, topic, row):
                for _ in range(2):  # a retried event notifies once
                    async with session_scope() as db:
                        await HANDLERS[event.topic](db, event)


# ------------------------------------------------------------ dispatch ---


async def test_the_first_call_waits_two_minutes(client: AsyncClient, voice: Voice):
    shop = await _shop(client, "01700000081")
    order = await _queued(client, shop, "01700000082")
    row = await _confirmation(shop, order)
    assert row.next_action_at == row.created_at + timedelta(minutes=2)

    voice.clock.at = row.created_at + timedelta(minutes=1)
    await jobs.dispatch_confirmation_calls()
    assert voice.mock.calls == []
    assert (await _confirmation(shop, order)).state == "CALL_QUEUED"


async def test_a_due_call_is_recorded_and_committed_before_the_provider_is_asked(
    client: AsyncClient, voice: Voice
):
    shop = await _shop(client, "01700000083")
    order = await _queued(client, shop, "01700000084")
    seen: list[tuple[str, list[str]]] = []

    async def peek(_call: VoiceCall) -> None:
        # A fresh session: only committed rows are visible here.
        row = await _confirmation(shop, order)
        seen.append((row.state, [a.status for a in await _attempts(shop, row)]))

    voice.transport.before = peek
    counts = await jobs.dispatch_confirmation_calls()

    assert counts.get("PLACED") == 1, counts
    assert seen == [("CALLING", ["STARTED"])]
    (call,) = voice.mock.calls
    row = await _confirmation(shop, order)
    assert call.client_ref == f"confirmation:{row.id}:1"
    assert call.to_e164 == "+8801700000084" and call.audio_ref == AUDIO
    assert call.max_retries == 0 and [o.digit for o in call.dtmf_options] == ["1", "2", "3"]
    (attempt,) = await _attempts(shop, row)
    assert (attempt.channel, attempt.provider, attempt.status) == ("IVR", "mock", "PLACED")
    assert attempt.provider_ref == voice.mock.provider_ref_for(call.client_ref)
    assert row.state == "CALLING" and row.next_action_at == NOON + timedelta(minutes=10)
    async with system_session("test: health") as db:
        health = await db.scalar(sa.select(ProviderHealth).where(ProviderHealth.provider == "mock"))
    assert health is not None and health.success_count == 1

    # The next tick does not call it again.
    await jobs.dispatch_confirmation_calls()
    assert len(voice.mock.calls) == 1
    assert "01700000084" not in repr(call)


async def test_key_1_confirms_the_order_and_late_events_change_nothing(
    client: AsyncClient, voice: Voice
):
    shop = await _shop(client, "01700000085")
    order = await _queued(client, shop, "01700000086")
    await jobs.dispatch_confirmation_calls()
    row = await _confirmation(shop, order)

    answered = await _callback(
        client,
        voice,
        _event(voice, row, 1, VoiceEventKind.ANSWERED),
        _event(voice, row, 1, VoiceEventKind.DTMF, "1"),
    )
    assert answered.status_code == 200 and answered.json() == {"received": True, "processed": True}

    row = await _confirmation(shop, order)
    assert (row.state, row.method, row.order_outcome) == ("CONFIRMED", "IVR", "APPLIED")
    assert (await _order(shop, order)).status == "CONFIRMED"
    (attempt,) = await _attempts(shop, row)
    assert (attempt.status, attempt.dtmf) == ("CONFIRMED", "1")

    # The provider retries the same body: stored once, applied once.
    again = await _callback(
        client,
        voice,
        _event(voice, row, 1, VoiceEventKind.ANSWERED),
        _event(voice, row, 1, VoiceEventKind.DTMF, "1"),
    )
    assert again.status_code == 200
    async with system_session("test: deliveries") as db:
        (delivery,) = (await db.scalars(sa.select(VoiceCallbackDelivery))).all()
    assert (delivery.state, delivery.delivery_count, delivery.event_count) == ("PROCESSED", 2, 2)
    assert "x-mock-voice-signature" in delivery.header_names

    # A different key and the hang-up after the answer: recorded, ignored.
    late = await _callback(
        client,
        voice,
        _event(voice, row, 1, VoiceEventKind.DTMF, "2"),
        _event(voice, row, 1, VoiceEventKind.COMPLETED, duration=41),
    )
    assert late.status_code == 200
    (attempt,) = await _attempts(shop, row)
    assert (attempt.status, attempt.dtmf, attempt.duration_seconds) == ("CONFIRMED", "1", 41)
    assert attempt.ended_at is not None
    assert (await _confirmation(shop, order)).state == "CONFIRMED"
    async with system_session("test: events") as db:
        results = {
            (event.event_key, event.result)
            for event in (await db.scalars(sa.select(VoiceCallEvent))).all()
        }
    assert results == {
        ("ANSWERED", "RECORDED"),
        ("DTMF:1", "APPLIED"),
        ("DTMF:2", "LATE"),
        ("COMPLETED", "LATE"),
    }


async def test_key_2_cancels_and_a_key_the_script_does_not_offer_is_ignored(
    client: AsyncClient, voice: Voice
):
    shop = await _shop(client, "01700000087")
    order = await _queued(client, shop, "01700000088")
    await jobs.dispatch_confirmation_calls()
    row = await _confirmation(shop, order)

    await _callback(client, voice, _event(voice, row, 1, VoiceEventKind.DTMF, "9"))
    assert (await _confirmation(shop, order)).state == "CALLING"

    await _callback(client, voice, _event(voice, row, 1, VoiceEventKind.DTMF, "2"))
    row = await _confirmation(shop, order)
    assert (row.state, row.method) == ("CANCELLED", "IVR")
    placed = await _order(shop, order)
    assert (placed.status, placed.cancellation_reason) == ("CANCELLED", "CUSTOMER_DECLINED")


async def test_key_3_asks_the_owner_and_managers_to_call_back(client: AsyncClient, voice: Voice):
    shop = await _shop(client, "01700000089")
    order = await _queued(client, shop, "01700000090")
    await jobs.dispatch_confirmation_calls()
    row = await _confirmation(shop, order)

    await _callback(client, voice, _event(voice, row, 1, VoiceEventKind.DTMF, "3"))
    row = await _confirmation(shop, order)
    assert row.state == "SELLER_REVIEW" and row.method is None
    assert (await _order(shop, order)).status == "DRAFT"
    assert len(await _outbox(shop, OutboxTopic.CONFIRMATION_CALLBACK_REQUESTED, row)) == 1

    await _run_handlers(shop, row)
    notes = await _rows(shop, Notification)
    kinds = [note.kind for note in notes if note.entity_id == uuid.UUID(order["id"])]
    # One push, the specific one; not a second generic "needs attention".
    assert kinds == [str(NotificationKind.CHAT_CALLBACK_REQUESTED)]
    (note,) = [note for note in notes if note.kind == "CHAT_CALLBACK_REQUESTED"]
    assert note.payload["target"] == {"route": "call_queue"}
    assert "01700000090" not in note.body


@pytest.mark.parametrize("locale", ["en", "bn"])
def test_the_callback_push_names_no_customer(locale: str):
    rendered = render(NotificationKind.CHAT_CALLBACK_REQUESTED, {"digit": "3"}, locale)
    assert rendered is not None and all(rendered)
    assert ("৩" if locale == "bn" else "3") in rendered[1]


async def test_no_answer_is_called_once_more_after_30_minutes_then_goes_to_the_seller(
    client: AsyncClient, voice: Voice
):
    shop = await _shop(client, "01700000091")
    order = await _queued(client, shop, "01700000092")
    await jobs.dispatch_confirmation_calls()
    row = await _confirmation(shop, order)
    await _callback(client, voice, _event(voice, row, 1, VoiceEventKind.NO_ANSWER))
    row = await _confirmation(shop, order)
    assert row.state == "NO_ANSWER" and row.next_action_at == NOON + timedelta(minutes=30)

    voice.clock.at = NOON + timedelta(minutes=29)
    await jobs.follow_up_confirmations()
    await jobs.dispatch_confirmation_calls()
    assert len(voice.mock.calls) == 1

    voice.clock.at = NOON + timedelta(minutes=31)
    await jobs.follow_up_confirmations()
    assert (await _confirmation(shop, order)).state == "CALL_QUEUED"
    await jobs.dispatch_confirmation_calls()
    assert [call.client_ref for call in voice.mock.calls] == [
        f"confirmation:{row.id}:1",
        f"confirmation:{row.id}:2",
    ]

    # Answered, hung up without a key: as good as no answer.
    await _callback(client, voice, _event(voice, row, 2, VoiceEventKind.COMPLETED, duration=7))
    assert (await _confirmation(shop, order)).state == "NO_ANSWER"
    await jobs.follow_up_confirmations()  # no SMS link here: the seller
    row = await _confirmation(shop, order)
    assert row.state == "SELLER_REVIEW"
    assert [a.status for a in await _attempts(shop, row)] == ["NO_ANSWER", "NO_KEY"]
    await jobs.dispatch_confirmation_calls()
    assert len(voice.mock.calls) == 2


@pytest.mark.parametrize("raises", [False, True])
async def test_an_ambiguous_answer_goes_to_the_seller_and_is_never_called_again(
    client: AsyncClient, voice: Voice, raises: bool
):
    shop = await _shop(client, "01700000093")
    order = await _queued(client, shop, "01700000094")
    if raises:
        voice.transport.raises = True
    else:
        voice.mock.script(VoiceCallOutcome.UNKNOWN)

    counts = await jobs.dispatch_confirmation_calls()
    assert counts.get("UNKNOWN") == 1, counts
    row = await _confirmation(shop, order)
    assert row.state == "SELLER_REVIEW"
    (attempt,) = await _attempts(shop, row)
    assert attempt.status == "UNKNOWN" and attempt.ended_at is not None
    (moved,) = await _outbox(shop, OutboxTopic.CONFIRMATION_REVIEW_NEEDED, row)
    assert moved.payload["cause"] == "CALL_UNKNOWN"

    voice.transport.raises = False
    for minutes in (1, 40, 90):
        voice.clock.at = NOON + timedelta(minutes=minutes)
        await jobs.follow_up_confirmations()
        await jobs.dispatch_confirmation_calls()
    assert len(voice.mock.calls) == (0 if raises else 1)
    assert len(await _attempts(shop, row)) == 1


@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        (VoiceCallOutcome.FAILED, "NOT_PLACED"),
        (VoiceCallOutcome.REJECTED, "REJECTED"),
        (VoiceCallOutcome.NOT_CONFIGURED, "NOT_CONFIGURED"),
    ],
)
async def test_a_call_certainly_not_placed_goes_to_the_seller(
    client: AsyncClient, voice: Voice, outcome: VoiceCallOutcome, status: str
):
    shop = await _shop(client, "01700000095")
    order = await _queued(client, shop, "01700000096")
    voice.mock.script(outcome)
    await jobs.dispatch_confirmation_calls()
    row = await _confirmation(shop, order)
    assert row.state == "SELLER_REVIEW"
    (attempt,) = await _attempts(shop, row)
    assert attempt.status == status
    (moved,) = await _outbox(shop, OutboxTopic.CONFIRMATION_REVIEW_NEEDED, row)
    assert moved.payload["cause"] == "CALL_FAILED"


async def test_a_stuck_call_is_reconciled_after_ten_minutes_and_a_late_key_is_only_recorded(
    client: AsyncClient, voice: Voice
):
    shop = await _shop(client, "01700000097")
    order = await _queued(client, shop, "01700000098")
    row = await _confirmation(shop, order)
    # The worker dies right after transaction 1: CALLING, no provider answer.
    with _Tenant(shop):
        async with session_scope() as db:
            gate = await calls.voice_gate(db, row.tenant_id)
            prepared = await calls.services(db).prepare(row.id, gate=gate, now=NOON)
    assert isinstance(prepared, calls.PreparedCall)
    await voice.mock.place_call(prepared.call)  # it did ring

    voice.clock.at = NOON + timedelta(minutes=9)
    await jobs.dispatch_confirmation_calls()
    assert (await _confirmation(shop, order)).state == "CALLING"
    voice.clock.at = NOON + timedelta(minutes=10)
    assert (await jobs.dispatch_confirmation_calls())["reconciled"] >= 1
    row = await _confirmation(shop, order)
    assert row.state == "SELLER_REVIEW"
    (attempt,) = await _attempts(shop, row)
    assert attempt.status == "UNKNOWN"

    # The customer's key arrives after the hand-over: recorded, not applied.
    late = _event(voice, row, 1, VoiceEventKind.DTMF, "1")
    assert (await _callback(client, voice, late)).status_code == 200
    (attempt,) = await _attempts(shop, row)
    assert (attempt.status, attempt.dtmf) == ("UNKNOWN", "1")
    assert (await _confirmation(shop, order)).state == "SELLER_REVIEW"
    assert (await _order(shop, order)).status == "DRAFT"
    assert len(voice.mock.calls) == 1


async def test_a_callback_before_the_provider_answered_is_applied_once_the_ref_is_stored(
    client: AsyncClient, voice: Voice
):
    shop = await _shop(client, "01700000099")
    order = await _queued(client, shop, "01700000080")
    results: list[Any] = []

    async def fast_provider(call: VoiceCall) -> None:
        # The provider's next reference, sent before our transaction 2 ran,
        # without echoing our client_ref.
        ref = f"{voice.mock.ref_prefix}-call-{len(voice.mock.calls) + 1}"
        headers, body = voice.mock.callback(
            VoiceEvent(VoiceEventKind.DTMF, provider_ref=ref, digit="1")
        )
        results.append(await calls.receive_callback("mock", headers, body))

    voice.transport.before = fast_provider
    await jobs.dispatch_confirmation_calls()

    assert results[0].status_code == 200 and results[0].applied == 0
    row = await _confirmation(shop, order)
    assert (row.state, row.method) == ("CONFIRMED", "IVR")
    async with system_session("test: events") as db:
        (stored,) = (await db.scalars(sa.select(VoiceCallEvent))).all()
    assert (stored.result, stored.tenant_id) == ("APPLIED", row.tenant_id)


# ------------------------------------------------------------- guards ---


async def test_quiet_hours_hold_every_call(client: AsyncClient, voice: Voice):
    shop = await _shop(client, "01700000079")
    order = await _queued(client, shop, "01700000078")
    voice.clock.at = NIGHT
    counts = await jobs.dispatch_confirmation_calls()
    assert counts.get("quiet") == 1 and voice.mock.calls == []
    assert (await _confirmation(shop, order)).state == "CALL_QUEUED"


async def test_an_order_the_seller_moved_on_is_never_called(client: AsyncClient, voice: Voice):
    shop = await _shop(client, "01700000077")
    order = await _queued(client, shop, "01700000076")
    with _Tenant(shop):
        async with session_scope() as db:
            _links, _customers, orders = jobs._services(db)
            await orders.transition(uuid.UUID(order["id"]), OrderStatus.CONFIRMED)
    await jobs.dispatch_confirmation_calls()
    assert voice.mock.calls == []
    assert (await _confirmation(shop, order)).state == "CALL_QUEUED"


async def test_one_customer_gets_at_most_three_calls_a_day(client: AsyncClient, voice: Voice):
    shop = await _shop(client, "01700000075")
    orders = [await _queued(client, shop, "01700000074") for _ in range(4)]
    counts = await jobs.dispatch_confirmation_calls()

    assert len(voice.mock.calls) == CALLS_PER_CUSTOMER_PER_DAY == 3
    assert counts.get("CALL_LIMIT") == 1, counts
    states = [(await _confirmation(shop, order)).state for order in orders]
    assert sorted(states) == ["CALLING", "CALLING", "CALLING", "SELLER_REVIEW"]
    held = orders[states.index("SELLER_REVIEW")]
    (moved,) = await _outbox(
        shop, OutboxTopic.CONFIRMATION_REVIEW_NEEDED, await _confirmation(shop, held)
    )
    assert moved.payload["cause"] == "CALL_LIMIT"


async def test_a_call_that_never_rang_does_not_count_toward_the_cap(
    client: AsyncClient, voice: Voice
):
    shop = await _shop(client, "01700000073")
    first = await _queued(client, shop, "01700000072")
    voice.mock.script(VoiceCallOutcome.REJECTED, VoiceCallOutcome.REJECTED)
    await jobs.dispatch_confirmation_calls()
    row = await _confirmation(shop, first)
    with _Tenant(shop):
        async with session_scope() as db:
            order = await db.get(Order, row.order_id)
            assert order is not None
            assert await calls.services(db).calls_today(order.customer_id, NOON) == 0


async def test_voice_off_keeps_queued_rows_with_the_seller(
    client: AsyncClient, voice: Voice, monkeypatch: pytest.MonkeyPatch
):
    shop = await _shop(client, "01700000071")
    order = await _queued(client, shop, "01700000070")

    queue = (await client.get("/v1/confirmations/queue", headers=auth_header(shop))).json()
    assert queue["voice_available"] is True
    assert queue["items"] == []  # the IVR's row, not the seller's

    voice.mock.available = False  # the provider cannot take calls
    queue = (await client.get("/v1/confirmations/queue", headers=auth_header(shop))).json()
    assert queue["voice_available"] is False
    assert [item["order"]["id"] for item in queue["items"]] == [order["id"]]
    assert (await jobs.dispatch_confirmation_calls()).get("voice_off", 0) >= 1
    assert (await _confirmation(shop, order)).state == "CALL_QUEUED"

    voice.mock.available = True

    async def no_audio(_db: Any, _tenant_id: uuid.UUID, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(calls, "intro_audio_ref", no_audio)
    with _Tenant(shop):
        async with session_scope() as db:
            gate = await calls.voice_gate(db, uuid.UUID(shop["tenant_id"]))
    assert (gate.on, gate.reason) == (False, "NO_INTRO_AUDIO")
    assert voice.mock.calls == []


async def test_the_gate_names_each_reason_voice_is_off(client: AsyncClient, voice: Voice):
    shop = await _shop(client, "01700000066")
    plain = await signed_in_shop(client, "01700000065")  # flag not on for it

    async def gate(of: dict[str, Any], **kw: Any) -> calls.VoiceGate:
        with _Tenant(of):
            async with session_scope() as db:
                return await calls.voice_gate(db, uuid.UUID(of["tenant_id"]), **kw)

    assert (await gate(shop)).on is True
    assert (await gate(plain)).reason == "FLAG_OFF"
    disabled = calls.build_voice_transport(calls.get_settings())
    assert (await gate(shop, transport=disabled)).reason == "NOT_CONFIGURED"
    async with system_session("test: account off") as admin:
        await admin.execute(
            sa.update(VoiceProviderConnection)
            .where(VoiceProviderConnection.provider == "mock")
            .values(enabled=False)
        )
    assert (await gate(shop)).reason == "CONNECTION_OFF"


async def test_an_open_breaker_holds_calls_with_the_seller(client: AsyncClient, voice: Voice):
    shop = await _shop(client, "01700000069")
    orders = [await _queued(client, shop, f"0170000006{n}") for n in range(6)]
    voice.mock.script(*[VoiceCallOutcome.FAILED] * 5)
    await jobs.dispatch_confirmation_calls()
    states = [(await _confirmation(shop, order)).state for order in orders]
    # Five failures open the breaker; the sixth row waits, visible to the seller.
    assert states.count("SELLER_REVIEW") == 5 and states.count("CALL_QUEUED") == 1
    queue = (await client.get("/v1/confirmations/queue", headers=auth_header(shop))).json()
    assert queue["voice_available"] is False
    assert len(voice.mock.calls) == 5


# ----------------------------------------------------------- callback ---


async def test_a_callback_with_a_bad_signature_is_stored_and_refused(
    client: AsyncClient, voice: Voice
):
    shop = await _shop(client, "01700000068")
    order = await _queued(client, shop, "01700000067")
    await jobs.dispatch_confirmation_calls()
    row = await _confirmation(shop, order)

    forged = await _callback(
        client, voice, _event(voice, row, 1, VoiceEventKind.DTMF, "1"), secret="not-the-secret"
    )
    assert forged.status_code == 401 and forged.json() == {"received": True, "processed": False}
    assert (await _confirmation(shop, order)).state == "CALLING"
    async with system_session("test: deliveries") as db:
        (delivery,) = (await db.scalars(sa.select(VoiceCallbackDelivery))).all()
        assert await db.scalar(sa.select(sa.func.count(VoiceCallEvent.id))) == 0
    assert (delivery.state, delivery.reason) == ("REJECTED", "SIGNATURE_INVALID")
    assert '"digit":"1"' in delivery.raw_body

    unsigned = await client.post("/v1/webhooks/voice/mock", content=b'{"events":[]}')
    assert unsigned.status_code == 401


async def test_a_signed_but_malformed_callback_is_stored_as_malformed(
    client: AsyncClient, voice: Voice
):
    body = b'{"events": "not a list"}'
    response = await client.post(
        "/v1/webhooks/voice/mock",
        content=body,
        headers={"x-mock-voice-signature": voice.mock.sign(body)},
    )
    assert response.status_code == 400
    async with system_session("test: deliveries") as db:
        (delivery,) = (await db.scalars(sa.select(VoiceCallbackDelivery))).all()
    assert (delivery.state, delivery.reason) == ("MALFORMED", "BODY_MALFORMED")


async def test_an_inactive_provider_or_an_oversized_body_stores_nothing(
    client: AsyncClient, voice: Voice, monkeypatch: pytest.MonkeyPatch
):
    other = await client.post("/v1/webhooks/voice/othervoice", content=b"{}")
    assert other.status_code == 404 and other.json()["received"] is False
    big = await client.post(
        "/v1/webhooks/voice/mock", content=b"x" * (calls.CALLBACK_MAX_BYTES + 1)
    )
    assert big.status_code == 413
    monkeypatch.setattr(
        calls, "voice_transport", lambda: calls.build_voice_transport(calls.get_settings())
    )
    disabled = await client.post("/v1/webhooks/voice/disabled", content=b"{}")
    assert disabled.status_code == 404
    async with system_session("test: deliveries") as db:
        assert await db.scalar(sa.select(sa.func.count(VoiceCallbackDelivery.id))) == 0


async def test_an_event_for_no_call_of_ours_is_kept_unmatched(client: AsyncClient, voice: Voice):
    response = await _callback(
        client, voice, VoiceEvent(VoiceEventKind.NO_ANSWER, provider_ref="mock-call-999")
    )
    assert response.status_code == 200
    async with system_session("test: events") as db:
        (stored,) = (await db.scalars(sa.select(VoiceCallEvent))).all()
    assert (stored.result, stored.tenant_id, stored.attempt_id) == ("UNMATCHED", None, None)


def test_ivr_attempts_record_the_channel_and_statuses_the_cap_reads():
    assert AttemptChannel.IVR == "IVR"
    assert {IvrStatus.NOT_PLACED, IvrStatus.REJECTED, IvrStatus.NOT_CONFIGURED} == set(
        calls._NOT_PLACED
    )
