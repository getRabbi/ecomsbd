"""Courier account connection, credential storage and validation.

The claims under test are the ones that lose a seller's trust or their data:

* a credential is never returned, logged, or stored in plaintext;
* a provider outage never marks a working key as wrong;
* only a repeated *deterministic* rejection moves an account to
  ``NEEDS_RECONNECT``;
* an encrypted credential cannot be moved between rows or shops.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from tests.conftest_commerce import signed_in_shop
from tests.fixtures.steadfast import bodies
from tests.test_auth_flow import auth_header, sign_in

from app.core.security import CredentialVault
from app.couriers.accounts import ConnectRequest, CourierAccountService
from app.couriers.models import (
    AUTH_FAILURES_BEFORE_RECONNECT,
    CourierAccount,
    CourierAccountStatus,
    CredentialValidation,
)
from app.couriers.registry import CourierAdapterRegistry
from app.couriers.steadfast.adapter import SteadfastAdapter
from app.couriers.steadfast.client import SteadfastClient, SteadfastConfig
from app.couriers.steadfast.transport import FakeSteadfastTransport

# Synthetic. Deliberately *not* shaped like a live key: a test constant that
# looks production-shaped trains reviewers to wave the pattern through, and a
# secret scanner is right to flag one.
API_KEY = "synthetic-courier-key-for-tests-abcd"
SECRET_KEY = "synthetic-courier-secret-for-tests-wxyz"


@pytest.fixture
async def shop(client: AsyncClient, unique_phone: str) -> dict[str, Any]:
    """A signed-in shop, with the tenant installed into the ambient context.

    The context is what the tenancy guard scopes every query to, so a
    service-level test has to set it exactly as a request would.
    """
    from app.core.context import RequestContext, set_context

    session = await signed_in_shop(client, unique_phone, shop_name="Courier Shop")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
    return session


async def _no_sleep(_seconds: float) -> None:
    return None


def _service(db: Any, transport: FakeSteadfastTransport, settings: Any) -> CourierAccountService:
    def _factory() -> SteadfastAdapter:
        return SteadfastAdapter(
            SteadfastClient(transport, config=SteadfastConfig(max_read_retries=0), sleep=_no_sleep)
        )

    return CourierAccountService(
        db,
        vault=CredentialVault(settings),
        registry=CourierAdapterRegistry({"steadfast": _factory}),
    )


def _connect() -> ConnectRequest:
    return ConnectRequest(provider="steadfast", api_key=API_KEY, secret_key=SECRET_KEY)


# ------------------------------------------------------------- connecting --


async def test_connecting_stores_encrypted_credentials(db, settings, shop) -> None:
    transport = FakeSteadfastTransport().enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    service = _service(db, transport, settings)

    account, outcome = await service.connect(_connect())

    assert outcome.result is CredentialValidation.VALID
    assert account.account_status is CourierAccountStatus.CONNECTED
    # Ciphertext, not plaintext.
    assert account.api_key_encrypted is not None
    assert API_KEY not in account.api_key_encrypted
    assert SECRET_KEY not in (account.secret_key_encrypted or "")
    assert account.masked_identifier == "****abcd"


async def test_credentials_round_trip_through_the_vault(db, settings, shop) -> None:
    transport = FakeSteadfastTransport().always(body=bodies.BALANCE_OK)
    service = _service(db, transport, settings)

    account, _ = await service.connect(_connect())
    credentials = service.credentials_for(account)

    assert credentials.api_key == API_KEY
    assert credentials.secret_key == SECRET_KEY


async def test_a_ciphertext_cannot_be_moved_to_another_account(db, settings, shop) -> None:
    """The vault binds each envelope to its own row id as AAD.

    Without that, copying a ciphertext into another shop's row would silently
    work and one shop would be booking with another's credentials.
    """
    transport = FakeSteadfastTransport().always(body=bodies.BALANCE_OK)
    service = _service(db, transport, settings)
    account, _ = await service.connect(_connect())

    impostor = CourierAccount(
        provider="pathao",
        api_key_encrypted=account.api_key_encrypted,
        secret_key_encrypted=account.secret_key_encrypted,
        status=str(CourierAccountStatus.CONNECTED),
    )
    db.add(impostor)
    await db.flush()

    with pytest.raises(Exception):  # noqa: B017 - cryptography raises InvalidTag
        service.credentials_for(impostor)


async def test_public_view_never_contains_a_credential(db, settings, shop) -> None:
    transport = FakeSteadfastTransport().always(body=bodies.BALANCE_OK)
    service = _service(db, transport, settings)
    account, _ = await service.connect(_connect())

    rendered = str(account.public_view())

    assert API_KEY not in rendered
    assert SECRET_KEY not in rendered
    assert account.api_key_encrypted not in rendered
    assert "****abcd" in rendered


async def test_a_rejected_key_is_never_stored(db, settings, shop) -> None:
    transport = FakeSteadfastTransport().always(status_code=401, body="{}")
    service = _service(db, transport, settings)

    with pytest.raises(Exception) as caught:
        await service.connect(_connect())

    assert "INVALID_COURIER_CREDENTIALS" in str(getattr(caught.value, "code", ""))
    assert await service.for_provider("steadfast") is None


async def test_a_provider_outage_still_stores_the_key_but_flags_it(db, settings, shop) -> None:
    """Refusing to save a correct key during an outage blames the seller."""
    transport = FakeSteadfastTransport().always(status_code=503, body="{}")
    service = _service(db, transport, settings)

    account, outcome = await service.connect(_connect())

    assert outcome.result is CredentialValidation.PROVIDER_UNAVAILABLE
    assert account.has_credentials is True
    assert account.account_status is CourierAccountStatus.NEEDS_RECONNECT
    assert account.is_usable is False


async def test_reconnecting_replaces_credentials_in_place(db, settings, shop) -> None:
    transport = FakeSteadfastTransport().always(body=bodies.BALANCE_OK)
    service = _service(db, transport, settings)

    first, _ = await service.connect(_connect())
    original_id = first.id
    original_cipher = first.api_key_encrypted

    second, _ = await service.connect(
        ConnectRequest(provider="steadfast", api_key="sfk-new-key-9999", secret_key="sfs-new-9999")
    )

    assert second.id == original_id, "one account per provider per shop"
    assert second.api_key_encrypted != original_cipher
    assert second.masked_identifier == "****9999"

    count = await db.scalar(
        sa.select(sa.func.count())
        .select_from(CourierAccount)
        .where(CourierAccount.provider == "steadfast")
    )
    assert count == 1


# ------------------------------------------------------------- validating --


async def test_one_rejection_does_not_disconnect_an_account(db, settings, shop) -> None:
    """A single 401 during a provider deployment must not log a shop out."""
    transport = FakeSteadfastTransport().enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    service = _service(db, transport, settings)
    account, _ = await service.connect(_connect())

    transport.always(status_code=401, body="{}")
    outcome = await service.test_connection("steadfast")

    assert outcome.result is CredentialValidation.INVALID
    assert account.consecutive_auth_failures == 1
    assert account.account_status is CourierAccountStatus.CONNECTED


async def test_repeated_rejections_move_the_account_to_needs_reconnect(db, settings, shop) -> None:
    transport = FakeSteadfastTransport().enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    service = _service(db, transport, settings)
    account, _ = await service.connect(_connect())

    transport.always(status_code=403, body="{}")
    for _ in range(AUTH_FAILURES_BEFORE_RECONNECT):
        await service.test_connection("steadfast")

    assert account.account_status is CourierAccountStatus.NEEDS_RECONNECT
    assert account.is_usable is False


async def test_a_timeout_never_counts_against_a_working_key(db, settings, shop) -> None:
    transport = FakeSteadfastTransport().enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    service = _service(db, transport, settings)
    account, _ = await service.connect(_connect())

    transport.always(status_code=500, body="{}")
    for _ in range(AUTH_FAILURES_BEFORE_RECONNECT + 2):
        outcome = await service.test_connection("steadfast")
        assert outcome.result is CredentialValidation.PROVIDER_UNAVAILABLE

    assert account.consecutive_auth_failures == 0
    assert account.account_status is CourierAccountStatus.CONNECTED
    assert account.is_usable is True


async def test_a_successful_check_clears_the_failure_streak(db, settings, shop) -> None:
    transport = FakeSteadfastTransport().enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    service = _service(db, transport, settings)
    account, _ = await service.connect(_connect())

    transport.always(status_code=401, body="{}")
    await service.test_connection("steadfast")
    assert account.consecutive_auth_failures == 1

    transport.always(body=bodies.BALANCE_OK)
    await service.test_connection("steadfast")

    assert account.consecutive_auth_failures == 0
    assert account.account_status is CourierAccountStatus.CONNECTED


# ----------------------------------------------------------- disconnecting --


async def test_disconnect_erases_credentials_but_keeps_the_row(db, settings, shop) -> None:
    transport = FakeSteadfastTransport().always(body=bodies.BALANCE_OK)
    service = _service(db, transport, settings)
    account, _ = await service.connect(_connect())
    account_id = account.id

    disconnected = await service.disconnect("steadfast")

    assert disconnected.id == account_id, "the row survives for audit"
    assert disconnected.api_key_encrypted is None
    assert disconnected.secret_key_encrypted is None
    assert disconnected.account_status is CourierAccountStatus.DISCONNECTED
    assert disconnected.is_usable is False


async def test_usable_account_returns_none_rather_than_raising(db, settings, shop) -> None:
    """Manual courier mode is the normal fallback, not an error path."""
    transport = FakeSteadfastTransport().always(body=bodies.BALANCE_OK)
    service = _service(db, transport, settings)

    assert await service.usable_account("steadfast") is None

    await service.connect(_connect())
    assert await service.usable_account("steadfast") is not None

    await service.disconnect("steadfast")
    assert await service.usable_account("steadfast") is None


# -------------------------------------------------------------- balance ---


async def test_reported_balance_is_kept_separate_from_cod(db, settings, shop) -> None:
    """The provider's balance is its own figure, never an ecomsbd receivable."""
    transport = FakeSteadfastTransport().always(body=bodies.BALANCE_OK)
    service = _service(db, transport, settings)
    account, _ = await service.connect(_connect())

    await service.record_balance(account, balance_paisa=123_456)
    view = account.public_view()

    assert view["reported_balance_paisa"] == 123_456
    # There is no field here that could be mistaken for a COD total.
    assert "cod" not in str(view).lower()


# ------------------------------------------------------------------ API ---


class TestCourierApi:
    async def test_connect_and_read_back_never_exposes_the_secret(
        self, client: AsyncClient, unique_phone: str, steadfast_transport
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Courier API Shop")
        steadfast_transport.always(body=bodies.BALANCE_OK)

        created = await client.post(
            "/v1/couriers/accounts/steadfast/connect",
            headers=auth_header(session),
            json={"api_key": API_KEY, "secret_key": SECRET_KEY},
        )
        assert created.status_code == 201, created.text
        assert API_KEY not in created.text
        assert SECRET_KEY not in created.text
        assert created.json()["account"]["masked_identifier"] == "****abcd"

        listed = await client.get("/v1/couriers/accounts", headers=auth_header(session))
        assert listed.status_code == 200
        assert API_KEY not in listed.text
        assert SECRET_KEY not in listed.text
        assert listed.json()[0]["connected"] is True

    async def test_provider_evidence_reports_what_is_unknown(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Courier API Shop")
        response = await client.get(
            "/v1/couriers/providers/steadfast/evidence", headers=auth_header(session)
        )

        assert response.status_code == 200
        body = response.json()
        assert body["documentation_version"] == "V1"
        assert body["unknowns"]["WEBHOOK_CONTRACT"] == "unknown"
        assert "STEADFAST_WEBHOOK_CONTRACT_REQUIRED" in body["blockers"]
        assert body["manual_fallback"]

    async def test_disconnect_requires_the_credential_permission(
        self, client: AsyncClient, unique_phone: str, steadfast_transport
    ) -> None:
        """A packer can book; only an owner touches the keys that do the booking."""
        from app.tenants.models import TenantUser
        from app.tenants.roles import TenantRole

        session = await signed_in_shop(client, unique_phone, shop_name="Courier API Shop")
        steadfast_transport.always(body=bodies.BALANCE_OK)
        await client.post(
            "/v1/couriers/accounts/steadfast/connect",
            headers=auth_header(session),
            json={"api_key": API_KEY, "secret_key": SECRET_KEY},
        )

        # Demote the member to PACKER and re-authenticate.
        from app.core.config import get_settings
        from app.db.session import get_sessionmaker
        from app.db.tenancy import mark_session_system

        factory = get_sessionmaker(get_settings())
        async with factory() as raw:
            mark_session_system(raw.sync_session, "test: demote member")
            await raw.execute(
                sa.update(TenantUser)
                .where(TenantUser.user_id == uuid.UUID(session["user_id"]))
                .values(role=str(TenantRole.PACKER))
            )
            await raw.commit()

        refreshed = await sign_in(client, unique_phone)
        blocked = await client.delete(
            "/v1/couriers/accounts/steadfast", headers=auth_header(refreshed)
        )
        assert blocked.status_code == 403
