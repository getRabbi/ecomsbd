"""Which status table a provider's status strings are read against.

Status sync and the courier event log both turn a provider's raw status into an
ecomsbd consignment state, and both used to read every string against
Steadfast's table. That is only right for Steadfast: RedX's ``delivered`` is not
Steadfast's ``delivered`` (RedX qualifies it with a delivery type), and RedX's
``returned`` is not in Steadfast's vocabulary at all.

So the table is chosen per provider, here, once. A provider registers its own
mapper; every provider without one keeps exactly the table it was read against
before this module existed, so nothing already in production changes meaning.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from app.consignments.models import ConsignmentStatus
from app.couriers.redx.contract import PROVIDER as REDX
from app.couriers.redx.mapping import map_parcel_status
from app.couriers.steadfast.mapping import map_delivery_status

__all__ = ["CourierStatusMapping", "map_courier_status"]


class CourierStatusMapping(Protocol):
    """What status sync and the event log read from any provider's mapping."""

    @property
    def provider_status(self) -> str: ...

    @property
    def canonical(self) -> ConsignmentStatus | None: ...

    @property
    def is_final(self) -> bool: ...

    @property
    def needs_quantity_resolution(self) -> bool: ...

    @property
    def is_documented(self) -> bool: ...


def _redx(raw_status: str | None, detail: str | None) -> CourierStatusMapping:
    return map_parcel_status(raw_status, detail)


#: Providers with a status table of their own. ``detail`` is the provider's
#: qualifier for a status, when it has one — RedX's delivery type.
_MAPPERS: dict[str, Callable[[str | None, str | None], CourierStatusMapping]] = {
    REDX: _redx,
}


def map_courier_status(
    provider: str, raw_status: str | None, *, detail: str | None = None
) -> CourierStatusMapping:
    """Map a raw status against the provider's own table. Never raises."""
    mapper = _MAPPERS.get(provider)
    if mapper is not None:
        return mapper(raw_status, detail)
    return map_delivery_status(raw_status)
