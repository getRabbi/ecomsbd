"""Invoice and shipping-label HTML, and the Code 128-B encoder (V4 WP0.5)."""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Any

import pytest
from httpx import AsyncClient

from app.documents import code128
from app.documents.render import (
    DocumentItem,
    DocumentOrder,
    LabelSize,
    render_invoices,
    render_labels,
)
from tests.conftest_commerce import create_order, signed_in_shop
from tests.test_auth_flow import auth_header

# --------------------------------------------------------------------------- #
# Code 128-B
# --------------------------------------------------------------------------- #


def test_the_symbol_table_is_well_formed() -> None:
    assert len(code128.PATTERNS) == 107
    assert len(set(code128.PATTERNS)) == 107
    for value, pattern in enumerate(code128.PATTERNS[:106]):
        widths = [int(w) for w in pattern]
        assert len(widths) == 6 and sum(widths) == 11, value
        assert sum(widths[0::2]) % 2 == 0, f"bars of {value} must be an even module count"
        assert all(1 <= w <= 4 for w in widths), value
    assert sum(int(w) for w in code128.PATTERNS[code128.STOP]) == 13


# Hand-derived from the ISO/IEC 15417 value table: start B = 104 (211214),
# "A" = 33 (111323), "1" = 17 (123221), "b" = 66 (121421), space = 0 (212222),
# stop = 2331112.
# "A1": checksum (104 + 1*33 + 2*17) % 103 = 171 % 103 = 68 -> 141221.
# "Ab": checksum (104 + 33 + 2*66) % 103 = 269 % 103 = 63 -> 111224.
# " ":  checksum (104 + 0) % 103 = 1 -> 222122.
@pytest.mark.parametrize(
    ("text", "values", "widths"),
    [
        ("A1", [104, 33, 17, 68, 106], "211214111323123221141221" + "2331112"),
        ("Ab", [104, 33, 66, 63, 106], "211214111323121421111224" + "2331112"),
        (" ", [104, 0, 1, 106], "211214212222222122" + "2331112"),
    ],
)
def test_known_vectors(text: str, values: list[int], widths: str) -> None:
    assert code128.encode_values(text) == values
    assert code128.modules(text) == widths


def test_checksum_follows_the_weighted_sum() -> None:
    text = "ORD-0042"
    expected = (104 + sum(i * (ord(c) - 32) for i, c in enumerate(text, start=1))) % 103
    assert code128.checksum(text) == expected


@pytest.mark.parametrize("text", ["", "ঢাকা", "tab\there", "é"])
def test_only_printable_ascii_is_encoded(text: str) -> None:
    with pytest.raises(ValueError):
        code128.encode_values(text)


def test_svg_has_one_rect_per_bar_and_escapes_its_label() -> None:
    svg = code128.svg('A"1')
    bars = len(code128.modules('A"1')[0::2])
    assert svg.count('<rect x="') == bars
    assert 'aria-label="A&quot;1"' in svg


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _doc(number: str = "ORD-0001", **overrides: Any) -> DocumentOrder:
    values: dict[str, Any] = {
        "order_number": number,
        "order_date": date(2026, 11, 18),
        "shop_name": "রহিম ফ্যাশন",
        "shop_phone": "01700000041",
        "customer_name": "সুমি",
        "customer_phone": "01700000042",
        "address": "বাড়ি 12, রোড 5, মিরপুর 10",
        "area": "Mirpur",
        "district": "Dhaka",
        "courier": "Steadfast",
        "tracking": "SF123456",
        "subtotal_paisa": 185_000,
        "discount_paisa": 5_000,
        "delivery_fee_paisa": 6_000,
        "cod_amount_paisa": 186_000,
        "items": (DocumentItem("কালো পাঞ্জাবি", "XL", 2, 92_500, 185_000),),
    }
    values.update(overrides)
    return DocumentOrder(**values)


def test_every_field_is_escaped() -> None:
    hostile = '<script>alert(1)</script>"'
    for html in (
        render_invoices([_doc(customer_name=hostile, address=hostile, note=hostile)]),
        render_labels([_doc(customer_name=hostile, address=hostile)], LabelSize.FOUR_BY_SIX),
    ):
        assert "<script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;&quot;" in html


def test_invoice_has_lines_totals_and_a_cod_badge() -> None:
    html = render_invoices([_doc()])
    assert html.startswith("<!doctype html>") and '<meta charset="utf-8">' in html
    assert "@page{size:A4" in html
    assert "কালো পাঞ্জাবি" in html and "XL" in html
    assert "COD ৳1,860" in html
    # total = subtotal - discount + delivery = 1,850 - 50 + 60
    assert "-৳50" in html and "৳60" in html


def test_the_bangla_font_is_embedded_once_per_document() -> None:
    html = render_labels([_doc(f"ORD-{n}") for n in range(3)], LabelSize.FOUR_BY_SIX)
    assert html.count("@font-face") == 2  # 400 and 700, once each
    assert "data:font/ttf;base64," in html


def test_label_shows_what_the_courier_needs() -> None:
    html = render_labels([_doc()], LabelSize.FOUR_BY_SIX)
    for text in (
        "রহিম ফ্যাশন",
        "01700000041",
        "সুমি",
        "01700000042",
        "মিরপুর 10",
        "Steadfast",
        "#ORD-0001",
        "2026-11-18",
    ):
        assert text in html, text
    assert "COD ৳1,860" in html
    assert 'aria-label="SF123456"' in html, "the tracking id is the barcode"
    assert "কালো পাঞ্জাবি (XL) &times; 2" in html


def test_an_unbooked_label_barcodes_the_order_number() -> None:
    html = render_labels([_doc(tracking=None, courier=None)], LabelSize.THREE_BY_FOUR)
    assert 'aria-label="ORD-0001"' in html
    assert "@page{size:3in 4in" in html


def test_one_page_per_document_and_eight_labels_per_a4_sheet() -> None:
    four_by_six = render_labels([_doc(f"ORD-{n}") for n in range(3)], LabelSize.FOUR_BY_SIX)
    assert four_by_six.count('class="doc label"') == 3
    sheets = render_labels([_doc(f"ORD-{n}") for n in range(9)], LabelSize.A4_EIGHT_UP)
    assert sheets.count('class="doc sheet"') == 2
    assert sheets.count('class="label"') == 9
    invoices = render_invoices([_doc(f"ORD-{n}") for n in range(2)])
    assert invoices.count('class="doc invoice"') == 2


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


async def _order(client: AsyncClient, shop: dict[str, Any], phone: str = "01700000043") -> Any:
    created = await create_order(
        client,
        shop,
        phone=phone,
        customer_name="Sumi <b>",
        address="House 4, Road 2, Mirpur 10, Dhaka",
        items=[{"name": "Black Panjabi", "quantity": 2, "unit_price_paisa": 92_500}],
    )
    return created["order"]


async def test_flag_off_answers_feature_disabled(client: AsyncClient, unique_phone: str) -> None:
    shop = await signed_in_shop(client, unique_phone)
    order = await _order(client, shop)
    response = await client.get(
        f"/v1/orders/{order['id']}/documents/invoice", headers=auth_header(shop)
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FEATURE_DISABLED"


async def test_invoice_and_label_are_uncached_html(
    client: AsyncClient, unique_phone: str, order_documents_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    order = await _order(client, shop)
    body = ""
    for path in ("invoice", "label"):
        response = await client.get(
            f"/v1/orders/{order['id']}/documents/{path}", headers=auth_header(shop)
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert response.headers["cache-control"] == "no-store"
        body = response.text
        assert order["order_number"] in body
        assert "Sumi &lt;b&gt;" in body and "Sumi <b>" not in body
    assert "01700000043" in body, "the full phone by default: the courier needs it"


async def test_a_masked_label_hides_the_phone(
    client: AsyncClient, unique_phone: str, order_documents_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    order = await _order(client, shop)
    response = await client.get(
        f"/v1/orders/{order['id']}/documents/label",
        params={"mask_phone": "true", "size": "3x4"},
        headers=auth_header(shop),
    )
    assert response.status_code == 200, response.text
    assert "01700000043" not in response.text
    assert "01700****43" in response.text


async def test_bulk_is_one_document_in_the_order_asked(
    client: AsyncClient, unique_phone: str, order_documents_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    first = await _order(client, shop, "01700000044")
    second = await _order(client, shop, "01700000045")
    response = await client.post(
        "/v1/orders/documents/bulk",
        json={"order_ids": [second["id"], first["id"]], "kind": "label", "size": "a4-8up"},
        headers=auth_header(shop),
    )
    assert response.status_code == 200, response.text
    body = response.text
    assert body.count('class="doc sheet"') == 1 and body.count('class="label"') == 2
    assert body.index(second["order_number"]) < body.index(first["order_number"])

    invoices = await client.post(
        "/v1/orders/documents/bulk",
        json={"order_ids": [first["id"], second["id"]], "kind": "invoice"},
        headers=auth_header(shop),
    )
    assert invoices.status_code == 200
    assert invoices.text.count('class="doc invoice"') == 2


async def test_bulk_limits_and_unknown_orders(
    client: AsyncClient, unique_phone: str, order_documents_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    order = await _order(client, shop)
    too_many = [str(uuid.uuid4()) for _ in range(101)]
    response = await client.post(
        "/v1/orders/documents/bulk",
        json={"order_ids": too_many, "kind": "invoice"},
        headers=auth_header(shop),
    )
    assert response.status_code == 422
    missing = await client.post(
        "/v1/orders/documents/bulk",
        json={"order_ids": [order["id"], str(uuid.uuid4())], "kind": "invoice"},
        headers=auth_header(shop),
    )
    assert missing.status_code == 404
    bad_size = await client.get(
        f"/v1/orders/{order['id']}/documents/label",
        params={"size": "5x7"},
        headers=auth_header(shop),
    )
    assert bad_size.status_code == 422


async def test_another_shops_order_is_not_found(
    client: AsyncClient, unique_phone: str, order_documents_on: None
) -> None:
    owner = await signed_in_shop(client, unique_phone, shop_name="Owner")
    order = await _order(client, owner)
    other_phone = f"017{uuid.uuid4().int % 100_000_000:08d}"
    other = await signed_in_shop(client, other_phone, shop_name="Other")
    response = await client.get(
        f"/v1/orders/{order['id']}/documents/label", headers=auth_header(other)
    )
    assert response.status_code == 404
    assert order["order_number"] not in response.text
    assert re.search(r"0170000004\d", response.text) is None


def test_every_courier_with_a_manifest_prints_by_name() -> None:
    """A label or invoice never shows a raw provider key (V4 WP7.2: Carrybee)."""
    from app.couriers.capabilities import load_all_manifests
    from app.documents.service import COURIER_NAMES

    for provider, manifest in load_all_manifests().items():
        assert provider in COURIER_NAMES, provider
        if provider != "manual":
            assert COURIER_NAMES[provider] == manifest.display_name, provider
