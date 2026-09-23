"""Multi-courier operations: three providers, one set of business rules.

The claim this file defends is the one module 3 exists for: **generic booking
code knows nothing about any particular courier.** Before this, the booking
service called Steadfast's payload builder for every provider, so a Pathao
booking recorded a Steadfast-shaped request in ``courier_raw_payloads`` — an
evidence row describing a call nobody made, in the one table whose whole purpose
is explaining a result months later.

These are unit tests against the adapter seam and the readiness rules, so they
run without the database fixtures the V1 courier suites need.
"""

from __future__ import annotations

import uuid

import pytest

from app.common.money import BDT, Money
from app.couriers.adapter import BookingPreview, BookingRequest, CourierAdapter
from app.couriers.capabilities import Capability, load_all_manifests
from app.couriers.credentials import spec_for
from app.couriers.http import FakeProviderTransport
from app.couriers.pathao.adapter import PathaoAdapter
from app.couriers.pathao.client import PathaoClient, PathaoConfig
from app.couriers.redx.adapter import RedxAdapter
from app.couriers.redx.client import RedxClient, RedxConfig
from app.couriers.registry import build_registry
from app.couriers.steadfast.adapter import SteadfastAdapter
from app.couriers.steadfast.client import SteadfastClient, SteadfastConfig
from app.couriers.steadfast.transport import FakeSteadfastTransport

ALL_PROVIDERS = ("steadfast", "pathao", "redx")


def _booking(**overrides: object) -> BookingRequest:
    base: dict[str, object] = {
        "order_id": uuid.uuid4(),
        "merchant_reference": "CP-20260918-0042",
        "recipient_name": "Rahim Uddin",
        "recipient_phone_e164": "+8801712345678",
        "recipient_address": "House 12, Road 5, Dhanmondi, Dhaka",
        "cod_amount": Money(paisa=105000, currency=BDT),
        "item_description": "Cotton shirt",
        "item_quantity": 2,
        "store_reference": "12345",
    }
    base.update(overrides)
    return BookingRequest(**base)  # type: ignore[arg-type]


def _steadfast() -> SteadfastAdapter:
    return SteadfastAdapter(SteadfastClient(FakeSteadfastTransport(), config=SteadfastConfig()))


def _pathao() -> PathaoAdapter:
    return PathaoAdapter(
        PathaoClient(FakeProviderTransport(provider="pathao"), config=PathaoConfig())
    )


def _redx() -> RedxAdapter:
    return RedxAdapter(RedxClient(FakeProviderTransport(provider="redx"), config=RedxConfig()))


# ------------------------------------------------------------- coexistence --


def test_all_three_couriers_are_registered_together() -> None:
    registry = build_registry()

    for provider in ALL_PROVIDERS:
        assert registry.supports(provider), provider
        assert registry.get(provider) is not None, provider


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_every_adapter_satisfies_the_one_contract(provider: str) -> None:
    """One interface, three providers. This is what keeps the domain generic."""
    adapter = build_registry().get(provider)

    assert isinstance(adapter, CourierAdapter)
    assert adapter is not None
    assert adapter.provider == provider


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_every_adapter_declares_its_own_batch_size(provider: str) -> None:
    """Each provider's blast radius when a batch fails ambiguously is its own.

    Generic booking reads this from the adapter, so it no longer falls back to
    Steadfast's setting when chunking a Pathao batch.
    """
    adapter = build_registry().get(provider)
    assert adapter is not None

    size = adapter.bulk_chunk_size
    assert isinstance(size, int)
    assert size >= 1


# --------------------------------------------------- the describe_booking seam --


def test_each_adapter_describes_its_own_payload_shape() -> None:
    """A Pathao evidence row must describe the Pathao call, not a Steadfast one."""
    request = _booking()

    steadfast = _steadfast().describe_booking(request, "CP-20260918-0042")
    pathao = _pathao().describe_booking(request, "CP-20260918-0042")

    assert isinstance(steadfast, BookingPreview)
    assert isinstance(pathao, BookingPreview)

    # Steadfast's documented field names.
    assert "invoice" in steadfast.redacted_payload
    assert "cod_amount" in steadfast.redacted_payload

    # Pathao's own, which are different ones.
    assert "merchant_order_id" in pathao.redacted_payload
    assert "amount_to_collect" in pathao.redacted_payload
    assert pathao.redacted_payload["store_id"] == 12345
    # Pathao's auto-address path: the location ids are omitted, not blank.
    assert "recipient_city" not in pathao.redacted_payload

    # The two payloads genuinely differ; before this seam they were identical.
    assert set(steadfast.redacted_payload) != set(pathao.redacted_payload)


@pytest.mark.parametrize("build", [_steadfast, _pathao], ids=["steadfast", "pathao"])
def test_a_described_payload_is_safe_to_store(build) -> None:
    """It is persisted as evidence, so it must already be masked at rest."""
    preview = build().describe_booking(_booking(), "CP-20260918-0042")

    serialised = repr(preview.redacted_payload)
    assert "01712345678" not in serialised
    assert "+8801712345678" not in serialised
    assert preview.recipient_phone_masked
    assert "01712345678" not in preview.recipient_phone_masked


@pytest.mark.parametrize("build", [_steadfast, _pathao], ids=["steadfast", "pathao"])
def test_the_cod_split_agrees_across_providers(build) -> None:
    """Both couriers collect banknotes, so both are asked for whole taka.

    The residual is reported rather than dropped: a receivable must never be
    quietly a few paisa away from the cash that can actually arrive.
    """
    preview = build().describe_booking(
        _booking(cod_amount=Money(paisa=105050, currency=BDT)), "CP-20260918-0042"
    )

    assert preview.cod_taka == 1051
    assert preview.cod_residual_paisa == 105050 - 105100


@pytest.mark.parametrize("build", [_steadfast, _pathao], ids=["steadfast", "pathao"])
def test_a_locally_invalid_booking_is_refused_before_anything_is_sent(build) -> None:
    """ValueError is the adapter saying "I would reject this myself".

    Raised from describe_booking, which the booking service calls before an
    attempt row exists — so a bad phone number never becomes a persisted
    attempt or a provider call.
    """
    with pytest.raises(ValueError):
        build().describe_booking(_booking(recipient_phone_e164="+12025550123"), "CP-1")


def test_pathao_refuses_a_booking_with_no_pickup_store() -> None:
    """Pathao rejects every create without store_id, so we refuse first."""
    with pytest.raises(ValueError, match="pickup store"):
        _pathao().describe_booking(_booking(store_reference=None), "CP-1")


def test_steadfast_does_not_care_about_a_pickup_store() -> None:
    """A provider-specific requirement stays that provider's business."""
    preview = _steadfast().describe_booking(_booking(store_reference=None), "CP-1")

    assert preview.redacted_payload["invoice"] == "CP-1"


def test_redx_refuses_a_booking_with_no_delivery_area() -> None:
    """RedX requires a delivery area id and name on every create.

    Refused here, before anything is persisted, rather than sent without one
    and discovered after a parcel might exist.
    """
    with pytest.raises(ValueError, match="delivery area"):
        _redx().describe_booking(_booking(), "CP-1")


def test_redx_describes_its_own_payload_shape() -> None:
    preview = _redx().describe_booking(
        _booking(delivery_area_id="12", delivery_area_name="Mirpur DOHS"), "CP-20260918-0042"
    )

    assert preview.redacted_payload["merchant_invoice_id"] == "CP-20260918-0042"
    assert preview.redacted_payload["delivery_area_id"] == 12
    assert preview.redacted_payload["delivery_area"] == "Mirpur DOHS"
    assert preview.redacted_payload["cash_collection_amount"] == "1050"
    # Stored as evidence, so it must already be masked at rest.
    assert "01712345678" not in repr(preview.redacted_payload)
    assert preview.cod_taka == 1050


# ------------------------------------------------------------- readiness --


def test_only_couriers_with_a_verified_create_can_be_offered() -> None:
    """The UI reacts to capability, not to a provider name.

    Steadfast, Pathao and RedX have a documented create; manual mode does not,
    and must never appear as a booking option.
    """
    manifests = load_all_manifests()

    assert manifests["steadfast"].supports(Capability.CREATE_SINGLE)
    assert manifests["pathao"].supports(Capability.CREATE_SINGLE)
    assert manifests["redx"].supports(Capability.CREATE_SINGLE)
    assert not manifests["manual"].supports(Capability.CREATE_SINGLE)


def test_a_courier_needing_a_store_says_so_in_its_declaration() -> None:
    """So the client can tell "connected" from "bookable" without a provider check."""
    pathao = spec_for("pathao")
    steadfast = spec_for("steadfast")
    redx = spec_for("redx")
    assert pathao is not None and steadfast is not None and redx is not None

    assert pathao.requires_store
    assert not steadfast.requires_store
    # RedX's pickup_store_id is optional: a store may be chosen, never must be.
    assert not redx.requires_store
    assert redx.as_dict()["supports_store"] is True
    # ...but every RedX booking must name a delivery area.
    assert redx.requires_delivery_area
    assert not pathao.requires_delivery_area and not steadfast.requires_delivery_area


# ------------------------------------------------- which couriers can book --


class _FakeHealth:
    """Provider health, without a database."""

    def __init__(self, *, allows: bool = True) -> None:
        self._allows = allows

    async def allows(self, provider: str, *, capability: str = "*", tenant_id=None) -> bool:
        return self._allows


class _FakeAccount:
    """Just the fields the bookability ladder reads."""

    def __init__(
        self,
        provider: str,
        *,
        connected: bool = True,
        usable: bool = True,
        store_id: str | None = None,
        store_name: str | None = None,
    ) -> None:
        self.provider = provider
        self.has_credentials = connected
        self.is_usable = usable
        self.tenant_id = uuid.uuid4()
        self.metadata_json = {}
        if store_id:
            self.metadata_json["store_id"] = store_id
        if store_name:
            self.metadata_json["store_name"] = store_name


def _service(accounts: list[_FakeAccount], *, healthy: bool = True):
    """A CourierAccountService with its two I/O points stubbed.

    Everything the ladder decides on — manifests, credential declarations,
    account state — is pure, so this exercises the real logic.
    """
    from app.couriers.accounts import CourierAccountService

    service = CourierAccountService.__new__(CourierAccountService)
    service._health = _FakeHealth(allows=healthy)  # type: ignore[attr-defined]

    async def _list_accounts():
        return accounts

    service.list_accounts = _list_accounts  # type: ignore[method-assign]
    return service


def _by_provider(rows) -> dict[str, object]:
    return {row.provider: row for row in rows}


@pytest.mark.asyncio
async def test_a_courier_with_nothing_to_connect_is_not_a_booking_option() -> None:
    """Manual mode is absent entirely, not listed as broken.

    Offering a courier that cannot be connected would make the picker a list of
    disappointments.
    """
    rows = _by_provider(await _service([]).bookable_couriers())

    assert "manual" not in rows
    assert set(rows) == {"steadfast", "pathao", "redx"}


@pytest.mark.asyncio
async def test_an_unconnected_courier_reports_exactly_that() -> None:
    from app.couriers.accounts import BookableReason

    rows = _by_provider(await _service([]).bookable_couriers())

    for provider in ("steadfast", "pathao", "redx"):
        assert not rows[provider].bookable  # type: ignore[attr-defined]
        assert rows[provider].reason is BookableReason.NOT_CONNECTED  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_connected_courier_without_its_pickup_store_is_not_bookable() -> None:
    """The distinction module 3 exists for.

    Pathao rejects every create without store_id. Reporting it as bookable
    would spend a seller's attempt on an order they were trying to ship.
    """
    from app.couriers.accounts import BookableReason

    rows = _by_provider(
        await _service([_FakeAccount("pathao"), _FakeAccount("steadfast")]).bookable_couriers()
    )

    assert not rows["pathao"].bookable  # type: ignore[attr-defined]
    assert rows["pathao"].reason is BookableReason.NEEDS_PICKUP_STORE  # type: ignore[attr-defined]
    assert rows["pathao"].requires_store is True  # type: ignore[attr-defined]

    # Steadfast needs no store, so being connected is enough.
    assert rows["steadfast"].bookable  # type: ignore[attr-defined]
    assert rows["steadfast"].requires_store is False  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_both_couriers_are_bookable_once_each_is_ready() -> None:
    rows = _by_provider(
        await _service(
            [
                _FakeAccount("pathao", store_id="12345", store_name="Mirpur"),
                _FakeAccount("steadfast"),
            ]
        ).bookable_couriers()
    )

    assert rows["pathao"].bookable  # type: ignore[attr-defined]
    assert rows["pathao"].store_name == "Mirpur"  # type: ignore[attr-defined]
    assert rows["steadfast"].bookable  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_courier_needing_reconnection_says_so_rather_than_not_connected() -> None:
    """Two different problems with two different fixes."""
    from app.couriers.accounts import BookableReason

    rows = _by_provider(
        await _service([_FakeAccount("steadfast", usable=False)]).bookable_couriers()
    )

    assert rows["steadfast"].reason is BookableReason.NEEDS_RECONNECT  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_an_open_breaker_withholds_a_courier_that_is_otherwise_ready() -> None:
    """We already know it is failing; every ambiguous create is a lost parcel."""
    from app.couriers.accounts import BookableReason

    rows = _by_provider(
        await _service([_FakeAccount("steadfast")], healthy=False).bookable_couriers()
    )

    assert not rows["steadfast"].bookable  # type: ignore[attr-defined]
    assert rows["steadfast"].reason is BookableReason.PROVIDER_UNAVAILABLE  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_courier_the_shop_was_not_given_is_reported_before_anything_else() -> None:
    """So "connect it" is never shown for a courier the shop cannot use."""
    from app.couriers.accounts import BookableReason

    rows = _by_provider(
        await _service([]).bookable_couriers(enabled={"pathao": False, "steadfast": True})
    )

    assert rows["pathao"].reason is BookableReason.NOT_ENABLED  # type: ignore[attr-defined]
    # Steadfast is enabled, so it reports the next thing to fix instead.
    assert rows["steadfast"].reason is BookableReason.NOT_CONNECTED  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_bookable_couriers_come_first() -> None:
    """The picker should open on something a seller can actually use."""
    rows = await _service([_FakeAccount("steadfast"), _FakeAccount("pathao")]).bookable_couriers()

    assert [row.bookable for row in rows] == sorted((row.bookable for row in rows), reverse=True)


@pytest.mark.asyncio
async def test_a_courier_declares_whether_a_delivery_type_may_be_offered() -> None:
    """The booking sheet reads this instead of checking a provider name.

    Steadfast documents two delivery types and its API takes them. Pathao
    documents two as well, but the booking payload's `delivery_type` field is
    Steadfast-shaped (0 or 1) while Pathao's values are 48 and 12 — so offering
    the choice would send Pathao a value it does not recognise.
    """
    rows = _by_provider(
        await _service(
            [
                _FakeAccount("steadfast"),
                _FakeAccount("pathao", store_id="12345"),
            ]
        ).bookable_couriers()
    )

    assert rows["steadfast"].supports_delivery_type is True  # type: ignore[attr-defined]
    assert rows["pathao"].supports_delivery_type is False  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_connected_redx_account_is_bookable_without_a_store() -> None:
    """RedX needs no pickup store, but tells the booking sheet to ask for an area."""
    rows = _by_provider(await _service([_FakeAccount("redx")]).bookable_couriers())

    assert rows["redx"].bookable  # type: ignore[attr-defined]
    assert rows["redx"].requires_store is False  # type: ignore[attr-defined]
    assert rows["redx"].requires_delivery_area is True  # type: ignore[attr-defined]
    assert rows["redx"].as_dict()["requires_delivery_area"] is True  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_redx_stays_off_for_a_shop_that_was_not_given_it() -> None:
    from app.couriers.accounts import BookableReason

    rows = _by_provider(
        await _service([_FakeAccount("redx")]).bookable_couriers(enabled={"redx": False})
    )

    assert not rows["redx"].bookable  # type: ignore[attr-defined]
    assert rows["redx"].reason is BookableReason.NOT_ENABLED  # type: ignore[attr-defined]
