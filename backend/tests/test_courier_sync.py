"""Status polling, returns, payment ingestion and the webhook receiver.

The claims under test:

* an approval-pending status moves the parcel's visible state and **no money**;
* a partial delivery flags the parcel for a person and infers no quantity;
* an undocumented status is stored, signalled and settles nothing;
* a return is never requested twice, including while one is unconfirmed;
* a payment imported twice produces one payout, and a payment that changed
  after import is flagged rather than rewritten;
* an unexplained deduction stays an unexplained deduction;
* the webhook endpoint stores what it receives and verifies nothing, because
  there is nothing to verify against.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.consignments.models import Consignment, ConsignmentStatus
from app.consignments.service import ConsignmentService
from app.core.context import RequestContext, set_context
from app.core.errors import ConflictError
from app.core.security import CredentialVault
from app.couriers.accounts import ConnectRequest, CourierAccountService
from app.couriers.booking import CourierBookingService
from app.couriers.metrics import CourierMetric, metrics_snapshot
from app.couriers.models import (
    CourierBookingAttempt,
    CourierEvent,
    CourierWebhookDelivery,
    PaymentSyncState,
    ProviderPayment,
    ReturnRequestState,
    WebhookDeliveryState,
)
from app.couriers.payments import PaymentSyncService
from app.couriers.registry import CourierAdapterRegistry
from app.couriers.returns import CourierReturnService
from app.couriers.status_sync import StatusSyncService, next_poll_at
from app.couriers.steadfast.adapter import SteadfastAdapter
from app.couriers.steadfast.client import SteadfastClient, SteadfastConfig
from app.couriers.steadfast.transport import FakeSteadfastTransport
from app.couriers.webhooks import (
    WEBHOOK_BLOCKER,
    SteadfastWebhookParser,
    SteadfastWebhookVerifier,
    WebhookReceiver,
)
from app.money.models import CodReceivable, ReceivableStatus
from app.money.service import ReceivableService
from app.payouts.models import AdjustmentType, Payout, PayoutAdjustment, PayoutLine
from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.fixtures.steadfast import bodies

# Each test here is about a courier a shop may use, so the kill switches are on.
pytestmark = pytest.mark.usefixtures("courier_flags_on")

API_KEY = "sfk-sync-test-key-abcd"
SECRET_KEY = "sfs-sync-test-secret-wxyz"


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
def transport() -> FakeSteadfastTransport:
    return FakeSteadfastTransport()


@pytest.fixture
def registry(transport: FakeSteadfastTransport) -> CourierAdapterRegistry:
    def _factory() -> SteadfastAdapter:
        return SteadfastAdapter(
            SteadfastClient(
                transport,
                config=SteadfastConfig(bulk_chunk_size=5, max_read_retries=0),
                sleep=_no_sleep,
            )
        )

    return CourierAdapterRegistry({"steadfast": _factory})


@pytest.fixture
async def shop(client: AsyncClient, unique_phone: str) -> dict[str, Any]:
    session = await signed_in_shop(client, unique_phone, shop_name="Sync Shop")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
    return session


@pytest.fixture
async def accounts(db, settings, registry, transport, shop) -> CourierAccountService:
    service = CourierAccountService(db, vault=CredentialVault(settings), registry=registry)
    transport.enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    await service.connect(
        ConnectRequest(provider="steadfast", api_key=API_KEY, secret_key=SECRET_KEY)
    )
    await db.commit()
    return service


@pytest.fixture
def sync(db, settings, accounts) -> StatusSyncService:
    return StatusSyncService(
        db,
        accounts=accounts,
        consignments=ConsignmentService(db, receivables=ReceivableService(db)),
        settings=settings,
    )


@pytest.fixture
def booking(db, settings, accounts) -> CourierBookingService:
    return CourierBookingService(
        db,
        accounts=accounts,
        consignments=ConsignmentService(db, receivables=ReceivableService(db)),
        vault=CredentialVault(settings),
        settings=settings,
    )


@pytest.fixture
def returns(db, accounts) -> CourierReturnService:
    return CourierReturnService(db, accounts=accounts)


@pytest.fixture
def payments(db, settings, accounts) -> PaymentSyncService:
    return PaymentSyncService(db, accounts=accounts, settings=settings)


async def _booked_parcel(
    client: AsyncClient,
    shop: dict[str, Any],
    db: Any,
    booking: CourierBookingService,
    transport: FakeSteadfastTransport,
    *,
    cod_paisa: int = 125_000,
    consignment_id: int = 9900001,
) -> Consignment:
    product = await create_product(
        client, shop, name="Synced", sku=f"SKU-{uuid.uuid4().hex[:6]}", opening_stock=10
    )
    order = await create_order(
        client,
        shop,
        items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": cod_paisa}],
        cod_amount_paisa=cod_paisa,
        address="House 5, Road 2, Uttara, Dhaka",
    )
    order_body = order["order"]
    transport.enqueue(
        "POST",
        "/create_order",
        body=bodies.create_ok(invoice=order_body["order_number"], consignment_id=consignment_id),
    )
    await booking.book(uuid.UUID(order_body["id"]))
    parcel = (
        (
            await db.execute(
                sa.select(Consignment).where(Consignment.order_id == uuid.UUID(order_body["id"]))
            )
        )
        .scalars()
        .first()
    )
    assert parcel is not None
    return parcel


# ---------------------------------------------------------- poll cadence --


def test_polling_is_adaptive_to_parcel_age(settings) -> None:
    """A three-week-old parcel does not deserve the same attention as an hour-old one."""
    from datetime import timedelta

    from app.core.clock import utc_now

    now = utc_now()
    fresh = next_poll_at(settings, booked_at=now - timedelta(hours=1), now=now)
    active = next_poll_at(settings, booked_at=now - timedelta(days=3), now=now)
    stale = next_poll_at(settings, booked_at=now - timedelta(days=21), now=now)

    assert fresh < active < stale


def test_repeated_failures_back_the_poll_off(settings) -> None:
    from app.core.clock import utc_now

    now = utc_now()
    clean = next_poll_at(settings, booked_at=now, now=now, failures=0)
    struggling = next_poll_at(settings, booked_at=now, now=now, failures=3)
    assert struggling > clean


# --------------------------------------------------------- status status --


async def test_a_settled_delivery_moves_the_money(
    client, shop, db, booking, sync, transport, accounts
) -> None:
    parcel = await _booked_parcel(client, shop, db, booking, transport)
    transport.enqueue("GET", "/status_by_cid/9900001", body=bodies.status_body("delivered"))

    account = await accounts.usable_account("steadfast")
    assert account is not None
    mapping = await sync.poll_one(parcel, account=account)

    assert mapping is not None and mapping.is_final
    await db.refresh(parcel)
    assert parcel.consignment_status is ConsignmentStatus.DELIVERED
    # A settled parcel comes off the polling schedule.
    assert parcel.next_poll_at is None

    receivable = await db.scalar(
        sa.select(CodReceivable).where(CodReceivable.consignment_id == parcel.id)
    )
    assert receivable is not None
    assert receivable.status != str(ReceivableStatus.EXPECTED)


@pytest.mark.parametrize(
    "status",
    [
        "delivered_approval_pending",
        "partial_delivered_approval_pending",
        "cancelled_approval_pending",
        "unknown_approval_pending",
    ],
)
async def test_approval_pending_never_settles_money(
    client, shop, db, booking, sync, transport, accounts, status: str
) -> None:
    """The document says these await admin approval; balance is added later."""
    parcel = await _booked_parcel(client, shop, db, booking, transport)
    transport.enqueue("GET", "/status_by_cid/9900001", body=bodies.status_body(status))

    account = await accounts.usable_account("steadfast")
    assert account is not None
    mapping = await sync.poll_one(parcel, account=account)

    assert mapping is not None
    assert mapping.is_approval_pending is True
    assert mapping.moves_money is False

    await db.refresh(parcel)
    assert parcel.consignment_status.is_terminal is False
    assert parcel.provider_raw_status == status
    # Still on the schedule: the outcome has not landed yet.
    assert parcel.next_poll_at is not None

    receivable = await db.scalar(
        sa.select(CodReceivable).where(CodReceivable.consignment_id == parcel.id)
    )
    assert receivable is not None
    assert receivable.status == str(ReceivableStatus.EXPECTED), "nothing is collectible yet"


async def test_partial_delivery_asks_a_person_for_quantities(
    client, shop, db, booking, sync, transport, accounts
) -> None:
    """The status string carries no numbers, so no number may be invented."""
    parcel = await _booked_parcel(client, shop, db, booking, transport)
    transport.enqueue("GET", "/status_by_cid/9900001", body=bodies.status_body("partial_delivered"))

    account = await accounts.usable_account("steadfast")
    assert account is not None
    await sync.poll_one(parcel, account=account)

    await db.refresh(parcel)
    assert parcel.needs_quantity_resolution is True
    assert parcel.consignment_status is not ConsignmentStatus.PARTIAL_DELIVERED, (
        "a partial delivery is not recorded until someone says how many arrived"
    )

    awaiting = await sync.parcels_awaiting_quantities()
    assert parcel.id in {p.id for p in awaiting}


async def test_an_undocumented_status_is_stored_and_signalled(
    client, shop, db, booking, sync, transport, accounts
) -> None:
    parcel = await _booked_parcel(client, shop, db, booking, transport)
    transport.enqueue("GET", "/status_by_cid/9900001", body=bodies.status_body("beamed_up"))

    account = await accounts.usable_account("steadfast")
    assert account is not None
    mapping = await sync.poll_one(parcel, account=account)

    assert mapping is not None
    assert mapping.canonical is None
    await db.refresh(parcel)
    assert parcel.provider_raw_status == "beamed_up"
    assert parcel.consignment_status is ConsignmentStatus.BOOKED, "state unchanged"

    event = (
        (
            await db.execute(
                sa.select(CourierEvent).where(
                    CourierEvent.consignment_id == parcel.id,
                    CourierEvent.raw_status == "beamed_up",
                )
            )
        )
        .scalars()
        .first()
    )
    assert event is not None
    assert event.status_undocumented is True
    assert any(str(CourierMetric.STATUS_UNKNOWN_VALUE) in key for key in metrics_snapshot())


async def test_repeated_identical_observations_do_not_multiply_events(
    client, shop, db, booking, sync, transport, accounts
) -> None:
    """A parcel polled hourly for a week is one row and a counter."""
    parcel = await _booked_parcel(client, shop, db, booking, transport)
    transport.always(body=bodies.status_body("pending"))

    account = await accounts.usable_account("steadfast")
    assert account is not None
    for _ in range(4):
        await sync.poll_one(parcel, account=account)

    events = list(
        (
            await db.execute(
                sa.select(CourierEvent).where(
                    CourierEvent.consignment_id == parcel.id,
                    CourierEvent.raw_status == "pending",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].observation_count == 4


async def test_a_failed_poll_changes_nothing_about_the_parcel(
    client, shop, db, booking, sync, transport, accounts
) -> None:
    parcel = await _booked_parcel(client, shop, db, booking, transport)
    before = parcel.status
    transport.always(status_code=500, body="{}")

    account = await accounts.usable_account("steadfast")
    assert account is not None
    result = await sync.poll_one(parcel, account=account)

    assert result is None
    await db.refresh(parcel)
    assert parcel.status == before
    assert parcel.poll_failure_count == 1
    assert parcel.next_poll_at is not None


async def test_a_recovered_parcel_is_polled_by_its_invoice(
    client, shop, db, settings, booking, sync, transport, accounts
) -> None:
    """Recovery proves existence but cannot obtain a consignment id.

    The by-invoice lookup is what keeps such a parcel synchronised, which is
    the reason all three documented lookup forms are implemented.
    """
    from app.couriers.steadfast.errors import SteadfastAmbiguousError

    product = await create_product(
        client, shop, name="Recovered", sku=f"SKU-{uuid.uuid4().hex[:6]}"
    )
    order = await create_order(
        client,
        shop,
        items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": 100_000}],
        cod_amount_paisa=100_000,
        address="House 9, Road 4, Banani, Dhaka",
    )
    order_body = order["order"]
    transport.enqueue_error("POST", "/create_order", SteadfastAmbiguousError("lost"))
    await booking.book(uuid.UUID(order_body["id"]))

    parcel = (
        (
            await db.execute(
                sa.select(Consignment).where(Consignment.order_id == uuid.UUID(order_body["id"]))
            )
        )
        .scalars()
        .first()
    )
    assert parcel is not None
    assert parcel.provider_consignment_id is None

    # Recover it the way the job would: the by-invoice lookup proves the parcel
    # exists, and that is the only handle it will ever have.
    from app.couriers.recovery import BookingRecoveryService

    recovery = BookingRecoveryService(
        db,
        accounts=accounts,
        consignments=ConsignmentService(db, receivables=ReceivableService(db)),
        settings=settings,
    )
    transport.enqueue(
        "GET",
        f"/status_by_invoice/{order_body['order_number']}",
        body=bodies.status_body("pending"),
    )
    attempt = (
        (
            await db.execute(
                sa.select(CourierBookingAttempt).where(
                    CourierBookingAttempt.consignment_id == parcel.id
                )
            )
        )
        .scalars()
        .first()
    )
    assert attempt is not None
    await recovery.recover(attempt)
    await db.refresh(parcel)
    assert parcel.consignment_status is ConsignmentStatus.BOOKED
    assert parcel.provider_consignment_id is None, (
        "status_by_invoice returns a status and nothing else, so no id is claimed"
    )

    transport.enqueue(
        "GET",
        f"/status_by_invoice/{order_body['order_number']}",
        body=bodies.status_body("delivered"),
    )
    account = await accounts.usable_account("steadfast")
    assert account is not None
    mapping = await sync.poll_one(parcel, account=account)

    assert mapping is not None and mapping.is_final
    assert len(transport.calls_to("GET", f"/status_by_invoice/{order_body['order_number']}")) == 2


# ---------------------------------------------------------------- returns --


async def test_a_return_is_requested_once(client, shop, db, booking, returns, transport) -> None:
    parcel = await _booked_parcel(client, shop, db, booking, transport)
    transport.enqueue("POST", "/create_return_request", body=bodies.RETURN_REQUEST_CREATED)

    result = await returns.request_return(parcel.id, reason="Customer refused")

    assert result.state is ReturnRequestState.REQUESTED
    assert result.provider_return_id == "4242"
    sent = transport.calls_to("POST", "/create_return_request")[0]["json"]
    # Exactly one reference, as the document requires.
    assert set(sent) == {"consignment_id", "reason"}


async def test_a_second_return_request_is_refused(
    client, shop, db, booking, returns, transport
) -> None:
    parcel = await _booked_parcel(client, shop, db, booking, transport)
    transport.enqueue("POST", "/create_return_request", body=bodies.RETURN_REQUEST_CREATED)
    await returns.request_return(parcel.id)

    sent_before = len(transport.calls_to("POST", "/create_return_request"))
    with pytest.raises(ConflictError, match="already been requested"):
        await returns.request_return(parcel.id)

    assert len(transport.calls_to("POST", "/create_return_request")) == sent_before


async def test_an_unconfirmed_return_also_blocks_a_second_request(
    client, shop, db, booking, returns, transport
) -> None:
    """A lost answer does not mean the courier did not receive it."""
    from app.couriers.steadfast.errors import SteadfastAmbiguousError

    parcel = await _booked_parcel(client, shop, db, booking, transport)
    transport.enqueue_error("POST", "/create_return_request", SteadfastAmbiguousError("lost"))

    result = await returns.request_return(parcel.id)
    assert result.state is ReturnRequestState.UNKNOWN
    assert "আবার পাঠাবেন না" in result.message

    with pytest.raises(ConflictError, match="not confirmed"):
        await returns.request_return(parcel.id)


async def test_a_return_cannot_be_requested_for_an_unconfirmed_parcel(
    client, shop, db, booking, returns, transport
) -> None:
    from app.couriers.steadfast.errors import SteadfastAmbiguousError

    product = await create_product(client, shop, name="Unsure", sku=f"SKU-{uuid.uuid4().hex[:6]}")
    order = await create_order(
        client,
        shop,
        items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": 90_000}],
        cod_amount_paisa=90_000,
        address="House 1, Road 1, Dhaka",
    )
    transport.enqueue_error("POST", "/create_order", SteadfastAmbiguousError("lost"))
    await booking.book(uuid.UUID(order["order"]["id"]))
    parcel = (
        (
            await db.execute(
                sa.select(Consignment).where(
                    Consignment.order_id == uuid.UUID(order["order"]["id"])
                )
            )
        )
        .scalars()
        .first()
    )
    assert parcel is not None

    with pytest.raises(ConflictError, match="not been confirmed"):
        await returns.request_return(parcel.id)


async def test_a_completed_return_request_does_not_restore_stock_here(
    client, shop, db, booking, returns, transport
) -> None:
    """Brief section 28: stock moves through the return domain, not from here."""
    parcel = await _booked_parcel(client, shop, db, booking, transport)
    completed = json.dumps(
        {
            "id": 4243,
            "consignment_id": 9900001,
            "status": "completed",
            "reason": None,
            "created_at": "2026-09-11T10:00:00.000000Z",
            "updated_at": "2026-09-11T12:00:00.000000Z",
        }
    )
    transport.enqueue("POST", "/create_return_request", body=completed)

    await returns.request_return(parcel.id)
    await db.refresh(parcel)

    # The request is finished; the parcel is not declared returned by it.
    assert parcel.consignment_status is not ConsignmentStatus.RETURNED


# --------------------------------------------------------------- payments --


async def test_a_payment_becomes_a_payout_with_one_line_per_parcel(
    client, shop, db, payments, transport
) -> None:
    transport.enqueue("GET", "/payments", body=bodies.PAYMENTS_LIST)
    transport.enqueue("GET", "/payments/55001", body=bodies.PAYMENT_DETAIL_WITH_CONSIGNMENTS)
    transport.enqueue("GET", "/payments/55002", body=bodies.PAYMENT_DETAIL_WITH_CONSIGNMENTS)

    report = await payments.sync()

    assert report.seen == 2
    assert report.imported >= 1

    payout = (
        (await db.execute(sa.select(Payout).where(Payout.provider_reference == "55001")))
        .scalars()
        .first()
    )
    assert payout is not None
    assert payout.total_paisa == 1_845_000
    lines = list(
        (await db.execute(sa.select(PayoutLine).where(PayoutLine.payout_id == payout.id)))
        .scalars()
        .all()
    )
    assert len(lines) == 3
    assert lines[0].provider_consignment_id == "9900001"
    assert lines[0].merchant_reference == "ECB-SYNTH-0001"


async def test_syncing_twice_does_not_double_a_payout(
    client, shop, db, payments, transport
) -> None:
    """The property that stops a repeated sync doubling a settled total."""
    for _ in range(2):
        transport.enqueue("GET", "/payments", body=bodies.PAYMENTS_LIST)
        transport.enqueue("GET", "/payments/55001", body=bodies.PAYMENT_DETAIL_WITH_CONSIGNMENTS)
        transport.enqueue("GET", "/payments/55002", body=bodies.PAYMENT_DETAIL_WITH_CONSIGNMENTS)

    first = await payments.sync()
    second = await payments.sync()

    assert second.imported == 0, "already-imported payments are recognised, not re-imported"
    count = await db.scalar(
        sa.select(sa.func.count()).select_from(Payout).where(Payout.provider == "steadfast")
    )
    assert count == first.imported


async def test_a_payment_changed_after_import_is_flagged_not_rewritten(
    client, shop, db, payments, transport
) -> None:
    transport.enqueue("GET", "/payments", body=bodies.PAYMENTS_LIST)
    transport.enqueue("GET", "/payments/55001", body=bodies.PAYMENT_DETAIL_WITH_CONSIGNMENTS)
    transport.enqueue("GET", "/payments/55002", body=bodies.PAYMENT_DETAIL_WITH_CONSIGNMENTS)
    await payments.sync()

    # The courier's copy now says a different amount.
    changed_list = json.dumps(
        {
            "data": [
                {
                    "id": 55001,
                    "amount": "19999.00",
                    "status": "paid",
                    "paid_at": "2026-09-10T12:00:00.000000Z",
                    "reference": "PAY-SYNTH-55001",
                }
            ]
        }
    )
    transport.enqueue("GET", "/payments", body=changed_list)

    report = await payments.sync()

    assert report.changed == 1
    record = (
        (
            await db.execute(
                sa.select(ProviderPayment).where(ProviderPayment.provider_payment_id == "55001")
            )
        )
        .scalars()
        .first()
    )
    assert record is not None
    assert record.state is PaymentSyncState.CHANGED
    # The payout it already produced is untouched.
    payout = await db.get(Payout, record.payout_id)
    assert payout is not None
    assert payout.total_paisa == 1_845_000


async def test_itemised_charges_become_their_own_adjustments(
    client, shop, db, payments, transport
) -> None:
    transport.enqueue(
        "GET", "/payments", body=json.dumps({"data": [{"id": 55001, "amount": "18450.00"}]})
    )
    transport.enqueue("GET", "/payments/55001", body=bodies.PAYMENT_DETAIL_WITH_CONSIGNMENTS)

    await payments.sync()

    payout = (
        (await db.execute(sa.select(Payout).where(Payout.provider_reference == "55001")))
        .scalars()
        .first()
    )
    assert payout is not None
    adjustments = list(
        (
            await db.execute(
                sa.select(PayoutAdjustment).where(PayoutAdjustment.payout_id == payout.id)
            )
        )
        .scalars()
        .all()
    )
    kinds = {adjustment.type for adjustment in adjustments}
    assert str(AdjustmentType.DELIVERY_FEE) in kinds
    assert str(AdjustmentType.COD_FEE) in kinds
    assert str(AdjustmentType.RETURN_FEE) in kinds


async def test_an_aggregate_only_payment_keeps_one_unknown_deduction(
    client, shop, db, payments, transport
) -> None:
    """No breakdown is invented from a net figure (master spec section 84)."""
    transport.enqueue(
        "GET", "/payments", body=json.dumps({"data": [{"id": 55009, "amount": "5000.00"}]})
    )
    transport.enqueue("GET", "/payments/55009", body=bodies.PAYMENT_DETAIL_AGGREGATE_ONLY)

    await payments.sync()

    payout = (
        (await db.execute(sa.select(Payout).where(Payout.provider_reference == "55009")))
        .scalars()
        .first()
    )
    assert payout is not None
    adjustments = list(
        (
            await db.execute(
                sa.select(PayoutAdjustment).where(PayoutAdjustment.payout_id == payout.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(adjustments) == 1
    assert adjustments[0].type == str(AdjustmentType.UNKNOWN_DEDUCTION)
    # ৳5300 collected, ৳5000 paid: ৳300 unexplained, stated as such.
    assert adjustments[0].amount_paisa == 30_000
    assert adjustments[0].recognized_rule is None


async def test_a_payment_with_no_identifier_is_skipped_not_guessed(
    client, shop, db, payments, transport
) -> None:
    transport.enqueue(
        "GET", "/payments", body=json.dumps({"data": [{"amount": "100.00", "status": "paid"}]})
    )

    report = await payments.sync()

    assert report.errors == 1
    assert report.imported == 0


async def test_the_payout_records_that_its_schema_was_inferred(
    client, shop, db, payments, transport
) -> None:
    """A support engineer reading this months later must know."""
    transport.enqueue(
        "GET", "/payments", body=json.dumps({"data": [{"id": 55001, "amount": "18450.00"}]})
    )
    transport.enqueue("GET", "/payments/55001", body=bodies.PAYMENT_DETAIL_WITH_CONSIGNMENTS)

    await payments.sync()

    payout = (
        (await db.execute(sa.select(Payout).where(Payout.provider_reference == "55001")))
        .scalars()
        .first()
    )
    assert payout is not None
    assert payout.metadata_json["schema_undocumented"] is True
    assert payout.metadata_json["observed_fields"]


async def test_pagination_is_followed_only_when_the_response_declares_it(
    client, shop, db, payments, transport
) -> None:
    """No ``?page=`` parameter is invented against an endpoint that documents none."""
    transport.enqueue("GET", "/payments", body=bodies.PAYMENTS_LIST)
    transport.enqueue("GET", "/payments/55001", body=bodies.PAYMENT_DETAIL_WITH_CONSIGNMENTS)
    transport.enqueue("GET", "/payments/55002", body=bodies.PAYMENT_DETAIL_WITH_CONSIGNMENTS)

    report = await payments.sync()

    assert report.pages == 1
    assert transport.calls_to("GET", "/payments")[0]["path"] == "/payments"


# -------------------------------------------------------------- webhooks --


async def test_the_webhook_receiver_stores_and_refuses_to_verify(db) -> None:
    receiver = WebhookReceiver(
        db, verifier=SteadfastWebhookVerifier(), parser=SteadfastWebhookParser()
    )

    result = await receiver.ingest(
        headers={"x-some-signature": "anything"}, body=b'{"delivery_status":"delivered"}'
    )

    assert result.state is WebhookDeliveryState.NOT_CONFIGURED
    assert result.was_processed is False
    assert result.http_status == 202

    delivery = await db.get(CourierWebhookDelivery, result.delivery_id)
    assert delivery is not None
    assert delivery.raw_body == '{"delivery_status":"delivered"}'
    assert WEBHOOK_BLOCKER in (delivery.reason or "")


async def test_the_webhook_receiver_stores_header_names_but_no_values(db) -> None:
    """A signature header is credential-adjacent; its value never lands in a row."""
    receiver = WebhookReceiver(
        db, verifier=SteadfastWebhookVerifier(), parser=SteadfastWebhookParser()
    )

    result = await receiver.ingest(
        headers={"authorization": "Bearer super-secret-value", "x-sig": "abc123"},
        body=b'{"a":1}',
    )

    delivery = await db.get(CourierWebhookDelivery, result.delivery_id)
    assert delivery is not None
    assert "authorization" in delivery.header_names
    assert "super-secret-value" not in str(delivery.header_names)


async def test_a_replayed_webhook_body_is_recognised(db) -> None:
    receiver = WebhookReceiver(
        db, verifier=SteadfastWebhookVerifier(), parser=SteadfastWebhookParser()
    )
    body = b'{"consignment_id": 1, "status": "delivered"}'

    first = await receiver.ingest(headers={}, body=body)
    second = await receiver.ingest(headers={}, body=body)

    assert first.state is WebhookDeliveryState.NOT_CONFIGURED
    assert second.state is WebhookDeliveryState.DUPLICATE
    assert second.delivery_id == first.delivery_id

    delivery = await db.get(CourierWebhookDelivery, first.delivery_id)
    assert delivery is not None
    assert delivery.delivery_count == 2


async def test_an_oversized_webhook_body_is_not_stored(db) -> None:
    receiver = WebhookReceiver(
        db, verifier=SteadfastWebhookVerifier(), parser=SteadfastWebhookParser()
    )

    result = await receiver.ingest(headers={}, body=b"x" * (300 * 1024))

    assert result.state is WebhookDeliveryState.REJECTED
    assert result.http_status == 413


async def test_the_webhook_verifier_cannot_be_switched_on_by_a_flag(settings) -> None:
    """A flag does not supply a contract, so it cannot make this configured."""
    verifier = SteadfastWebhookVerifier(settings)
    assert verifier.is_configured is False
    assert verifier.verify(headers={"x-signature": "whatever"}, body=b"{}") is False


class TestWebhookRoute:
    async def test_the_route_accepts_stores_and_processes_nothing(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            "/v1/webhooks/couriers/steadfast",
            content=b'{"delivery_status": "delivered", "consignment_id": 9900001}',
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 202
        body = response.json()
        assert body["received"] is True
        assert body["processed"] is False
        assert body["blocker"] == WEBHOOK_BLOCKER

    async def test_the_route_is_not_published_in_the_schema(self, client: AsyncClient) -> None:
        """An endpoint whose contract does not exist must not invite callers."""
        schema = (await client.get("/openapi.json")).json()
        assert "/v1/webhooks/couriers/steadfast" not in schema["paths"]
