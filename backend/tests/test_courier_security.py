"""Tenant isolation, permissions, PII and concurrency for the courier integration.

Brief sections 50 and 51. Every test here is about something that is not
supposed to be *possible*, rather than about something working:

* one shop cannot reach another shop's courier account, parcels or payments;
* a role without the permission cannot book, request a return, or see keys;
* a credential never appears in a response, an audit row, a raw payload or a
  log line;
* two concurrent booking attempts for one order produce one parcel.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.fixtures.steadfast import bodies
from tests.test_auth_flow import auth_header, sign_in

from app.common.audit import AuditLog
from app.consignments.models import Consignment, ConsignmentStatus
from app.consignments.service import ConsignmentService
from app.core.context import RequestContext, set_context
from app.core.errors import ConflictError
from app.core.redaction import redact_value
from app.core.security import CredentialVault
from app.couriers.accounts import ConnectRequest, CourierAccountService
from app.couriers.booking import CourierBookingService
from app.couriers.models import CourierAccount, CourierRawPayload
from app.couriers.registry import CourierAdapterRegistry
from app.couriers.steadfast.adapter import SteadfastAdapter
from app.couriers.steadfast.client import SteadfastClient, SteadfastConfig, SteadfastCredentials
from app.couriers.steadfast.transport import FakeSteadfastTransport
from app.money.service import ReceivableService
from app.tenants.models import TenantUser
from app.tenants.roles import Permission, TenantRole, has_permission

API_KEY = "sfk-security-test-key-abcd"
SECRET_KEY = "sfs-security-test-secret-wxyz"


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
def transport() -> FakeSteadfastTransport:
    return FakeSteadfastTransport()


@pytest.fixture
def registry(transport: FakeSteadfastTransport) -> CourierAdapterRegistry:
    def _factory() -> SteadfastAdapter:
        return SteadfastAdapter(
            SteadfastClient(transport, config=SteadfastConfig(max_read_retries=0), sleep=_no_sleep)
        )

    return CourierAdapterRegistry({"steadfast": _factory})


# ------------------------------------------------------- tenant isolation --


async def test_a_shop_cannot_see_another_shops_courier_account(
    client: AsyncClient, db, settings, registry, transport
) -> None:
    """The tenancy guard covers the new tables, not only the old ones."""
    first = await signed_in_shop(client, "01711000001", shop_name="Shop One")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(first["tenant_id"])))
    service = CourierAccountService(db, vault=CredentialVault(settings), registry=registry)
    transport.always(body=bodies.BALANCE_OK)
    await service.connect(
        ConnectRequest(provider="steadfast", api_key=API_KEY, secret_key=SECRET_KEY)
    )
    await db.commit()

    second = await signed_in_shop(client, "01711000002", shop_name="Shop Two")
    listed = await client.get("/v1/couriers/accounts", headers=auth_header(second))

    assert listed.status_code == 200
    assert listed.json() == [], "the second shop sees none of the first shop's accounts"


async def test_a_shop_cannot_book_another_shops_order(
    client: AsyncClient, db, settings, registry, transport
) -> None:
    first = await signed_in_shop(client, "01711000011", shop_name="Owner Shop")
    product = await create_product(client, first, name="Theirs", sku=f"S-{uuid.uuid4().hex[:6]}")
    order = await create_order(
        client,
        first,
        items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": 100_000}],
        cod_amount_paisa=100_000,
        address="House 1, Dhaka",
    )
    order_id = order["order"]["id"]

    second = await signed_in_shop(client, "01711000012", shop_name="Other Shop")
    response = await client.post(
        f"/v1/couriers/orders/{order_id}/book", headers=auth_header(second), json={}
    )

    # 404, never 403: a 403 would confirm the other shop's order exists.
    assert response.status_code in (404, 409)
    assert transport.calls_to("POST", "/create_order") == []


async def test_a_shop_cannot_read_another_shops_parcel_tracking(
    client: AsyncClient, db, settings, registry, transport
) -> None:
    first = await signed_in_shop(client, "01711000021", shop_name="Tracked Shop")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(first["tenant_id"])))
    accounts = CourierAccountService(db, vault=CredentialVault(settings), registry=registry)
    transport.enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    await accounts.connect(
        ConnectRequest(provider="steadfast", api_key=API_KEY, secret_key=SECRET_KEY)
    )
    await db.commit()

    product = await create_product(client, first, name="Tracked", sku=f"S-{uuid.uuid4().hex[:6]}")
    order = await create_order(
        client,
        first,
        items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": 100_000}],
        cod_amount_paisa=100_000,
        address="House 2, Dhaka",
    )
    booking = CourierBookingService(
        db,
        accounts=accounts,
        consignments=ConsignmentService(db, receivables=ReceivableService(db)),
        vault=CredentialVault(settings),
        settings=settings,
    )
    transport.enqueue(
        "POST", "/create_order", body=bodies.create_ok(invoice=order["order"]["order_number"])
    )
    report = await booking.book(uuid.UUID(order["order"]["id"]))
    consignment_id = report.items[0].consignment_id
    assert consignment_id is not None
    # Release this session's write transaction: the HTTP client below opens a
    # second connection, and on SQLite it would block on the file lock.
    await db.commit()

    second = await signed_in_shop(client, "01711000022", shop_name="Nosy Shop")
    response = await client.get(
        f"/v1/couriers/consignments/{consignment_id}/tracking", headers=auth_header(second)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["attempts"] == []
    assert body["events"] == []


async def test_one_courier_account_per_shop_per_provider(
    client: AsyncClient, db, settings, registry, transport
) -> None:
    """Two accounts would make 'which key booked this parcel?' unanswerable."""
    shop = await signed_in_shop(client, "01711000031", shop_name="Single Shop")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(shop["tenant_id"])))
    service = CourierAccountService(db, vault=CredentialVault(settings), registry=registry)
    transport.always(body=bodies.BALANCE_OK)

    await service.connect(
        ConnectRequest(provider="steadfast", api_key=API_KEY, secret_key=SECRET_KEY)
    )
    await service.connect(
        ConnectRequest(provider="steadfast", api_key="sfk-second-key-9999", secret_key="s2")
    )

    count = await db.scalar(
        sa.select(sa.func.count())
        .select_from(CourierAccount)
        .where(CourierAccount.provider == "steadfast")
    )
    assert count == 1


# ------------------------------------------------------------ permissions --


def test_the_permission_matrix_keeps_packers_away_from_keys() -> None:
    """A packer can book; only an owner touches the keys that do the booking."""
    assert has_permission(TenantRole.PACKER, Permission.ORDER_BOOK) is True
    assert has_permission(TenantRole.PACKER, Permission.COURIER_CREDENTIAL_MANAGE) is False
    assert has_permission(TenantRole.MANAGER, Permission.COURIER_CREDENTIAL_MANAGE) is False
    assert has_permission(TenantRole.ACCOUNTANT, Permission.ORDER_BOOK) is False
    assert has_permission(TenantRole.VIEWER, Permission.ORDER_BOOK) is False
    assert has_permission(TenantRole.OWNER, Permission.COURIER_CREDENTIAL_MANAGE) is True


async def _demote(session_body: dict[str, Any], role: TenantRole) -> None:
    from app.core.config import get_settings
    from app.db.session import get_sessionmaker
    from app.db.tenancy import mark_session_system

    factory = get_sessionmaker(get_settings())
    async with factory() as raw:
        mark_session_system(raw.sync_session, "test: change member role")
        await raw.execute(
            sa.update(TenantUser)
            .where(TenantUser.user_id == uuid.UUID(session_body["user_id"]))
            .values(role=str(role))
        )
        await raw.commit()


async def test_a_viewer_cannot_book(client: AsyncClient, steadfast_transport) -> None:
    phone = "01711000041"
    shop = await signed_in_shop(client, phone, shop_name="Viewer Shop")
    product = await create_product(client, shop, name="V", sku=f"S-{uuid.uuid4().hex[:6]}")
    order = await create_order(
        client,
        shop,
        items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": 100_000}],
        cod_amount_paisa=100_000,
        address="House 3, Dhaka",
    )
    await _demote(shop, TenantRole.VIEWER)

    refreshed = await sign_in(client, phone)
    response = await client.post(
        f"/v1/couriers/orders/{order['order']['id']}/book",
        headers=auth_header(refreshed),
        json={},
    )

    assert response.status_code == 403
    assert steadfast_transport.calls_to("POST", "/create_order") == []


async def test_a_packer_cannot_read_courier_credentials(
    client: AsyncClient, steadfast_transport
) -> None:
    phone = "01711000042"
    shop = await signed_in_shop(client, phone, shop_name="Packer Shop")
    steadfast_transport.always(body=bodies.BALANCE_OK)
    created = await client.post(
        "/v1/couriers/accounts/steadfast/connect",
        headers=auth_header(shop),
        json={"api_key": API_KEY, "secret_key": SECRET_KEY},
    )
    assert created.status_code == 201

    await _demote(shop, TenantRole.PACKER)
    refreshed = await sign_in(client, phone)

    listed = await client.get("/v1/couriers/accounts", headers=auth_header(refreshed))
    assert listed.status_code == 403


async def test_an_accountant_cannot_request_a_return(client: AsyncClient) -> None:
    phone = "01711000043"
    shop = await signed_in_shop(client, phone, shop_name="Accountant Shop")
    await _demote(shop, TenantRole.ACCOUNTANT)
    refreshed = await sign_in(client, phone)

    response = await client.post(
        f"/v1/couriers/consignments/{uuid.uuid4()}/return",
        headers=auth_header(refreshed),
        json={"reason": "test"},
    )
    assert response.status_code == 403


# -------------------------------------------------------------------- PII --


async def test_no_response_in_the_courier_api_contains_a_credential(
    client: AsyncClient, steadfast_transport
) -> None:
    shop = await signed_in_shop(client, "01711000051", shop_name="Secret Shop")
    steadfast_transport.always(body=bodies.BALANCE_OK)

    responses = [
        await client.post(
            "/v1/couriers/accounts/steadfast/connect",
            headers=auth_header(shop),
            json={"api_key": API_KEY, "secret_key": SECRET_KEY},
        ),
        await client.get("/v1/couriers/accounts", headers=auth_header(shop)),
        await client.post("/v1/couriers/accounts/steadfast/test", headers=auth_header(shop)),
        await client.get("/v1/couriers/providers/steadfast/evidence", headers=auth_header(shop)),
    ]

    for response in responses:
        assert API_KEY not in response.text, response.request.url
        assert SECRET_KEY not in response.text, response.request.url


async def test_no_audit_row_contains_a_credential(
    client: AsyncClient, db, settings, registry, transport
) -> None:
    shop = await signed_in_shop(client, "01711000052", shop_name="Audited Shop")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(shop["tenant_id"])))
    service = CourierAccountService(db, vault=CredentialVault(settings), registry=registry)
    transport.always(body=bodies.BALANCE_OK)
    await service.connect(
        ConnectRequest(provider="steadfast", api_key=API_KEY, secret_key=SECRET_KEY)
    )
    await db.flush()

    rows = list((await db.execute(sa.select(AuditLog))).scalars().all())
    rendered = json.dumps([row.context for row in rows], default=str)

    assert API_KEY not in rendered
    assert SECRET_KEY not in rendered


async def test_a_stored_raw_payload_holds_no_credential_and_no_plain_phone(
    client: AsyncClient, db, settings, registry, transport
) -> None:
    """A raw payload is evidence for support, so the stored copy is already safe."""
    shop = await signed_in_shop(client, "01711000053", shop_name="Raw Shop")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(shop["tenant_id"])))
    accounts = CourierAccountService(db, vault=CredentialVault(settings), registry=registry)
    transport.enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    await accounts.connect(
        ConnectRequest(provider="steadfast", api_key=API_KEY, secret_key=SECRET_KEY)
    )
    await db.commit()

    product = await create_product(client, shop, name="Raw", sku=f"S-{uuid.uuid4().hex[:6]}")
    order = await create_order(
        client,
        shop,
        phone="01799887766",
        items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": 100_000}],
        cod_amount_paisa=100_000,
        address="House 4, Dhaka",
    )
    booking = CourierBookingService(
        db,
        accounts=accounts,
        consignments=ConsignmentService(db, receivables=ReceivableService(db)),
        vault=CredentialVault(settings),
        settings=settings,
    )
    transport.enqueue(
        "POST", "/create_order", body=bodies.create_ok(invoice=order["order"]["order_number"])
    )
    await booking.book(uuid.UUID(order["order"]["id"]))

    payloads = list((await db.execute(sa.select(CourierRawPayload))).scalars().all())
    rendered = json.dumps(
        [{"request": p.request_payload, "response": p.response_payload} for p in payloads],
        default=str,
    )

    assert API_KEY not in rendered
    assert SECRET_KEY not in rendered
    assert "01799887766" not in rendered, "the phone is masked before storage"
    assert "01799****66" in rendered


def test_the_log_redactor_scrubs_courier_credential_keys() -> None:
    scrubbed = redact_value(
        {
            "Api-Key": API_KEY,
            "Secret-Key": SECRET_KEY,
            "api_key": API_KEY,
            "secret_key": SECRET_KEY,
            "recipient_phone": "01799887766",
            "provider": "steadfast",
        }
    )

    assert API_KEY not in json.dumps(scrubbed)
    assert SECRET_KEY not in json.dumps(scrubbed)
    assert scrubbed["recipient_phone"] == "01799****66"
    assert scrubbed["provider"] == "steadfast"


def test_credentials_do_not_render_themselves_anywhere() -> None:
    credentials = SteadfastCredentials(api_key=API_KEY, secret_key=SECRET_KEY)

    for rendering in (repr(credentials), str(credentials), f"{credentials}", f"{credentials!r}"):
        assert API_KEY not in rendering
        assert SECRET_KEY not in rendering


# ------------------------------------------------------------ concurrency --


async def test_two_concurrent_bookings_produce_one_parcel(
    client: AsyncClient, db, settings, registry, transport
) -> None:
    """The check-and-set is durable, so the loser sees BOOKING and refuses.

    Concurrency on SQLite is serialised by the file lock, so this exercises the
    *state* guard rather than the row lock — which is the guard that actually
    has to hold, because the booking service commits before the provider call
    and therefore cannot rely on holding a lock across it.
    """
    shop = await signed_in_shop(client, "01711000061", shop_name="Racing Shop")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(shop["tenant_id"])))
    accounts = CourierAccountService(db, vault=CredentialVault(settings), registry=registry)
    transport.enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    await accounts.connect(
        ConnectRequest(provider="steadfast", api_key=API_KEY, secret_key=SECRET_KEY)
    )
    await db.commit()

    product = await create_product(client, shop, name="Race", sku=f"S-{uuid.uuid4().hex[:6]}")
    order = await create_order(
        client,
        shop,
        items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": 100_000}],
        cod_amount_paisa=100_000,
        address="House 5, Dhaka",
    )
    order_id = uuid.UUID(order["order"]["id"])

    booking = CourierBookingService(
        db,
        accounts=accounts,
        consignments=ConsignmentService(db, receivables=ReceivableService(db)),
        vault=CredentialVault(settings),
        settings=settings,
    )
    transport.enqueue(
        "POST", "/create_order", body=bodies.create_ok(invoice=order["order"]["order_number"])
    )

    first = await booking.book(order_id)
    assert first.booked == 1

    # The second attempt, arriving after the first has taken the order, is
    # refused without a provider call.
    with pytest.raises(ConflictError):
        await booking.book(order_id)

    assert len(transport.calls_to("POST", "/create_order")) == 1
    parcels = await db.scalar(
        sa.select(sa.func.count()).select_from(Consignment).where(Consignment.order_id == order_id)
    )
    assert parcels == 1


async def test_an_in_flight_booking_blocks_a_second_attempt(
    client: AsyncClient, db, settings, registry, transport
) -> None:
    """A parcel stuck in BOOKING is durable state, and it is what stops the race."""
    shop = await signed_in_shop(client, "01711000062", shop_name="InFlight Shop")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(shop["tenant_id"])))
    accounts = CourierAccountService(db, vault=CredentialVault(settings), registry=registry)
    transport.enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    await accounts.connect(
        ConnectRequest(provider="steadfast", api_key=API_KEY, secret_key=SECRET_KEY)
    )
    await db.commit()

    product = await create_product(client, shop, name="Stuck", sku=f"S-{uuid.uuid4().hex[:6]}")
    order = await create_order(
        client,
        shop,
        items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": 100_000}],
        cod_amount_paisa=100_000,
        address="House 6, Dhaka",
    )
    order_id = uuid.UUID(order["order"]["id"])
    booking = CourierBookingService(
        db,
        accounts=accounts,
        consignments=ConsignmentService(db, receivables=ReceivableService(db)),
        vault=CredentialVault(settings),
        settings=settings,
    )

    # Simulate a process that died between the commit and the provider answer:
    # the consignment is left in BOOKING.
    transport.enqueue(
        "POST", "/create_order", body=bodies.create_ok(invoice=order["order"]["order_number"])
    )
    await booking.book(order_id)
    parcel = (
        (await db.execute(sa.select(Consignment).where(Consignment.order_id == order_id)))
        .scalars()
        .first()
    )
    assert parcel is not None
    parcel.status = str(ConsignmentStatus.BOOKING)
    await db.commit()

    with pytest.raises(ConflictError, match="already in progress"):
        await booking.book(order_id)


async def test_the_same_order_twice_in_one_bulk_selection_books_once(
    client: AsyncClient, db, settings, registry, transport
) -> None:
    shop = await signed_in_shop(client, "01711000063", shop_name="Dup Shop")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(shop["tenant_id"])))
    accounts = CourierAccountService(db, vault=CredentialVault(settings), registry=registry)
    transport.enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    await accounts.connect(
        ConnectRequest(provider="steadfast", api_key=API_KEY, secret_key=SECRET_KEY)
    )
    await db.commit()

    product = await create_product(client, shop, name="Dup", sku=f"S-{uuid.uuid4().hex[:6]}")
    order = await create_order(
        client,
        shop,
        items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": 100_000}],
        cod_amount_paisa=100_000,
        address="House 7, Dhaka",
    )
    order_id = uuid.UUID(order["order"]["id"])
    booking = CourierBookingService(
        db,
        accounts=accounts,
        consignments=ConsignmentService(db, receivables=ReceivableService(db)),
        vault=CredentialVault(settings),
        settings=settings,
    )
    transport.enqueue(
        "POST",
        "/create_order/bulk-order",
        body=bodies.bulk_result(
            [
                {
                    "invoice": order["order"]["order_number"],
                    "consignment_id": 9900001,
                    "tracking_code": "T1",
                }
            ]
        ),
    )

    report = await booking.book_bulk([order_id, order_id])

    assert len(report.items) == 1
    parcels = await db.scalar(
        sa.select(sa.func.count()).select_from(Consignment).where(Consignment.order_id == order_id)
    )
    assert parcels == 1
