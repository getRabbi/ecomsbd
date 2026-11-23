"""BD Courier (bdcourier.com) external risk lookup (V4 WP0.6).

Contract: ``docs/providers/bdcourier/CONTRACT.md``. A fake HTTP transport stands
in for ``api.bdcourier.com``; numbers are in the 017000000xx test range.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from app.common.feature_flags import RISK_PROVIDER_FLAGS, FeatureFlag, FlagKey
from app.core.clock import utc_now
from app.db.session import system_session
from app.orders.models import Order
from app.risk_providers import bdcourier, registry
from app.risk_providers import service as risk_service
from app.risk_providers.contract import (
    Capability,
    FailureKind,
    LookupRequest,
    ProviderError,
    normalize_courier_history,
)
from app.risk_providers.models import ExternalRiskLookup
from tests.conftest_commerce import create_order, signed_in_shop
from tests.test_auth_flow import auth_header

KEY = "bdc_live_DO_NOT_LEAK_4321"
PHONE = "01700000061"
REPO = Path(__file__).resolve().parents[2]


def _courier(name: str, total: int, success: int, cancelled: int, ratio: float) -> dict:
    return {
        "name": name,
        "logo": "https://api.bdcourier.com/c-logo/x.png",
        "total_parcel": total,
        "success_parcel": success,
        "cancelled_parcel": cancelled,
        "success_ratio": ratio,
    }


#: CONTRACT.md §2 sample response (logos shortened).
SAMPLE: dict[str, Any] = {
    "status": "success",
    "data": {
        "pathao": _courier("Pathao", 150, 120, 30, 80.0),
        "steadfast": _courier("SteadFast", 200, 175, 25, 87.5),
        "parceldex": _courier("ParcelDex", 0, 0, 0, 0),
        "courrierfast": _courier("CourrierFast", 50, 45, 5, 90.0),
        "redx": _courier("Redx", 80, 65, 15, 81.25),
        "paperfly": _courier("PaperFly", 100, 90, 10, 90.0),
        "carrybee": _courier("CarryBee", 40, 35, 5, 87.5),
        "summary": {
            "total_parcel": 620,
            "success_parcel": 530,
            "cancelled_parcel": 90,
            "success_ratio": 85.48,
        },
    },
    "reports": [],
    "risk_verdict": {
        "level": "safe",
        "label": "Safe",
        "action": "OK to ship",
        "color": "green",
        "reasons": ["Strong delivery success rate (85.48%)"],
    },
}


class FakeBdCourier:
    """Scripted ``api.bdcourier.com``: records every request it sees."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.check: httpx.Response | Exception = httpx.Response(200, json=SAMPLE)
        self.plans: httpx.Response | Exception = httpx.Response(
            200, json=[{"id": 1, "name": "Free", "price": 0, "features": []}]
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.check if request.url.path == bdcourier.CHECK_PATH else self.plans
        if isinstance(answer, Exception):
            raise answer
        return answer

    def adapter(self) -> bdcourier.BdCourierAdapter:
        return bdcourier.BdCourierAdapter(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeBdCourier:
    api = FakeBdCourier()
    monkeypatch.setattr(bdcourier.ADAPTER, "_transport", httpx.MockTransport(api.handler))
    monkeypatch.setattr(risk_service, "BACKOFF_SECONDS", 0)
    return api


@pytest.fixture
async def bdcourier_on(settings: Any) -> AsyncIterator[None]:
    from tests.conftest import _global_flag_on

    async for _ in _global_flag_on(settings, str(FlagKey.BDCOURIER_ENABLED)):
        yield


def _request(phone: str = "+8801700000061") -> LookupRequest:
    return LookupRequest(phone_e164=phone)


async def _connect(client: Any, shop: dict) -> None:
    headers = auth_header(shop)
    saved = await client.put(
        "/v1/external-risk/providers/bdcourier",
        headers=headers,
        json={"credentials": {"api_key": KEY}},
    )
    assert saved.status_code == 200, saved.text
    assert KEY not in saved.text and saved.json()["credential_hint"] == KEY[-4:]
    tested = await client.post("/v1/external-risk/providers/bdcourier/test", headers=headers)
    assert tested.json()["ok"] is True, tested.text
    on = await client.patch(
        "/v1/external-risk/providers/bdcourier/state", headers=headers, json={"enabled": True}
    )
    assert on.status_code == 200 and on.json()["enabled"] is True


async def _lookup(client: Any, shop: dict, customer_id: str) -> dict:
    response = await client.post(
        f"/v1/external-risk/customers/{customer_id}/lookup", headers=auth_header(shop), json={}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _rows(customer_id: str) -> list[ExternalRiskLookup]:
    async with system_session("test: bdcourier lookups") as db:
        return list(
            (
                await db.scalars(
                    sa.select(ExternalRiskLookup).where(
                        ExternalRiskLookup.customer_id == uuid.UUID(customer_id)
                    )
                )
            ).all()
        )


# --------------------------------------------------------------------------- #
# Spec: what the contract and the terms allow
# --------------------------------------------------------------------------- #


def test_the_spec_claims_only_what_the_page_documents() -> None:
    spec = bdcourier.SPEC
    assert spec.provider_id == "bdcourier" and spec.markets == frozenset({"BD"})
    assert (REPO / spec.official_contract).is_file()
    # No key-check endpoint is documented: the test proves reachability only.
    assert spec.capabilities == frozenset({Capability.PHONE_DELIVERY_HISTORY})
    assert [(f.name, f.secret) for f in spec.credential_fields] == [("api_key", True)]
    assert spec.new_orders_only is True and spec.retention_days == 30
    assert spec.default_cache_ttl_hours == 24 and spec.max_cache_ttl_hours == 72
    assert "own" in spec.notice_en and "নিজের" in spec.notice_bn
    assert RISK_PROVIDER_FLAGS == {"bdcourier": FlagKey.BDCOURIER_ENABLED}
    assert KEY not in repr(bdcourier.ADAPTER)


# --------------------------------------------------------------------------- #
# Reading the documented answer
# --------------------------------------------------------------------------- #


def test_the_sample_answer_becomes_counts_and_never_a_verdict() -> None:
    result = bdcourier.parse_courier_check(SAMPLE)
    assert result.found is True and result.sample_size == 620
    assert [(f.code, f.value) for f in result.facts] == [
        ("TOTAL_PARCELS", 620),
        ("DELIVERED_PARCELS", 530),
    ]
    history = normalize_courier_history(result.courier_history)
    assert history is not None
    # ParcelDex had no parcels: left out of the chip.
    assert [row["courier"] for row in history["provider_counts"]] == [
        "pathao",
        "steadfast",
        "courrierfast",
        "redx",
        "paperfly",
        "carrybee",
    ]
    assert history["provider_counts"][1] == {
        "courier": "steadfast",
        "delivered": 175,
        "cancelled": 25,
        "total": 200,
    }
    assert history["aggregate"] == {"delivered": 530, "cancelled": 90, "total": 620}
    assert history["success_bps"] == 530 * 10_000 // 620
    stored = repr(result) + json.dumps(history)
    for dropped in ("safe", "risk", "verdict", "OK to ship", "85.48", "logo", "SteadFast"):
        assert dropped not in stored, dropped
    assert result.confidence is None and result.reference is None and result.observed_at is None


def test_no_parcels_anywhere_is_not_found() -> None:
    empty = {
        "status": "success",
        "data": {
            "pathao": {"total_parcel": 0, "success_parcel": 0, "cancelled_parcel": 0},
            "summary": {"total_parcel": 0, "success_parcel": 0, "cancelled_parcel": 0},
        },
    }
    result = bdcourier.parse_courier_check(empty)
    assert result.found is False and result.facts == () and result.courier_history is None


def test_without_a_summary_the_rows_are_summed_and_no_fact_is_claimed() -> None:
    body = {
        "status": "success",
        "data": {"redx": {"total_parcel": 5, "success_parcel": 4, "cancelled_parcel": 1}},
    }
    result = bdcourier.parse_courier_check(body)
    assert result.found is True and result.facts == () and result.sample_size == 5
    history = normalize_courier_history(result.courier_history)
    assert history is not None and history["aggregate"] == {
        "delivered": 4,
        "cancelled": 1,
        "total": 5,
    }


@pytest.mark.parametrize(
    "row",
    [
        {"total_parcel": "5", "success_parcel": 4, "cancelled_parcel": 1},
        {"total_parcel": 5.0, "success_parcel": 4, "cancelled_parcel": 1},
        {"total_parcel": True, "success_parcel": 1, "cancelled_parcel": 0},
        {"total_parcel": 5, "success_parcel": -1, "cancelled_parcel": 1},
        {"total_parcel": 5, "success_parcel": 4},
        [5, 4, 1],
    ],
)
def test_a_count_that_is_not_a_whole_number_drops_the_row(row: Any) -> None:
    body = {
        "status": "success",
        "data": {
            "pathao": row,
            "redx": {"total_parcel": 3, "success_parcel": 3, "cancelled_parcel": 0},
        },
    }
    history = normalize_courier_history(bdcourier.parse_courier_check(body).courier_history)
    assert history is not None
    assert [r["courier"] for r in history["provider_counts"]] == ["redx"]


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ({"status": "error", "message": "x"}, FailureKind.REJECTED),
        ({"data": {}}, FailureKind.BAD_RESPONSE),
        ({"status": "success"}, FailureKind.BAD_RESPONSE),
        ({"status": "success", "data": []}, FailureKind.BAD_RESPONSE),
        ([], FailureKind.BAD_RESPONSE),
        ("ok", FailureKind.BAD_RESPONSE),
    ],
)
def test_an_unreadable_or_refused_answer_is_typed(body: Any, kind: FailureKind) -> None:
    with pytest.raises(ProviderError) as caught:
        bdcourier.parse_courier_check(body)
    assert caught.value.kind is kind


# --------------------------------------------------------------------------- #
# The HTTP call
# --------------------------------------------------------------------------- #


async def test_the_lookup_sends_the_documented_request_with_the_shops_key() -> None:
    api = FakeBdCourier()
    result = await api.adapter().lookup(_request(), {"api_key": f" {KEY} "}, {})
    assert result.found is True
    [request] = api.requests
    assert request.method == "POST"
    assert str(request.url) == "https://api.bdcourier.com/courier-check"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert "x-api-key" not in request.headers and "api_key" not in str(request.url)
    assert request.headers["content-type"] == "application/json"
    # Only the 11-digit national number: nothing about the customer, order or shop.
    assert json.loads(request.content) == {"phone": PHONE}


@pytest.mark.parametrize(
    ("answer", "kind", "retry_after"),
    [
        (httpx.Response(401, json={"status": "error"}), FailureKind.AUTH_FAILED, None),
        (httpx.Response(403), FailureKind.AUTH_FAILED, None),
        (httpx.Response(429, headers={"Retry-After": "120"}), FailureKind.RATE_LIMITED, 120),
        (httpx.Response(429, headers={"Retry-After": "soon"}), FailureKind.RATE_LIMITED, None),
        (httpx.Response(500), FailureKind.UNAVAILABLE, None),
        (httpx.Response(503, text="<html>down</html>"), FailureKind.UNAVAILABLE, None),
        (httpx.Response(400, json={"status": "error"}), FailureKind.REJECTED, None),
        (httpx.Response(404), FailureKind.REJECTED, None),
        (httpx.Response(302), FailureKind.BAD_RESPONSE, None),
        (httpx.Response(200, text="<html>not json</html>"), FailureKind.BAD_RESPONSE, None),
        (httpx.ReadTimeout("slow"), FailureKind.TIMEOUT, None),
        (httpx.ConnectError("refused"), FailureKind.UNAVAILABLE, None),
    ],
)
async def test_http_failures_are_typed(
    answer: httpx.Response | Exception, kind: FailureKind, retry_after: int | None
) -> None:
    api = FakeBdCourier()
    api.check = answer
    with pytest.raises(ProviderError) as caught:
        await api.adapter().lookup(_request(), {"api_key": KEY}, {})
    assert caught.value.kind is kind
    assert caught.value.retry_after_seconds == retry_after
    assert KEY not in str(caught.value) and PHONE not in str(caught.value)


@pytest.mark.parametrize(
    ("request_", "credentials", "kind"),
    [
        (LookupRequest(phone_e164="+14155550100"), {"api_key": KEY}, FailureKind.REJECTED),
        (
            LookupRequest(phone_e164="+8801700000061", market="IN"),
            {"api_key": KEY},
            FailureKind.REJECTED,
        ),
        (LookupRequest(phone_e164="+8801700000061"), {"api_key": "  "}, FailureKind.AUTH_FAILED),
        (LookupRequest(phone_e164="+8801700000061"), {}, FailureKind.AUTH_FAILED),
    ],
)
async def test_nothing_is_sent_for_a_number_or_key_the_page_does_not_accept(
    request_: LookupRequest, credentials: dict, kind: FailureKind
) -> None:
    api = FakeBdCourier()
    with pytest.raises(ProviderError) as caught:
        await api.adapter().lookup(request_, credentials, {})
    assert caught.value.kind is kind and api.requests == []


async def test_the_connection_test_asks_plans_without_the_key() -> None:
    api = FakeBdCourier()
    await api.adapter().test_connection({"api_key": KEY}, {})
    [request] = api.requests
    assert request.method == "GET" and str(request.url) == "https://api.bdcourier.com/plans"
    assert "authorization" not in request.headers and KEY not in str(request.url)

    api.plans = httpx.Response(503)
    with pytest.raises(ProviderError) as caught:
        await api.adapter().test_connection({"api_key": KEY}, {})
    assert caught.value.kind is FailureKind.UNAVAILABLE

    api.plans = httpx.Response(200, text="maintenance")
    with pytest.raises(ProviderError) as caught:
        await api.adapter().test_connection({"api_key": KEY}, {})
    assert caught.value.kind is FailureKind.BAD_RESPONSE

    before = len(api.requests)
    with pytest.raises(ProviderError) as caught:
        await api.adapter().test_connection({"api_key": ""}, {})
    assert caught.value.kind is FailureKind.AUTH_FAILED and len(api.requests) == before


# --------------------------------------------------------------------------- #
# Through the shop: flag, own key, new orders only
# --------------------------------------------------------------------------- #


async def test_flag_off_offers_nothing(client: Any, unique_phone: str, fake: FakeBdCourier) -> None:
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    catalog = (await client.get("/v1/external-risk/providers", headers=headers)).json()
    assert catalog == {"status": "GATED", "blocker": registry.BLOCKER, "providers": []}
    refused = await client.put(
        "/v1/external-risk/providers/bdcourier",
        headers=headers,
        json={"credentials": {"api_key": KEY}},
    )
    assert refused.status_code == 404
    capability = (await client.get("/v1/external-risk/capability", headers=headers)).json()
    assert capability["status"] == "GATED" and capability["available"] is False
    assert fake.requests == []


@pytest.mark.usefixtures("bdcourier_on")
async def test_a_shop_connects_its_own_key_and_checks_a_new_order(
    client: Any, unique_phone: str, fake: FakeBdCourier, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    catalog = (await client.get("/v1/external-risk/providers", headers=headers)).json()
    assert catalog["status"] == "AVAILABLE"
    [entry] = catalog["providers"]
    assert entry["provider_id"] == "bdcourier" and entry["connection"] is None
    assert entry["new_orders_only"] is True and entry["retention_days"] == 30
    assert entry["notice_en"] and entry["notice_bn"]
    assert entry["official_contract"] == "docs/providers/bdcourier/CONTRACT.md"

    await _connect(client, shop)
    capability = (await client.get("/v1/external-risk/capability", headers=headers)).json()
    assert capability["available"] is True and capability["enabled_providers"] == ["bdcourier"]

    customer = (await create_order(client, shop, phone=PHONE))["order"]["customer_id"]
    body = await _lookup(client, shop, customer)
    assert body["outcomes"] == [
        {"provider_id": "bdcourier", "outcome": "FETCHED", "error_code": None}
    ]
    [section] = body["providers"]
    assert section["name"] == "BD Courier" and section["result"]["status"] == "FOUND"
    history = section["result"]["courier_history"]
    assert history["aggregate"] == {"delivered": 530, "cancelled": 90, "total": 620}
    assert "risk_level" not in json.dumps(body) and "verdict" not in json.dumps(body)
    check = [r for r in fake.requests if r.url.path == bdcourier.CHECK_PATH]
    assert len(check) == 1 and json.loads(check[0].content) == {"phone": PHONE}
    assert check[0].headers["authorization"] == f"Bearer {KEY}"

    # A fresh answer is served from the shop's cache: no second call.
    again = await _lookup(client, shop, customer)
    assert again["outcomes"][0]["outcome"] == "CACHED"
    assert len([r for r in fake.requests if r.url.path == bdcourier.CHECK_PATH]) == 1

    [row] = await _rows(customer)
    assert row.expires_at is not None and row.expires_at - row.checked_at == timedelta(hours=24)
    # App records with their extras (the SQLite driver's DEBUG trace prints parameters).
    logs = " | ".join(
        f"{record.getMessage()} {record.__dict__}"
        for record in caplog.records
        if record.name.startswith(("app", "httpx"))
    )
    assert KEY not in logs and PHONE not in logs and "+8801700000061" not in logs


@pytest.mark.usefixtures("bdcourier_on")
async def test_no_new_order_means_no_lookup(
    client: Any, unique_phone: str, fake: FakeBdCourier
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    await _connect(client, shop)
    order = (await create_order(client, shop, phone="01700000062"))["order"]
    async with system_session("test: age the order") as db:
        row = await db.get(Order, uuid.UUID(order["id"]))
        assert row is not None
        row.created_at = utc_now() - timedelta(days=3, minutes=5)

    body = await _lookup(client, shop, order["customer_id"])
    assert body["outcomes"] == [
        {"provider_id": "bdcourier", "outcome": "SKIPPED", "error_code": "NEW_ORDER_REQUIRED"}
    ]
    assert [r for r in fake.requests if r.url.path == bdcourier.CHECK_PATH] == []
    assert await _rows(order["customer_id"]) == []

    # Inside the window but already past confirmation: still not checked.
    async with system_session("test: pack the order") as db:
        row = await db.get(Order, uuid.UUID(order["id"]))
        assert row is not None
        row.created_at = utc_now() - timedelta(days=1)
        row.status = "PACKED"
    body = await _lookup(client, shop, order["customer_id"])
    assert body["outcomes"][0]["error_code"] == "NEW_ORDER_REQUIRED"
    assert [r for r in fake.requests if r.url.path == bdcourier.CHECK_PATH] == []


@pytest.mark.usefixtures("bdcourier_on")
async def test_a_refused_key_shows_on_the_first_lookup(
    client: Any, unique_phone: str, fake: FakeBdCourier
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    await _connect(client, shop)
    fake.check = httpx.Response(401, json={"status": "error"})
    customer = (await create_order(client, shop, phone="01700000063"))["order"]["customer_id"]
    body = await _lookup(client, shop, customer)
    assert body["outcomes"][0] == {
        "provider_id": "bdcourier",
        "outcome": "FAILED",
        "error_code": "PROVIDER_AUTH_FAILED",
    }
    # Not retried: a refused key stays refused.
    assert len([r for r in fake.requests if r.url.path == bdcourier.CHECK_PATH]) == 1
    [section] = body["providers"]
    assert section["health"] == "DOWN"


@pytest.mark.usefixtures("bdcourier_on")
async def test_another_shop_never_uses_this_shops_key(
    client: Any, unique_phone: str, fake: FakeBdCourier
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    await _connect(client, shop)
    other = await signed_in_shop(client, "018" + unique_phone[3:])
    customer = (await create_order(client, other, phone="01700000064"))["order"]["customer_id"]
    body = await _lookup(client, other, customer)
    assert body["outcomes"] == [] and body["status"] == "NOT_CONFIGURED"
    catalog = (await client.get("/v1/external-risk/providers", headers=auth_header(other))).json()
    assert catalog["providers"][0]["connection"] is None
    assert fake.requests == [r for r in fake.requests if r.url.path == bdcourier.PLANS_PATH]


async def test_turning_the_flag_off_stops_lookups_and_hides_answers(
    client: Any, unique_phone: str, fake: FakeBdCourier, settings: Any
) -> None:
    from app.automation.facts import Facts
    from tests.conftest import _global_flag_on

    shop = await signed_in_shop(client, unique_phone)
    flag = _global_flag_on(settings, str(FlagKey.BDCOURIER_ENABLED))
    await anext(flag)
    try:
        await _connect(client, shop)
        customer = (await create_order(client, shop, phone="01700000065"))["order"]["customer_id"]
        await _lookup(client, shop, customer)
        async with system_session("test: condition on") as db:
            assert await Facts(db, {"customer_id": customer}).get("external_success_bps") == (
                530 * 10_000 // 620
            )
    finally:
        await anext(flag, None)

    async with system_session("test: flag gone") as db:
        assert (
            await db.scalar(
                sa.select(FeatureFlag).where(FeatureFlag.key == str(FlagKey.BDCOURIER_ENABLED))
            )
        ) is None
    calls = len(fake.requests)
    body = await _lookup(client, shop, customer)
    assert body["status"] == "GATED" and body["providers"] == [] and body["outcomes"] == []
    assert len(fake.requests) == calls
    async with system_session("test: condition off") as db:
        assert await Facts(db, {"customer_id": customer}).get("external_success_bps") is None


@pytest.mark.usefixtures("bdcourier_on")
async def test_answers_are_pruned_after_thirty_days(
    client: Any, unique_phone: str, fake: FakeBdCourier
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    customer = (await create_order(client, shop, phone="01700000066"))["order"]["customer_id"]
    now = utc_now()
    async with system_session("test: seed lookups") as db:
        for provider, age in (("bdcourier", 31), ("bdcourier", 29), ("other_licensed", 31)):
            db.add(
                ExternalRiskLookup(
                    tenant_id=uuid.UUID(shop["tenant_id"]),
                    customer_id=uuid.UUID(customer),
                    provider_id=provider,
                    status="NOT_FOUND",
                    facts=[],
                    dropped_facts=0,
                    checked_at=now - timedelta(days=age),
                    expires_at=now - timedelta(days=age - 1),
                    trigger="MANUAL",
                    attempts=1,
                    duration_ms=1,
                )
            )
    result = await risk_service.prune_external_risk_lookups()
    assert result["deleted"] >= 1
    left = sorted((r.provider_id, (now - r.checked_at).days) for r in await _rows(customer))
    assert left == [("bdcourier", 29), ("other_licensed", 31)]
