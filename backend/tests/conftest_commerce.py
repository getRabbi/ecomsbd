"""Shared helpers for the commerce-core tests.

Kept out of ``conftest.py`` so the Phase A fixtures stay readable, and imported
explicitly by the suites that need a signed-in shop.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from tests.test_auth_flow import auth_header, sign_in


async def grant_plan(tenant_id: str, plan: str, *, days: int = 365) -> None:
    """Put a shop on a paid plan, the way support would.

    Written through a system session so it is committed and visible to the
    application's own request sessions. Uses the manual-admin provider, which
    is the only source that can grant without provider evidence — the same path
    the admin console uses (master spec section 103).
    """
    import uuid as _uuid
    from datetime import timedelta

    from app.billing.models import BillingProviderKind
    from app.core.clock import utc_now
    from app.db.session import system_session
    from app.entitlements.models import Subscription, SubscriptionStatus

    async with system_session("test fixture: plan grant") as session:
        session.add(
            Subscription(
                tenant_id=_uuid.UUID(tenant_id),
                plan_code=plan,
                status=str(SubscriptionStatus.ACTIVE),
                source=str(BillingProviderKind.MANUAL_ADMIN),
                current_period_start=utc_now(),
                current_period_end=utc_now() + timedelta(days=days),
                verified_at=utc_now(),
                grant_reason="test fixture",
            )
        )


async def signed_in_shop(
    client: AsyncClient, phone: str, *, shop_name: str = "Test Shop", plan: str | None = None
) -> dict[str, Any]:
    """Sign in and create a shop, returning the tenant-bound session.

    ``plan`` is explicit rather than defaulted to a paid tier: a suite that
    exercises a gated feature has to say so, and a suite that says nothing gets
    the Free plan a real new shop gets.
    """
    session = await sign_in(client, phone)
    response = await client.post(
        "/v1/tenants",
        json={"name": shop_name, "business_category": "CLOTHING"},
        headers=auth_header(session),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    if plan is not None:
        await grant_plan(str(body["tenant_id"]), plan)
    # Merged, not replaced. `POST /tenants` answers with ShopSessionResponse,
    # which carries no tokens on purpose: creating a shop binds the *session*
    # to it server-side, so the token the caller already holds becomes
    # tenant-scoped without being reissued. Returning the shop body alone loses
    # that token, and every caller that then builds an auth header from it gets
    # a KeyError rather than a request.
    return {**session, **body}


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


async def dispatched_parcel(
    client: AsyncClient,
    session: dict[str, Any],
    db: Any,
    *,
    cod_paisa: int = 125_000,
    quantity: int = 1,
    opening_stock: int = 10,
) -> dict[str, Any]:
    """An order with a product, dispatched with a courier.

    Built over HTTP so the order goes through the real create path, then handed
    to the domain services for the money half. Returns the ids the money tests
    need plus the live service objects.
    """
    from app.consignments.service import ConsignmentService
    from app.ledger.service import LedgerService
    from app.money.service import ReceivableService

    product = await create_product(
        client, session, name="Cotton Abaya", opening_stock=opening_stock
    )
    order = await create_order(
        client,
        session,
        items=[
            {
                "product_id": product["id"],
                "quantity": quantity,
                "unit_price_paisa": cod_paisa // quantity,
            }
        ],
        cod_amount_paisa=cod_paisa,
    )

    ledger = LedgerService(db)
    receivables = ReceivableService(db, ledger=ledger)
    consignments = ConsignmentService(db, receivables=receivables)

    import uuid as _uuid

    consignment = await consignments.dispatch_manual(_uuid.UUID(order["order"]["id"]))
    await db.commit()

    return {
        "product": product,
        "order": order["order"],
        "consignment": consignment,
        "consignments": consignments,
        "receivables": receivables,
        "ledger": ledger,
    }
