"""Pathao and RedX operator smoke checks (V4 WP0.7), against fakes only."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app import provider_smoke, provider_smoke_generic
from app.couriers.adapter import (
    BookingOutcome,
    BookingResult,
    ProviderConsignment,
    ProviderStatus,
    Store,
    ValidationResult,
)
from app.couriers.http import ProviderAuthError


@dataclass
class _Call:
    value: Any


@dataclass
class _Row:
    raw: dict[str, Any]
    city_id: int = 1
    zone_id: int = 10


@dataclass
class _Token:
    raw: dict[str, Any] = field(default_factory=lambda: {"access_token": "x", "expires_in": 432000})
    expires_in: int = 432000
    refresh_token: str | None = "r"


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def login(self, creds: Any) -> _Call:
        self.calls.append("login")
        return _Call(_Token())

    async def list_cities(self, creds: Any) -> _Call:
        self.calls.append("cities")
        return _Call([_Row({"city_id": 1, "city_name": "Dhaka"})])

    async def list_zones(self, creds: Any, city_id: int) -> _Call:
        self.calls.append(f"zones:{city_id}")
        return _Call([_Row({"zone_id": 10, "zone_name": "Mirpur"})])

    async def list_areas(self, creds: Any, zone_id: int | None = None) -> _Call:
        self.calls.append(f"areas:{zone_id}")
        return _Call([_Row({"area_id": 100, "area_name": "Mirpur 10", "secret_count": 5})])


class FakeAdapter:
    def __init__(self, *, valid: bool = True, auth_error: bool = False) -> None:
        self.client = FakeClient()
        self.valid = valid
        self.auth_error = auth_error
        self.created: list[Any] = []

    async def validate_credentials(self, creds: Any) -> ValidationResult:
        if self.auth_error:
            raise ProviderAuthError("refused")
        return ValidationResult(valid=self.valid, message=None if self.valid else "bad key")

    async def list_stores(self, creds: Any) -> list[Store]:
        return [Store(provider_store_id="st-1", name="Main")]

    async def get_status(self, creds: Any, reference: str) -> ProviderStatus:
        return ProviderStatus(
            provider_consignment_id=reference, raw_status="Delivered", normalized_status="DELIVERED"
        )

    async def create_consignment(
        self, creds: Any, req: Any, merchant_reference: str
    ) -> BookingResult:
        self.created.append(req)
        return BookingResult(
            outcome=BookingOutcome.BOOKED,
            consignment=ProviderConsignment(
                provider_consignment_id="C-1",
                tracking_code="T-1",
                merchant_reference=merchant_reference,
            ),
        )


class FakeRegistry:
    def __init__(self, adapter: FakeAdapter) -> None:
        self.adapter = adapter

    def get(self, provider: str) -> FakeAdapter:
        return self.adapter

    async def aclose(self) -> None:
        return None


def _args(provider: str, *extra: str) -> Any:
    args = provider_smoke.build_parser().parse_args([provider, "--env-credentials", *extra])
    args.create_invoice = None
    return args


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATHAO_SMOKE_CLIENT_ID", "client-id")
    monkeypatch.setenv("PATHAO_SMOKE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("REDX_SMOKE_API_TOKEN", "redx-token")


def _install(monkeypatch: pytest.MonkeyPatch, adapter: FakeAdapter) -> None:
    monkeypatch.setattr(
        provider_smoke_generic, "build_registry", lambda settings: FakeRegistry(adapter)
    )


def _names(report: Any) -> dict[str, Any]:
    return {r.name: r for r in report.results}


def test_the_parser_accepts_all_three_couriers() -> None:
    parser = provider_smoke.build_parser()
    for provider in ("steadfast", "pathao", "redx"):
        assert parser.parse_args([provider, "--env-credentials"]).provider == provider
    with pytest.raises(SystemExit):
        parser.parse_args(["dhl"])


async def test_read_only_by_default(monkeypatch: pytest.MonkeyPatch, env: None) -> None:
    adapter = FakeAdapter()
    _install(monkeypatch, adapter)
    report = await provider_smoke.run_smoke(_args("pathao", "--tracking-code", "T-9"))
    results = _names(report)
    assert results["authenticate"].ok and results["stores"].observed == {"count": 1}
    assert results["status"].detail == "status = 'DELIVERED'"
    assert results["undocumented shapes"].skipped and results["create parcel"].skipped
    assert adapter.client.calls == [] and adapter.created == []
    assert report.exit_code == 0


async def test_probe_records_shapes_not_values(monkeypatch: pytest.MonkeyPatch, env: None) -> None:
    adapter = FakeAdapter()
    _install(monkeypatch, adapter)
    report = await provider_smoke.run_smoke(_args("pathao", "--probe-undocumented"))
    results = _names(report)
    assert results["token (lifetime)"].observed["expires_in"] == 432000
    assert "access_token" in results["token (lifetime)"].observed["fields"]
    assert results["areas (first zone)"].observed == {
        "count": 1,
        "fields": ["area_id", "area_name", "secret_count"],
    }
    assert "Mirpur 10" not in str(report.as_dict()), "values never reach the transcript"
    assert adapter.client.calls == ["login", "cities", "zones:1", "areas:10"]


async def test_redx_probe_reads_the_area_list(monkeypatch: pytest.MonkeyPatch, env: None) -> None:
    adapter = FakeAdapter()
    _install(monkeypatch, adapter)
    report = await provider_smoke.run_smoke(_args("redx", "--probe-undocumented", "--sandbox"))
    assert _names(report)["areas"].observed["count"] == 1
    assert _names(report)["credentials"].observed == {"sandbox": True}


async def test_refused_credentials_stop_everything(
    monkeypatch: pytest.MonkeyPatch, env: None
) -> None:
    for adapter in (FakeAdapter(valid=False), FakeAdapter(auth_error=True)):
        _install(monkeypatch, adapter)
        report = await provider_smoke.run_smoke(_args("redx", "--probe-undocumented"))
        assert not _names(report)["authenticate"].ok
        assert "stores" not in _names(report) and report.exit_code != 0


async def test_create_needs_a_fixture_and_redx_an_area(
    monkeypatch: pytest.MonkeyPatch, env: None
) -> None:
    adapter = FakeAdapter()
    _install(monkeypatch, adapter)
    no_fixture = _args("pathao", "--allow-create")
    no_fixture.create_invoice = "SMOKE-1"
    report = await provider_smoke.run_smoke(no_fixture)
    assert not _names(report)["create parcel"].ok and adapter.created == []

    fixture = [
        "--allow-create",
        "--recipient-name",
        "Test",
        "--recipient-phone",
        "01700000061",
        "--recipient-address",
        "Mirpur 10, Dhaka",
    ]
    redx = _args("redx", *fixture)
    redx.create_invoice = "SMOKE-2"
    report = await provider_smoke.run_smoke(redx)
    assert "delivery-area" in _names(report)["create parcel"].detail and adapter.created == []

    cancelled = _args("pathao", *fixture)
    report = await provider_smoke.run_smoke(
        cancelled
    )  # create_invoice None: cancelled at the prompt
    assert _names(report)["create parcel"].skipped and adapter.created == []


async def test_a_confirmed_create_goes_through_the_adapter(
    monkeypatch: pytest.MonkeyPatch, env: None
) -> None:
    adapter = FakeAdapter()
    _install(monkeypatch, adapter)
    args = _args(
        "redx",
        "--allow-create",
        "--recipient-name", "Test",
        "--recipient-phone", "01700000062",
        "--recipient-address", "Mirpur 10, Dhaka",
        "--delivery-area-id", "100",
        "--delivery-area-name", "Mirpur 10",
        "--cod-amount", "50",
    )  # fmt: skip
    args.create_invoice = "SMOKE-3"
    report = await provider_smoke.run_smoke(args)
    created = _names(report)["create parcel"]
    assert created.ok and created.observed["tracking"] == "T-1"
    [request] = adapter.created
    assert request.merchant_reference == "SMOKE-3" and request.store_reference == "st-1"
    assert request.recipient_phone_e164 == "+8801700000062"
    assert request.cod_amount.paisa == 5_000


def test_env_credentials_must_be_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PATHAO_SMOKE_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("PATHAO_SMOKE_CLIENT_ID", "id")
    with pytest.raises(SystemExit, match="PATHAO_SMOKE_CLIENT_SECRET"):
        provider_smoke_generic._credentials_from_env("pathao", sandbox=False)


def test_ci_is_refused_without_allow_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    assert provider_smoke.main(["redx", "--env-credentials"]) == 2
