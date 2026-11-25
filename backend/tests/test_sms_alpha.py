"""Alpha SMS behind ``SmsTransport`` (V4 WP7.1).

Fake HTTP only (``httpx.MockTransport``); numbers are ``017000000xx``. The
contract is ``docs/providers/alpha_sms/CONTRACT.md``.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import AsyncClient

from app.common.audit import AuditLog
from app.confirmations import links
from app.confirmations.links import FollowUp
from app.confirmations.models import ConfirmationAttempt, ConfirmationLink, OrderConfirmation
from app.couriers.capabilities import CapabilityState
from app.db.session import system_session
from app.notifications.transport import DeliveryOutcome, SmsMessage
from app.sms_providers.alpha_sms import AlphaSmsTransport, outcome_for_code
from app.sms_providers.manifest import load_sms_manifests, parse_sms_manifest
from app.sms_providers.models import SmsProviderConnection
from tests.conftest_commerce import signed_in_shop
from tests.test_admin import BOOTSTRAP_TOKEN, admin_header

KEY = "alpha-test-key-not-real-0001"
PHONE = "+8801700000011"
PROVIDERS = "/v1/admin/sms/providers"


def _fake(
    answer: Callable[[httpx.Request], httpx.Response], seen: list[httpx.Request]
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _json(body: Any, status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _request: httpx.Response(status, json=body)


def _raise(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def answer(request: httpx.Request) -> httpx.Response:
        raise exc

    return answer


def _form(request: httpx.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


# ----------------------------------------------------------- the codes ---


@pytest.mark.parametrize(
    ("code", "outcome"),
    [
        (0, DeliveryOutcome.SENT),
        *[
            (code, DeliveryOutcome.REJECTED)
            for code in (400, 403, 404, 405, 410, 411, 412, 413, 414, 415, 416, 420, 421)
        ],
        (417, DeliveryOutcome.FAILED),
        (409, DeliveryOutcome.UNKNOWN),
        (418, DeliveryOutcome.UNKNOWN),
        (500, DeliveryOutcome.UNKNOWN),
    ],
)
def test_every_documented_code_and_nothing_else(code: int, outcome: DeliveryOutcome) -> None:
    assert outcome_for_code(code) is outcome


def test_unknown_is_never_retryable() -> None:
    from app.notifications.transport import TransportResult

    assert not TransportResult(DeliveryOutcome.UNKNOWN, "alpha_sms").retryable
    assert TransportResult(DeliveryOutcome.FAILED, "alpha_sms").retryable


# ----------------------------------------------------------- the send ---


async def test_a_send_posts_the_documented_form_and_keeps_the_request_id() -> None:
    seen: list[httpx.Request] = []
    client = _fake(
        _json({"error": 0, "msg": "Request successfully submitted", "data": {"request_id": 4521}}),
        seen,
    )
    transport = AlphaSmsTransport(KEY, sender_id="ecomsbd", client=client)

    result = await transport.send(SmsMessage(phone_e164=PHONE, body="Rupkotha: order Tk 500."))

    assert result.outcome is DeliveryOutcome.SENT and result.provider == "alpha_sms"
    assert result.provider_reference == "4521" and result.provider_segments is None
    (request,) = seen
    assert request.method == "POST" and str(request.url) == "https://api.sms.net.bd/sendsms"
    assert KEY not in str(request.url)  # form field, never the URL
    assert _form(request) == {
        "api_key": KEY,
        "msg": "Rupkotha: order Tk 500.",
        "to": "8801700000011",
        "sender_id": "ecomsbd",
    }


async def test_no_sender_id_sends_none_and_a_string_zero_is_success() -> None:
    seen: list[httpx.Request] = []
    transport = AlphaSmsTransport(KEY, client=_fake(_json({"error": "0", "data": {}}), seen))
    result = await transport.send(SmsMessage(phone_e164="01700000012", body="hello"))
    assert result.outcome is DeliveryOutcome.SENT and result.provider_reference is None
    assert "sender_id" not in _form(seen[0]) and _form(seen[0])["to"] == "8801700000012"


@pytest.mark.parametrize(
    ("answer", "outcome", "detail"),
    [
        (
            _json({"error": 413, "msg": "Invalid Sender ID"}),
            DeliveryOutcome.REJECTED,
            "ALPHA_SMS_413",
        ),
        (
            _json({"error": 417, "msg": "Insufficient balance"}),
            DeliveryOutcome.FAILED,
            "ALPHA_SMS_417",
        ),
        (_json({"error": 409, "msg": "Unknown error"}), DeliveryOutcome.UNKNOWN, "ALPHA_SMS_409"),
        (_json({"error": 499}), DeliveryOutcome.UNKNOWN, "ALPHA_SMS_499"),
        (_json({"ok": True}), DeliveryOutcome.UNKNOWN, "ALPHA_SMS_UNREADABLE_ANSWER"),
        (_json({"error": True}), DeliveryOutcome.UNKNOWN, "ALPHA_SMS_UNREADABLE_ANSWER"),
        (
            lambda _r: httpx.Response(502, text="<html>bad gateway</html>"),
            DeliveryOutcome.UNKNOWN,
            "ALPHA_SMS_UNREADABLE_ANSWER",
        ),
        (_raise(httpx.ConnectError("refused")), DeliveryOutcome.FAILED, "ALPHA_SMS_UNREACHABLE"),
        (_raise(httpx.ConnectTimeout("slow")), DeliveryOutcome.FAILED, "ALPHA_SMS_UNREACHABLE"),
        (_raise(httpx.ReadTimeout("no answer")), DeliveryOutcome.UNKNOWN, "ALPHA_SMS_NO_ANSWER"),
        (
            _raise(httpx.RemoteProtocolError("dropped")),
            DeliveryOutcome.UNKNOWN,
            "ALPHA_SMS_NO_ANSWER",
        ),
    ],
)
async def test_only_a_provable_non_send_is_retryable(
    answer: Callable[[httpx.Request], httpx.Response], outcome: DeliveryOutcome, detail: str
) -> None:
    transport = AlphaSmsTransport(KEY, client=_fake(answer, []))
    result = await transport.send(SmsMessage(phone_e164=PHONE, body="hello"))
    assert (result.outcome, result.detail) == (outcome, detail)
    assert result.retryable is (outcome is DeliveryOutcome.FAILED)


async def test_a_bad_message_never_reaches_the_provider() -> None:
    seen: list[httpx.Request] = []
    transport = AlphaSmsTransport(KEY, client=_fake(_json({"error": 0}), seen))
    for message in (
        SmsMessage(phone_e164="+14155550100", body="hello"),
        SmsMessage(phone_e164=PHONE, body="   "),
        SmsMessage(phone_e164=PHONE, body="x" * 700),
    ):
        assert (await transport.send(message)).outcome is DeliveryOutcome.REJECTED
    assert not AlphaSmsTransport("  ").is_configured
    no_key = await AlphaSmsTransport("  ", client=_fake(_json({"error": 0}), seen)).send(
        SmsMessage(phone_e164=PHONE, body="hello")
    )
    assert no_key.outcome is DeliveryOutcome.NOT_CONFIGURED and seen == []


async def test_no_key_number_or_text_in_logs_or_repr(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    transport = AlphaSmsTransport(KEY, client=_fake(_json({"error": 416}), []))
    await transport.send(SmsMessage(phone_e164=PHONE, body="secret order text"))
    await AlphaSmsTransport(KEY, client=_fake(_raise(httpx.ReadTimeout("x")), [])).send(
        SmsMessage(phone_e164=PHONE, body="secret order text")
    )
    logged = caplog.text + "".join(
        json.dumps(record.__dict__, default=str) for record in caplog.records
    )
    assert "alpha sms" in logged
    for secret in (KEY, "1700000011", "secret order text"):
        assert secret not in logged
    assert KEY not in repr(transport)


# ------------------------------------------------------------ manifest ---


def test_the_manifest_claims_only_what_the_page_states() -> None:
    manifest = load_sms_manifests()["alpha_sms"]
    assert manifest.base_url == "https://api.sms.net.bd"
    assert manifest.credential_fields == ("api_key",)
    for name in ("send", "sender_id", "error_codes", "delivery_report", "balance"):
        claim = manifest.capabilities[name]
        assert claim.state is CapabilityState.SUPPORTED
        assert claim.verified_at is not None and "portal.sms.net.bd/api" in (claim.evidence or "")
    for name in (
        "idempotency_key",
        "delivery_callback",
        "unicode_bangla",
        "segment_count",
        "segment_price",
        "max_length",
        "rate_limit",
    ):
        assert manifest.capabilities[name].state is CapabilityState.UNKNOWN
        assert not manifest.supports(name)
    contract = Path(__file__).resolve().parents[2] / "docs/providers/alpha_sms/CONTRACT.md"
    assert "https://portal.sms.net.bd/api" in contract.read_text(encoding="utf-8")


def test_a_claim_without_a_dated_source_is_unknown() -> None:
    manifest = parse_sms_manifest(
        {
            "provider": "other_sms",
            "base_url": "https://sms.example.invalid",
            "credential_fields": ["api_key"],
            "capabilities": {
                "send": True,
                "sender_id": {"state": True, "evidence": "no date"},
                "balance": {"state": True, "verified_at": "2026-11-23"},
            },
        }
    )
    assert not any(manifest.supports(name) for name in ("send", "sender_id", "balance"))
    with pytest.raises(ValueError):
        parse_sms_manifest(
            {"provider": "x_sms", "base_url": "http://plain", "credential_fields": ["k"]}
        )


# --------------------------------------------------- account + switch ---


@pytest.fixture
def sms_admin(settings: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A bootstrap SUPERADMIN token for the running application."""
    from app.core.config import get_settings, reset_settings_cache

    monkeypatch.setenv("ADMIN_API_TOKENS", json.dumps([BOOTSTRAP_TOKEN]))
    reset_settings_cache()
    try:
        yield get_settings()
    finally:
        monkeypatch.undo()
        reset_settings_cache()


@pytest_asyncio.fixture
async def clean_sms() -> AsyncIterator[None]:
    yield
    async with system_session("test: clean sms") as db:
        await db.execute(sa.delete(SmsProviderConnection))


@pytest_asyncio.fixture
async def alpha_flag(settings: Any) -> AsyncIterator[None]:
    from tests.conftest import _global_flag_on

    async for _ in _global_flag_on(settings, "alpha_sms_enabled"):
        yield


async def _connect(client: AsyncClient, **body: Any) -> httpx.Response:
    return await client.put(
        f"{PROVIDERS}/alpha_sms",
        headers=admin_header(),
        json={"reason": "connect alpha sms", **body},
    )


async def test_the_console_connects_without_ever_returning_the_key(
    client: AsyncClient, sms_admin: Any, clean_sms: None
) -> None:
    listed = await client.get(PROVIDERS, headers=admin_header())
    assert listed.status_code == 200
    (item,) = listed.json()
    assert item["provider"] == "alpha_sms" and item["connection"] is None
    assert item["flag_enabled"] is False
    assert item["manifest"]["capabilities"]["segment_price"]["state"] == "unknown"

    not_ready = await _connect(client, enabled=True)
    assert not_ready.status_code == 422  # no row yet: credentials are required
    wrong = await _connect(client, credentials={"token": KEY})
    assert wrong.status_code == 422
    bad_sender = await _connect(client, credentials={"api_key": KEY}, sender_id="no/slash")
    assert bad_sender.status_code == 422
    unknown = await client.put(
        f"{PROVIDERS}/other_sms", headers=admin_header(), json={"reason": "connect other one"}
    )
    assert unknown.status_code == 404 and unknown.json()["code"] == "SMS_PROVIDER_UNKNOWN"

    response = await _connect(
        client, credentials={"api_key": KEY}, sender_id="ecomsbd", enabled=True
    )
    assert response.status_code == 200, response.text
    connection = response.json()["connection"]
    assert connection["enabled"] is True and connection["has_credentials"] is True
    assert connection["masked_identifier"] == "****0001" and connection["sender_id"] == "ecomsbd"
    for text in (response.text, (await client.get(PROVIDERS, headers=admin_header())).text):
        assert KEY not in text and "credentials_encrypted" not in text and "key_version" not in text

    async with system_session("test: read sms row") as db:
        row = await db.scalar(sa.select(SmsProviderConnection))
        assert row is not None and KEY not in (row.credentials_encrypted or "")
        audits = (
            await db.scalars(
                sa.select(AuditLog).where(AuditLog.action == "admin.sms_provider_updated")
            )
        ).all()
    assert audits and all(KEY not in json.dumps(a.context, default=str) for a in audits)

    cleared = await _connect(client, sender_id="", enabled=False)
    assert cleared.status_code == 200 and cleared.json()["connection"]["sender_id"] is None


async def test_ops_reads_but_cannot_set_credentials(
    client: AsyncClient, sms_admin: Any, clean_sms: None
) -> None:
    from app.admin.models import PlatformAdminRole
    from tests.test_admin import _provision_admin

    token = f"INSECURE_DEV_ops_sms_token_for_tests_only_{uuid.uuid4().hex}"
    await _provision_admin(PlatformAdminRole.OPS, token, sms_admin)
    assert (await client.get(PROVIDERS, headers=admin_header(token))).status_code == 200
    refused = await client.put(
        f"{PROVIDERS}/alpha_sms",
        headers=admin_header(token),
        json={"credentials": {"api_key": KEY}, "reason": "ops tries to connect"},
    )
    assert refused.status_code == 403


async def test_the_gateway_sends_only_with_both_flag_and_account_on(
    client: AsyncClient, sms_admin: Any, settings: Any, clean_sms: None
) -> None:
    from app.sms_providers.service import active_sms_transport
    from tests.conftest import _global_flag_on

    async def active() -> Any:
        async with system_session("test: sms switch") as db:
            return await active_sms_transport(db)

    assert await active() is None  # nothing stored, flag off
    assert (await _connect(client, credentials={"api_key": KEY}, enabled=True)).status_code == 200
    assert await active() is None  # flag still off (default)
    async for _ in _global_flag_on(settings, "alpha_sms_enabled"):
        transport = await active()
        assert isinstance(transport, AlphaSmsTransport) and transport.is_configured
        assert (await _connect(client, enabled=False)).status_code == 200
        assert await active() is None  # account switched off


# ------------------------------------------- the WP2.3 confirm link ---


async def test_the_confirm_link_goes_out_through_alpha_sms_and_unknown_is_never_resent(
    client: AsyncClient,
    sms_admin: Any,
    settings: Any,
    unique_phone: str,
    clean_sms: None,
    alpha_flag: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.sms_providers import service as sms_service
    from tests.test_confirmation_links import NOON, _follow, _row, _send, _unanswered

    seen: list[httpx.Request] = []
    replies = iter([httpx.ReadTimeout("no answer")])

    def answer(request: httpx.Request) -> httpx.Response:
        raise next(replies)

    real = sms_service.AlphaSmsTransport

    def with_fake(*args: Any, **kwargs: Any) -> AlphaSmsTransport:
        return real(*args, client=_fake(answer, seen), **kwargs)

    monkeypatch.setattr(sms_service, "AlphaSmsTransport", with_fake)
    monkeypatch.setattr(links, "public_base", lambda: "https://app.ecomsbd.com")

    async def switched_on(db: Any, tenant_id: Any) -> bool:
        return True  # the shop's own switch (launch run 14)

    monkeypatch.setattr(links, "shop_link_switch", switched_on)
    assert (await _connect(client, credentials={"api_key": KEY}, enabled=True)).status_code == 200

    from app.common.feature_flags import FlagKey
    from tests.conftest import _global_flag_on

    shop = await signed_in_shop(client, unique_phone)
    async for _ in _global_flag_on(settings, str(FlagKey.CONFIRMATION_ENGINE_ENABLED)):
        async for _ in _global_flag_on(settings, str(FlagKey.CONFIRMATION_SMS_LINK_ENABLED)):
            confirmation_id = await _unanswered(client, shop, "01700000061")
            assert await _follow(shop, confirmation_id, NOON) is FollowUp.LINK
            await _send(shop, confirmation_id)  # runs the handler twice

    assert len(seen) == 1  # sent once; the lost answer is never re-sent
    assert _form(seen[0])["to"] == "8801700000061"
    (row,) = await _row(shop, OrderConfirmation, OrderConfirmation.id == confirmation_id)
    assert row.state == "SMS_LINK_SENT"  # the customer may have it: still answerable
    (attempt,) = await _row(
        shop,
        ConfirmationAttempt,
        ConfirmationAttempt.confirmation_id == confirmation_id,
        ConfirmationAttempt.channel == "SMS_LINK",
    )
    assert attempt.status == "UNKNOWN" and attempt.provider == "alpha_sms"
    (link,) = await _row(
        shop, ConfirmationLink, ConfirmationLink.confirmation_id == confirmation_id
    )
    assert link.used_at is None and link.expires_at > NOON
