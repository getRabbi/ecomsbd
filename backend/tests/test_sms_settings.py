"""The shop's own SMS confirm-link switch (launch run 14).

With the platform flag ``confirmation_sms_link_enabled`` on, a customer gets a
link only from a shop that switched it on in Settings → SMS (default off). The
switch is offered only while the platform can send; turning it on is audited.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient

from app.common.audit import AuditAction, AuditLog
from app.confirmations import links
from app.confirmations.links import FollowUp
from app.confirmations.models import OrderConfirmation
from app.notifications.transport import MockSmsTransport
from tests.conftest_commerce import signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_confirmation_links import BASE, NOON, _follow, _row, _unanswered

URL = "/v1/shop/sms-settings"


@pytest.fixture
def sms(monkeypatch: pytest.MonkeyPatch) -> MockSmsTransport:
    transport = MockSmsTransport()
    monkeypatch.setattr(links, "sms_transport", lambda: transport)
    monkeypatch.setattr(links, "public_base", lambda: BASE)
    return transport


@pytest.fixture
async def platform_on(settings: Any) -> AsyncIterator[None]:
    from app.common.feature_flags import FlagKey
    from tests.conftest import _global_flag_on

    async for _ in _global_flag_on(settings, str(FlagKey.CONFIRMATION_ENGINE_ENABLED)):
        async for _ in _global_flag_on(settings, str(FlagKey.CONFIRMATION_SMS_LINK_ENABLED)):
            yield


async def test_the_switch_is_off_by_default_and_hidden_without_the_platform(
    client: AsyncClient, unique_phone: str, sms: MockSmsTransport
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    got = await client.get(URL, headers=auth_header(shop))
    assert got.status_code == 200, got.text
    assert got.json() == {"confirmation_link": {"available": False, "enabled": False}}

    refused = await client.patch(
        URL, json={"confirmation_link_enabled": True}, headers=auth_header(shop)
    )
    assert refused.status_code in (403, 409, 422), refused.text
    assert refused.json()["code"] == "FEATURE_DISABLED"
    # Turning it off is always allowed.
    off = await client.patch(
        URL, json={"confirmation_link_enabled": False}, headers=auth_header(shop)
    )
    assert off.status_code == 200, off.text


async def test_with_the_platform_on_no_link_goes_until_the_shop_switches_it_on(
    client: AsyncClient,
    unique_phone: str,
    sms: MockSmsTransport,
    platform_on: None,
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    assert (await client.get(URL, headers=auth_header(shop))).json() == {
        "confirmation_link": {"available": True, "enabled": False}
    }

    # Off (the default): the seller gets the row, no SMS.
    first = await _unanswered(client, shop, "01700000071")
    assert await _follow(shop, first, NOON) is FollowUp.REVIEW
    assert sms.sent == []

    on = await client.patch(
        URL, json={"confirmation_link_enabled": True}, headers=auth_header(shop)
    )
    assert on.status_code == 200, on.text
    assert on.json() == {"confirmation_link": {"available": True, "enabled": True}}

    second = await _unanswered(client, shop, "01700000072")
    assert await _follow(shop, second, NOON) is FollowUp.LINK
    (row,) = await _row(shop, OrderConfirmation, OrderConfirmation.id == second)
    assert row.state == "SMS_LINK_SENT"

    # Audited, with the value and no customer data.
    audits = await _row(
        shop,
        AuditLog,
        AuditLog.action == str(AuditAction.SMS_SETTINGS_UPDATED),
        AuditLog.tenant_id == uuid.UUID(shop["tenant_id"]),
    )
    assert [a.context for a in audits] == [{"confirmation_link_enabled": True}]

    # Off again: the next unanswered order goes to the seller.
    await client.patch(URL, json={"confirmation_link_enabled": False}, headers=auth_header(shop))
    third = await _unanswered(client, shop, "01700000073")
    assert await _follow(shop, third, NOON) is FollowUp.REVIEW


async def test_one_shops_switch_is_not_anothers(
    client: AsyncClient,
    unique_phone: str,
    sms: MockSmsTransport,
    platform_on: None,
) -> None:
    mine = await signed_in_shop(client, unique_phone)
    other_phone = f"019{uuid.uuid4().int % 100_000_000:08d}"
    other = await signed_in_shop(client, other_phone, shop_name="Other Shop")
    await client.patch(URL, json={"confirmation_link_enabled": True}, headers=auth_header(mine))
    assert (await client.get(URL, headers=auth_header(other))).json()["confirmation_link"][
        "enabled"
    ] is False


async def test_only_settings_managers_may_see_or_change_it(
    client: AsyncClient, unique_phone: str
) -> None:
    from app.tenants.roles import TenantRole
    from tests.conftest_commerce import grant_plan
    from tests.test_team import _member_session

    shop = await signed_in_shop(client, unique_phone)
    await grant_plan(shop["tenant_id"], "pro")
    operator = await _member_session(
        client, shop, f"016{uuid.uuid4().int % 100_000_000:08d}", TenantRole.ORDER_OPERATOR
    )
    assert (await client.get(URL, headers=auth_header(operator))).status_code == 403
    denied = await client.patch(
        URL, json={"confirmation_link_enabled": False}, headers=auth_header(operator)
    )
    assert denied.status_code == 403


def test_unknown_fields_are_refused() -> None:
    from pydantic import ValidationError

    from app.api.v1.sms_settings import SmsSettingsPatch

    with pytest.raises(ValidationError):
        SmsSettingsPatch.model_validate({"confirmation_link_enabled": True, "x": 1})
