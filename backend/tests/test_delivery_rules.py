"""Shop delivery rules, zone resolver and COD calculator (V4 WP0.4)."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.db.session import system_session
from app.delivery_rules.models import DEFAULTS, ShopDeliveryRules
from app.orders.cod import CodLine, calculate_cod, is_chat_mismatch
from app.orders.zones import DeliveryZone, resolve_zone
from tests.conftest_commerce import signed_in_shop
from tests.test_auth_flow import auth_header

URL = "/v1/shop/delivery-rules"


# --------------------------------------------------------------------------- #
# Zone resolver
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "zone"),
    [
        ("House 4, Road 2, Mirpur 10, Dhaka", DeliveryZone.INSIDE_DHAKA),
        ("বাড়ি ১২, রোড ৫, মিরপুর ১০, ঢাকা", DeliveryZone.INSIDE_DHAKA),
        ("উত্তরা সেক্টর ৭, রোড ১৫", DeliveryZone.INSIDE_DHAKA),
        ("Flat 3B, House 22, Road 11, Banani, Dhaka", DeliveryZone.INSIDE_DHAKA),
        ("new  market, dhaka", DeliveryZone.INSIDE_DHAKA),
        ("Savar, Dhaka", DeliveryZone.DHAKA_SUBURB),
        ("কেরানীগঞ্জ", DeliveryZone.DHAKA_SUBURB),
        ("tongi, gazipur", DeliveryZone.DHAKA_SUBURB),
        ("Board Bazar, Gazipur", DeliveryZone.DHAKA_SUBURB),
        ("khulna sonadanga", DeliveryZone.OUTSIDE_DHAKA),
        ("chandpur sadar", DeliveryZone.OUTSIDE_DHAKA),
        ("গ্রাম: চরপাড়া, ডাকঘর: বেলকুচি, জেলা: সিরাজগঞ্জ", DeliveryZone.OUTSIDE_DHAKA),
        ("আগ্রাবাদ, চট্টগ্রাম", DeliveryZone.OUTSIDE_DHAKA),
        # "Mirpur" is also an upazila of Kushtia: the district decides.
        ("Mirpur, Kushtia", DeliveryZone.OUTSIDE_DHAKA),
    ],
)
def test_known_places_resolve(text: str, zone: DeliveryZone) -> None:
    assert resolve_zone(text) is zone


@pytest.mark.parametrize(
    "text",
    [None, "", "Dhaka", "ঢাকায়", "Gazipur", "Narayanganj", "Agrabad", "Banani, Gazipur", "abc"],
)
def test_anything_unsettled_is_unknown_never_guessed(text: str | None) -> None:
    assert resolve_zone(text) is None


def test_every_district_outside_the_split_three_is_outside_dhaka() -> None:
    from app.orders.parser.districts import DISTRICTS

    assert len(DISTRICTS) == 64
    for name in DISTRICTS:
        expected = (
            None if name in {"Dhaka", "Gazipur", "Narayanganj"} else DeliveryZone.OUTSIDE_DHAKA
        )
        assert resolve_zone(name) is expected, name


# --------------------------------------------------------------------------- #
# COD calculator
# --------------------------------------------------------------------------- #


def test_cod_is_catalog_plus_zone_minus_discount() -> None:
    lines = [CodLine(92_500, 2), CodLine(30_000, 1)]
    assert calculate_cod(lines) == 215_000
    assert calculate_cod(lines, zone_charge_paisa=6_000, discount_paisa=1_000) == 220_000
    assert calculate_cod([CodLine(10_000, 1)], discount_paisa=50_000) == 0, "never negative"


def test_cod_needs_a_price_for_every_line() -> None:
    assert calculate_cod([]) is None
    assert calculate_cod([CodLine(92_500, 1), CodLine(0, 1)]) is None
    assert calculate_cod([CodLine(92_500, 0)]) is None
    with pytest.raises(ValueError):
        calculate_cod([CodLine(1, 1)], zone_charge_paisa=-1)


def test_a_stated_cod_mismatches_only_beyond_the_zone_charge() -> None:
    # 1,910 calculated with a 60 taka charge: 1,850 (charge left out) is fine.
    assert is_chat_mismatch(185_000, 191_000, zone_charge_paisa=6_000) is False
    assert is_chat_mismatch(191_000, 191_000, zone_charge_paisa=6_000) is False
    assert is_chat_mismatch(250_000, 191_000, zone_charge_paisa=6_000) is True
    assert is_chat_mismatch(184_999, 191_000, zone_charge_paisa=6_000) is True


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


async def _rows(tenant_id: str) -> list[ShopDeliveryRules]:
    async with system_session("test: delivery rules") as db:
        stmt = sa.select(ShopDeliveryRules).where(
            ShopDeliveryRules.tenant_id == uuid.UUID(tenant_id)
        )
        return list((await db.scalars(stmt)).all())


async def test_flag_off_answers_feature_disabled(client: AsyncClient, unique_phone: str) -> None:
    shop = await signed_in_shop(client, unique_phone)
    for method in ("get", "patch"):
        kwargs = {"json": {"call_everyone": True}} if method == "patch" else {}
        response = await getattr(client, method)(URL, headers=auth_header(shop), **kwargs)
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "FEATURE_DISABLED"
    assert await _rows(shop["tenant_id"]) == []


async def test_get_returns_defaults_without_writing(
    client: AsyncClient, unique_phone: str, delivery_rules_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    response = await client.get(URL, headers=auth_header(shop))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.pop("configured") is False
    assert body == DEFAULTS
    assert body["max_auto_cod_paisa"] == 500_000 and body["quiet_start"] == "21:00"
    assert await _rows(shop["tenant_id"]) == []


async def test_patch_upserts_only_the_fields_sent(
    client: AsyncClient, unique_phone: str, delivery_rules_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    first = await client.patch(
        URL,
        json={"inside_dhaka_charge_paisa": 6_000, "outside_dhaka_charge_paisa": 12_000},
        headers=auth_header(shop),
    )
    assert first.status_code == 200, first.text
    second = await client.patch(
        URL,
        json={"default_courier": " Steadfast ", "quiet_end": "08:30"},
        headers=auth_header(shop),
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["configured"] is True
    assert body["inside_dhaka_charge_paisa"] == 6_000
    assert body["outside_dhaka_charge_paisa"] == 12_000
    assert body["dhaka_suburb_charge_paisa"] == 0
    assert body["default_courier"] == "steadfast"
    assert body["quiet_end"] == "08:30"
    assert len(await _rows(shop["tenant_id"])) == 1, "one row per shop"

    cleared = await client.patch(URL, json={"default_courier": None}, headers=auth_header(shop))
    assert cleared.json()["default_courier"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"default_courier": "dhl"},
        {"quiet_start": "25:00"},
        {"quiet_start": "9:00"},
        {"inside_dhaka_charge_paisa": -1},
        {"risk_low_bps": 10_001},
        {"max_auto_cod_paisa": None},
        {"unknown_rule": 1},
        {"call_everyone": True, "call_nobody": True},
        {},
    ],
)
async def test_invalid_changes_are_refused(
    client: AsyncClient, unique_phone: str, delivery_rules_on: None, payload: dict
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    response = await client.patch(URL, json=payload, headers=auth_header(shop))
    assert response.status_code in (409, 422), response.text
    assert await _rows(shop["tenant_id"]) == []


async def test_switching_one_call_switch_on_while_the_other_is_on_is_refused(
    client: AsyncClient, unique_phone: str, delivery_rules_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    assert (
        await client.patch(URL, json={"call_everyone": True}, headers=auth_header(shop))
    ).status_code == 200
    response = await client.patch(URL, json={"call_nobody": True}, headers=auth_header(shop))
    assert response.status_code == 422, response.text
    assert response.json()["details"]["field"] == "call_nobody"


async def test_each_shop_has_its_own_rules(
    client: AsyncClient, unique_phone: str, delivery_rules_on: None
) -> None:
    first = await signed_in_shop(client, unique_phone, shop_name="First")
    await client.patch(URL, json={"inside_dhaka_charge_paisa": 7_000}, headers=auth_header(first))
    second_phone = f"017{uuid.uuid4().int % 100_000_000:08d}"
    second = await signed_in_shop(client, second_phone, shop_name="Second")
    body = (await client.get(URL, headers=auth_header(second))).json()
    assert body["configured"] is False and body["inside_dhaka_charge_paisa"] == 0
