"""Booking parcels, and recovering the ones whose answer was lost.

Every test here defends a claim that costs a seller real money if it breaks.
The three that matter most:

* an ambiguous create becomes ``BOOKING_UNKNOWN``, never ``FAILED``, and the
  order cannot be booked again from the normal path;
* a bulk request whose answer is lost is **not resent**, and each of its items
  is recoverable independently by its own invoice;
* a 404 from the recovery lookup does not prove the parcel is absent, because
  the provider documents no "not found" response.
"""

from __future__ import annotations

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
from app.couriers.booking import CourierBookingService, merchant_reference_for
from app.couriers.metrics import CourierMetric, metrics_snapshot
from app.couriers.models import (
    BookingAttemptState,
    CourierBookingAttempt,
    CourierEvent,
)
from app.couriers.recovery import BookingRecoveryService, RecoveryOutcome
from app.couriers.registry import CourierAdapterRegistry
from app.couriers.steadfast.adapter import SteadfastAdapter
from app.couriers.steadfast.client import SteadfastClient, SteadfastConfig
from app.couriers.steadfast.errors import SteadfastAmbiguousError
from app.couriers.steadfast.transport import FakeSteadfastTransport
from app.money.models import CodReceivable
from app.money.service import ReceivableService
from app.products.models import Product
from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.fixtures.steadfast import bodies

# Each test here is about a courier a shop may use, so the kill switches are on.
pytestmark = pytest.mark.usefixtures("courier_flags_on")

API_KEY = "sfk-booking-test-key-abcd"
SECRET_KEY = "sfs-booking-test-secret-wxyz"


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
                config=SteadfastConfig(bulk_chunk_size=2, max_read_retries=0),
                sleep=_no_sleep,
            )
        )

    return CourierAdapterRegistry({"steadfast": _factory})


@pytest.fixture
async def shop(client: AsyncClient, unique_phone: str) -> dict[str, Any]:
    session = await signed_in_shop(client, unique_phone, shop_name="Booking Shop")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
    return session


@pytest.fixture
async def accounts(
    db, settings, registry: CourierAdapterRegistry, transport: FakeSteadfastTransport, shop
) -> CourierAccountService:
    service = CourierAccountService(db, vault=CredentialVault(settings), registry=registry)
    transport.enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    await service.connect(
        ConnectRequest(provider="steadfast", api_key=API_KEY, secret_key=SECRET_KEY)
    )
    # Committed so this session stops holding a write transaction. On SQLite a
    # second connection — the HTTP client creating orders — would otherwise
    # block on the file lock; on PostgreSQL it is simply tidy.
    await db.commit()
    return service


@pytest.fixture
def booking(db, settings, accounts: CourierAccountService) -> CourierBookingService:
    return CourierBookingService(
        db,
        accounts=accounts,
        consignments=ConsignmentService(db, receivables=ReceivableService(db)),
        vault=CredentialVault(settings),
        settings=settings,
    )


@pytest.fixture
def recovery(db, settings, accounts: CourierAccountService) -> BookingRecoveryService:
    return BookingRecoveryService(
        db,
        accounts=accounts,
        consignments=ConsignmentService(db, receivables=ReceivableService(db)),
        settings=settings,
    )


async def _order(
    client: AsyncClient,
    shop: dict[str, Any],
    *,
    product: dict[str, Any] | None = None,
    quantity: int = 1,
    cod_paisa: int = 125_000,
) -> dict[str, Any]:
    """A bookable order: a real product, a phone number and a delivery address.

    The address matters — booking refuses an order without one before anything
    reaches the provider, which is a separate test.
    """
    if product is None:
        product = await create_product(
            client, shop, name="Booking Item", sku=f"SKU-{uuid.uuid4().hex[:6]}"
        )
    created = await create_order(
        client,
        shop,
        items=[
            {
                "product_id": product["id"],
                "quantity": quantity,
                "unit_price_paisa": cod_paisa // quantity,
            }
        ],
        cod_amount_paisa=cod_paisa,
        address="House 12, Road 3, Mirpur 10, Dhaka",
    )
    return created["order"]


async def _consignment(db, order_id: str) -> Consignment | None:
    return (
        (
            await db.execute(
                sa.select(Consignment).where(Consignment.order_id == uuid.UUID(order_id))
            )
        )
        .scalars()
        .first()
    )


async def _attempts(db, consignment_id: uuid.UUID) -> list[CourierBookingAttempt]:
    return list(
        (
            await db.execute(
                sa.select(CourierBookingAttempt)
                .where(CourierBookingAttempt.consignment_id == consignment_id)
                .order_by(CourierBookingAttempt.attempt_number)
            )
        )
        .scalars()
        .all()
    )


# ------------------------------------------------------------- reference --


def test_the_merchant_reference_is_stable_and_never_reused() -> None:
    """The property the whole recovery model rests on."""
    assert merchant_reference_for("CP-20260911-0042", 1) == "CP-20260911-0042"
    # A *replacement* parcel gets a new reference, so a courier statement
    # naming the original is unambiguously about the original, forever.
    assert merchant_reference_for("CP-20260911-0042", 2) == "CP-20260911-0042-2"
    assert merchant_reference_for("CP-20260911-0042", 3) == "CP-20260911-0042-3"


# ------------------------------------------------------ successful booking --


async def test_a_successful_booking_records_provider_identity(
    client, shop, db, booking, transport
) -> None:
    order = await _order(client, shop)
    transport.enqueue("POST", "/create_order", body=bodies.create_ok(invoice=order["order_number"]))

    report = await booking.book(uuid.UUID(order["id"]))

    assert report.booked == 1
    assert report.ambiguous == 0
    item = report.items[0]
    assert item.outcome == str(ConsignmentStatus.BOOKED)
    assert item.merchant_reference == order["order_number"]
    assert item.tracking_code == "TESTAA01"

    consignment = await _consignment(db, order["id"])
    assert consignment is not None
    assert consignment.consignment_status is ConsignmentStatus.BOOKED
    assert consignment.provider_consignment_id == "9900001"
    assert consignment.next_poll_at is not None


async def test_a_successful_booking_moves_stock_and_opens_a_receivable(
    client, shop, db, booking, transport
) -> None:
    """Only a confirmed parcel leaves the shelf."""
    product = await create_product(
        client, shop, name="Stocked", sku=f"SKU-{uuid.uuid4().hex[:6]}", opening_stock=10
    )
    order = await _order(client, shop, product=product, quantity=3, cod_paisa=300_000)
    transport.enqueue("POST", "/create_order", body=bodies.create_ok(invoice=order["order_number"]))

    await booking.book(uuid.UUID(order["id"]))

    stock = await db.scalar(
        sa.select(Product.stock_on_hand).where(Product.id == uuid.UUID(product["id"]))
    )
    assert stock == 7

    consignment = await _consignment(db, order["id"])
    assert consignment is not None
    receivable = await db.scalar(
        sa.select(CodReceivable).where(CodReceivable.consignment_id == consignment.id)
    )
    assert receivable is not None


async def test_the_booking_attempt_is_persisted_before_the_call(
    client, shop, db, booking, transport
) -> None:
    """The invoice has to survive a crash mid-call, or recovery is impossible."""
    order = await _order(client, shop)
    transport.enqueue("POST", "/create_order", body=bodies.create_ok(invoice=order["order_number"]))

    await booking.book(uuid.UUID(order["id"]))
    consignment = await _consignment(db, order["id"])
    assert consignment is not None

    attempts = await _attempts(db, consignment.id)
    assert len(attempts) == 1
    assert attempts[0].merchant_reference == order["order_number"]
    assert attempts[0].attempt_state is BookingAttemptState.SUCCEEDED
    assert attempts[0].requested_cod_taka is not None


async def test_the_stored_request_masks_the_customer_phone(
    client, shop, db, booking, transport
) -> None:
    order = await _order(client, shop)
    transport.enqueue("POST", "/create_order", body=bodies.create_ok(invoice=order["order_number"]))

    await booking.book(uuid.UUID(order["id"]))

    from app.couriers.models import CourierRawPayload

    payload = (
        (
            await db.execute(
                sa.select(CourierRawPayload).order_by(CourierRawPayload.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert payload is not None
    rendered = str(payload.request_payload)
    assert "****" in rendered
    assert "recipient_email" not in rendered or "[redacted]" in rendered


# -------------------------------------------------------- safe failure ----


async def test_a_rejected_booking_leaves_the_order_bookable(
    client, shop, db, booking, transport
) -> None:
    order = await _order(client, shop)
    transport.enqueue(
        "POST", "/create_order", status_code=422, body=bodies.CREATE_VALIDATION_FAILURE
    )

    report = await booking.book(uuid.UUID(order["id"]))

    assert report.failed == 1
    consignment = await _consignment(db, order["id"])
    assert consignment is not None
    assert consignment.consignment_status is ConsignmentStatus.NOT_BOOKED

    # Nothing moved: a refused create created nothing.
    receivable = await db.scalar(
        sa.select(CodReceivable).where(CodReceivable.consignment_id == consignment.id)
    )
    assert receivable is None


async def test_a_safe_failure_may_be_retried_with_the_same_reference(
    client, shop, db, booking, transport
) -> None:
    """The provider said no, so nothing exists under that invoice — reuse it."""
    order = await _order(client, shop)
    transport.enqueue("POST", "/create_order", status_code=422, body="{}")
    await booking.book(uuid.UUID(order["id"]))

    transport.enqueue("POST", "/create_order", body=bodies.create_ok(invoice=order["order_number"]))
    report = await booking.book(uuid.UUID(order["id"]))

    assert report.booked == 1
    consignment = await _consignment(db, order["id"])
    assert consignment is not None
    attempts = await _attempts(db, consignment.id)
    assert len(attempts) == 2
    assert attempts[0].merchant_reference == attempts[1].merchant_reference


# ---------------------------------------------------- ambiguous booking ----


async def test_a_lost_answer_becomes_booking_unknown_not_failed(
    client, shop, db, booking, transport
) -> None:
    order = await _order(client, shop)
    transport.enqueue_error(
        "POST", "/create_order", SteadfastAmbiguousError("The courier did not answer in time")
    )

    report = await booking.book(uuid.UUID(order["id"]))

    assert report.ambiguous == 1
    assert report.failed == 0
    item = report.items[0]
    assert item.outcome == str(ConsignmentStatus.BOOKING_UNKNOWN)
    assert "আবার বুক করবেন না" in (item.message or "")

    consignment = await _consignment(db, order["id"])
    assert consignment is not None
    assert consignment.consignment_status is ConsignmentStatus.BOOKING_UNKNOWN


async def test_an_ambiguous_booking_moves_no_stock_and_no_money(
    client, shop, db, booking, transport
) -> None:
    """A parcel that may not exist has not left the shelf."""
    product = await create_product(
        client, shop, name="Untouched", sku=f"SKU-{uuid.uuid4().hex[:6]}", opening_stock=10
    )
    order = await _order(client, shop, product=product, quantity=4, cod_paisa=400_000)
    transport.enqueue_error("POST", "/create_order", SteadfastAmbiguousError("lost"))

    await booking.book(uuid.UUID(order["id"]))

    stock = await db.scalar(
        sa.select(Product.stock_on_hand).where(Product.id == uuid.UUID(product["id"]))
    )
    assert stock == 10, "an unconfirmed parcel must not decrement stock"

    consignment = await _consignment(db, order["id"])
    assert consignment is not None
    receivable = await db.scalar(
        sa.select(CodReceivable).where(CodReceivable.consignment_id == consignment.id)
    )
    assert receivable is None


async def test_an_ambiguous_booking_cannot_be_rebooked(
    client, shop, db, booking, transport
) -> None:
    """The whole point. A second parcel would be shipped and billed."""
    order = await _order(client, shop)
    transport.enqueue_error("POST", "/create_order", SteadfastAmbiguousError("lost"))
    await booking.book(uuid.UUID(order["id"]))

    sent_before = len(transport.calls_to("POST", "/create_order"))
    with pytest.raises(ConflictError) as caught:
        await booking.book(uuid.UUID(order["id"]))

    assert "not been confirmed" in str(caught.value)
    assert len(transport.calls_to("POST", "/create_order")) == sent_before
    assert metrics_snapshot().get(
        f"{CourierMetric.DUPLICATE_PREVENTED}{{provider=steadfast,state=BOOKING_UNKNOWN}}"
    )


async def test_an_unexpected_exception_is_treated_as_ambiguous(
    client, shop, db, booking, transport
) -> None:
    """An adapter bug is not evidence that nothing happened."""
    order = await _order(client, shop)

    class _Boom(Exception):
        pass

    transport.enqueue_error("POST", "/create_order", _Boom("kaboom"))  # type: ignore[arg-type]

    report = await booking.book(uuid.UUID(order["id"]))

    assert report.ambiguous == 1


# ----------------------------------------------------------- recovery ----


async def test_recovery_promotes_a_proven_parcel_to_booked(
    client, shop, db, booking, recovery, transport
) -> None:
    order = await _order(client, shop)
    transport.enqueue_error("POST", "/create_order", SteadfastAmbiguousError("lost"))
    await booking.book(uuid.UUID(order["id"]))

    # The provider answers about the invoice, which proves the parcel exists.
    transport.enqueue(
        "GET",
        f"/status_by_invoice/{order['order_number']}",
        body=bodies.status_body("in_review"),
    )
    consignment = await _consignment(db, order["id"])
    assert consignment is not None
    attempt = (await _attempts(db, consignment.id))[0]

    result = await recovery.recover(attempt)

    assert result.outcome is RecoveryOutcome.RECOVERED
    assert attempt.attempt_state is BookingAttemptState.RECOVERED
    await db.refresh(consignment)
    assert consignment.consignment_status is ConsignmentStatus.BOOKED
    assert consignment.provider_raw_status == "in_review"


async def test_recovery_opens_the_receivable_it_had_deferred(
    client, shop, db, booking, recovery, transport
) -> None:
    order = await _order(client, shop)
    transport.enqueue_error("POST", "/create_order", SteadfastAmbiguousError("lost"))
    await booking.book(uuid.UUID(order["id"]))
    consignment = await _consignment(db, order["id"])
    assert consignment is not None

    transport.enqueue(
        "GET", f"/status_by_invoice/{order['order_number']}", body=bodies.status_body("pending")
    )
    await recovery.recover((await _attempts(db, consignment.id))[0])

    receivable = await db.scalar(
        sa.select(CodReceivable).where(CodReceivable.consignment_id == consignment.id)
    )
    assert receivable is not None, "the parcel is proven to exist, so the money is expected"


async def test_a_404_does_not_prove_the_parcel_is_absent(
    client, shop, db, booking, recovery, transport
) -> None:
    """The document describes no 'not found' body, so absence is unprovable.

    Treating a 404 as "safe to rebook" is the most expensive mistake available
    in this integration, and it would be invisible until the seller was billed
    for two deliveries.
    """
    order = await _order(client, shop)
    transport.enqueue_error("POST", "/create_order", SteadfastAmbiguousError("lost"))
    await booking.book(uuid.UUID(order["id"]))
    consignment = await _consignment(db, order["id"])
    assert consignment is not None
    attempt = (await _attempts(db, consignment.id))[0]

    transport.enqueue(
        "GET", f"/status_by_invoice/{order['order_number']}", status_code=404, body="{}"
    )
    result = await recovery.recover(attempt)

    assert result.outcome is RecoveryOutcome.INCONCLUSIVE
    assert attempt.attempt_state is BookingAttemptState.UNKNOWN
    await db.refresh(consignment)
    assert consignment.consignment_status is ConsignmentStatus.BOOKING_UNKNOWN
    assert attempt.next_recovery_at is not None, "it is asked again, not abandoned"


async def test_a_provider_outage_during_recovery_is_inconclusive(
    client, shop, db, booking, recovery, transport
) -> None:
    order = await _order(client, shop)
    transport.enqueue_error("POST", "/create_order", SteadfastAmbiguousError("lost"))
    await booking.book(uuid.UUID(order["id"]))
    consignment = await _consignment(db, order["id"])
    assert consignment is not None

    transport.always(status_code=503, body="{}")
    result = await recovery.recover((await _attempts(db, consignment.id))[0])

    assert result.outcome is RecoveryOutcome.INCONCLUSIVE


async def test_recovery_gives_up_to_a_person_after_the_attempt_budget(
    client, shop, db, booking, recovery, transport, settings
) -> None:
    order = await _order(client, shop)
    transport.enqueue_error("POST", "/create_order", SteadfastAmbiguousError("lost"))
    await booking.book(uuid.UUID(order["id"]))
    consignment = await _consignment(db, order["id"])
    assert consignment is not None
    attempt = (await _attempts(db, consignment.id))[0]

    transport.always(status_code=500, body="{}")
    for _ in range(settings.courier_recovery_max_attempts):
        attempt.next_recovery_at = None
        await recovery.recover(attempt)

    assert attempt.attempt_state is BookingAttemptState.MANUAL_REVIEW
    await db.refresh(consignment)
    # Still unknown. Manual review is not a resolution, it is a queue.
    assert consignment.consignment_status is ConsignmentStatus.BOOKING_UNKNOWN


async def test_a_disconnected_account_does_not_consume_the_attempt_budget(
    client, shop, db, booking, recovery, transport, accounts
) -> None:
    order = await _order(client, shop)
    transport.enqueue_error("POST", "/create_order", SteadfastAmbiguousError("lost"))
    await booking.book(uuid.UUID(order["id"]))
    consignment = await _consignment(db, order["id"])
    assert consignment is not None
    attempt = (await _attempts(db, consignment.id))[0]

    await accounts.disconnect("steadfast")
    result = await recovery.recover(attempt)

    assert result.outcome is RecoveryOutcome.CANNOT_ASK
    assert attempt.recovery_attempts == 0


async def test_only_a_person_can_declare_a_parcel_was_never_created(
    client, shop, db, booking, recovery, transport
) -> None:
    """The one path that makes an order bookable again after an ambiguous create."""
    order = await _order(client, shop)
    transport.enqueue_error("POST", "/create_order", SteadfastAmbiguousError("lost"))
    await booking.book(uuid.UUID(order["id"]))
    consignment = await _consignment(db, order["id"])
    assert consignment is not None
    attempt = (await _attempts(db, consignment.id))[0]

    await recovery.resolve_manually(
        attempt.id, parcel_exists=False, reason="Checked the Steadfast portal; no such parcel."
    )

    assert attempt.attempt_state is BookingAttemptState.ABANDONED
    await db.refresh(consignment)
    assert consignment.consignment_status is ConsignmentStatus.NOT_BOOKED

    # And now — only now — booking again is allowed.
    transport.enqueue("POST", "/create_order", body=bodies.create_ok(invoice=order["order_number"]))
    report = await booking.book(uuid.UUID(order["id"]))
    assert report.booked == 1


async def test_manual_resolution_requires_a_reason(
    client, shop, db, booking, recovery, transport
) -> None:
    order = await _order(client, shop)
    transport.enqueue_error("POST", "/create_order", SteadfastAmbiguousError("lost"))
    await booking.book(uuid.UUID(order["id"]))
    consignment = await _consignment(db, order["id"])
    assert consignment is not None
    attempt = (await _attempts(db, consignment.id))[0]

    with pytest.raises(Exception, match="reason"):
        await recovery.resolve_manually(attempt.id, parcel_exists=False, reason="   ")


# ---------------------------------------------------------------- bulk ----


async def test_bulk_books_each_order_with_its_own_reference(
    client, shop, db, booking, transport
) -> None:
    orders = [await _order(client, shop) for _ in range(3)]
    invoices = [o["order_number"] for o in orders]
    # Chunk size is 2, so this is two requests.
    transport.enqueue(
        "POST",
        "/create_order/bulk-order",
        body=bodies.bulk_result(
            [
                {"invoice": invoices[0], "consignment_id": 9900001, "tracking_code": "T1"},
                {"invoice": invoices[1], "consignment_id": 9900002, "tracking_code": "T2"},
            ]
        ),
    )
    transport.enqueue(
        "POST",
        "/create_order/bulk-order",
        body=bodies.bulk_result(
            [{"invoice": invoices[2], "consignment_id": 9900003, "tracking_code": "T3"}]
        ),
    )

    report = await booking.book_bulk([uuid.UUID(o["id"]) for o in orders])

    assert report.booked == 3
    assert len(transport.calls_to("POST", "/create_order/bulk-order")) == 2
    assert sorted(item.merchant_reference for item in report.items) == sorted(invoices)


async def test_one_failed_bulk_item_does_not_fail_the_batch(
    client, shop, db, booking, transport
) -> None:
    orders = [await _order(client, shop) for _ in range(2)]
    invoices = [o["order_number"] for o in orders]
    transport.enqueue(
        "POST",
        "/create_order/bulk-order",
        body=bodies.bulk_result(
            [
                {"invoice": invoices[0], "consignment_id": 9900001, "tracking_code": "T1"},
                {
                    "invoice": invoices[1],
                    "consignment_id": None,
                    "tracking_code": None,
                    "status": "error",
                },
            ]
        ),
    )

    report = await booking.book_bulk([uuid.UUID(o["id"]) for o in orders])

    assert report.booked == 1
    assert report.failed == 1
    assert report.ambiguous == 0

    booked = await _consignment(db, orders[0]["id"])
    refused = await _consignment(db, orders[1]["id"])
    assert booked is not None and booked.consignment_status is ConsignmentStatus.BOOKED
    assert refused is not None and refused.consignment_status is ConsignmentStatus.NOT_BOOKED


async def test_a_bulk_timeout_is_never_resent_and_every_item_is_unknown(
    client, shop, db, booking, transport
) -> None:
    """Brief section 12. Resending a batch after a timeout is N duplicate parcels."""
    orders = [await _order(client, shop) for _ in range(2)]
    transport.enqueue_error(
        "POST", "/create_order/bulk-order", SteadfastAmbiguousError("The courier did not answer")
    )

    report = await booking.book_bulk([uuid.UUID(o["id"]) for o in orders])

    assert report.ambiguous == 2
    assert report.booked == 0
    assert len(transport.calls_to("POST", "/create_order/bulk-order")) == 1

    for order in orders:
        consignment = await _consignment(db, order["id"])
        assert consignment is not None
        assert consignment.consignment_status is ConsignmentStatus.BOOKING_UNKNOWN


async def test_each_bulk_item_is_recovered_independently_by_its_own_invoice(
    client, shop, db, booking, recovery, transport
) -> None:
    orders = [await _order(client, shop) for _ in range(2)]
    transport.enqueue_error("POST", "/create_order/bulk-order", SteadfastAmbiguousError("lost"))
    await booking.book_bulk([uuid.UUID(o["id"]) for o in orders])

    # The first parcel exists; the second cannot be confirmed.
    transport.enqueue(
        "GET",
        f"/status_by_invoice/{orders[0]['order_number']}",
        body=bodies.status_body("pending"),
    )
    transport.enqueue(
        "GET", f"/status_by_invoice/{orders[1]['order_number']}", status_code=500, body="{}"
    )

    outcomes = []
    for order in orders:
        consignment = await _consignment(db, order["id"])
        assert consignment is not None
        attempt = (await _attempts(db, consignment.id))[0]
        outcomes.append((await recovery.recover(attempt)).outcome)

    assert outcomes == [RecoveryOutcome.RECOVERED, RecoveryOutcome.INCONCLUSIVE]

    first = await _consignment(db, orders[0]["id"])
    second = await _consignment(db, orders[1]["id"])
    assert first is not None and first.consignment_status is ConsignmentStatus.BOOKED
    assert second is not None and second.consignment_status is ConsignmentStatus.BOOKING_UNKNOWN


async def test_a_duplicated_order_in_one_selection_is_booked_once(
    client, shop, db, booking, transport
) -> None:
    order = await _order(client, shop)
    transport.enqueue(
        "POST",
        "/create_order/bulk-order",
        body=bodies.bulk_result(
            [{"invoice": order["order_number"], "consignment_id": 9900001, "tracking_code": "T1"}]
        ),
    )

    order_id = uuid.UUID(order["id"])
    report = await booking.book_bulk([order_id, order_id, order_id])

    assert len(report.items) == 1
    assert report.booked == 1


# ---------------------------------------------------------------- guards --


async def test_booking_without_a_connected_account_refuses_cleanly(
    client, shop, db, settings, registry, transport
) -> None:
    """Manual courier mode stays available; the failure names it."""
    accounts = CourierAccountService(db, vault=CredentialVault(settings), registry=registry)
    service = CourierBookingService(
        db,
        accounts=accounts,
        consignments=ConsignmentService(db, receivables=ReceivableService(db)),
        vault=CredentialVault(settings),
        settings=settings,
    )
    order = await _order(client, shop)

    with pytest.raises(ConflictError, match="manual courier mode"):
        await service.book(uuid.UUID(order["id"]))
    assert transport.calls == []


async def test_a_cancelled_order_cannot_be_booked(client, shop, db, booking, transport) -> None:
    from tests.test_auth_flow import auth_header

    order = await _order(client, shop)
    cancelled = await client.patch(
        f"/v1/orders/{order['id']}",
        headers=auth_header(shop),
        json={"status": "CANCELLED", "cancellation_reason": "Customer changed mind"},
    )
    assert cancelled.status_code == 200, cancelled.text

    with pytest.raises(ConflictError):
        await booking.book(uuid.UUID(order["id"]))
    # Refused locally: nothing was sent, so nothing can have been created.
    assert transport.calls_to("POST", "/create_order") == []


async def test_a_booking_records_an_immutable_provider_event(
    client, shop, db, booking, transport
) -> None:
    order = await _order(client, shop)
    transport.enqueue("POST", "/create_order", body=bodies.create_ok(invoice=order["order_number"]))
    await booking.book(uuid.UUID(order["id"]))

    consignment = await _consignment(db, order["id"])
    assert consignment is not None
    events = list(
        (
            await db.execute(
                sa.select(CourierEvent).where(CourierEvent.consignment_id == consignment.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].raw_status == "in_review"
    assert events[0].normalized_status == str(ConsignmentStatus.BOOKED)
    assert events[0].status_undocumented is False
