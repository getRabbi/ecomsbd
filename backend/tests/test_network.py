import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from app.analytics import network
from app.analytics.network_models import NetworkBenchmark
from app.db.session import system_session
from app.tenants.models import TenantUser
from tests.conftest_commerce import signed_in_shop
from tests.test_auth_flow import auth_header


@pytest.mark.parametrize(
    "counts",
    [
        [(10, 2)] * 19,
        [(9, 2)] * 30,
        [(10, 0)] * 20,
        [(10, 10)] * 20,
        [(10000, 2000)] + [(10, 2)] * 19,
        [(10, 11)] * 20,
        [(-1, 0)] * 20,
    ],
)
def test_privacy_sample_and_concentration_gates(counts):
    assert network.anonymous_facts(counts) is None


def test_anonymous_output_is_coarse_and_contains_no_identifiers():
    facts = network.anonymous_facts([(1000, 233)] * 20)
    assert facts["rto_percent_rounded"] == 25
    assert facts["delivery_percent_rounded"] == 75
    assert set(facts) == {
        "rto_percent_rounded",
        "delivery_percent_rounded",
        "precision_percent",
        "cohort",
        "minimum_shops",
        "minimum_sample",
    }
    assert network.anonymous_facts([(10, 2)] * 20)["rto_percent_rounded"] == 20


async def test_monthly_release_is_gated_and_immutable(client, unique_phone, monkeypatch):
    shop = await signed_in_shop(client, unique_phone)
    monkeypatch.setattr(network, "utc_now", lambda: datetime(2026, 9, 22, tzinfo=UTC))
    result = await network.build_network_benchmarks()
    assert result["status"] in {"GATED", "ALREADY_BUILT"}
    assert (await network.build_network_benchmarks())["status"] == "ALREADY_BUILT"
    response = await client.get("/v1/network-intelligence", headers=auth_header(shop))
    assert response.json()["facts"] == {}
    assert response.json()["status"] == "GATED"
    assert response.json()["blocker"] == "NETWORK_MINIMUM_SAMPLE_REQUIRED"
    async with system_session("test benchmark privacy") as db:
        row = await db.scalar(
            sa.select(NetworkBenchmark).where(NetworkBenchmark.period == "2026-08")
        )
        assert row.facts == {}


async def test_worker_never_releases_an_open_period(monkeypatch):
    monkeypatch.setattr(network, "utc_now", lambda: datetime(2026, 9, 2, tzinfo=UTC))
    assert (await network.build_network_benchmarks())["blocker"] == "NETWORK_CLOSED_PERIOD_REQUIRED"


async def test_consent_is_shop_scoped_and_owner_controlled(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    assert not (await client.get("/v1/network-intelligence", headers=headers)).json()["opted_in"]
    assert (
        await client.patch(
            "/v1/network-intelligence/preference", headers=headers, json={"opted_in": True}
        )
    ).json()["opted_in"]
    other = await signed_in_shop(client, "018" + unique_phone[3:])
    assert not (await client.get("/v1/network-intelligence", headers=auth_header(other))).json()[
        "opted_in"
    ]
    async with system_session("test permission") as db:
        row = await db.scalar(
            sa.select(TenantUser).where(TenantUser.tenant_id == uuid.UUID(shop["tenant_id"]))
        )
        row.role = "VIEWER"
    assert (
        await client.patch(
            "/v1/network-intelligence/preference", headers=headers, json={"opted_in": False}
        )
    ).status_code == 403
    # Query strings cannot select a phone, shop, or smaller cohort.
    a = (await client.get("/v1/network-intelligence", headers=headers)).json()
    b = (
        await client.get(
            "/v1/network-intelligence?phone=01712345678&tenant_id=" + other["tenant_id"],
            headers=headers,
        )
    ).json()
    assert a == b
