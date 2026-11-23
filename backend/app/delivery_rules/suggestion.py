"""Which courier the quick-order sheet suggests (V4 WP1.5).

Display only: saving an order never contacts a courier, and booking stays
with the booking service / automation ``BOOK_COURIER``.

1.  The shop's ``default_courier`` (delivery rules, with
    ``delivery_rules_enabled`` on).
2.  Else the courier with the best delivered rate in the order's zone, read
    from the shop's own finished parcels of the last :data:`HISTORY_WINDOW`.
    A courier with fewer than :data:`SAMPLE_FLOOR` parcels there is not
    judged (the courier-history floor from WP0.6, "kom data").
3.  Else nothing.

Delivered rate = ``DELIVERED`` ÷ finished (delivered, partly delivered,
returned, lost, damaged). Parcels that never left (cancelled, failed booking)
or are still on the way do not count.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.feature_flags import FeatureFlagService, FlagKey
from app.consignments.models import MANUAL_PROVIDER, Consignment, ConsignmentStatus
from app.core.clock import utc_now
from app.delivery_rules.service import DeliveryRulesService
from app.orders.models import Order
from app.orders.zones import DeliveryZone, resolve_zone
from app.risk_providers.contract import SAMPLE_FLOOR

__all__ = [
    "HISTORY_WINDOW",
    "MAX_PARCELS",
    "CourierSuggestion",
    "compare_couriers",
    "suggest_courier",
]

HISTORY_WINDOW = timedelta(days=180)
#: The newest parcels read; enough to judge, bounded for a request path.
MAX_PARCELS = 2000

_FINISHED = (
    ConsignmentStatus.DELIVERED,
    ConsignmentStatus.PARTIAL_DELIVERED,
    ConsignmentStatus.RETURNED,
    ConsignmentStatus.LOST,
    ConsignmentStatus.DAMAGED,
)

SHOP_DEFAULT = "SHOP_DEFAULT"
HISTORY = "HISTORY"


@dataclass(frozen=True, slots=True)
class CourierSuggestion:
    courier: str
    #: ``SHOP_DEFAULT`` or ``HISTORY``.
    source: str
    zone: DeliveryZone | None = None
    #: The shop's own parcels behind a ``HISTORY`` suggestion.
    delivered: int | None = None
    parcels: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "courier": self.courier,
            "source": self.source,
            "zone": str(self.zone) if self.zone is not None else None,
            "delivered": self.delivered,
            "parcels": self.parcels,
        }


async def suggest_courier(
    db: AsyncSession, tenant_id: uuid.UUID, *, address: str | None
) -> CourierSuggestion | None:
    """The courier to suggest for an order to ``address``, or ``None``."""
    if await FeatureFlagService(db).is_enabled(FlagKey.DELIVERY_RULES_ENABLED, tenant_id=tenant_id):
        rules = await DeliveryRulesService(db).get(tenant_id)
        default = rules["default_courier"]
        if isinstance(default, str) and default:
            return CourierSuggestion(courier=default, source=SHOP_DEFAULT)
    zone = resolve_zone(address)
    if zone is None:
        return None
    return await _best_in_zone(db, tenant_id, zone)


async def _tally(
    db: AsyncSession, tenant_id: uuid.UUID, zone: DeliveryZone | None
) -> dict[str, list[int]]:
    """``{courier: [delivered, finished]}`` from the shop's own recent parcels,
    in ``zone`` (or every parcel when ``zone`` is ``None``)."""
    rows = (
        await db.execute(
            sa.select(
                Consignment.provider,
                Consignment.status,
                Order.delivery_area,
                Order.delivery_district,
            )
            .join(Order, Order.id == Consignment.order_id)
            .where(
                Consignment.tenant_id == tenant_id,
                Consignment.provider != MANUAL_PROVIDER,
                Consignment.status.in_([str(status) for status in _FINISHED]),
                Consignment.created_at >= utc_now() - HISTORY_WINDOW,
                Order.deleted_at.is_(None),
            )
            .order_by(Consignment.created_at.desc())
            .limit(MAX_PARCELS)
        )
    ).all()
    counts: dict[str, list[int]] = {}
    for provider, status, area, district in rows:
        if zone is not None and resolve_zone(area, district) != zone:
            continue
        tally = counts.setdefault(str(provider), [0, 0])
        tally[1] += 1
        if status == ConsignmentStatus.DELIVERED:
            tally[0] += 1
    return counts


def _ranked(counts: dict[str, list[int]]) -> list[tuple[str, int, int]]:
    """Couriers with enough parcels, best delivered rate first (then more parcels)."""
    judged = [(c, d, p) for c, (d, p) in sorted(counts.items()) if p >= SAMPLE_FLOOR]
    ranked: list[tuple[str, int, int]] = []
    for row in judged:
        at = next(
            (i for i, other in enumerate(ranked) if _better(row[1], row[2], other[1], other[2])),
            len(ranked),
        )
        ranked.insert(at, row)
    return ranked


async def _best_in_zone(
    db: AsyncSession, tenant_id: uuid.UUID, zone: DeliveryZone
) -> CourierSuggestion | None:
    ranked = _ranked(await _tally(db, tenant_id, zone))
    if not ranked:
        return None
    courier, delivered, parcels = ranked[0]
    return CourierSuggestion(
        courier=courier, source=HISTORY, zone=zone, delivered=delivered, parcels=parcels
    )


async def compare_couriers(
    db: AsyncSession, tenant_id: uuid.UUID, *, zone: DeliveryZone | None = None, limit: int = 3
) -> list[CourierSuggestion]:
    """Up to ``limit`` couriers ranked by the shop's own delivered rate.

    Display only, beside the shop's default courier (Autopilot books with the
    default; this is the comparison the seller sees). A courier under the
    sample floor is not ranked.
    """
    return [
        CourierSuggestion(
            courier=courier, source=HISTORY, zone=zone, delivered=delivered, parcels=parcels
        )
        for courier, delivered, parcels in _ranked(await _tally(db, tenant_id, zone))[:limit]
    ]


def _better(delivered: int, parcels: int, best_delivered: int, best_parcels: int) -> bool:
    """A higher delivered rate (integer cross-multiplication), then more parcels."""
    left, right = delivered * best_parcels, best_delivered * parcels
    if left != right:
        return left > right
    return parcels > best_parcels
