"""Customer confirmations (V4 WP2.1).

*   the state machine is exactly the spec's table; every other move is refused;
*   one open confirmation per order, enforced by the database;
*   a customer's yes moves a ``DRAFT`` order with the system actor, but only at
    the version they were asked about; otherwise it is recorded and the seller
    is alerted;
*   a customer's no cancels the order (``CUSTOMER_DECLINED``) and flags nobody;
*   a material edit supersedes the open confirmation and starts a new one; an
    order cancel ends it;
*   every confirmation topic has a handler.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_hasher, get_vault
from app.common.audit import AuditAction, AuditLog
from app.common.outbox import OutboxEvent, OutboxTopic
from app.confirmations.models import (
    OPEN_STATES,
    AttemptChannel,
    ConfirmationAttempt,
    ConfirmationMethod,
    ConfirmationState,
    OrderConfirmation,
)
from app.confirmations.service import (
    CUSTOMER_CONFIRMATION_ACTOR,
    CUSTOMER_DECLINED,
    MAX_CALL_ATTEMPTS,
    RETRY_AFTER,
    ConfirmationService,
    is_material_edit,
)
from app.confirmations.state_machine import (
    TRANSITIONS,
    Cause,
    ConfirmationStateMachine,
)
from app.core.clock import utc_now
from app.core.context import RequestContext, clear_context, set_context
from app.core.errors import ConflictError, ValidationError
from app.customers.service import CustomerService
from app.db.session import session_scope
from app.notifications.models import Notification, NotificationKind
from app.notifications.templates import render
from app.orders.models import Order, OrderChannel, OrderItem, OrderStatus
from app.orders.service import OrderItemDraft, OrderService
from app.tenants.models import Tenant
from tests.conftest_commerce import create_order, signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_intake_capture import intake_on

__all__ = ["intake_on"]  # fixture used by name below

S = ConfirmationState
C = Cause


# ------------------------------------------------------- state machine ---


def test_the_transition_table_is_exactly_the_spec():
    expected: dict[tuple[S, S], set[C]] = {
        (S.PENDING, S.CONFIRMED): {C.AUTO_TRUSTED},
        (S.PENDING, S.CALL_QUEUED): {C.DECIDED_CALL},
        (S.PENDING, S.SELLER_REVIEW): {C.DECIDED_REVIEW},
        (S.CALL_QUEUED, S.CALLING): {C.DIALING},
        # WP3.2 adds CALL_LIMIT (per-customer daily cap) and CALL_FAILED
        # (certainly not placed) on moves the spec already has.
        (S.CALL_QUEUED, S.SELLER_REVIEW): {C.VOICE_UNAVAILABLE, C.CALL_LIMIT, C.NO_VOICE_CREDIT},
        (S.CALLING, S.CONFIRMED): {C.DTMF_CONFIRM},
        (S.CALLING, S.CANCELLED): {C.DTMF_CANCEL},
        (S.CALLING, S.SELLER_REVIEW): {C.DTMF_CALLBACK, C.CALL_UNKNOWN, C.CALL_FAILED},
        (S.CALLING, S.NO_ANSWER): {C.CALL_NO_ANSWER},
        (S.NO_ANSWER, S.CALL_QUEUED): {C.RETRY},
        (S.NO_ANSWER, S.SMS_LINK_SENT): {C.LINK_SENT},
        (S.NO_ANSWER, S.SELLER_REVIEW): {C.ATTEMPTS_EXHAUSTED},
        (S.SMS_LINK_SENT, S.CONFIRMED): {C.LINK_CONFIRM},
        (S.SMS_LINK_SENT, S.CANCELLED): {C.LINK_CANCEL},
        (S.SMS_LINK_SENT, S.SELLER_REVIEW): {C.LINK_EXPIRED},
        (S.SELLER_REVIEW, S.CONFIRMED): {C.SELLER_CONFIRM},
        (S.SELLER_REVIEW, S.CANCELLED): {C.SELLER_CANCEL},
        # Operator decision 2026-11-20: the seller's "Dhoreni" sends the link.
        (S.SELLER_REVIEW, S.SMS_LINK_SENT): {C.SELLER_NO_ANSWER},
    }
    for state in OPEN_STATES:
        expected.setdefault((state, S.SUPERSEDED), set()).add(C.ORDER_EDITED)
        expected.setdefault((state, S.CANCELLED), set()).add(C.ORDER_CANCELLED)
    assert {pair: set(causes) for pair, causes in TRANSITIONS.items()} == expected


def test_every_illegal_transition_is_refused():
    checked = 0
    for current, target, cause in itertools.product(S, S, C):
        legal = cause in TRANSITIONS.get((current, target), frozenset())
        assert ConfirmationStateMachine.is_allowed(current, target, cause) is legal
        if legal:
            ConfirmationStateMachine.check(current, target, cause)
            continue
        with pytest.raises(ConflictError) as refused:
            ConfirmationStateMachine.check(current, target, cause)
        assert refused.value.details["code"] == "CONFIRMATION_TRANSITION_ILLEGAL"
        checked += 1
    assert checked == len(S) ** 2 * len(C) - sum(len(c) for c in TRANSITIONS.values())
    for state in (S.CONFIRMED, S.CANCELLED, S.SUPERSEDED):
        assert ConfirmationStateMachine.targets(state) == frozenset()


def test_each_terminal_cause_records_its_own_method():
    method = ConfirmationStateMachine.method_for
    assert method(C.AUTO_TRUSTED) is ConfirmationMethod.AUTO_TRUSTED
    assert method(C.DTMF_CONFIRM) is method(C.DTMF_CANCEL) is ConfirmationMethod.IVR
    assert method(C.LINK_CONFIRM) is method(C.LINK_CANCEL) is ConfirmationMethod.SMS_LINK
    assert method(C.SELLER_CONFIRM) is ConfirmationMethod.SELLER
    assert method(C.SELLER_CANCEL, ConfirmationMethod.MANUAL_CALL) is ConfirmationMethod.MANUAL_CALL
    assert method(C.ORDER_CANCELLED) is None and method(C.ORDER_EDITED) is None
    # The seller cannot claim the customer pressed 1.
    with pytest.raises(ValidationError):
        method(C.SELLER_CONFIRM, ConfirmationMethod.IVR)
    with pytest.raises(ValidationError):
        method(C.DTMF_CONFIRM, ConfirmationMethod.SELLER)


# ------------------------------------------------------------ fixtures ---


async def _shop(db: Any, settings: Any) -> tuple[Tenant, OrderService, ConfirmationService]:
    tenant = Tenant(name="Confirmation Shop")
    db.add(tenant)
    await db.flush()
    set_context(RequestContext(trace_id="test", tenant_id=tenant.id, user_id=uuid.uuid4()))
    customers = CustomerService(db, hasher=get_hasher(settings), vault=get_vault(settings))
    orders = OrderService(db, customers=customers)
    return tenant, orders, ConfirmationService(db, orders=orders)


async def _order(db: Any, *, cod: int = 100_000, status: OrderStatus = OrderStatus.DRAFT) -> Order:
    order = Order(
        order_number=f"CF-{uuid.uuid4().hex[:10]}",
        client_id=uuid.uuid4(),
        status=str(status),
        channel=str(OrderChannel.MANUAL),
        business_date=utc_now().date(),
        subtotal_paisa=cod,
        cod_amount_paisa=cod,
        items=[OrderItem(product_name="Black Abaya", quantity=1, unit_price_paisa=cod)],
    )
    db.add(order)
    await db.flush()
    return order


async def _calling(svc: ConfirmationService, order: Order) -> OrderConfirmation:
    row = (await svc.request(order.id, requested_by=None)).confirmation
    await svc.transition(row, S.CALL_QUEUED, C.DECIDED_CALL)
    await svc.add_attempt(row, AttemptChannel.IVR, provider="fake")
    await svc.transition(row, S.CALLING, C.DIALING)
    return row


async def _events(db: Any, tenant_id: uuid.UUID) -> list[OutboxEvent]:
    return list(
        (
            await db.scalars(
                sa.select(OutboxEvent)
                .where(OutboxEvent.tenant_id == tenant_id, OutboxEvent.topic.like("confirmation.%"))
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
            )
        ).all()
    )


# ------------------------------------------------------------- service ---


async def test_a_request_starts_one_confirmation_per_draft_order(db, settings):
    _tenant, _orders, svc = await _shop(db, settings)
    order = await _order(db, cod=150_000)

    first = await svc.request(order.id, requested_by=None)
    again = await svc.request(order.id, requested_by=None)

    assert first.created and not again.created
    assert again.confirmation.id == first.confirmation.id
    row = first.confirmation
    assert (row.state, row.order_version, row.cod_paisa_snapshot) == ("PENDING", 1, 150_000)
    assert row.decision is None and row.method is None and row.attempt_count == 0

    confirmed = await _order(db, status=OrderStatus.CONFIRMED)
    with pytest.raises(ConflictError) as refused:
        await svc.request(confirmed.id, requested_by=None)
    assert refused.value.details["code"] == "ORDER_NOT_DRAFT"


async def test_a_customer_yes_confirms_the_draft_order_as_the_system(db, settings):
    tenant, _orders, svc = await _shop(db, settings)
    order = await _order(db)
    row = await _calling(svc, order)

    await svc.transition(row, S.CONFIRMED, C.DTMF_CONFIRM)

    assert (row.state, row.method, row.order_outcome) == ("CONFIRMED", "IVR", "APPLIED")
    assert row.resolved_at is not None and row.resolved_by is None
    await db.refresh(order)
    assert order.status == "CONFIRMED"
    audit = await db.scalar(
        sa.select(AuditLog).where(
            AuditLog.entity_id == str(order.id),
            AuditLog.action == str(AuditAction.ORDER_STATUS_CHANGED),
        )
    )
    assert audit is not None and audit.actor_type == "SYSTEM"
    assert audit.context["actor"] == CUSTOMER_CONFIRMATION_ACTOR
    events = await _events(db, tenant.id)
    assert [event.topic for event in events] == ["confirmation.confirmed"]
    payload = events[0].payload
    assert payload["order_outcome"] == "APPLIED" and payload["cause"] == "DTMF_CONFIRM"
    # Ids and codes only.
    assert set(payload) == {
        "confirmation_id",
        "order_id",
        "state",
        "cause",
        "method",
        "decision",
        "reason_code",
        "order_outcome",
        "attempt_count",
    }
    attempt = await db.scalar(
        sa.select(ConfirmationAttempt).where(ConfirmationAttempt.confirmation_id == row.id)
    )
    assert attempt is not None and (attempt.attempt_no, attempt.channel) == (1, "IVR")


async def test_a_yes_after_an_edit_is_recorded_but_does_not_move_the_order(db, settings):
    tenant, orders, svc = await _shop(db, settings)
    order = await _order(db)
    row = (await svc.request(order.id, requested_by=None)).confirmation

    await orders.update(order.id, note="gift wrap")  # not material: same confirmation
    assert row.state == "PENDING"
    await svc.transition(row, S.CONFIRMED, C.AUTO_TRUSTED)

    assert row.order_outcome == "ORDER_CHANGED" and row.method == "AUTO_TRUSTED"
    await db.refresh(order)
    assert order.status == "DRAFT"
    (event,) = await _events(db, tenant.id)
    assert event.payload["order_outcome"] == "ORDER_CHANGED"


async def test_a_no_cancels_the_draft_order_and_flags_nobody(db, settings):
    tenant, _orders, svc = await _shop(db, settings)
    order = await _order(db)
    row = await _calling(svc, order)

    await svc.transition(row, S.CANCELLED, C.DTMF_CANCEL)

    await db.refresh(order)
    assert (order.status, order.cancellation_reason) == ("CANCELLED", CUSTOMER_DECLINED)
    assert (row.method, row.order_outcome) == ("IVR", "APPLIED")
    assert [event.topic for event in await _events(db, tenant.id)] == ["confirmation.cancelled"]
    # The confirmation ended before the order was cancelled: no second move.
    assert row.state == "CANCELLED"


async def test_an_answer_after_the_order_moved_on_is_left_to_the_seller(db, settings):
    _tenant, orders, svc = await _shop(db, settings)
    order = await _order(db)
    row = await _calling(svc, order)
    await orders.transition(order.id, OrderStatus.CONFIRMED)  # the seller confirmed it

    await svc.transition(row, S.CANCELLED, C.DTMF_CANCEL)

    await db.refresh(order)
    assert order.status == "CONFIRMED"
    assert row.order_outcome == "ORDER_NOT_DRAFT"


async def test_seller_review_confirm_moves_the_order_as_the_seller(db, settings):
    _tenant, _orders, svc = await _shop(db, settings)
    order = await _order(db)
    row = (await svc.request(order.id, requested_by=None)).confirmation
    seller = uuid.uuid4()
    await svc.transition(row, S.SELLER_REVIEW, C.DECIDED_REVIEW)

    await svc.transition(
        row,
        S.CONFIRMED,
        C.SELLER_CONFIRM,
        actor_id=seller,
        seller_method=ConfirmationMethod.MANUAL_CALL,
    )

    assert (row.method, row.resolved_by, row.order_outcome) == ("MANUAL_CALL", seller, "APPLIED")
    audit = await db.scalar(
        sa.select(AuditLog).where(
            AuditLog.entity_id == str(order.id),
            AuditLog.action == str(AuditAction.ORDER_STATUS_CHANGED),
        )
    )
    assert audit is not None and audit.actor_id == seller and "actor" not in audit.context


async def test_already_confirmed_in_chat_is_born_confirmed(db, settings):
    _tenant, _orders, svc = await _shop(db, settings)
    order = await _order(db)
    seller = uuid.uuid4()

    done = await svc.resolve_in_chat(order.id, actor_id=seller)
    again = await svc.resolve_in_chat(order.id, actor_id=seller)

    row = done.confirmation
    assert done.created and not again.created and again.confirmation.id == row.id
    assert (row.state, row.method, row.resolved_by) == ("CONFIRMED", "SELLER_CHAT", seller)
    await db.refresh(order)
    assert order.status == "CONFIRMED"
    # A later "call the customer" finds the finished confirmation, not a new one.
    assert (await svc.request(order.id, requested_by=None)).confirmation.id == row.id

    running = await _order(db)
    await svc.request(running.id, requested_by=None)
    with pytest.raises(ConflictError) as refused:
        await svc.resolve_in_chat(running.id, actor_id=seller)
    assert refused.value.details["code"] == "CONFIRMATION_IN_PROGRESS"


async def test_no_answer_waits_before_a_retry_and_stops_at_the_call_limit(db, settings):
    tenant, _orders, svc = await _shop(db, settings)
    order = await _order(db)
    row = await _calling(svc, order)
    start = utc_now()

    await svc.transition(row, S.NO_ANSWER, C.CALL_NO_ANSWER, now=start)
    assert row.next_action_at == start + RETRY_AFTER
    with pytest.raises(ConflictError) as early:
        await svc.transition(row, S.CALL_QUEUED, C.RETRY, now=start + timedelta(minutes=5))
    assert early.value.details["code"] == "CONFIRMATION_RETRY_TOO_EARLY"

    await svc.transition(row, S.CALL_QUEUED, C.RETRY, now=start + RETRY_AFTER)
    await svc.add_attempt(row, AttemptChannel.IVR, provider="fake")
    await svc.transition(row, S.CALLING, C.DIALING)
    later = start + RETRY_AFTER * 2
    await svc.transition(row, S.NO_ANSWER, C.CALL_NO_ANSWER, now=later)
    assert row.attempt_count == MAX_CALL_ATTEMPTS
    with pytest.raises(ConflictError) as spent:
        await svc.transition(row, S.CALL_QUEUED, C.RETRY, now=later + RETRY_AFTER)
    assert spent.value.details["code"] == "CONFIRMATION_ATTEMPTS_EXHAUSTED"

    await svc.transition(row, S.SELLER_REVIEW, C.ATTEMPTS_EXHAUSTED)
    topics = sorted(event.topic for event in await _events(db, tenant.id))
    # One no_answer event per unanswered call.
    assert topics == [
        "confirmation.no_answer",
        "confirmation.no_answer",
        "confirmation.review_needed",
    ]


async def test_a_callback_request_goes_to_the_seller(db, settings):
    tenant, _orders, svc = await _shop(db, settings)
    row = await _calling(svc, await _order(db))

    await svc.transition(row, S.SELLER_REVIEW, C.DTMF_CALLBACK)

    assert row.state == "SELLER_REVIEW" and row.next_action_at is None
    assert sorted(event.topic for event in await _events(db, tenant.id)) == [
        "confirmation.callback_requested",
        "confirmation.review_needed",
    ]


async def test_a_material_edit_supersedes_and_starts_again(db, settings):
    _tenant, orders, svc = await _shop(db, settings)
    order = await _order(db, cod=100_000)
    first = (await svc.request(order.id, requested_by=None)).confirmation
    await svc.transition(first, S.CALL_QUEUED, C.DECIDED_CALL)

    await orders.update(
        order.id, items=[OrderItemDraft(name="Red Abaya", unit_price_paisa=100_000)]
    )

    assert first.state == "SUPERSEDED" and first.method is None
    second = await db.get(OrderConfirmation, first.superseded_by)
    # The restart runs the policy (WP2.2): a buyer the shop does not know is called.
    assert second is not None
    assert (second.state, second.decision, second.reason_code) == (
        "CALL_QUEUED",
        "CALL",
        "VERIFY_NEW",
    )
    assert second.order_version == order.version and second.cod_paisa_snapshot == 100_000

    # The same items again is not a change.
    await orders.update(
        order.id, items=[OrderItemDraft(name="Red Abaya", unit_price_paisa=100_000)]
    )
    assert second.state == "CALL_QUEUED"


async def test_cod_moves_past_ten_percent_of_the_snapshot_are_material(db, settings):
    _tenant, orders, svc = await _shop(db, settings)
    order = await _order(db, cod=100_000)
    row = (await svc.request(order.id, requested_by=None)).confirmation

    await orders.update(order.id, cod_amount_paisa=110_000)  # exactly 10%: same order
    assert row.state == "PENDING"
    await orders.update(order.id, cod_amount_paisa=94_000)  # 6% from the snapshot
    assert row.state == "PENDING"
    await orders.update(order.id, cod_amount_paisa=89_999)  # > 10% below the snapshot
    assert row.state == "SUPERSEDED"

    zero = OrderConfirmation(order_version=1, cod_paisa_snapshot=0)
    order.cod_amount_paisa = 1
    assert is_material_edit(zero, order, items_changed=False)
    order.cod_amount_paisa = 0
    assert not is_material_edit(zero, order, items_changed=False)


async def test_cancelling_the_order_ends_its_confirmation(db, settings):
    tenant, orders, svc = await _shop(db, settings)
    order = await _order(db)
    row = (await svc.request(order.id, requested_by=None)).confirmation

    await orders.transition(order.id, OrderStatus.CANCELLED, reason="out of stock")

    assert (row.state, row.method, row.order_outcome) == ("CANCELLED", None, None)
    (event,) = await _events(db, tenant.id)
    assert event.topic == "confirmation.cancelled"
    assert event.payload["cause"] == "ORDER_CANCELLED"
    await db.refresh(order)
    assert order.cancellation_reason == "out of stock"


async def test_one_open_confirmation_per_order_is_a_database_rule(db, settings):
    _tenant, _orders, svc = await _shop(db, settings)
    order = await _order(db)
    first = (await svc.request(order.id, requested_by=None)).confirmation
    await svc.transition(first, S.SELLER_REVIEW, C.DECIDED_REVIEW)
    await svc.transition(first, S.CANCELLED, C.SELLER_CANCEL)
    # A finished one does not count: another may start.
    db.add(OrderConfirmation(order_id=order.id, order_version=1, cod_paisa_snapshot=0))
    await db.flush()

    db.add(OrderConfirmation(order_id=order.id, order_version=1, cod_paisa_snapshot=0))
    with pytest.raises(IntegrityError):
        await db.flush()


# ------------------------------------------------------------- handlers ---


def test_every_confirmation_topic_has_a_handler():
    import app.confirmations.jobs  # noqa: F401  (registers the handlers, as the worker does)
    from app.worker.jobs import HANDLERS

    for topic in OutboxTopic:
        if str(topic).startswith("confirmation."):
            assert str(topic) in HANDLERS, topic


@pytest.mark.parametrize("locale", ["en", "bn"])
def test_the_attention_alert_says_why_without_customer_details(locale: str):
    seen = set()
    for reason in ("REVIEW_NEEDED", "ORDER_CHANGED", "ORDER_NOT_DRAFT"):
        rendered = render(NotificationKind.CONFIRMATION_NEEDS_ATTENTION, {"reason": reason}, locale)
        assert rendered is not None and all(rendered)
        seen.add(rendered)
    assert len(seen) == 3


async def test_the_seller_is_alerted_once_when_review_is_needed_or_an_answer_is_not_applied(
    client: AsyncClient, unique_phone: str, settings: Any
):
    import app.confirmations.jobs  # noqa: F401
    from app.worker.jobs import HANDLERS

    shop = await signed_in_shop(client, unique_phone)
    tenant_id = uuid.UUID(shop["tenant_id"])
    token = set_context(RequestContext(trace_id="test", tenant_id=tenant_id))
    try:
        async with session_scope() as db:
            customers = CustomerService(db, hasher=get_hasher(settings), vault=get_vault(settings))
            orders = OrderService(db, customers=customers)
            svc = ConfirmationService(db, orders=orders)
            review = (await svc.request((await _order(db)).id, requested_by=None)).confirmation
            await svc.transition(review, S.SELLER_REVIEW, C.DECIDED_REVIEW)
            changed_order = await _order(db)
            changed = (await svc.request(changed_order.id, requested_by=None)).confirmation
            await orders.update(changed_order.id, note="edited")
            await svc.transition(changed, S.CONFIRMED, C.AUTO_TRUSTED)
            applied = (await svc.request((await _order(db)).id, requested_by=None)).confirmation
            await svc.transition(applied, S.CONFIRMED, C.AUTO_TRUSTED)
            events = await _events(db, tenant_id)
        assert sorted(event.topic for event in events) == [
            "confirmation.confirmed",
            "confirmation.confirmed",
            "confirmation.review_needed",
        ]
        for _ in range(2):  # a retried event alerts once
            for event in events:
                async with session_scope() as db:
                    await HANDLERS[event.topic](db, event)
        async with session_scope() as db:
            alerts = (
                await db.scalars(
                    sa.select(Notification).where(
                        Notification.kind == str(NotificationKind.CONFIRMATION_NEEDS_ATTENTION)
                    )
                )
            ).all()
        assert sorted(alert.payload["params"]["reason"] for alert in alerts) == [
            "ORDER_CHANGED",
            "REVIEW_NEEDED",
        ]
        assert {alert.entity_type for alert in alerts} == {"order"}
        assert {alert.entity_id for alert in alerts} == {review.order_id, changed_order.id}
    finally:
        clear_context(token)


# ------------------------------------------------------------------ API ---


@pytest.fixture
async def confirmation_on(settings: Any) -> AsyncIterator[None]:
    async for _ in _flag(settings):
        yield


async def _new_order(client: AsyncClient, shop: dict[str, Any], phone: str) -> dict[str, Any]:
    return (await create_order(client, shop, phone=phone))["order"]


def _url(order: dict[str, Any]) -> str:
    return f"/v1/orders/{order['id']}/confirmation"


async def test_the_api_is_off_until_the_flag_is_on(client: AsyncClient, unique_phone: str):
    shop = await signed_in_shop(client, unique_phone)
    order = await _new_order(client, shop, "01700000021")

    refused = await client.post(_url(order), json={"mode": "REQUEST"}, headers=auth_header(shop))
    assert refused.status_code == 403 and refused.json()["code"] == "FEATURE_DISABLED"
    shown = await client.get(_url(order), headers=auth_header(shop))
    assert shown.status_code == 200
    assert shown.json() == {"enabled": False, "confirmation": None}


async def test_request_and_already_confirmed_in_chat_through_the_api(
    client: AsyncClient, unique_phone: str, confirmation_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    order = await _new_order(client, shop, "01700000022")

    first = await client.post(_url(order), json={"mode": "REQUEST"}, headers=headers)
    again = await client.post(_url(order), json={"mode": "REQUEST"}, headers=headers)
    assert first.status_code == again.status_code == 200, first.text
    assert first.json()["created"] and not again.json()["created"]
    row = first.json()["confirmation"]
    assert again.json()["confirmation"]["id"] == row["id"]
    assert (row["state"], row["order_id"]) == ("CALL_QUEUED", order["id"])
    assert (row["decision"], row["reason_code"]) == ("CALL", "VERIFY_NEW")
    assert row["facts"]["own_band"] == "INSUFFICIENT_DATA" and row["facts"]["external"] is None
    shown = (await client.get(_url(order), headers=headers)).json()
    assert shown["enabled"] and shown["confirmation"]["id"] == row["id"]

    chat = await _new_order(client, shop, "01700000023")
    done = await client.post(_url(chat), json={"mode": "SELLER_CHAT"}, headers=headers)
    assert done.status_code == 200, done.text
    body = done.json()["confirmation"]
    assert (body["state"], body["method"], body["order_outcome"]) == (
        "CONFIRMED",
        "SELLER_CHAT",
        "APPLIED",
    )
    detail = await client.get(f"/v1/orders/{chat['id']}", headers=headers)
    assert detail.json()["status"] == "CONFIRMED"

    busy = await client.post(_url(order), json={"mode": "SELLER_CHAT"}, headers=headers)
    assert busy.status_code == 409 and busy.json()["details"]["code"] == "CONFIRMATION_IN_PROGRESS"
    bad = await client.post(_url(order), json={"mode": "CALL"}, headers=headers)
    assert bad.status_code == 422


async def test_another_shops_order_is_not_found(
    client: AsyncClient, unique_phone: str, confirmation_on: None
):
    owner = await signed_in_shop(client, unique_phone)
    order = await _new_order(client, owner, "01700000024")
    other = await signed_in_shop(client, "018" + unique_phone[3:], shop_name="Other")

    for response in (
        await client.post(_url(order), json={"mode": "REQUEST"}, headers=auth_header(other)),
        await client.get(_url(order), headers=auth_header(other)),
    ):
        assert response.status_code == 404


async def test_the_quick_order_reply_says_whether_confirmation_is_on(
    client: AsyncClient, unique_phone: str, intake_on: None, settings: Any
):
    from tests.test_intake_capture import CHAT, MANUAL

    shop = await signed_in_shop(client, unique_phone)

    async def paste() -> dict[str, Any]:
        response = await client.post(
            MANUAL,
            json={"source": "SHARE", "messages": [{"text": "\n".join(CHAT)}]},
            headers=auth_header(shop),
        )
        assert response.status_code == 200, response.text
        return response.json()

    assert (await paste())["confirmation_enabled"] is False
    async for _ in _flag(settings):
        assert (await paste())["confirmation_enabled"] is True


async def _flag(settings: Any) -> AsyncIterator[None]:
    from app.common.feature_flags import FlagKey
    from tests.conftest import _global_flag_on

    async for _ in _global_flag_on(settings, str(FlagKey.CONFIRMATION_ENGINE_ENABLED)):
        yield


# --------------------------------------------- the quick-order sheet's buttons ---


async def _draft(client: AsyncClient, shop: dict[str, Any]) -> dict[str, Any]:
    from tests.test_intake_capture import CHAT, MANUAL

    response = await client.post(
        MANUAL,
        json={"source": "SHARE", "messages": [{"text": "\n".join(CHAT)}]},
        headers=auth_header(shop),
    )
    assert response.status_code == 200, response.text
    return response.json()["draft"]


_SHEET_ORDER = {"phone": "01700000011", "items": [{"name": "Panjabi", "quantity": 2}]}


@pytest.mark.parametrize(
    ("mode", "state", "order_status"),
    [("REQUEST", "CALL_QUEUED", "DRAFT"), ("SELLER_CHAT", "CONFIRMED", "CONFIRMED")],
)
async def test_the_sheet_creates_the_order_and_its_confirmation_in_one_call(
    client: AsyncClient,
    unique_phone: str,
    intake_on: None,
    confirmation_on: None,
    mode: str,
    state: str,
    order_status: str,
):
    shop = await signed_in_shop(client, unique_phone)
    draft = await _draft(client, shop)
    url = f"/v1/chat-orders/{draft['id']}/confirm"

    first = await client.post(
        url, json={**_SHEET_ORDER, "confirmation": mode}, headers=auth_header(shop)
    )
    again = await client.post(
        url, json={**_SHEET_ORDER, "confirmation": mode}, headers=auth_header(shop)
    )

    assert first.status_code == again.status_code == 200, first.text
    body = first.json()
    assert body["confirmation"]["state"] == state and body["order"]["status"] == order_status
    assert (
        again.json()["replayed"]
        and again.json()["confirmation"]["id"] == body["confirmation"]["id"]
    )


async def test_without_the_flag_the_sheet_makes_a_plain_order(
    client: AsyncClient, unique_phone: str, intake_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    draft = await _draft(client, shop)
    response = await client.post(
        f"/v1/chat-orders/{draft['id']}/confirm",
        json={**_SHEET_ORDER, "confirmation": "REQUEST"},
        headers=auth_header(shop),
    )
    assert response.status_code == 200, response.text
    assert response.json()["confirmation"] is None
    assert response.json()["order"]["status"] == "DRAFT"


async def test_a_queued_create_carries_its_confirmation_request(
    client: AsyncClient, unique_phone: str, intake_on: None, confirmation_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    draft = await _draft(client, shop)
    response = await client.post(
        "/v1/sync/mutations",
        json={
            "mutations": [
                {
                    "mutation_id": str(uuid.uuid4()),
                    "entity_type": "ORDER",
                    "entity_id": draft["id"],
                    "operation": "CREATE",
                    "payload": {**_SHEET_ORDER, "confirmation": "REQUEST"},
                }
            ]
        },
        headers=auth_header(shop),
    )
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["status"] == "APPLIED"
    shown = await client.get(
        f"/v1/orders/{result['entity_id']}/confirmation", headers=auth_header(shop)
    )
    assert shown.json()["confirmation"]["state"] == "CALL_QUEUED"


async def test_a_plain_order_create_can_ask_for_a_confirmation(
    client: AsyncClient, unique_phone: str, confirmation_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    order = (await create_order(client, shop, phone="01700000025", confirmation="REQUEST"))["order"]
    shown = await client.get(_url(order), headers=auth_header(shop))
    assert shown.json()["confirmation"]["state"] == "CALL_QUEUED"


def test_an_order_source_cannot_claim_a_confirmation():
    from pydantic import ValidationError as SchemaError

    from app.order_sources.service import NativeOrder

    order = {"phone": "01700000026", "items": [{"name": "Panjabi", "quantity": 1}]}
    assert NativeOrder.model_validate(order).confirmation is None
    for mode in ("REQUEST", "SELLER_CHAT"):
        with pytest.raises(SchemaError):
            NativeOrder.model_validate({**order, "confirmation": mode})
