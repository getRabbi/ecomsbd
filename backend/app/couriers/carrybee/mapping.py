"""Carrybee event names → ecomsbd consignment states (V4 WP7.2).

The table covers exactly the 25 webhook events Carrybee documents
(``docs/providers/carrybee/CONTRACT.md`` §8). An event whose meaning for the
parcel is not plain from the page (a failed attempt, a hold, a paid return, an
exchange, a payment) makes **no state claim**: it is documented, stored, and
changes nothing. ``transfer_status`` from the order-details lookup has no
published value list, so it is read against the same table and anything that
is not an event name is undocumented.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from app.consignments.models import ConsignmentStatus
from app.couriers.carrybee.contract import WEBHOOK_EVENTS

__all__ = ["STATUS_MAP", "CarrybeeStatusMapping", "assert_map_is_complete", "map_carrybee_status"]

S = ConsignmentStatus


@dataclass(frozen=True, slots=True)
class CarrybeeStatusMapping:
    """Shaped like the Steadfast, Pathao and RedX mappings."""

    provider_status: str
    #: ``None``: no parcel-state claim.
    canonical: ConsignmentStatus | None
    #: Only a settled outcome may drive money.
    is_final: bool
    documented: bool = True
    needs_quantity_resolution: bool = False
    keep_polling: bool = True
    description: str = ""
    is_approval_pending: bool = False

    @property
    def is_documented(self) -> bool:
        return self.documented

    @property
    def moves_money(self) -> bool:
        return self.is_final and self.canonical is not None


def _m(
    status: str,
    canonical: ConsignmentStatus | None,
    description: str,
    *,
    is_final: bool = False,
    needs_quantity_resolution: bool = False,
) -> CarrybeeStatusMapping:
    return CarrybeeStatusMapping(
        provider_status=status,
        canonical=canonical,
        is_final=is_final,
        needs_quantity_resolution=needs_quantity_resolution,
        keep_polling=not is_final,
        description=description,
    )


_NO_CLAIM = "Recorded as Carrybee sent it; the parcel's state is not changed by it."

STATUS_MAP: Final[MappingProxyType[str, CarrybeeStatusMapping]] = MappingProxyType(
    {
        mapping.provider_status: mapping
        for mapping in (
            _m("order.created", S.BOOKED, "Carrybee created the order."),
            _m("order.create-failed", None, "A bulk create failed. " + _NO_CLAIM),
            _m("order.updated", None, "The order's details changed. " + _NO_CLAIM),
            _m("order.pickup-requested", None, "Pickup requested. " + _NO_CLAIM),
            _m("order.assigned-for-pickup", None, "A rider is assigned to pick up. " + _NO_CLAIM),
            _m("order.picked", S.PICKED_UP, "Carrybee picked the parcel up."),
            _m("order.pickup-failed", None, "A pickup attempt failed. " + _NO_CLAIM),
            _m("order.pickup-cancelled", None, "The pickup was cancelled. " + _NO_CLAIM),
            _m("order.at-the-sorting-hub", S.IN_TRANSIT, "At the sorting hub."),
            _m(
                "order.on-the-way-to-central-warehouse",
                S.IN_TRANSIT,
                "On the way to the central warehouse.",
            ),
            _m("order.at-central-warehouse", S.IN_TRANSIT, "At the central warehouse."),
            _m("order.in-transit", S.IN_TRANSIT, "In transit."),
            _m("order.received-at-last-mile-hub", S.IN_TRANSIT, "At the last-mile hub."),
            _m("order.assigned-for-delivery", S.OUT_FOR_DELIVERY, "Out with a delivery rider."),
            _m("order.delivery-on-hold", None, "Delivery is on hold. " + _NO_CLAIM),
            _m("order.delivered", S.DELIVERED, "Delivered.", is_final=True),
            _m(
                "order.partial-delivery",
                S.PARTIAL_DELIVERED,
                "Partly delivered; which items came back needs a person.",
                is_final=True,
                needs_quantity_resolution=True,
            ),
            _m("order.delivery-failed", None, "A delivery attempt failed. " + _NO_CLAIM),
            _m("order.returned", S.RETURNING, "Marked for return."),
            _m(
                "order.paid-return",
                None,
                "Returned with a collected amount. Check it in Carrybee. " + _NO_CLAIM,
            ),
            _m("order.exchange", None, "An exchange. " + _NO_CLAIM),
            _m("order.paid", None, "Carrybee paid this order (invoice). " + _NO_CLAIM),
            _m("order.returned-at-sorting", S.RETURNING, "Return at the sorting hub."),
            _m("order.returned-in-transit", S.RETURNING, "Return in transit."),
            _m(
                "order.returned-to-merchant",
                S.RETURNED,
                "Returned to the merchant.",
                is_final=True,
            ),
        )
    }
)


def map_carrybee_status(raw_status: str | None, detail: str | None = None) -> CarrybeeStatusMapping:
    """Map a Carrybee event name (or lookup status). Never raises; never guesses."""
    if raw_status is None or not raw_status.strip():
        return CarrybeeStatusMapping(
            provider_status="",
            canonical=None,
            is_final=False,
            documented=False,
            description="The courier sent no status.",
        )
    mapping = STATUS_MAP.get(raw_status.strip())
    if mapping is not None:
        return mapping
    return CarrybeeStatusMapping(
        provider_status=raw_status,
        canonical=None,
        is_final=False,
        documented=False,
        keep_polling=True,
        description=(
            "A status Carrybee has not published. It is stored exactly as sent and "
            "changes nothing until it is understood."
        ),
    )


def assert_map_is_complete() -> None:
    """Every documented event has a row, and nothing else does."""
    missing = set(WEBHOOK_EVENTS) - set(STATUS_MAP)
    extra = set(STATUS_MAP) - set(WEBHOOK_EVENTS)
    if missing or extra:
        raise AssertionError(f"carrybee map: missing={sorted(missing)} extra={sorted(extra)}")
