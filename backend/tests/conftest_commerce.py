"""Shared helpers for the commerce-core tests.

Kept out of ``conftest.py`` so the Phase A fixtures stay readable, and imported
explicitly by the suites that need a signed-in shop.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from tests.test_auth_flow import auth_header, sign_in


async def signed_in_shop(
    client: AsyncClient, phone: str, *, shop_name: str = "Test Shop"
) -> dict[str, Any]:
    """Sign in and create a shop, returning the tenant-bound session."""
    session = await sign_in(client, phone)
    response = await client.post(
        "/v1/tenants",
        json={"name": shop_name, "business_category": "CLOTHING"},
        headers=auth_header(session),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_product(
    client: AsyncClient,
    session: dict[str, Any],
    *,
    name: str = "Black Abaya",
    sku: str | None = None,
    cost_paisa: int = 65_000,
    price_paisa: int = 125_000,
    opening_stock: int = 10,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "cost_paisa": cost_paisa,
        "default_selling_price_paisa": price_paisa,
        "opening_stock": opening_stock,
    }
    if sku is not None:
        payload["sku"] = sku
    payload.update(extra)

    response = await client.post("/v1/products", json=payload, headers=auth_header(session))
    assert response.status_code == 201, response.text
    return response.json()


async def create_order(
    client: AsyncClient,
    session: dict[str, Any],
    *,
    phone: str = "01712345678",
    items: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "phone": phone,
        "items": items or [{"name": "Black Abaya XL", "quantity": 1, "unit_price_paisa": 125_000}],
    }
    payload.update(extra)

    response = await client.post("/v1/orders", json=payload, headers=auth_header(session))
    assert response.status_code == 201, response.text
    return response.json()
