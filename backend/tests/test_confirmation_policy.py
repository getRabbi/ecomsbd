"""The confirmation policy (V4 WP2.2).

*   ``decide`` is pure: each of the six rules, first match wins, its
    boundaries, and the ``call_nobody`` / ``call_everyone`` overrides;
*   external data: unavailable → the external half of rule 2 and rule 5 are
    skipped; stale → rule 5 is skipped but a risky answer still counts;
*   wired in: starting a confirmation runs the policy on the shop's own rules,
    stores the decision, reason and facts, and moves the row out of PENDING.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa

from app.api.deps import get_hasher, get_vault
from app.common.outbox import OutboxEvent
from app.confirmations.models import ConfirmationDecision, OrderConfirmation
from app.confirmations.policy import (
    EXTERNAL_MIN_PARCELS,
    ExternalHistory,
    PolicyFacts,
    PolicyRules,
    ReasonCode,
    decide,
)
from app.confirmations.service import ConfirmationService
from app.consignments.models import Consignment, ConsignmentStatus
from app.core.clock import utc_now
from app.core.context import RequestContext, set_context
from app.customers.models import Customer, CustomerFlag
from app.customers.risk import RiskState
from app.customers.service import CustomerService
from app.delivery_rules.models import DEFAULTS
from app.delivery_rules.service import DeliveryRulesService
from app.orders.models import Order, OrderChannel, OrderItem, OrderStatus
from app.orders.service import OrderItemDraft, OrderService
from app.risk_providers import registry
from app.risk_providers.models import ExternalRiskLookup, RiskProviderConnection
from app.tenants.models import Tenant

D = ConfirmationDecision
R = ReasonCode

# ---------------------------------------------------------------- pure ---


def _facts(**changes: Any) -> PolicyFacts:
    """A buyer the shop does not know, COD ৳1,000, no external data."""
    base = PolicyFacts(
        customer_blocked=False,
        cod_paisa=100_000,
        own_band=RiskState.INSUFFICIENT_DATA,
        own_delivered=0,
        own_returned=0,
        external=None,
    )
    return replace(base, **changes)


def _ext(success_bps: int, parcels: int = 10, *, fresh: bool = True) -> ExternalHistory:
    return ExternalHistory(parcels=parcels, success_bps=success_bps, fresh=fresh)


RULES = PolicyRules()
TRUSTED = {"own_band": RiskState.LOW, "own_delivered": 3}


def test_the_defaults_are_the_spec_and_match_the_delivery_rules():
    assert (RULES.risk_low_bps, RULES.trusted_network_bps, RULES.trusted_repeat_min) == (
        5000,
        8500,
        2,
    )
    assert EXTERNAL_MIN_PARCELS == 5
    assert PolicyRules.from_values(DEFAULTS) == RULES


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        # 1. blocked, whatever else is true
        (_facts(customer_blocked=True, **TRUSTED), (D.SELLER_REVIEW, R.CUSTOMER_BLOCKED)),
        # 2. external risky, or own band HIGH
        (_facts(external=_ext(4999)), (D.SELLER_REVIEW, R.HIGH_RISK)),
        (_facts(own_band=RiskState.HIGH), (D.SELLER_REVIEW, R.HIGH_RISK)),
        # 3. COD above the shop's limit
        (_facts(cod_paisa=500_001), (D.CALL, R.HIGH_VALUE)),
        # 4. trusted repeat buyer
        (_facts(**TRUSTED), (D.AUTO_CONFIRM, R.TRUSTED_REPEAT)),
        # 5. trusted across couriers, no return here
        (_facts(external=_ext(8500)), (D.AUTO_CONFIRM, R.TRUSTED_NETWORK)),
        # 6. everyone else
        (_facts(), (D.CALL, R.VERIFY_NEW)),
    ],
)
def test_each_rule(facts: PolicyFacts, expected: tuple[D, R]):
    assert decide(facts, RULES) == expected


def test_the_first_matching_rule_wins():
    high_cod = 900_000
    assert decide(_facts(customer_blocked=True, own_band=RiskState.HIGH), RULES)[1] is (
        R.CUSTOMER_BLOCKED
    )
    assert decide(_facts(own_band=RiskState.HIGH, cod_paisa=high_cod), RULES)[1] is R.HIGH_RISK
    # A trusted buyer is still called about a big order.
    assert decide(_facts(cod_paisa=high_cod, **TRUSTED), RULES) == (D.CALL, R.HIGH_VALUE)
    assert decide(_facts(cod_paisa=high_cod, external=_ext(9500)), RULES)[1] is R.HIGH_VALUE
    # A bad external record outranks a good own-shop one.
    assert decide(_facts(external=_ext(2000), **TRUSTED), RULES)[1] is R.HIGH_RISK
    assert decide(_facts(external=_ext(9500), **TRUSTED), RULES)[1] is R.TRUSTED_REPEAT


def test_the_boundaries():
    assert decide(_facts(cod_paisa=500_000), RULES)[1] is R.VERIFY_NEW  # not above
    assert decide(_facts(external=_ext(5000)), RULES)[1] is R.VERIFY_NEW  # not below
    assert decide(_facts(external=_ext(8499)), RULES)[1] is R.VERIFY_NEW
    # Four parcels are not enough either way.
    assert decide(_facts(external=_ext(0, parcels=4)), RULES)[1] is R.VERIFY_NEW
    assert decide(_facts(external=_ext(10_000, parcels=4)), RULES)[1] is R.VERIFY_NEW
    assert decide(_facts(external=_ext(0, parcels=5)), RULES)[1] is R.HIGH_RISK
    # Repeat: enough deliveries *and* the LOW band.
    assert decide(_facts(own_band=RiskState.LOW, own_delivered=1), RULES)[1] is R.VERIFY_NEW
    assert decide(_facts(own_band=RiskState.MEDIUM, own_delivered=9), RULES)[1] is R.VERIFY_NEW
    assert decide(_facts(own_band=RiskState.LOW, own_delivered=2), RULES)[1] is R.TRUSTED_REPEAT


def test_the_thresholds_are_the_shops_own():
    strict = PolicyRules(
        max_auto_cod_paisa=50_000,
        risk_low_bps=7000,
        trusted_network_bps=9900,
        trusted_repeat_min=5,
    )
    assert decide(_facts(), strict) == (D.CALL, R.HIGH_VALUE)  # ৳1,000 > ৳500
    cheap = {"cod_paisa": 10_000}
    assert decide(_facts(external=_ext(6999), **cheap), strict)[1] is R.HIGH_RISK
    assert decide(_facts(external=_ext(9800), **cheap), strict)[1] is R.VERIFY_NEW
    assert decide(_facts(**TRUSTED, **cheap), strict)[1] is R.VERIFY_NEW  # 3 < 5


def test_a_return_at_this_shop_stops_network_trust():
    facts = _facts(external=_ext(9800), own_returned=1, own_band=RiskState.MEDIUM)
    assert decide(facts, RULES) == (D.CALL, R.VERIFY_NEW)


def test_without_external_data_only_the_shops_own_history_counts():
    assert decide(_facts(external=None), RULES)[1] is R.VERIFY_NEW
    assert decide(_facts(external=None, own_band=RiskState.HIGH), RULES)[1] is R.HIGH_RISK
    # A provider that knew nothing (0 parcels) is the same as no data.
    none_found = ExternalHistory(parcels=0, success_bps=None, fresh=True)
    assert decide(_facts(external=none_found), RULES)[1] is R.VERIFY_NEW


def test_an_old_answer_never_auto_confirms_but_still_warns():
    assert decide(_facts(external=_ext(9800, fresh=False)), RULES) == (D.CALL, R.VERIFY_NEW)
    assert decide(_facts(external=_ext(1000, fresh=False)), RULES) == (
        D.SELLER_REVIEW,
        R.HIGH_RISK,
    )


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (_facts(customer_blocked=True), (D.SELLER_REVIEW, R.CUSTOMER_BLOCKED)),
        (_facts(own_band=RiskState.HIGH), (D.SELLER_REVIEW, R.HIGH_RISK)),
        (_facts(cod_paisa=900_000), (D.SELLER_REVIEW, R.SHOP_MANUAL)),
        (_facts(**TRUSTED), (D.SELLER_REVIEW, R.SHOP_MANUAL)),
        (_facts(external=_ext(9000)), (D.SELLER_REVIEW, R.SHOP_MANUAL)),
        (_facts(), (D.SELLER_REVIEW, R.SHOP_MANUAL)),
    ],
)
def test_call_nobody_sends_rules_3_to_6_to_the_seller(facts: PolicyFacts, expected: Any):
    assert decide(facts, PolicyRules(call_nobody=True)) == expected


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (_facts(customer_blocked=True), (D.SELLER_REVIEW, R.CUSTOMER_BLOCKED)),
        (_facts(own_band=RiskState.HIGH), (D.SELLER_REVIEW, R.HIGH_RISK)),
        (_facts(cod_paisa=900_000), (D.CALL, R.HIGH_VALUE)),
        (_facts(**TRUSTED), (D.CALL, R.CALL_EVERYONE)),
        (_facts(external=_ext(9000)), (D.CALL, R.CALL_EVERYONE)),
        (_facts(), (D.CALL, R.VERIFY_NEW)),
    ],
)
def test_call_everyone_calls_even_trusted_buyers(facts: PolicyFacts, expected: Any):
    assert decide(facts, PolicyRules(call_everyone=True)) == expected


def test_the_stored_facts_are_counts_and_codes_only():
    stored = _facts(external=_ext(9000), **TRUSTED).as_dict()
    assert stored == {
        "customer_blocked": False,
        "cod_paisa": 100_000,
        "own_band": "LOW",
        "own_delivered": 3,
        "own_returned": 0,
        "external": {"parcels": 10, "success_bps": 9000, "fresh": True},
    }


# --------------------------------------------------------- integration ---

PROVIDER = "fake_policy_provider"


@pytest.fixture
def provider() -> Iterator[None]:
    # The policy never calls the adapter; it only has to be shipped.
    registry.register(SimpleNamespace(spec=SimpleNamespace(provider_id=PROVIDER)))  # type: ignore[arg-type]
    yield
    registry.unregister(PROVIDER)


async def _shop(db: Any, settings: Any) -> tuple[Tenant, CustomerService, ConfirmationService]:
    tenant = Tenant(name="Policy Shop")
    db.add(tenant)
    await db.flush()
    set_context(RequestContext(trace_id="test", tenant_id=tenant.id, user_id=uuid.uuid4()))
    customers = CustomerService(db, hasher=get_hasher(settings), vault=get_vault(settings))
    orders = OrderService(db, customers=customers)
    return tenant, customers, ConfirmationService(db, orders=orders)


async def _customer(customers: CustomerService, phone: str = "01700000031") -> Customer:
    customer, _ = await customers.get_or_create(phone=phone)
    return customer


async def _order(
    db: Any,
    customer: Customer | None,
    *,
    cod: int = 100_000,
    status: OrderStatus = OrderStatus.DRAFT,
) -> Order:
    order = Order(
        order_number=f"PL-{uuid.uuid4().hex[:10]}",
        client_id=uuid.uuid4(),
        customer_id=customer.id if customer is not None else None,
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


async def _delivered(db: Any, customer: Customer, count: int) -> None:
    """``count`` earlier orders of this customer, each delivered."""
    for _ in range(count):
        past = await _order(db, customer, status=OrderStatus.CONFIRMED)
        db.add(
            Consignment(
                order_id=past.id,
                provider="steadfast",
                merchant_reference=past.order_number,
                status=str(ConsignmentStatus.DELIVERED),
                cod_amount_paisa=100_000,
            )
        )
    await db.flush()


async def _lookup(
    db: Any,
    customer: Customer,
    *,
    delivered: int,
    total: int,
    fresh: bool = True,
    enabled: bool = True,
) -> None:
    now = utc_now()
    db.add(
        RiskProviderConnection(
            provider_id=PROVIDER, enabled=enabled, credentials_enc="sealed", config={}
        )
    )
    db.add(
        ExternalRiskLookup(
            customer_id=customer.id,
            provider_id=PROVIDER,
            status="FOUND",
            facts=[],
            courier_history={
                "provider_counts": [],
                "aggregate": {
                    "delivered": delivered,
                    "cancelled": total - delivered,
                    "total": total,
                },
            },
            checked_at=now - timedelta(hours=1),
            expires_at=now + (timedelta(hours=10) if fresh else -timedelta(minutes=1)),
            trigger="MANUAL",
        )
    )
    await db.flush()


async def _start(svc: ConfirmationService, order: Order) -> OrderConfirmation:
    return (await svc.start(order.id, requested_by=None)).confirmation


async def _topics(db: Any, tenant_id: uuid.UUID) -> list[str]:
    return list(
        (
            await db.scalars(
                sa.select(OutboxEvent.topic)
                .where(OutboxEvent.tenant_id == tenant_id, OutboxEvent.topic.like("confirmation.%"))
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
            )
        ).all()
    )


async def test_a_trusted_repeat_buyer_is_confirmed_without_a_call(db, settings):
    tenant, customers, svc = await _shop(db, settings)
    customer = await _customer(customers)
    await _delivered(db, customer, 3)
    order = await _order(db, customer)

    row = await _start(svc, order)

    assert (row.state, row.decision, row.reason_code, row.method) == (
        "CONFIRMED",
        "AUTO_CONFIRM",
        "TRUSTED_REPEAT",
        "AUTO_TRUSTED",
    )
    assert row.order_outcome == "APPLIED" and order.status == "CONFIRMED"
    assert row.facts is not None
    assert (row.facts["own_band"], row.facts["own_delivered"], row.facts["external"]) == (
        "LOW",
        3,
        None,
    )
    assert row.facts["rules"]["trusted_repeat_min"] == 2
    assert await _topics(db, tenant.id) == ["confirmation.confirmed"]


async def test_a_new_buyer_is_queued_for_a_call(db, settings):
    _tenant, customers, svc = await _shop(db, settings)
    order = await _order(db, await _customer(customers))

    row = await _start(svc, order)

    assert (row.state, row.decision, row.reason_code) == ("CALL_QUEUED", "CALL", "VERIFY_NEW")
    assert row.next_action_at is not None and order.status == "DRAFT"


async def test_an_order_without_a_customer_is_a_new_buyer(db, settings):
    _tenant, _customers, svc = await _shop(db, settings)
    row = await _start(svc, await _order(db, None))
    assert (row.decision, row.reason_code) == ("CALL", "VERIFY_NEW")
    assert row.facts is not None and row.facts["customer_blocked"] is False


async def test_a_blocked_buyer_goes_to_the_seller(db, settings):
    tenant, customers, svc = await _shop(db, settings)
    customer = await _customer(customers)
    await _delivered(db, customer, 3)  # trust does not outrank the seller's block
    customer.flag = str(CustomerFlag.BLOCKED)
    await db.flush()

    row = await _start(svc, await _order(db, customer))

    assert (row.state, row.decision, row.reason_code) == (
        "SELLER_REVIEW",
        "SELLER_REVIEW",
        "CUSTOMER_BLOCKED",
    )
    assert await _topics(db, tenant.id) == ["confirmation.review_needed"]


async def test_the_shops_saved_rules_are_used(db, settings):
    tenant, customers, svc = await _shop(db, settings)
    rules = DeliveryRulesService(db)
    await rules.update(tenant.id, {"max_auto_cod_paisa": 50_000})
    customer = await _customer(customers)
    await _delivered(db, customer, 3)

    big = await _start(svc, await _order(db, customer, cod=60_000))
    assert (big.state, big.reason_code) == ("CALL_QUEUED", "HIGH_VALUE")
    assert big.facts is not None and big.facts["rules"]["max_auto_cod_paisa"] == 50_000

    await rules.update(tenant.id, {"call_nobody": True})
    manual = await _start(svc, await _order(db, customer, cod=10_000))
    assert (manual.state, manual.reason_code) == ("SELLER_REVIEW", "SHOP_MANUAL")

    await rules.update(tenant.id, {"call_nobody": False, "call_everyone": True})
    everyone = await _start(svc, await _order(db, customer, cod=10_000))
    assert (everyone.state, everyone.reason_code) == ("CALL_QUEUED", "CALL_EVERYONE")


async def test_a_fresh_external_record_can_vouch_for_a_new_buyer(db, settings, provider):
    _tenant, customers, svc = await _shop(db, settings)
    customer = await _customer(customers)
    await _lookup(db, customer, delivered=9, total=10)

    row = await _start(svc, await _order(db, customer))

    assert (row.state, row.reason_code) == ("CONFIRMED", "TRUSTED_NETWORK")
    assert row.facts is not None
    assert row.facts["external"] == {"parcels": 10, "success_bps": 9000, "fresh": True}


@pytest.mark.parametrize(
    ("delivered", "fresh", "enabled", "state", "reason"),
    [
        (9, False, True, "CALL_QUEUED", "VERIFY_NEW"),  # old good news: no auto-confirm
        (1, False, True, "SELLER_REVIEW", "HIGH_RISK"),  # old bad news still warns
        (9, True, False, "CALL_QUEUED", "VERIFY_NEW"),  # provider switched off
        (1, True, False, "CALL_QUEUED", "VERIFY_NEW"),
    ],
)
async def test_external_data_that_is_old_or_switched_off(
    db, settings, provider, delivered, fresh, enabled, state, reason
):
    _tenant, customers, svc = await _shop(db, settings)
    customer = await _customer(customers)
    await _lookup(db, customer, delivered=delivered, total=10, fresh=fresh, enabled=enabled)

    row = await _start(svc, await _order(db, customer))

    assert (row.state, row.reason_code) == (state, reason)
    assert row.facts is not None and (row.facts["external"] is None) is (not enabled)


async def test_an_unshipped_provider_is_unavailable(db, settings):
    _tenant, customers, svc = await _shop(db, settings)
    customer = await _customer(customers)
    await _lookup(db, customer, delivered=9, total=10)  # no adapter registered

    row = await _start(svc, await _order(db, customer))

    assert (row.reason_code, row.facts and row.facts["external"]) == ("VERIFY_NEW", None)


async def test_start_decides_once(db, settings):
    _tenant, customers, svc = await _shop(db, settings)
    order = await _order(db, await _customer(customers))

    bare = (await svc.request(order.id, requested_by=None)).confirmation
    assert (bare.state, bare.decision, bare.facts) == ("PENDING", None, None)

    first = await svc.start(order.id, requested_by=None)
    again = await svc.start(order.id, requested_by=None)
    assert first.confirmation.id == again.confirmation.id == bare.id
    assert not again.created and again.confirmation.state == "CALL_QUEUED"
    count = await db.scalar(
        sa.select(sa.func.count())
        .select_from(OrderConfirmation)
        .where(OrderConfirmation.order_id == order.id)
    )
    assert count == 1


async def test_a_restart_after_a_material_edit_is_decided_again(db, settings):
    tenant, customers, svc = await _shop(db, settings)
    await DeliveryRulesService(db).update(tenant.id, {"max_auto_cod_paisa": 150_000})
    customer = await _customer(customers)
    await _delivered(db, customer, 3)
    order = await _order(db, customer, cod=200_000)
    first = await _start(svc, order)
    assert first.reason_code == "HIGH_VALUE"

    orders = OrderService(
        db, customers=CustomerService(db, hasher=get_hasher(settings), vault=get_vault(settings))
    )
    await orders.update(
        order.id,
        items=[OrderItemDraft(name="Scarf", unit_price_paisa=50_000)],
        cod_amount_paisa=50_000,
    )

    second = await db.get(OrderConfirmation, first.superseded_by)
    assert second is not None
    assert (second.state, second.reason_code) == ("CONFIRMED", "TRUSTED_REPEAT")
    assert second.facts is not None and second.facts["cod_paisa"] == order.cod_amount_paisa
    assert order.status == "CONFIRMED"
