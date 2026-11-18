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

__all__ = ["HISTORY_WINDOW", "MAX_PARCELS", "CourierSuggestion", "suggest_courier"]

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


async def _best_in_zone(
    db: AsyncSession, tenant_id: uuid.UUID, zone: DeliveryZone
) -> CourierSuggestion | None:
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
        if resolve_zone(area, district) != zone:
            continue
        tally = counts.setdefault(str(provider), [0, 0])
        tally[1] += 1
        if status == ConsignmentStatus.DELIVERED:
            tally[0] += 1
    best: tuple[str, int, int] | None = None
    for courier in sorted(counts):
        delivered, parcels = counts[courier]
        if parcels < SAMPLE_FLOOR:
            continue
        if best is None or _better(delivered, parcels, best[1], best[2]):
            best = (courier, delivered, parcels)
    if best is None:
        return None
    return CourierSuggestion(
        courier=best[0], source=HISTORY, zone=zone, delivered=best[1], parcels=best[2]
    )


def _better(delivered: int, parcels: int, best_delivered: int, best_parcels: int) -> bool:
    """A higher delivered rate (integer cross-multiplication), then more parcels."""
    left, right = delivered * best_parcels, best_delivered * parcels
    if left != right:
        return left > right
    return parcels > best_parcels
