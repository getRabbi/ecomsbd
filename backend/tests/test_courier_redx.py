"""RedX: registered, honest, and inert.

No RedX documentation has been supplied, so the whole of this file defends one
claim: **RedX makes no call and claims no capability, and says why.**

These are not placeholder tests. They are the ones that would fail the day
someone implements RedX from a community package — which is the specific
mistake the capability manifest exists to prevent, and which for Pathao would
have shipped a login call asking sellers for their account password.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest

from app.common.money import BDT, Money
from app.couriers.adapter import BookingOutcome, BookingRequest, CourierAdapter, Unavailable
from app.couriers.capabilities import Capability, load_manifest
from app.couriers.redx.adapter import SUPPORTED_CAPABILITIES, RedxAdapter
from app.couriers.redx.contract import (
    CONTRACT_BLOCKER,
    REQUIRED_CONTRACT_ITEMS,
    UNAVAILABLE_REASON_BN,
    UNAVAILABLE_REASON_EN,
)
from app.couriers.registry import build_registry


def _booking() -> BookingRequest:
    return BookingRequest(
        order_id=uuid.uuid4(),
        merchant_reference="ECB-2001",
        recipient_name="Rahim Uddin",
        recipient_phone_e164="+8801712345678",
        recipient_address="House 12, Road 5, Dhanmondi, Dhaka",
        cod_amount=Money(paisa=105000, currency=BDT),
        item_description="Cotton shirt",
    )


# ------------------------------------------------------------- registration --


def test_redx_is_registered_rather_than_missing() -> None:
    """A registered adapter can explain itself; a `None` cannot.

    Before RedX was registered, every caller turned the missing adapter into
    "redx is not a courier ecomsbd can connect to", which reads as *never will
    be* and tells a seller nothing about what would change it.
    """
    registry = build_registry()

    assert registry.supports("redx")
    adapter = registry.get("redx")
    assert isinstance(adapter, RedxAdapter)
    assert isinstance(adapter, CourierAdapter)


def test_redx_coexists_with_steadfast_and_pathao() -> None:
    registry = build_registry()

    assert registry.supports("steadfast")
    assert registry.supports("pathao")
    assert registry.supports("redx")


# -------------------------------------------------------------- capabilities --


def test_redx_claims_no_capability() -> None:
    assert frozenset() == SUPPORTED_CAPABILITIES


@pytest.mark.asyncio
async def test_capabilities_are_empty_without_calling_the_provider() -> None:
    assert await RedxAdapter().capabilities({}) == set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability",
    [
        Capability.CREATE_BULK,
        Capability.STATUS_LOOKUP,
        Capability.PRICE_QUOTE,
        Capability.CANCEL,
        Capability.RETURNS,
        Capability.BALANCE,
        Capability.PAYOUTS,
        Capability.LIST_STORES,
        Capability.CUSTOMER_STATS,
    ],
)
async def test_every_capability_reports_unavailable_with_a_reason(
    capability: Capability,
) -> None:
    """`Unavailable` is falsy and carries its reason, so the UI can explain."""
    adapter = RedxAdapter()
    # Lazily, so only the capability under test is actually invoked: building
    # nine coroutines and awaiting one leaves eight unawaited.
    calls: dict[Capability, Callable[[], Awaitable[object]]] = {
        Capability.CREATE_BULK: lambda: adapter.create_bulk({}, [_booking()]),
        Capability.STATUS_LOOKUP: lambda: adapter.get_status({}, "RX1"),
        Capability.PRICE_QUOTE: lambda: adapter.quote({}, _booking()),
        Capability.CANCEL: lambda: adapter.cancel({}, "RX1"),
        Capability.RETURNS: lambda: adapter.request_return({}, "RX1", "damaged"),
        Capability.BALANCE: lambda: adapter.get_balance({}),
        Capability.PAYOUTS: lambda: adapter.list_payouts({}, since=None),  # type: ignore[arg-type]
        Capability.LIST_STORES: lambda: adapter.list_stores({}),
        Capability.CUSTOMER_STATS: lambda: adapter.customer_stats({}, "+8801712345678"),
    }

    result = await calls[capability]()

    assert isinstance(result, Unavailable)
    assert not result
    assert result.reason
    # Says what to do instead, rather than naming a missing document section.
    assert "manual" in result.reason.lower() or "by hand" in result.reason.lower()


# ------------------------------------------------------------- credentials --


@pytest.mark.asyncio
async def test_a_credential_check_is_inconclusive_never_a_rejection() -> None:
    """`rejected=True` would tell a seller their real token is wrong.

    It may not be. With nothing safe to call, "we could not find out" is both
    the honest answer and the safe one.
    """
    result = await RedxAdapter().validate_credentials({"token": "rx-live-0001"})

    assert not result.valid
    assert not result.rejected
    assert result.is_inconclusive
    assert result.detected_capabilities == frozenset()


# ---------------------------------------------------------------- booking --


@pytest.mark.asyncio
async def test_a_booking_fails_cleanly_and_is_never_ambiguous() -> None:
    """FAILED, not UNKNOWN: the request provably never left the process.

    Reporting it as ambiguous would put a parcel into booking recovery that has
    nothing to recover, and BOOKING_UNKNOWN is reserved for requests that may
    actually have reached a provider.
    """
    result = await RedxAdapter().create_consignment({}, _booking(), "ECB-2001")

    assert result.outcome is BookingOutcome.FAILED
    assert not result.is_ambiguous
    assert result.consignment is None
    assert result.error_code == CONTRACT_BLOCKER


# --------------------------------------------------------------- webhooks --


def test_a_webhook_is_never_verified_without_a_signature_scheme() -> None:
    """Accepting one would let anyone mark any parcel delivered."""
    adapter = RedxAdapter()

    assert adapter.verify_webhook({"x-redx-signature": "anything"}, b"{}") is False
    assert adapter.verify_webhook({}, b"{}") is False


def test_no_webhook_payload_is_parsed() -> None:
    body = b'{"event": "delivered", "tracking_id": "RX1"}'

    assert RedxAdapter().parse_webhook({}, body) == []


# --------------------------------------------------------------- manifest --


def test_the_manifest_claims_nothing_and_asserts_nothing() -> None:
    """Every capability is `unknown` — and none is `false`.

    A `false` would claim RedX does not offer something, and the documentation
    that would say so is exactly what is missing.
    """
    manifest = load_manifest("redx")
    assert manifest is not None

    assert manifest.is_fully_unverified
    assert manifest.verified_at is None
    for capability in Capability:
        state = manifest.state(capability)
        assert not state.is_usable, capability
        assert str(state) == "unknown", capability


def test_the_manifest_names_the_blocker_the_code_uses() -> None:
    """One string, so the manifest, the adapter and the console agree."""
    manifest = load_manifest("redx")
    assert manifest is not None

    assert CONTRACT_BLOCKER in manifest.blockers
    assert manifest.manual_fallback is not None


def test_the_missing_contract_is_enumerated_not_hand_waved() -> None:
    """An operator chasing RedX should know what to ask for in one message."""
    assert len(REQUIRED_CONTRACT_ITEMS) >= 10
    joined = " ".join(REQUIRED_CONTRACT_ITEMS).lower()
    for topic in ("base url", "authentication", "create parcel", "webhook", "status"):
        assert topic in joined, topic


def test_the_seller_facing_reason_exists_in_both_languages() -> None:
    for reason in (UNAVAILABLE_REASON_EN, UNAVAILABLE_REASON_BN):
        assert reason.strip()
    # Provider names stay English in Bangla copy, as they do everywhere else.
    assert "RedX" in UNAVAILABLE_REASON_BN


# ------------------------------------------------- refusing to hold a secret --


def test_credentials_cannot_be_stored_for_an_unverified_provider() -> None:
    """RedX credentials are refused, not saved-and-ignored.

    There is no call they could serve, so keeping them would mean holding a
    seller's secret for no purpose while showing a connected courier that
    cannot move a parcel. The error carries the blocker, so the client can say
    what would change it.

    ``_guard_inputs`` is exercised directly: it reads only the provider's
    declaration and manifest, so it needs no database, and this is the check
    that runs before anything is validated or written.
    """
    from app.core.errors import ErrorCode, ValidationError
    from app.couriers.accounts import ConnectRequest, CourierAccountService

    guard = CourierAccountService._guard_inputs
    request = ConnectRequest(
        provider="redx", api_key="rx-live-0001", secret_key="rx-secret-0001"
    ).cleaned()

    with pytest.raises(ValidationError) as caught:
        guard(None, request)  # type: ignore[arg-type]

    assert caught.value.code is ErrorCode.COURIER_PROVIDER_UNAVAILABLE
    assert caught.value.details is not None
    assert CONTRACT_BLOCKER in caught.value.details["blockers"]
    # Points at the path that does work rather than only refusing.
    assert "manual" in str(caught.value).lower()


def test_a_verified_provider_is_still_connectable() -> None:
    """The guard refuses the unverified, not the unfamiliar.

    Pathao has verified capabilities, so it must pass the same check RedX
    fails — otherwise the guard would have quietly broken module 1.
    """
    from app.couriers.accounts import ConnectRequest, CourierAccountService

    guard = CourierAccountService._guard_inputs
    for provider, primary, secondary in (
        ("pathao", "pathao-client-id-0001", "pathao-client-secret-0001"),
        ("steadfast", "sfk-live-0001", "sfs-live-0001"),
    ):
        request = ConnectRequest(provider=provider, api_key=primary, secret_key=secondary).cleaned()
        guard(None, request)  # type: ignore[arg-type]
