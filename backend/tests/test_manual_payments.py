"""Seller payment by bKash / Nagad TrxID (V4 WP2.5).

What must hold:

*   the form is offered only with the flag on, Free Launch Mode off, a build
    that may show an external payment (never Play) and a receiving number;
*   a submission grants nothing; the payer's number is kept masked only;
*   one TrxID pays once — per method, across every shop and state — while the
    same shop retrying the same submission gets its first row back;
*   an operator approves (BILLING_GRANT) with an absolute period end, once;
    rejects with a reason the seller sees; the seller is notified once.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.admin.models import PlatformAdminRole
from app.billing.manual_payments import (
    amount_for,
    normalise_trx_id,
    period_end_for,
    receiving_accounts,
)
from app.billing.models import BillingPaymentSubmission, BillingTransaction
from app.common.audit import AuditAction, AuditLog
from app.common.outbox import OutboxEvent, OutboxTopic
from app.core.context import RequestContext, clear_context, set_context
from app.core.errors import ValidationError
from app.entitlements.catalog import PlanCode
from app.entitlements.models import Subscription
from app.notifications.models import Notification, NotificationKind
from app.notifications.templates import render
from app.tenants.roles import TenantRole
from tests.conftest_commerce import grant_plan, signed_in_shop
from tests.test_admin import BOOTSTRAP_TOKEN, _provision_admin, admin_header
from tests.test_auth_flow import auth_header
from tests.test_team import _member_session

ACCOUNTS = [
    {"method": "BKASH", "number": "01700000001", "label": "Personal"},
    {"method": "nagad", "number": "+8801700000002", "label": "Nagad"},
    {"method": "ROCKET", "number": "01700000003"},  # unknown method: ignored
    {"method": "BKASH", "number": "12345"},  # not a mobile: ignored
    "not an object",
]
PAYER = "01700000077"
OPTIONS = "/v1/billing/manual-payments/options"
SUBMIT = "/v1/billing/manual-payments"


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> Any:
    from app.core.config import get_settings, reset_settings_cache

    for key, value in values.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()
    return get_settings()


@pytest.fixture
async def payments_on(settings: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    """Flag on, two receiving accounts, the bootstrap admin token."""
    from app.common.feature_flags import FlagKey
    from app.core.config import reset_settings_cache
    from tests.conftest import _global_flag_on

    live = _env(
        monkeypatch,
        MANUAL_PAYMENT_ACCOUNTS=json.dumps(ACCOUNTS),
        ADMIN_API_TOKENS=json.dumps([BOOTSTRAP_TOKEN]),
    )
    try:
        async for _ in _global_flag_on(settings, str(FlagKey.MANUAL_PAYMENTS_ENABLED)):
            yield live
    finally:
        monkeypatch.undo()
        reset_settings_cache()


def _another_phone() -> str:
    return f"018{uuid.uuid4().int % 100_000_000:08d}"


def _trx() -> str:
    return "TX" + uuid.uuid4().hex[:10].upper()


def _body(trx: str, **overrides: Any) -> dict[str, Any]:
    body = {
        "plan_code": "starter",
        "months": 3,
        "method": "BKASH",
        "payer_number": PAYER,
        "trx_id": trx,
    }
    body.update(overrides)
    return body


async def _submit(client: AsyncClient, shop: dict[str, Any], trx: str, **overrides: Any) -> Any:
    return await client.post(SUBMIT, json=_body(trx, **overrides), headers=auth_header(shop))


async def _rows(model: Any, *where: Any) -> list[Any]:
    from app.core.config import get_settings
    from app.db.session import get_sessionmaker
    from app.db.tenancy import mark_session_system

    async with get_sessionmaker(get_settings())() as raw:
        mark_session_system(raw.sync_session, "test: read manual payment rows")
        return list((await raw.scalars(sa.select(model).where(*where))).all())


# ----------------------------------------------------------------- units ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8n7a6d5c4b", "8N7A6D5C4B"),
        (" 8N7A 6D5C-4B ", "8N7A6D5C4B"),
        ("৮N৭A৬D৫C৪B", "8N7A6D5C4B"),
    ],
)
def test_a_trx_id_is_normalised(raw: str, expected: str):
    assert normalise_trx_id(raw) == expected


@pytest.mark.parametrize("raw", ["", "12345", "ABC$12345", "A" * 21, None])
def test_a_malformed_trx_id_is_refused(raw: str | None):
    with pytest.raises(ValidationError) as caught:
        normalise_trx_id(raw)
    assert caught.value.details == {"code": "TRX_ID_INVALID", "field": "trx_id"}


def test_only_sensible_receiving_accounts_are_kept(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import reset_settings_cache

    try:
        live = _env(monkeypatch, MANUAL_PAYMENT_ACCOUNTS=json.dumps(ACCOUNTS))
        assert [account.as_dict() for account in receiving_accounts(live)] == [
            {"method": "BKASH", "number": "01700000001", "label": "Personal"},
            {"method": "NAGAD", "number": "01700000002", "label": "Nagad"},
        ]
        assert receiving_accounts(_env(monkeypatch, MANUAL_PAYMENT_ACCOUNTS="")) == []
    finally:
        monkeypatch.undo()
        reset_settings_cache()


def test_the_amount_is_the_catalogue_price_times_months_and_paid_time_is_never_lost():
    assert amount_for(PlanCode.STARTER, 3) == 19_900 * 3
    assert amount_for(PlanCode.PRO, 12) == 39_900 * 12
    now = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)
    assert period_end_for(None, 1, now) == now + timedelta(days=30)
    assert period_end_for(now - timedelta(days=5), 2, now) == now + timedelta(days=60)
    later = now + timedelta(days=10)
    assert period_end_for(later, 3, now) == later + timedelta(days=90)


# ------------------------------------------------------------ options ---


async def test_the_form_is_off_by_default_and_says_nothing(client: AsyncClient, unique_phone: str):
    shop = await signed_in_shop(client, unique_phone)
    body = (await client.get(OPTIONS, headers=auth_header(shop))).json()
    assert body["available"] is False
    assert body["unavailable_reason"] == "FEATURE_OFF"
    assert body["accounts"] == [] and body["plans"] == []
    refused = await _submit(client, shop, _trx())
    assert refused.status_code == 403, refused.text
    assert refused.json()["details"]["code"] == "MANUAL_PAYMENT_UNAVAILABLE"


async def test_options_show_the_receiving_numbers_and_the_monthly_prices(
    client: AsyncClient, unique_phone: str, payments_on: Any
):
    shop = await signed_in_shop(client, unique_phone)
    body = (await client.get(OPTIONS, headers=auth_header(shop))).json()
    assert body["available"] is True and body["unavailable_reason"] is None
    assert [a["method"] for a in body["accounts"]] == ["BKASH", "NAGAD"]
    assert {p["code"]: p["monthly_price"]["amount_paisa"] for p in body["plans"]} == {
        "starter": 19_900,
        "pro": 39_900,
    }
    assert (body["min_months"], body["max_months"]) == (1, 12)
    starter = body["plans"][0]
    assert [(q["months"], q["amount"]["amount_paisa"]) for q in starter["amounts"]] == [
        (1, 19_900),
        (3, 59_700),
        (6, 119_400),
        (12, 238_800),
    ]


async def test_a_play_build_never_gets_the_form(
    client: AsyncClient, unique_phone: str, payments_on: Any
):
    shop = await signed_in_shop(client, unique_phone)
    play = {**auth_header(shop), "X-Distribution-Channel": "PLAY"}
    body = (await client.get(OPTIONS, headers=play)).json()
    assert (body["available"], body["unavailable_reason"]) == (False, "CHANNEL")
    assert body["accounts"] == []
    refused = await client.post(SUBMIT, json=_body(_trx()), headers=play)
    assert refused.status_code == 403
    assert refused.json()["details"]["reason"] == "CHANNEL"


async def test_free_launch_mode_and_no_accounts_hide_the_form(
    client: AsyncClient, unique_phone: str, payments_on: Any, monkeypatch: pytest.MonkeyPatch
):
    shop = await signed_in_shop(client, unique_phone)
    _env(monkeypatch, FREE_LAUNCH_MODE="true")
    body = (await client.get(OPTIONS, headers=auth_header(shop))).json()
    assert (body["available"], body["unavailable_reason"]) == (False, "FREE_LAUNCH")
    _env(monkeypatch, FREE_LAUNCH_MODE="false", MANUAL_PAYMENT_ACCOUNTS="[]")
    body = (await client.get(OPTIONS, headers=auth_header(shop))).json()
    assert (body["available"], body["unavailable_reason"]) == (False, "NO_ACCOUNTS")


async def test_only_the_owner_may_pay(client: AsyncClient, unique_phone: str, payments_on: Any):
    owner = await signed_in_shop(client, unique_phone, plan="pro")
    manager = await _member_session(client, owner, _another_phone(), TenantRole.MANAGER)
    assert (await client.get(OPTIONS, headers=auth_header(manager))).status_code == 403
    assert (await _submit(client, manager, _trx())).status_code == 403


# ------------------------------------------------------------- submit ---


async def test_a_submission_grants_nothing_and_keeps_the_payer_number_masked(
    client: AsyncClient, unique_phone: str, payments_on: Any, caplog: pytest.LogCaptureFixture
):
    shop = await signed_in_shop(client, unique_phone)
    trx = _trx()
    caplog.set_level(logging.DEBUG)
    response = await _submit(client, shop, trx.lower())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["state"] == "SUBMITTED"
    assert body["trx_id"] == trx
    assert body["amount"]["amount_paisa"] == 59_700
    assert body["payer_number_masked"] == "01700****77"
    assert PAYER not in response.text
    # The application's own logs (the SQLite driver's DEBUG echo is not ours).
    app_logs = " ".join(
        f"{record.getMessage()} {record.__dict__}"
        for record in caplog.records
        if record.name.startswith("app")
    )
    assert app_logs and PAYER not in app_logs and trx not in app_logs

    (row,) = await _rows(BillingPaymentSubmission, BillingPaymentSubmission.trx_id == trx)
    assert row.payer_number_masked == "01700****77"
    assert str(row.tenant_id) == shop["tenant_id"]
    entitlements = (await client.get("/v1/billing/entitlements", headers=auth_header(shop))).json()
    assert entitlements["plan"] == "free"

    (audit,) = await _rows(
        AuditLog,
        AuditLog.action == str(AuditAction.BILLING_MANUAL_PAYMENT_SUBMITTED),
        AuditLog.entity_id == str(row.id),
    )
    assert PAYER not in json.dumps(audit.context)

    listed = (await client.get(SUBMIT, headers=auth_header(shop))).json()
    assert [item["id"] for item in listed] == [body["id"]]


async def test_one_trx_id_pays_once(client: AsyncClient, unique_phone: str, payments_on: Any):
    shop = await signed_in_shop(client, unique_phone)
    other = await signed_in_shop(client, _another_phone())
    trx = _trx()
    first = await _submit(client, shop, trx)
    assert first.status_code == 201

    retry = await _submit(client, shop, f" {trx.lower()} ")
    assert retry.status_code == 200 and retry.json()["id"] == first.json()["id"]

    for refused in (
        await _submit(client, shop, trx, months=1),
        await _submit(client, other, trx),
    ):
        assert refused.status_code == 409, refused.text
        assert refused.json()["details"]["code"] == "TRX_ID_ALREADY_USED"

    # Unique per method: the same letters on Nagad are a different payment.
    assert (await _submit(client, other, trx, method="NAGAD")).status_code == 201
    assert len(await _rows(BillingPaymentSubmission, BillingPaymentSubmission.trx_id == trx)) == 2


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"plan_code": "free"}, "PLAN_NOT_PAYABLE"),
        ({"plan_code": "gold"}, "PLAN_NOT_PAYABLE"),
        ({"method": "ROCKET"}, "METHOD_NOT_OFFERED"),
        ({"trx_id": "AB$#12"}, "TRX_ID_INVALID"),
        ({"payer_number": "0123456789"}, None),
        ({"months": 13}, None),
        ({"months": 0}, None),
    ],
)
async def test_a_bad_submission_is_refused(
    client: AsyncClient,
    unique_phone: str,
    payments_on: Any,
    overrides: dict[str, Any],
    code: str | None,
):
    shop = await signed_in_shop(client, unique_phone)
    refused = await _submit(client, shop, _trx(), **overrides)
    assert refused.status_code == 422, refused.text
    if code is not None:
        assert refused.json()["details"]["code"] == code


# -------------------------------------------------------------- admin ---


async def _submitted(client: AsyncClient, shop: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    response = await _submit(client, shop, _trx(), **overrides)
    assert response.status_code == 201, response.text
    return response.json()


async def test_approve_grants_the_plan_once_with_a_fixed_end(
    client: AsyncClient, unique_phone: str, payments_on: Any
):
    shop = await signed_in_shop(client, unique_phone)
    submitted = await _submitted(client, shop)
    queue = await client.get(
        "/v1/admin/payments", params={"state": "SUBMITTED"}, headers=admin_header()
    )
    assert queue.status_code == 200
    assert submitted["id"] in [row["id"] for row in queue.json()]
    assert PAYER not in queue.text

    url = f"/v1/admin/payments/{submitted['id']}/approve"
    before = datetime.now(UTC)
    approved = await client.post(url, json={"note": "seen in bKash app"}, headers=admin_header())
    assert approved.status_code == 200, approved.text
    first = approved.json()
    assert first["state"] == "APPROVED"
    period_end = datetime.fromisoformat(first["period_end"])
    assert before + timedelta(days=90) <= period_end <= datetime.now(UTC) + timedelta(days=90)

    again = await client.post(url, json={}, headers=admin_header())
    assert again.status_code == 200 and again.json()["period_end"] == first["period_end"]

    tenant_id = uuid.UUID(shop["tenant_id"])
    (subscription,) = await _rows(Subscription, Subscription.tenant_id == tenant_id)
    assert (subscription.plan_code, subscription.source) == ("starter", "manual_admin")
    assert subscription.current_period_end == period_end
    (transaction,) = await _rows(BillingTransaction, BillingTransaction.tenant_id == tenant_id)
    assert (transaction.kind, transaction.amount_paisa) == ("PURCHASE", 59_700)
    assert transaction.provider_event_id == f"manual_payment:{submitted['id']}"
    entitlements = (await client.get("/v1/billing/entitlements", headers=auth_header(shop))).json()
    assert entitlements["plan"] == "starter"
    # Paid, so not "given to you by support", although the source is manual.
    seen = (await client.get("/v1/billing/subscription", headers=auth_header(shop))).json()
    assert (seen["provider"], seen["support_credit"]) == ("manual_admin", False)
    assert seen["status_reason"].startswith("Paid: ")

    refused = await client.post(
        f"/v1/admin/payments/{submitted['id']}/reject",
        json={"reason": "changed my mind"},
        headers=admin_header(),
    )
    assert refused.status_code == 409
    assert refused.json()["details"]["code"] == "PAYMENT_ALREADY_REVIEWED"


async def test_approve_adds_after_time_already_paid(
    client: AsyncClient, unique_phone: str, payments_on: Any
):
    shop = await signed_in_shop(client, unique_phone)
    await grant_plan(shop["tenant_id"], "starter", days=20)
    (paid,) = await _rows(
        Subscription,
        Subscription.tenant_id == uuid.UUID(shop["tenant_id"]),
        Subscription.plan_code == "starter",
    )
    submitted = await _submitted(client, shop, months=1, plan_code="pro")
    approved = await client.post(
        f"/v1/admin/payments/{submitted['id']}/approve", json={}, headers=admin_header()
    )
    assert approved.status_code == 200, approved.text
    assert datetime.fromisoformat(approved.json()["period_end"]) == paid.current_period_end + (
        timedelta(days=30)
    )


async def test_a_support_grant_is_still_called_support_credit(
    client: AsyncClient, unique_phone: str, payments_on: Any
):
    shop = await signed_in_shop(client, unique_phone)
    await grant_plan(shop["tenant_id"], "starter", days=20)
    seen = (await client.get("/v1/billing/subscription", headers=auth_header(shop))).json()
    assert (seen["provider"], seen["support_credit"]) == ("manual_admin", True)


async def test_reject_needs_a_reason_the_seller_then_sees(
    client: AsyncClient, unique_phone: str, payments_on: Any
):
    shop = await signed_in_shop(client, unique_phone)
    submitted = await _submitted(client, shop)
    url = f"/v1/admin/payments/{submitted['id']}/reject"
    assert (await client.post(url, json={"reason": ""}, headers=admin_header())).status_code == 422
    reason = "No payment with this TrxID reached our bKash"
    rejected = await client.post(url, json={"reason": reason}, headers=admin_header())
    assert rejected.status_code == 200 and rejected.json()["state"] == "REJECTED"
    assert (await client.post(url, json={"reason": reason}, headers=admin_header())).json()[
        "reviewed_at"
    ] == rejected.json()["reviewed_at"]

    (mine,) = (await client.get(SUBMIT, headers=auth_header(shop))).json()
    assert (mine["state"], mine["reason"]) == ("REJECTED", reason)
    approve = await client.post(
        f"/v1/admin/payments/{submitted['id']}/approve", json={}, headers=admin_header()
    )
    assert approve.status_code == 409
    tenant_id = uuid.UUID(shop["tenant_id"])
    assert await _rows(BillingTransaction, BillingTransaction.tenant_id == tenant_id) == []
    entitlements = (await client.get("/v1/billing/entitlements", headers=auth_header(shop))).json()
    assert entitlements["plan"] == "free"


async def test_a_support_admin_may_read_the_queue_but_not_approve(
    client: AsyncClient, unique_phone: str, payments_on: Any
):
    shop = await signed_in_shop(client, unique_phone)
    submitted = await _submitted(client, shop)
    token = "INSECURE_DEV_support_admin_" + uuid.uuid4().hex
    await _provision_admin(PlatformAdminRole.SUPPORT, token, payments_on)
    support = admin_header(token)
    assert (await client.get("/v1/admin/payments", headers=support)).status_code == 200
    for action, body in (("approve", {}), ("reject", {"reason": "not found"})):
        denied = await client.post(
            f"/v1/admin/payments/{submitted['id']}/{action}", json=body, headers=support
        )
        assert denied.status_code == 403, denied.text
    assert (await client.get("/v1/admin/payments", headers=auth_header(shop))).status_code == 401
    missing = await client.post(
        f"/v1/admin/payments/{uuid.uuid4()}/approve", json={}, headers=admin_header()
    )
    assert missing.status_code == 404


# ------------------------------------------------------- notification ---


def test_the_review_topic_has_a_handler():
    import app.billing.jobs  # noqa: F401  (registers the handlers, as the worker does)
    from app.worker.jobs import HANDLERS

    assert str(OutboxTopic.BILLING_MANUAL_PAYMENT_REVIEWED) in HANDLERS


@pytest.mark.parametrize("locale", ["en", "bn"])
def test_the_review_notification_reads_in_both_languages(locale: str):
    approved = render(
        NotificationKind.BILLING_PAYMENT_REVIEWED,
        {"state": "APPROVED", "plan": "starter", "valid_until": "2027-02-17"},
        locale,
    )
    rejected = render(NotificationKind.BILLING_PAYMENT_REVIEWED, {"state": "REJECTED"}, locale)
    assert approved and rejected and approved != rejected
    assert "2027-02-17" in approved[1]


async def test_the_seller_is_notified_once_per_review(
    client: AsyncClient, unique_phone: str, payments_on: Any
):
    import app.billing.jobs  # noqa: F401
    from app.db.session import session_scope
    from app.worker.jobs import HANDLERS

    shop = await signed_in_shop(client, unique_phone)
    approved = await _submitted(client, shop)
    rejected = await _submitted(client, shop)
    await client.post(
        f"/v1/admin/payments/{approved['id']}/approve", json={}, headers=admin_header()
    )
    await client.post(
        f"/v1/admin/payments/{rejected['id']}/reject",
        json={"reason": "Amount does not match"},
        headers=admin_header(),
    )
    tenant_id = uuid.UUID(shop["tenant_id"])
    events = await _rows(
        OutboxEvent,
        OutboxEvent.tenant_id == tenant_id,
        OutboxEvent.topic == str(OutboxTopic.BILLING_MANUAL_PAYMENT_REVIEWED),
    )
    assert sorted(event.payload["state"] for event in events) == ["APPROVED", "REJECTED"]

    token = set_context(RequestContext(trace_id="test", tenant_id=tenant_id))
    try:
        for _ in range(2):  # a retried event notifies once
            for event in events:
                async with session_scope() as db:
                    await HANDLERS[event.topic](db, event)
    finally:
        clear_context(token)

    alerts = await _rows(
        Notification,
        Notification.tenant_id == tenant_id,
        Notification.kind == str(NotificationKind.BILLING_PAYMENT_REVIEWED),
    )
    assert sorted(alert.payload["params"]["state"] for alert in alerts) == [
        "APPROVED",
        "REJECTED",
    ]
    for alert in alerts:
        assert alert.audience == "billing.manage"
        assert alert.payload["target"] == {"route": "billing"}
        assert approved["trx_id"] not in alert.body and "Amount does not match" not in alert.body
