"""Per-courier delivery history from an external provider (V4 WP0.6).

Provider-independent: a fake adapter stands in for the licensed provider
whose contract is still missing (``BLOCKED: EXTERNAL_RISK_CONTRACT_REQUIRED``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.automation.facts import Facts
from app.automation.schemas import FIELDS
from app.risk_providers import registry
from app.risk_providers import service as risk_service
from app.risk_providers.contract import (
    SAMPLE_FLOOR,
    CourierCount,
    CourierHistory,
    LookupRequest,
    ProviderResult,
    history_state,
    normalize_courier_history,
)
from tests.conftest_commerce import signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_risk_network_v37 import PROVIDER, FakeAdapter, _connected, _customer

HISTORY = CourierHistory(
    provider_counts=(
        CourierCount("Steadfast", delivered=15, cancelled=2, total=17),
        CourierCount("pathao", delivered=8, cancelled=1, total=9),
    ),
    as_of=datetime(2026, 11, 1, tzinfo=UTC),
)


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #


def test_rows_are_normalised_and_summed() -> None:
    history = normalize_courier_history(HISTORY)
    assert history is not None
    assert history["provider_counts"] == [
        {"courier": "steadfast", "delivered": 15, "cancelled": 2, "total": 17},
        {"courier": "pathao", "delivered": 8, "cancelled": 1, "total": 9},
    ]
    assert history["aggregate"] == {"delivered": 23, "cancelled": 3, "total": 26}
    assert history["as_of"].startswith("2026-11-01")
    assert history["state"] == "OK"
    assert history["success_bps"] == 23 * 10_000 // 26


def test_the_providers_own_total_wins_when_valid() -> None:
    history = normalize_courier_history(
        CourierHistory(
            provider_counts=HISTORY.provider_counts,
            aggregate=CourierCount("all", delivered=30, cancelled=4, total=36),
        )
    )
    assert history is not None
    assert history["aggregate"] == {"delivered": 30, "cancelled": 4, "total": 36}


@pytest.mark.parametrize(
    "row",
    [
        CourierCount(
            "steadfast", delivered=10, cancelled=5, total=12
        ),  # more outcomes than parcels
        CourierCount("steadfast", delivered=-1, cancelled=0, total=3),
        CourierCount("steadfast", delivered=2_000_000, cancelled=0, total=2_000_000),
        CourierCount("<script>", delivered=1, cancelled=0, total=1),
        CourierCount("", delivered=1, cancelled=0, total=1),
    ],
)
def test_a_wrong_row_is_dropped_not_repaired(row: CourierCount) -> None:
    history = normalize_courier_history(
        CourierHistory(provider_counts=(row, CourierCount("redx", 4, 0, 5)))
    )
    assert history is not None
    assert [r["courier"] for r in history["provider_counts"]] == ["redx"]
    assert history["aggregate"]["total"] == 5


def test_duplicates_and_empty_history() -> None:
    history = normalize_courier_history(
        CourierHistory(
            provider_counts=(CourierCount("redx", 1, 0, 1), CourierCount("RedX", 9, 0, 9))
        )
    )
    assert history is not None and len(history["provider_counts"]) == 1
    assert normalize_courier_history(None) is None
    assert normalize_courier_history(CourierHistory()) is None


def test_under_three_parcels_is_kom_data_never_a_ratio() -> None:
    assert SAMPLE_FLOOR == 3
    assert history_state({"delivered": 2, "cancelled": 0, "total": 2}) == (
        "INSUFFICIENT_DATA",
        None,
    )
    assert history_state({"delivered": 3, "cancelled": 0, "total": 3}) == ("OK", 10_000)
    assert history_state(None) == ("INSUFFICIENT_DATA", None)
    thin = normalize_courier_history(
        CourierHistory(provider_counts=(CourierCount("pathao", 1, 1, 2),))
    )
    assert thin is not None and thin["state"] == "INSUFFICIENT_DATA" and thin["success_bps"] is None


def test_the_automation_condition_is_registered() -> None:
    entity, kind, ops = FIELDS["external_success_bps"]
    assert (entity, kind) == ("customer", "int") and ops == frozenset({"gte", "lte"})


# --------------------------------------------------------------------------- #
# Stored and served through the existing provider layer
# --------------------------------------------------------------------------- #


class HistoryAdapter(FakeAdapter):
    def __init__(self, history: CourierHistory | None) -> None:
        super().__init__()
        self.history = history

    async def lookup(
        self, request: LookupRequest, credentials: dict, config: dict
    ) -> ProviderResult:
        base = await super().lookup(request, credentials, config)
        return ProviderResult(
            found=True,
            facts=base.facts,
            observed_at=base.observed_at,
            sample_size=base.sample_size,
            courier_history=self.history,
        )


@pytest.fixture
def history_adapter(monkeypatch):  # type: ignore[no-untyped-def]
    fake = HistoryAdapter(HISTORY)
    registry.register(fake)
    monkeypatch.setattr(risk_service, "BACKOFF_SECONDS", 0)
    yield fake
    registry.unregister(PROVIDER)


async def test_a_lookup_stores_and_serves_courier_history(client, unique_phone, history_adapter):  # type: ignore[no-untyped-def]
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    await _connected(client, shop)
    customer = await _customer(client, shop, phone="01700000051")

    response = await client.post(
        f"/v1/external-risk/customers/{customer}/lookup", headers=headers, json={}
    )
    assert response.status_code == 200, response.text
    [section] = response.json()["providers"]
    history = section["result"]["courier_history"]
    assert [row["courier"] for row in history["provider_counts"]] == ["steadfast", "pathao"]
    assert history["state"] == "OK" and history["success_bps"] == 8846

    from app.db.session import system_session

    async with system_session("test: condition") as db:
        facts = Facts(db, {"customer_id": customer})
        assert await facts.get("external_success_bps") == 8846


async def test_thin_history_leaves_the_condition_unset(client, unique_phone, monkeypatch):  # type: ignore[no-untyped-def]
    fake = HistoryAdapter(CourierHistory(provider_counts=(CourierCount("redx", 2, 0, 2),)))
    registry.register(fake)
    monkeypatch.setattr(risk_service, "BACKOFF_SECONDS", 0)
    try:
        shop = await signed_in_shop(client, unique_phone)
        await _connected(client, shop)
        customer = await _customer(client, shop, phone="01700000052")
        await client.post(
            f"/v1/external-risk/customers/{customer}/lookup", headers=auth_header(shop), json={}
        )
        from app.db.session import system_session

        async with system_session("test: condition") as db:
            assert await Facts(db, {"customer_id": customer}).get("external_success_bps") is None
            assert (
                await Facts(db, {"customer_id": str(uuid.uuid4())}).get("external_success_bps")
                is None
            )
    finally:
        registry.unregister(PROVIDER)
