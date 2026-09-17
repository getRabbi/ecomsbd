"""Pathao status -> ecomsbd consignment state.

One table, exact keys, no fallthrough. The same three rules that govern the
Steadfast map govern this one:

*   **Exact match only.** No ``startswith``, no substring bucketing, no
    lowercasing into a guess. ``paid_return`` contains ``return``; a substring
    match would settle a parcel on a status whose meaning Pathao never states.

*   **Only a settled, documented outcome may move money.** Pathao's vocabulary
    is much richer than Steadfast's — it has real pickup, hub and transit
    states — so most entries here are genuinely in-flight and say so.

*   **An unknown status is data, not an error.** A value Pathao adds next year
    maps to ``None``, is stored raw, emits an observability signal, and leaves
    money untouched.

Three entries deserve their reasoning stated, because each one is a place where
the convenient mapping is the wrong mapping:

*   ``Delivery_Failed`` is **not** terminal. A failed delivery attempt in
    Pathao's network is followed by another attempt or a return; treating it as
    ``FAILED`` would close a parcel that is still moving and strand its COD.

*   ``Pickup_Cancelled`` is **not** ``CANCELLED``. Pathao publishes no
    statement that a cancelled pickup cancels the order, and ``CANCELLED`` is
    terminal and touches stock. It stays in flight and keeps polling; a person
    can cancel deliberately.

*   ``paid_return`` is **not** ``RETURNED``. Pathao publishes the label and
    nothing about what it settles. ``Return`` is the documented return outcome
    and terminalises; ``paid_return`` stays in flight rather than closing a
    parcel on an inference about a fee.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from app.consignments.models import ConsignmentStatus
from app.couriers.pathao.contract import (
    PathaoOrderStatus,
    is_documented_status,
    status_for_event,
)

__all__ = [
    "ORDER_STATUS_MAP",
    "PathaoStatusMapping",
    "assert_map_is_complete",
    "map_event",
    "map_order_status",
]


@dataclass(frozen=True, slots=True)
class PathaoStatusMapping:
    """What one Pathao status means for a parcel."""

    provider_status: str
    #: ``None`` means "no parcel-state claim". That covers two different
    #: cases, told apart by :attr:`documented`: a status Pathao publishes that
    #: simply is not about delivery state (a payment invoice, a metadata
    #: update), and a status we have never seen at all.
    canonical: ConsignmentStatus | None
    #: Whether this outcome is settled at Pathao. Only a settled outcome may
    #: drive money.
    is_final: bool
    #: Whether Pathao publishes this status at all.
    documented: bool = True
    #: Whether recording this outcome needs a person to supply quantities.
    needs_quantity_resolution: bool = False
    #: Whether polling or further events should still be expected.
    keep_polling: bool = True
    description: str = ""

    @property
    def is_documented(self) -> bool:
        return self.documented

    @property
    def moves_money(self) -> bool:
        """Whether this outcome can create or close a COD receivable."""
        return self.is_final and self.canonical is not None


def _m(
    status: PathaoOrderStatus,
    canonical: ConsignmentStatus | None,
    description: str,
    *,
    is_final: bool = False,
    needs_quantity_resolution: bool = False,
    keep_polling: bool = True,
) -> PathaoStatusMapping:
    return PathaoStatusMapping(
        provider_status=str(status),
        canonical=canonical,
        is_final=is_final,
        documented=True,
        needs_quantity_resolution=needs_quantity_resolution,
        keep_polling=keep_polling,
        description=description,
    )


#: The complete map. Nineteen entries, one per status Pathao publishes.
ORDER_STATUS_MAP: Final[MappingProxyType[str, PathaoStatusMapping]] = MappingProxyType(
    {
        # --- accepted, not yet collected ------------------------------------
        str(PathaoOrderStatus.ORDER_CREATED): _m(
            PathaoOrderStatus.ORDER_CREATED,
            ConsignmentStatus.BOOKED,
            "Pathao has accepted the parcel.",
        ),
        str(PathaoOrderStatus.ORDER_UPDATED): _m(
            PathaoOrderStatus.ORDER_UPDATED,
            # A change to the parcel's details is not a change to where it is.
            None,
            "Pathao updated the parcel's details.",
        ),
        str(PathaoOrderStatus.PICKUP_REQUESTED): _m(
            PathaoOrderStatus.PICKUP_REQUESTED,
            ConsignmentStatus.BOOKED,
            "A pickup has been requested.",
        ),
        str(PathaoOrderStatus.ASSIGNED_FOR_PICKUP): _m(
            PathaoOrderStatus.ASSIGNED_FOR_PICKUP,
            ConsignmentStatus.BOOKED,
            "A rider has been assigned to collect the parcel.",
        ),
        str(PathaoOrderStatus.PICKUP_FAILED): _m(
            PathaoOrderStatus.PICKUP_FAILED,
            # Still ours, still expected to be collected again.
            ConsignmentStatus.BOOKED,
            "The rider could not collect the parcel.",
        ),
        str(PathaoOrderStatus.PICKUP_CANCELLED): _m(
            PathaoOrderStatus.PICKUP_CANCELLED,
            # Deliberately not CANCELLED: that is terminal and touches stock,
            # and Pathao says nothing about the order being cancelled.
            ConsignmentStatus.BOOKED,
            "The pickup was cancelled.",
        ),
        # --- in Pathao's network --------------------------------------------
        str(PathaoOrderStatus.PICKED): _m(
            PathaoOrderStatus.PICKED,
            ConsignmentStatus.PICKED_UP,
            "The rider has collected the parcel.",
        ),
        str(PathaoOrderStatus.AT_THE_SORTING_HUB): _m(
            PathaoOrderStatus.AT_THE_SORTING_HUB,
            ConsignmentStatus.IN_TRANSIT,
            "The parcel is at the sorting hub.",
        ),
        str(PathaoOrderStatus.IN_TRANSIT): _m(
            PathaoOrderStatus.IN_TRANSIT,
            ConsignmentStatus.IN_TRANSIT,
            "The parcel is in transit.",
        ),
        str(PathaoOrderStatus.RECEIVED_AT_LAST_MILE_HUB): _m(
            PathaoOrderStatus.RECEIVED_AT_LAST_MILE_HUB,
            ConsignmentStatus.IN_TRANSIT,
            "The parcel has reached the delivery hub.",
        ),
        str(PathaoOrderStatus.ASSIGNED_FOR_DELIVERY): _m(
            PathaoOrderStatus.ASSIGNED_FOR_DELIVERY,
            ConsignmentStatus.OUT_FOR_DELIVERY,
            "A rider is out delivering the parcel.",
        ),
        str(PathaoOrderStatus.ON_HOLD): _m(
            PathaoOrderStatus.ON_HOLD,
            # "Held" says nothing about the outcome. A held parcel that later
            # delivers must not have passed through a terminal state on the way.
            ConsignmentStatus.IN_TRANSIT,
            "The parcel is on hold.",
        ),
        str(PathaoOrderStatus.DELIVERY_FAILED): _m(
            PathaoOrderStatus.DELIVERY_FAILED,
            # Not FAILED. Pathao re-attempts or returns; closing it here would
            # strand a parcel that is still moving.
            ConsignmentStatus.IN_TRANSIT,
            "A delivery attempt did not succeed.",
        ),
        # --- settled at Pathao ------------------------------------------------
        str(PathaoOrderStatus.DELIVERED): _m(
            PathaoOrderStatus.DELIVERED,
            ConsignmentStatus.DELIVERED,
            "The parcel was delivered.",
            is_final=True,
            keep_polling=False,
        ),
        str(PathaoOrderStatus.PARTIAL_DELIVERY): _m(
            PathaoOrderStatus.PARTIAL_DELIVERY,
            ConsignmentStatus.PARTIAL_DELIVERED,
            "Part of the parcel was delivered.",
            is_final=True,
            # Pathao attaches no quantities to this status anywhere. A person
            # supplies them, or the outcome is not recorded at all.
            needs_quantity_resolution=True,
            keep_polling=False,
        ),
        str(PathaoOrderStatus.RETURN): _m(
            PathaoOrderStatus.RETURN,
            ConsignmentStatus.RETURNED,
            "The parcel was returned.",
            is_final=True,
            keep_polling=False,
        ),
        # --- published, but not a delivery-state claim -------------------------
        str(PathaoOrderStatus.PAID_RETURN): _m(
            PathaoOrderStatus.PAID_RETURN,
            # Pathao publishes the label and nothing about what it settles.
            ConsignmentStatus.RETURNING,
            "Pathao recorded a paid return for this parcel.",
        ),
        str(PathaoOrderStatus.EXCHANGE): _m(
            PathaoOrderStatus.EXCHANGE,
            None,
            "Pathao recorded an exchange for this parcel.",
        ),
        str(PathaoOrderStatus.PAYMENT_INVOICE): _m(
            PathaoOrderStatus.PAYMENT_INVOICE,
            # A settlement event, not a parcel movement. Payment ingestion — not
            # the parcel state machine — is what may act on it.
            None,
            "Pathao issued a payment invoice covering this parcel.",
        ),
    }
)


def map_order_status(raw_status: str | None) -> PathaoStatusMapping:
    """Map a Pathao status string. Never raises; never guesses."""
    if raw_status is None:
        return PathaoStatusMapping(
            provider_status="",
            canonical=None,
            is_final=False,
            documented=False,
            description="The courier sent no status.",
        )

    mapping = ORDER_STATUS_MAP.get(raw_status)
    if mapping is not None:
        return mapping

    return PathaoStatusMapping(
        provider_status=raw_status,
        canonical=None,
        is_final=False,
        documented=False,
        # Keep asking. An undocumented state is not a terminal state, and
        # giving up on it would strand the parcel.
        keep_polling=True,
        description=(
            "A status this integration has not seen published. It is stored "
            "exactly as the courier sent it and changes nothing until it is "
            "understood."
        ),
    )


def map_event(event: str | None) -> PathaoStatusMapping:
    """Map a webhook event name via the status label Pathao records for it."""
    status = status_for_event(event)
    if status is None:
        return PathaoStatusMapping(
            provider_status=event or "",
            canonical=None,
            is_final=False,
            documented=False,
            description="A webhook event this integration has not seen published.",
        )
    return map_order_status(str(status))


def assert_map_is_complete() -> None:
    """Fail loudly if a published status has no mapping.

    Called by the test suite. A new member added to
    :class:`~app.couriers.pathao.contract.PathaoOrderStatus` without a mapping
    would otherwise silently behave like an undocumented one.
    """
    missing = [
        str(status)
        for status in PathaoOrderStatus
        if not is_documented_status(str(status)) or str(status) not in ORDER_STATUS_MAP
    ]
    if missing:
        raise AssertionError(f"published Pathao statuses with no mapping: {missing}")
