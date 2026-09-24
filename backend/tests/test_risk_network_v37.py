"""V3.7: external risk provider layer, cohort benchmarks and courier facts."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa

from app.analytics import network
from app.analytics import network_privacy as privacy
from app.analytics.network_models import NetworkBenchmarkCell, NetworkPreference
from app.automation.schemas import WorkflowDefinition
from app.automation.triggers import _risk_signals, read_event
from app.common.audit import AuditLog
from app.common.outbox import OutboxEvent, OutboxTopic
from app.consignments.models import Consignment
from app.core.clock import utc_now
from app.db.session import system_session
from app.risk_providers import registry
from app.risk_providers import service as risk_service
from app.risk_providers.contract import (
    Capability,
    CredentialField,
    FailureKind,
    LookupRequest,
    ProviderError,
    ProviderFact,
    ProviderResult,
    ProviderSpec,
)
from app.risk_providers.models import ExternalRiskLookup, RiskProviderConnection
from app.tenants.models import TenantUser
from tests.conftest_commerce import create_order, signed_in_shop
from tests.test_auth_flow import auth_header

SECRET = "sk_live_DO_NOT_LEAK_9876"
PROVIDER = "fake_licensed"


class FakeAdapter:
    def __init__(self) -> None:
        self.spec = ProviderSpec(
            provider_id=PROVIDER,
            display_name="Fake Licensed Provider",
            official_contract="https://provider.invalid/contract",
            markets=frozenset({"BD"}),
            capabilities=frozenset({Capability.PHONE_DELIVERY_HISTORY, Capability.CONNECTION_TEST}),
            credential_fields=(CredentialField("api_key", "API key", "এপিআই কী"),),
            timeout_seconds=0.2,
        )
        self.calls: list[LookupRequest] = []
        self.seen_credentials: list[dict[str, str]] = []
        self.mode = "ok"

    async def test_connection(self, credentials: dict[str, str], config: dict) -> None:
        self.seen_credentials.append(credentials)
        if self.mode == "auth":
            raise ProviderError(FailureKind.AUTH_FAILED)

    async def lookup(
        self, request: LookupRequest, credentials: dict[str, str], config: dict
    ) -> ProviderResult:
        self.calls.append(request)
        if self.mode == "down":
            raise ProviderError(FailureKind.UNAVAILABLE)
        if self.mode == "auth":
            raise ProviderError(FailureKind.AUTH_FAILED)
        if self.mode == "limited":
            raise ProviderError(FailureKind.RATE_LIMITED, retry_after_seconds=120)
        if self.mode == "slow":
            await asyncio.sleep(1)
        return ProviderResult(
            found=True,
            facts=(
                ProviderFact("DELIVERED_PARCELS", 7, period_days=365),
                ProviderFact("RETURNED_PARCELS", 3, period_days=365),
                ProviderFact("RISK_SCORE", 91),  # opaque: must be dropped
            ),
            observed_at=datetime(2026, 9, 20, tzinfo=UTC),
            sample_size=12,
        )


@pytest.fixture
def adapter(monkeypatch):
    fake = FakeAdapter()
    registry.register(fake)
    monkeypatch.setattr(risk_service, "BACKOFF_SECONDS", 0)
    yield fake
    registry.unregister(PROVIDER)


async def _role(shop: dict, role: str) -> None:
    async with system_session("test: change role") as db:
        member = await db.scalar(
            sa.select(TenantUser).where(TenantUser.tenant_id == uuid.UUID(shop["tenant_id"]))
        )
        member.role = role


async def _connected(client, shop) -> dict:
    headers = auth_header(shop)
    saved = await client.put(
        f"/v1/external-risk/providers/{PROVIDER}",
        headers=headers,
        json={"credentials": {"api_key": SECRET}, "cache_ttl_hours": 24},
    )
    assert saved.status_code == 200, saved.text
    tested = await client.post(f"/v1/external-risk/providers/{PROVIDER}/test", headers=headers)
    assert tested.json()["ok"] is True
    on = await client.patch(
        f"/v1/external-risk/providers/{PROVIDER}/state", headers=headers, json={"enabled": True}
    )
    assert on.status_code == 200 and on.json()["enabled"] is True
    return saved.json()


async def _customer(client, shop, phone: str = "01712345678") -> str:
    return (await create_order(client, shop, phone=phone))["order"]["customer_id"]


# ---------------------------------------------------------- provider layer ---


async def test_no_registered_provider_stays_gated(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    catalog = (await client.get("/v1/external-risk/providers", headers=headers)).json()
    assert catalog == {"status": "GATED", "blocker": registry.BLOCKER, "providers": []}
    missing = await client.put(
        "/v1/external-risk/providers/anything",
        headers=headers,
        json={"credentials": {"api_key": "x"}},
    )
    assert missing.status_code == 404
    customer = await _customer(client, shop)
    view = (await client.get(f"/v1/external-risk/customers/{customer}", headers=headers)).json()
    assert view["status"] == "GATED" and view["providers"] == []


async def test_credentials_are_encrypted_shown_never_and_audited(client, unique_phone, adapter):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    early = await client.put(
        f"/v1/external-risk/providers/{PROVIDER}",
        headers=headers,
        json={"credentials": {"api_key": SECRET}},
    )
    assert early.status_code == 200
    blocked = await client.patch(
        f"/v1/external-risk/providers/{PROVIDER}/state", headers=headers, json={"enabled": True}
    )
    assert blocked.status_code == 422
    assert blocked.json()["details"]["blocker"] == "CONNECTION_TEST_REQUIRED"
    saved = await _connected(client, shop)
    assert saved["credentials_set"] is True and saved["credential_hint"] == SECRET[-4:]
    catalog = await client.get("/v1/external-risk/providers", headers=headers)
    for body in (early.text, catalog.text, blocked.text):
        assert SECRET not in body
    assert adapter.seen_credentials == [{"api_key": SECRET}]
    async with system_session("test: inspect storage") as db:
        row = await db.scalar(
            sa.select(RiskProviderConnection).where(
                RiskProviderConnection.tenant_id == uuid.UUID(shop["tenant_id"])
            )
        )
        assert row.credentials_enc and SECRET not in row.credentials_enc
        audits = (
            await db.scalars(
                sa.select(AuditLog).where(
                    AuditLog.action.like("risk_provider.%"),
                    AuditLog.tenant_id == uuid.UUID(shop["tenant_id"]),
                )
            )
        ).all()
        assert {a.action for a in audits} >= {
            "risk_provider.configured",
            "risk_provider.tested",
            "risk_provider.enabled",
        }
        assert all(SECRET not in str(a.context) for a in audits)
    # Removing wipes the credentials and turns it off.
    assert (
        await client.delete(f"/v1/external-risk/providers/{PROVIDER}", headers=headers)
    ).status_code == 204
    async with system_session("test: inspect removal") as db:
        row = await db.scalar(
            sa.select(RiskProviderConnection).where(
                RiskProviderConnection.tenant_id == uuid.UUID(shop["tenant_id"])
            )
        )
        assert row.credentials_enc is None and row.enabled is False


async def test_failed_connection_test_keeps_provider_off(client, unique_phone, adapter):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    await client.put(
        f"/v1/external-risk/providers/{PROVIDER}",
        headers=headers,
        json={"credentials": {"api_key": SECRET}},
    )
    adapter.mode = "auth"
    tested = (
        await client.post(f"/v1/external-risk/providers/{PROVIDER}/test", headers=headers)
    ).json()
    assert tested["ok"] is False and tested["error_code"] == "PROVIDER_AUTH_FAILED"
    assert tested["connection"]["health"] == "DOWN" and tested["connection"]["enabled"] is False


async def test_lookup_sends_minimum_pii_caches_and_keeps_sources_apart(
    client, unique_phone, adapter
):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    await _connected(client, shop)
    customer = await _customer(client, shop)
    before = (await client.get(f"/v1/customers/{customer}/risk-profile", headers=headers)).json()

    first = await client.post(
        f"/v1/external-risk/customers/{customer}/lookup", headers=headers, json={}
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["outcomes"] == [{"provider_id": PROVIDER, "outcome": "FETCHED", "error_code": None}]
    [section] = body["providers"]
    result = section["result"]
    assert result["status"] == "FOUND" and result["freshness"] == "FRESH"
    assert [f["code"] for f in result["facts"]] == ["DELIVERED_PARCELS", "RETURNED_PARCELS"]
    assert result["provider_observed_at"].startswith("2026-09-20")
    assert result["sample_size"] == 12
    assert "score" not in first.text.lower()
    # Minimum PII: the number in E.164 and the market, nothing else.
    [request] = adapter.calls
    assert request == LookupRequest(phone_e164="+8801712345678", market="BD")

    # Fresh answer: no second paid call, not even on refresh within ten minutes.
    await client.post(f"/v1/external-risk/customers/{customer}/lookup", headers=headers, json={})
    again = await client.post(
        f"/v1/external-risk/customers/{customer}/lookup", headers=headers, json={"refresh": True}
    )
    assert again.json()["outcomes"][0]["outcome"] == "CACHED"
    assert len(adapter.calls) == 1

    # First-party history is untouched by provider facts.
    after = (await client.get(f"/v1/customers/{customer}/risk-profile", headers=headers)).json()
    assert before["own_shop"] == after["own_shop"]
    assert after["own_shop"]["source"] == "OWN_SHOP_HISTORY"
    assert after["external"]["source"] == "EXTERNAL_PROVIDER"
    assert after["network"]["source"] == "ANONYMOUS_NETWORK_COHORT"

    # Expired: a lookup asks again.
    async with system_session("test: expire") as db:
        row = await db.scalar(
            sa.select(ExternalRiskLookup).where(
                ExternalRiskLookup.customer_id == uuid.UUID(customer)
            )
        )
        row.checked_at = utc_now() - timedelta(days=2)
        row.expires_at = utc_now() - timedelta(days=1)
    await client.post(f"/v1/external-risk/customers/{customer}/lookup", headers=headers, json={})
    assert len(adapter.calls) == 2
    async with system_session("test: dropped facts") as db:
        rows = (
            await db.scalars(
                sa.select(ExternalRiskLookup).where(
                    ExternalRiskLookup.customer_id == uuid.UUID(customer)
                )
            )
        ).all()
        assert {r.dropped_facts for r in rows} == {1}
        assert all(SECRET not in str(r.facts) for r in rows)


async def test_provider_failure_falls_back_and_opens_the_circuit(client, unique_phone, adapter):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    await _connected(client, shop)
    customer = await _customer(client, shop)
    await client.post(f"/v1/external-risk/customers/{customer}/lookup", headers=headers, json={})
    async with system_session("test: age the answer") as db:
        row = await db.scalar(
            sa.select(ExternalRiskLookup).where(
                ExternalRiskLookup.customer_id == uuid.UUID(customer)
            )
        )
        row.checked_at = utc_now() - timedelta(days=2)
        row.expires_at = utc_now() - timedelta(days=1)

    adapter.mode = "down"
    adapter.calls.clear()
    failed = (
        await client.post(
            f"/v1/external-risk/customers/{customer}/lookup", headers=headers, json={}
        )
    ).json()
    assert failed["outcomes"][0] == {
        "provider_id": PROVIDER,
        "outcome": "FAILED",
        "error_code": "PROVIDER_UNAVAILABLE",
    }
    assert len(adapter.calls) == risk_service.MAX_ATTEMPTS  # transient: retried once
    [section] = failed["providers"]
    assert section["result"]["freshness"] == "STALE"  # the old answer stays, marked stale
    assert section["last_error"]["code"] == "PROVIDER_UNAVAILABLE"

    # The shop's own Risk Check keeps working.
    check = await client.get(
        "/v1/customers/risk-check", headers=headers, params={"phone": "01712345678"}
    )
    assert check.status_code == 200 and check.json()["found"] is True

    for _ in range(2):
        await client.post(
            f"/v1/external-risk/customers/{customer}/lookup", headers=headers, json={}
        )
    calls = len(adapter.calls)
    skipped = (
        await client.post(
            f"/v1/external-risk/customers/{customer}/lookup", headers=headers, json={}
        )
    ).json()
    assert skipped["outcomes"][0]["outcome"] == "SKIPPED"  # DOWN: calls pause
    assert len(adapter.calls) == calls


async def test_auth_failure_is_not_retried_and_rate_limit_is_respected(
    client, unique_phone, adapter
):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    await _connected(client, shop)
    customer = await _customer(client, shop)
    adapter.mode = "limited"
    first = (
        await client.post(
            f"/v1/external-risk/customers/{customer}/lookup", headers=headers, json={}
        )
    ).json()
    assert first["outcomes"][0]["error_code"] == "PROVIDER_RATE_LIMITED"
    assert len(adapter.calls) == 1
    second = (
        await client.post(
            f"/v1/external-risk/customers/{customer}/lookup", headers=headers, json={}
        )
    ).json()
    assert second["outcomes"][0]["outcome"] == "SKIPPED"
    assert len(adapter.calls) == 1

    async with system_session("test: clear limit") as db:
        row = await db.scalar(
            sa.select(RiskProviderConnection).where(
                RiskProviderConnection.tenant_id == uuid.UUID(shop["tenant_id"])
            )
        )
        row.rate_limited_until = None
        row.health = "DEGRADED"
    adapter.mode = "auth"
    await client.post(f"/v1/external-risk/customers/{customer}/lookup", headers=headers, json={})
    assert len(adapter.calls) == 2  # not retried


async def test_timeout_is_mapped_to_a_seller_safe_code(client, unique_phone, adapter):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    await _connected(client, shop)
    customer = await _customer(client, shop)
    adapter.mode = "slow"
    body = (
        await client.post(
            f"/v1/external-risk/customers/{customer}/lookup", headers=headers, json={}
        )
    ).json()
    assert body["outcomes"][0]["error_code"] == "PROVIDER_TIMEOUT"


async def test_rbac_and_tenant_isolation(client, unique_phone, adapter):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    await _connected(client, shop)
    customer = await _customer(client, shop)
    other = await signed_in_shop(client, "018" + unique_phone[3:])
    other_headers = auth_header(other)
    assert (
        await client.get(f"/v1/external-risk/customers/{customer}", headers=other_headers)
    ).status_code == 404
    assert (
        await client.post(
            f"/v1/external-risk/customers/{customer}/lookup", headers=other_headers, json={}
        )
    ).status_code == 404
    assert (
        await client.get(f"/v1/customers/{customer}/risk-profile", headers=other_headers)
    ).status_code == 404
    theirs = (await client.get("/v1/external-risk/providers", headers=other_headers)).json()
    assert theirs["providers"][0]["connection"] is None

    await _role(shop, "MANAGER")
    assert (
        await client.put(
            f"/v1/external-risk/providers/{PROVIDER}",
            headers=headers,
            json={"cache_ttl_hours": 2},
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/v1/external-risk/customers/{customer}/lookup", headers=headers, json={}
        )
    ).status_code == 200
    await _role(shop, "ORDER_OPERATOR")
    assert (
        await client.get(f"/v1/external-risk/customers/{customer}", headers=headers)
    ).status_code == 200
    assert (
        await client.post(
            f"/v1/external-risk/customers/{customer}/lookup", headers=headers, json={}
        )
    ).status_code == 403
    profile = (await client.get(f"/v1/customers/{customer}/risk-profile", headers=headers)).json()
    assert profile["can_lookup"] is False
    await _role(shop, "VIEWER")
    assert (
        await client.get(f"/v1/customers/{customer}/risk-profile", headers=headers)
    ).status_code == 403


# ------------------------------------------------------------- benchmarks ---


def test_thresholds_are_central_and_unchanged():
    assert (network.MIN_SHOPS, network.MIN_SAMPLE) == (privacy.MIN_SHOPS, privacy.MIN_SAMPLE)
    assert privacy.MIN_SHOPS >= 20 and privacy.MIN_SAMPLE >= 200
    assert privacy.MAX_SHARE_PERCENT <= 10 and privacy.RATE_PRECISION_PERCENT >= 5


@pytest.mark.parametrize(
    "contributions",
    [
        [(100, 20)] * 19,  # too few shops
        [(9, 2)] * 40,  # every shop below its own minimum
        [(100, 0)] * 20,  # one outcome missing
        [(100, 20)] + [(10, 2)] * 19,  # one shop dominates the clipped total
    ],
)
def test_rate_is_data_not_sufficient_below_thresholds(contributions):
    assert privacy.publish_rate(contributions) is None


def test_median_needs_enough_shops_each_with_enough_observations():
    assert privacy.publish_median([[30.0] * 10] * 19, step=12, unit="HOURS") is None
    assert privacy.publish_median([[30.0] * 9] * 40, step=12, unit="HOURS") is None
    published = privacy.publish_median([[30.0] * 10] * 20, step=12, unit="HOURS")
    assert published is not None and published.value == 24 and published.precision == 12
    assert published.sample_band == "200-499" and published.shops_band == "20-49"


def test_one_seller_cannot_infer_another_sellers_exact_data():
    """Even knowing every other shop's exact counts, the target stays ambiguous."""
    known = [(100, 20)] * 19
    candidates: dict[int | None, list[int]] = {}
    for target_returns in range(0, 101):
        published = privacy.publish_rate([*known, (100, target_returns)])
        key = published.value if published else None
        candidates.setdefault(key, []).append(target_returns)
    assert all(len(values) >= 20 for values in candidates.values())
    # And nothing exact is published.
    published = privacy.publish_rate([*known, (100, 37)])
    assert set(vars(published)) == {"value", "precision", "unit", "shops_band", "sample_band"}
    assert published.value % privacy.RATE_PRECISION_PERCENT == 0


def test_small_cohorts_can_be_differenced_only_to_rounded_values():
    """Adding one shop to a cohort moves the output by at most one rounding step."""
    base = [(100, 25)] * 20
    with_shop = privacy.publish_rate([*base, (100, 100)])
    without = privacy.publish_rate(base)
    assert abs(with_shop.value - without.value) <= privacy.RATE_PRECISION_PERCENT


async def _opted_in_shop_with_parcels(client, phone: str) -> dict:
    shop = await signed_in_shop(client, phone)
    tenant = uuid.UUID(shop["tenant_id"])
    order = (await create_order(client, shop))["order"]
    start = datetime(2026, 8, 1, tzinfo=UTC)
    async with system_session("test: opt in and parcels") as db:
        db.add(
            NetworkPreference(
                tenant_id=tenant, opted_in=True, opted_in_at=start - timedelta(days=30)
            )
        )
        for i, status in enumerate(("DELIVERED", "RETURNED", "IN_TRANSIT")):
            db.add(
                Consignment(
                    tenant_id=tenant,
                    order_id=uuid.UUID(order["id"]),
                    provider="steadfast",
                    merchant_reference=f"NB-{i}",
                    status=status,
                    booked_at=start + timedelta(days=2),
                    delivered_at=start + timedelta(days=4) if status == "DELIVERED" else None,
                    returned_at=start + timedelta(days=6) if status == "RETURNED" else None,
                    last_status_at=start + timedelta(days=3),
                )
            )
    return shop


async def test_cells_are_precomputed_once_fixed_and_suppressed(client, unique_phone, monkeypatch):
    shop = await _opted_in_shop_with_parcels(client, unique_phone)
    async with system_session("test: fresh release") as db:
        await db.execute(
            sa.delete(NetworkBenchmarkCell).where(NetworkBenchmarkCell.period == "2026-08")
        )
    monkeypatch.setattr(network, "utc_now", lambda: datetime(2026, 9, 22, tzinfo=UTC))
    first = await network.build_network_benchmarks()
    second = await network.build_network_benchmarks()
    assert first["cells"] == "BUILT" and second["cells"] == "ALREADY_BUILT"
    expected = sum(
        len(network.DIMENSIONS[d]) for _, dims, _, _ in network.METRICS.values() for d in dims
    )
    async with system_session("test: cells") as db:
        cells = (
            await db.scalars(
                sa.select(NetworkBenchmarkCell).where(NetworkBenchmarkCell.period == "2026-08")
            )
        ).all()
    assert len(cells) == expected
    # One shop can never be published: every cohort says so, with no value.
    assert {c.status for c in cells} == {"DATA_NOT_SUFFICIENT"}
    assert all(c.value is None and c.sample_band is None for c in cells)

    headers = auth_header(shop)
    drill = await client.get(
        "/v1/network-intelligence/benchmarks",
        headers=headers,
        params={"dimension": "COURIER", "tenant_id": shop["tenant_id"]},
    )
    assert drill.status_code == 200
    body = drill.json()
    assert body["period"] == "2026-08" and body["cohorts"] == list(network.LIVE_COURIERS)
    assert body["cohort_definition"]["minimum_shops"] == privacy.MIN_SHOPS
    assert {c["dimension"] for c in body["cells"]} == {"COURIER"}
    assert shop["tenant_id"] not in drill.text
    assert (
        await client.get(
            "/v1/network-intelligence/benchmarks", headers=headers, params={"dimension": "SHOP"}
        )
    ).status_code == 422
    summary = (await client.get("/v1/network-intelligence", headers=headers)).json()
    assert summary["benchmarks"]["period"] == "2026-08"


async def test_courier_facts_are_factual_with_no_score(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    tenant = uuid.UUID(shop["tenant_id"])
    order = (await create_order(client, shop))["order"]
    now = utc_now()
    async with system_session("test: courier parcels") as db:
        for i in range(6):
            db.add(
                Consignment(
                    tenant_id=tenant,
                    order_id=uuid.UUID(order["id"]),
                    provider="pathao",
                    merchant_reference=f"CF-{i}",
                    status="DELIVERED" if i < 5 else "RETURNED",
                    booked_at=now - timedelta(days=5),
                    delivered_at=now - timedelta(days=3) if i < 5 else None,
                    returned_at=now - timedelta(days=2) if i == 5 else None,
                    last_status_at=now - timedelta(days=2),
                )
            )
        db.add(
            Consignment(
                tenant_id=tenant,
                order_id=uuid.UUID(order["id"]),
                provider="pathao",
                merchant_reference="CF-stuck",
                status="IN_TRANSIT",
                booked_at=now - timedelta(days=20),
                last_status_at=now - timedelta(days=15),
            )
        )
    body = (await client.get("/v1/network-intelligence/couriers", headers=auth_header(shop))).json()
    [pathao] = body["own_shop"]["couriers"]
    assert pathao["completed_parcels"] == 6 and pathao["rto_parcels"] == 1
    assert pathao["delivery_rate_bps"] == 8333
    assert pathao["median_delivery_hours"] == 48.0
    assert pathao["stuck_parcels"] == 1 and pathao["in_transit_parcels"] == 1
    text = str(body).lower()
    assert "score" not in text and "best" not in text and "rank" not in text


# ------------------------------------------------------------- automation ---


async def test_provider_events_become_workflow_triggers(db):
    customer = uuid.uuid4()
    done = OutboxEvent(
        tenant_id=uuid.uuid4(),
        topic=str(OutboxTopic.EXTERNAL_RISK_LOOKUP_COMPLETED),
        payload={"customer_id": str(customer), "provider_id": PROVIDER, "found": True},
    )
    [match] = (await read_event(db, done)).matches
    assert match.trigger == "risk.external_lookup_completed"
    assert match.facts == {"external_found": True}
    down = OutboxEvent(
        tenant_id=uuid.uuid4(),
        topic=str(OutboxTopic.EXTERNAL_RISK_UNAVAILABLE),
        payload={"customer_id": str(customer), "external_data_state": "STALE"},
    )
    [match] = (await read_event(db, down)).matches
    assert match.trigger == "risk.external_provider_unavailable"
    assert match.facts == {"external_data_state": "STALE"}


async def test_first_party_risk_change_and_repeated_rto_are_signalled(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    tenant = uuid.UUID(shop["tenant_id"])
    orders = [(await create_order(client, shop))["order"] for _ in range(3)]
    customer = uuid.UUID(orders[0]["customer_id"])
    parcel_id = uuid.uuid4()
    async with system_session("test: history") as db:
        for i, (order, status) in enumerate(
            zip(orders, ("DELIVERED", "RETURNED", "RETURNED"), strict=True)
        ):
            db.add(
                Consignment(
                    id=parcel_id if i == 2 else uuid.uuid4(),
                    tenant_id=tenant,
                    order_id=uuid.UUID(order["id"]),
                    provider="steadfast",
                    merchant_reference=f"RS-{i}",
                    status=status,
                )
            )
    found: list[tuple[str, dict[str, Any]]] = []

    def add(trigger: str, subject: dict, key: str, facts: dict | None = None) -> None:
        found.append((trigger, facts or {}))

    payload = {
        "consignment_id": str(parcel_id),
        "old_status": "IN_TRANSIT",
        "new_status": "RETURNED",
    }
    async with system_session("test: signals") as db:
        await _risk_signals(db, customer, payload, {}, "order:x", add)
    assert (
        "risk.state_changed",
        {"risk_state": "HIGH", "previous_risk_state": "INSUFFICIENT_DATA"},
    ) in found
    assert ("risk.repeated_rto", {"returned_parcels": 2}) in found


def test_hold_for_review_needs_an_order_and_accepts_risk_conditions():
    WorkflowDefinition.model_validate(
        {
            "trigger": "order.created",
            "conditions": {
                "match": "all",
                "conditions": [{"field": "risk_state", "op": "eq", "value": "HIGH"}],
            },
            "steps": [
                {
                    "type": "action",
                    "id": "hold",
                    "action": "HOLD_FOR_REVIEW",
                    "config": {"reason": "FIRST_PARTY_RISK"},
                }
            ],
        }
    )


async def test_hold_for_review_runs_and_a_person_releases_it(client, unique_phone):
    from app.automation import jobs

    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    definition = {
        "trigger": "order.created",
        "conditions": {
            "match": "all",
            "conditions": [{"field": "risk_state", "op": "eq", "value": "INSUFFICIENT_DATA"}],
        },
        "steps": [
            {
                "type": "action",
                "id": "hold",
                "action": "HOLD_FOR_REVIEW",
                "config": {"reason": "FIRST_PARTY_RISK"},
            }
        ],
    }
    created = await client.post(
        "/v1/automation/workflows", headers=headers, json={"name": "Hold", "definition": definition}
    )
    assert created.status_code == 201, created.text
    published = await client.post(
        f"/v1/automation/workflows/{created.json()['id']}/publish",
        headers=headers,
        json={"enable": True},
    )
    assert published.status_code == 200, published.text
    order = (await create_order(client, shop))["order"]
    [queued] = (await client.get("/v1/automation/executions", headers=headers)).json()["items"]
    await jobs.execute(uuid.UUID(shop["tenant_id"]), uuid.UUID(queued["id"]))
    held = (await client.get(f"/v1/orders/{order['id']}", headers=headers)).json()
    assert held["review_hold"]["reason"] == "FIRST_PARTY_RISK"
    assert held["status"] == order["status"]  # nothing else changed
    released = await client.delete(f"/v1/orders/{order['id']}/review-hold", headers=headers)
    assert released.status_code == 200 and released.json()["review_hold"] is None
