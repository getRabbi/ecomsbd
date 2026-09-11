"""Steadfast status -> ecomsbd consignment state.

One table, exact keys, no fallthrough. The rules the brief fixes (sections 14,
15, 16) are encoded as structure rather than as comments:

*   **Exact match only.** :data:`DELIVERY_STATUS_MAP` is keyed on the literal
    documented strings. There is no ``startswith``, no ``in``, no lowercasing
    of an arbitrary input into a bucket. ``delivered_approval_pending`` contains
    ``delivered``; a substring match would settle money on a parcel the courier
    has not been paid for yet, which is the single most expensive bug available
    in this integration.

*   **Approval-pending is provisional.** The document says the four
    ``*_approval_pending`` states are "waiting for admin approval", and says
    "balance added" only for ``delivered``, ``partial_delivered`` and
    ``cancelled``. So the pending states map to in-flight ecomsbd states and
    carry ``is_final=False``. They move the parcel's *visible* status and
    nothing else; no receivable becomes eligible, no profit is frozen.

*   **Partial delivery never invents quantities.** ``partial_delivered`` is a
    status string with no numbers attached anywhere in the document. It maps to
    a decision that says "a person must say how many", and the domain refuses to
    record the outcome until they do.

*   **An unknown status is data, not an error.** A value Steadfast adds next
    year lands in :func:`map_delivery_status` as ``None`` mapping, is stored
    raw, emits an observability signal, and leaves money untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from app.consignments.models import ConsignmentStatus
from app.couriers.steadfast.contract import (
    DELIVERY_STATUS_DESCRIPTIONS,
    ReturnRequestStatus,
    SteadfastDeliveryStatus,
    is_documented_delivery_status,
)

__all__ = [
    "DELIVERY_STATUS_MAP",
    "RETURN_STATUS_MAP",
    "StatusMapping",
    "map_delivery_status",
    "map_return_status",
]


@dataclass(frozen=True, slots=True)
class StatusMapping:
    """What one provider status means for a parcel."""

    provider_status: str
    #: ``None`` for a status we have never seen documented.
    canonical: ConsignmentStatus | None
    #: Whether this outcome is settled at the provider. Only a settled outcome
    #: may drive money.
    is_final: bool
    #: Whether the provider says it is waiting for its own admin approval.
    is_approval_pending: bool
    #: Whether recording this outcome needs a person to supply quantities.
    needs_quantity_resolution: bool = False
    #: Whether polling should continue.
    keep_polling: bool = True
    #: The document's own sentence, for the seller-facing timeline.
    description: str = ""

    @property
    def is_documented(self) -> bool:
        return self.canonical is not None

    @property
    def moves_money(self) -> bool:
        """Whether this outcome may create or close a COD receivable.

        Final **and** documented. An approval-pending state is explicitly not
        final, and an undocumented state is not anything.
        """
        return self.is_final and self.canonical is not None


def _mapping(
    status: SteadfastDeliveryStatus,
    canonical: ConsignmentStatus,
    *,
    is_final: bool,
    needs_quantity_resolution: bool = False,
    keep_polling: bool = True,
) -> StatusMapping:
    return StatusMapping(
        provider_status=str(status),
        canonical=canonical,
        is_final=is_final,
        is_approval_pending=status.is_approval_pending,
        needs_quantity_resolution=needs_quantity_resolution,
        keep_polling=keep_polling,
        description=DELIVERY_STATUS_DESCRIPTIONS[status],
    )


#: The complete map. Eleven entries, one per documented status, no more.
#:
#: Two choices here deserve their reasons stated:
#:
#: * ``pending`` and ``in_review`` both map to ``BOOKED`` rather than to
#:   ``IN_TRANSIT``. Steadfast documents no transit states at all — its
#:   pre-delivery vocabulary is exactly these two — so claiming a parcel is in
#:   transit because it is not delivered would be ecomsbd inventing a fact.
#:   ``IN_TRANSIT``, ``PICKED_UP`` and ``OUT_FOR_DELIVERY`` remain reachable
#:   through manual mode and stay unused for Steadfast parcels.
#:
#: * ``hold`` maps to ``BOOKED`` and keeps polling. "Consignment is held" says
#:   nothing about the outcome, and a held parcel that later delivers must not
#:   have passed through a terminal state on the way.
DELIVERY_STATUS_MAP: Final[MappingProxyType[str, StatusMapping]] = MappingProxyType(
    {
        str(SteadfastDeliveryStatus.IN_REVIEW): _mapping(
            SteadfastDeliveryStatus.IN_REVIEW, ConsignmentStatus.BOOKED, is_final=False
        ),
        str(SteadfastDeliveryStatus.PENDING): _mapping(
            SteadfastDeliveryStatus.PENDING, ConsignmentStatus.BOOKED, is_final=False
        ),
        str(SteadfastDeliveryStatus.HOLD): _mapping(
            SteadfastDeliveryStatus.HOLD, ConsignmentStatus.BOOKED, is_final=False
        ),
        # --- awaiting the provider's own admin approval ----------------------
        # These keep the parcel in an in-flight ecomsbd state on purpose. The
        # seller sees "delivered, awaiting courier approval"; the money engine
        # sees a parcel that has not finished.
        str(SteadfastDeliveryStatus.DELIVERED_APPROVAL_PENDING): _mapping(
            SteadfastDeliveryStatus.DELIVERED_APPROVAL_PENDING,
            ConsignmentStatus.OUT_FOR_DELIVERY,
            is_final=False,
        ),
        str(SteadfastDeliveryStatus.PARTIAL_DELIVERED_APPROVAL_PENDING): _mapping(
            SteadfastDeliveryStatus.PARTIAL_DELIVERED_APPROVAL_PENDING,
            ConsignmentStatus.OUT_FOR_DELIVERY,
            is_final=False,
            needs_quantity_resolution=True,
        ),
        str(SteadfastDeliveryStatus.CANCELLED_APPROVAL_PENDING): _mapping(
            SteadfastDeliveryStatus.CANCELLED_APPROVAL_PENDING,
            ConsignmentStatus.RETURNING,
            is_final=False,
        ),
        str(SteadfastDeliveryStatus.UNKNOWN_APPROVAL_PENDING): _mapping(
            SteadfastDeliveryStatus.UNKNOWN_APPROVAL_PENDING,
            ConsignmentStatus.BOOKED,
            is_final=False,
        ),
        # --- settled at the provider ----------------------------------------
        str(SteadfastDeliveryStatus.DELIVERED): _mapping(
            SteadfastDeliveryStatus.DELIVERED,
            ConsignmentStatus.DELIVERED,
            is_final=True,
            keep_polling=False,
        ),
        str(SteadfastDeliveryStatus.PARTIAL_DELIVERED): _mapping(
            SteadfastDeliveryStatus.PARTIAL_DELIVERED,
            ConsignmentStatus.PARTIAL_DELIVERED,
            is_final=True,
            # The status carries no quantities. A person supplies them, or the
            # outcome is not recorded at all.
            needs_quantity_resolution=True,
            keep_polling=False,
        ),
        str(SteadfastDeliveryStatus.CANCELLED): _mapping(
            SteadfastDeliveryStatus.CANCELLED,
            ConsignmentStatus.CANCELLED,
            is_final=True,
            keep_polling=False,
        ),
        # --- the provider says to contact support ----------------------------
        # Documented, so not "unknown to us" — but it carries no outcome, so it
        # settles nothing and keeps being polled.
        str(SteadfastDeliveryStatus.UNKNOWN): _mapping(
            SteadfastDeliveryStatus.UNKNOWN, ConsignmentStatus.BOOKED, is_final=False
        ),
    }
)


def map_delivery_status(raw_status: str | None) -> StatusMapping:
    """Map a provider status string. Never raises; never guesses.

    An input that is not one of the eleven documented values returns a mapping
    with ``canonical=None``, which every caller treats as "store it, show it,
    change nothing". The caller is also expected to emit
    ``steadfast_status_unknown_value`` so an added provider state is noticed
    within a day rather than after a quarter of wrong reports.
    """
    if raw_status is None:
        return StatusMapping(
            provider_status="",
            canonical=None,
            is_final=False,
            is_approval_pending=False,
            description="The provider sent no status.",
        )

    mapping = DELIVERY_STATUS_MAP.get(raw_status)
    if mapping is not None:
        return mapping

    return StatusMapping(
        provider_status=raw_status,
        canonical=None,
        is_final=False,
        is_approval_pending=False,
        # Keep asking. An undocumented state is not a terminal state, and
        # giving up on it would strand the parcel.
        keep_polling=True,
        description=(
            "A status this integration has not seen documented. It is stored "
            "exactly as the courier sent it and changes nothing until it is "
            "understood."
        ),
    )


#: Return-request status -> what it means for the parcel, where it means
#: anything. A return request is a request: ``pending``, ``approved`` and
#: ``processing`` say the provider has accepted the *ask*, not that the parcel
#: has come back. Only ``completed`` is an outcome, and even then the parcel's
#: own delivery status is what the money engine reads — the return-request
#: record is evidence, not a second source of truth about the parcel.
RETURN_STATUS_MAP: Final[MappingProxyType[str, ConsignmentStatus | None]] = MappingProxyType(
    {
        str(ReturnRequestStatus.PENDING): ConsignmentStatus.RETURN_REQUESTED,
        str(ReturnRequestStatus.APPROVED): ConsignmentStatus.RETURN_REQUESTED,
        str(ReturnRequestStatus.PROCESSING): ConsignmentStatus.RETURNING,
        # Deliberately not RETURNED. "The return request is complete" and "the
        # parcel is back and its stock restored" are different claims, and the
        # second one belongs to the delivery-status stream and the existing
        # return domain (brief section 28).
        str(ReturnRequestStatus.COMPLETED): None,
        str(ReturnRequestStatus.CANCELLED): None,
    }
)


def map_return_status(raw_status: str | None) -> ConsignmentStatus | None:
    """Map a return-request status, or ``None`` when it implies no parcel change."""
    if raw_status is None:
        return None
    return RETURN_STATUS_MAP.get(raw_status)


def documented_statuses() -> tuple[str, ...]:
    """Every documented delivery status. Used by the manifest and the docs test."""
    return tuple(str(status) for status in SteadfastDeliveryStatus)


def assert_map_is_complete() -> None:
    """Fail loudly if a documented status has no mapping.

    Called by the test suite. A new value added to
    :class:`SteadfastDeliveryStatus` without a mapping would otherwise silently
    behave like an undocumented one.
    """
    missing = [
        status
        for status in SteadfastDeliveryStatus
        if not is_documented_delivery_status(str(status)) or str(status) not in DELIVERY_STATUS_MAP
    ]
    if missing:
        raise AssertionError(f"documented statuses with no mapping: {missing}")
