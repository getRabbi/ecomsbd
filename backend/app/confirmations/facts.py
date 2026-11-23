"""Gather what the confirmation policy reads about an order (V4 WP2.2).

Only stored data: the customer's seller-private flag, this shop's own parcel
history (the same band the customer screen shows) and the newest answer an
enabled external provider already gave. Nothing here calls a provider or a
courier: the policy runs inside the order's own transaction.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.rto import RtoService
from app.confirmations.policy import ExternalHistory, PolicyFacts
from app.core.clock import utc_now
from app.customers.models import Customer, CustomerFlag
from app.customers.risk import UNKNOWN, RiskAssessment, assess
from app.orders.models import Order
from app.risk_providers import registry
from app.risk_providers.contract import history_state
from app.risk_providers.models import ExternalRiskLookup, RiskProviderConnection

__all__ = ["collect", "collect_for", "external_history"]


async def external_history(db: AsyncSession, customer_id: uuid.UUID) -> ExternalHistory | None:
    """The newest answered lookup from a provider that is on, or ``None``.

    ``None`` means "unavailable": no provider is connected and enabled (or its
    adapter is not shipped), or none has answered for this customer yet. A
    provider that answered "not found" is available with 0 parcels.
    """
    connections = (
        await db.scalars(
            sa.select(RiskProviderConnection).where(RiskProviderConnection.enabled.is_(True))
        )
    ).all()
    providers = [
        row.provider_id
        for row in connections
        if row.credentials_enc
        and await registry.get_for_tenant(db, row.tenant_id, row.provider_id) is not None
    ]
    if not providers:
        return None
    lookup = await db.scalar(
        sa.select(ExternalRiskLookup)
        .where(
            ExternalRiskLookup.customer_id == customer_id,
            ExternalRiskLookup.provider_id.in_(providers),
            ExternalRiskLookup.status.in_(("FOUND", "NOT_FOUND")),
        )
        .order_by(ExternalRiskLookup.checked_at.desc(), ExternalRiskLookup.id.desc())
        .limit(1)
    )
    if lookup is None:
        return None
    fresh = lookup.expires_at is not None and lookup.expires_at > utc_now()
    aggregate = (
        (lookup.courier_history or {}).get("aggregate") if lookup.status == "FOUND" else None
    )
    parcels = int((aggregate or {}).get("total") or 0)
    _, success_bps = history_state(aggregate)
    return ExternalHistory(parcels=parcels, success_bps=success_bps, fresh=fresh)


async def collect(db: AsyncSession, order: Order) -> PolicyFacts:
    """The facts for ``order``. A customer-less order is an unknown buyer."""
    return await collect_for(db, order.customer_id, order.cod_amount_paisa)


async def collect_for(
    db: AsyncSession, customer_id: uuid.UUID | None, cod_paisa: int
) -> PolicyFacts:
    """The facts for an order of ``cod_paisa`` by this customer (or a new buyer).

    Autopilot (Phase 4) asks before the order exists, with the same inputs the
    order will have, so its gate sees the decision the confirmation will make.
    """
    customer = await db.get(Customer, customer_id) if customer_id is not None else None
    own: RiskAssessment = UNKNOWN
    external: ExternalHistory | None = None
    if customer is not None:
        own = assess(await RtoService(db).customer_history(customer.id))
        external = await external_history(db, customer.id)
    return PolicyFacts(
        customer_blocked=customer is not None and customer.flag == CustomerFlag.BLOCKED,
        cod_paisa=cod_paisa,
        own_band=own.state,
        own_delivered=own.delivered_count,
        own_returned=own.returned_count,
        external=external,
    )
