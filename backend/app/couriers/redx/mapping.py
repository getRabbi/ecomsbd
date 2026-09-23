"""RedX status -> ecomsbd consignment state.

One table, exact keys, no fallthrough — the same three rules the Steadfast and
Pathao maps follow:

*   **Exact match only.** No ``startswith``, no substring bucketing.
    ``agent-returning`` contains ``return``; a substring match would settle a
    parcel that is still on its way back.

*   **Only a settled, documented outcome may move money.**

*   **An unknown status is data, not an error.** A value RedX adds next year
    maps to ``None``, is stored raw, and leaves the parcel and its money alone.

RedX adds one rule of its own: **a status is read together with the parcel's
delivery type.** RedX's "Delivery Type Reference Table" says a parcel can be a
``regular`` forward delivery, or a partial, exchange or reverse one. The same
``delivered`` means "the whole parcel arrived" on a ``regular`` parcel and
"part of it arrived" on a ``partial-delivery`` one, so a settled outcome is
only ever recorded when the delivery type says which. A final status with any
other delivery type — or none — is stored and settles nothing.

The entries that deserve their reasoning stated, because each is a place where
the convenient mapping is the wrong one:

*   ``pickup-pending`` appears in the documentation only as the sample value of
    ``status`` in *Get Parcel Details*, with no meaning given. It makes **no**
    parcel-state claim: the parcel stays exactly where it is (booked).
*   ``agent-area-change`` ("Area change requested & in progress") says the
    delivery area is being changed, not where the parcel physically is. It
    makes no parcel-state claim either.
*   ``paid`` ("Parcel amount is paid") is a settlement event, not a movement.
    Payment data — not the parcel state machine — is what may act on it.
*   ``cancelled`` is not in the meanings table. It is the value the documented
    *Update Parcel* call writes to a parcel's ``status`` to cancel it, so a
    parcel that reads back ``cancelled`` has been cancelled at RedX.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from app.consignments.models import ConsignmentStatus
from app.couriers.redx.contract import (
    CANCEL_VALUE,
    STATUS_MEANINGS,
    DeliveryType,
    RedxStatus,
)

__all__ = [
    "STATUS_MAP",
    "RedxStatusMapping",
    "assert_map_is_complete",
    "map_parcel_status",
]


@dataclass(frozen=True, slots=True)
class RedxStatusMapping:
    """What one RedX status means for a parcel.

    Shaped like the Steadfast and Pathao mappings, so status sync and the event
    log apply it with the same code.
    """

    provider_status: str
    #: ``None`` means "no parcel-state claim". :attr:`documented` separates a
    #: status RedX publishes that simply is not about where the parcel is from
    #: one nobody has seen published.
    canonical: ConsignmentStatus | None
    #: Whether this outcome is settled at RedX. Only a settled outcome may
    #: drive money.
    is_final: bool
    documented: bool = True
    needs_quantity_resolution: bool = False
    keep_polling: bool = True
    description: str = ""
    #: RedX has no approval-pending states. Present so this reads like the
    #: other providers' mappings.
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
    *,
    is_final: bool = False,
    needs_quantity_resolution: bool = False,
    description: str | None = None,
) -> RedxStatusMapping:
    return RedxStatusMapping(
        provider_status=status,
        canonical=canonical,
        is_final=is_final,
        documented=True,
        needs_quantity_resolution=needs_quantity_resolution,
        keep_polling=not is_final,
        description=description if description is not None else _MEANINGS.get(status, ""),
    )


#: RedX's own words for each status, keyed by the wire value.
_MEANINGS: Final[dict[str, str]] = {str(status): text for status, text in STATUS_MEANINGS.items()}


#: Statuses whose meaning does not depend on the delivery type. None of them is
#: final, so none of them can move money.
STATUS_MAP: Final[MappingProxyType[str, RedxStatusMapping]] = MappingProxyType(
    {
        str(RedxStatus.PICKUP_PENDING): _m(
            str(RedxStatus.PICKUP_PENDING),
            None,
            description="RedX has not collected the parcel yet.",
        ),
        str(RedxStatus.READY_FOR_DELIVERY): _m(
            # "Parcel received from merchants": RedX has it.
            str(RedxStatus.READY_FOR_DELIVERY),
            ConsignmentStatus.PICKED_UP,
        ),
        str(RedxStatus.DELIVERY_IN_PROGRESS): _m(
            # "Parcels have been dispatched to rider".
            str(RedxStatus.DELIVERY_IN_PROGRESS),
            ConsignmentStatus.OUT_FOR_DELIVERY,
        ),
        str(RedxStatus.AGENT_HOLD): _m(
            # "On hold to agent" says nothing about the outcome. A held parcel
            # that later delivers must not have passed through a terminal state.
            str(RedxStatus.AGENT_HOLD),
            ConsignmentStatus.IN_TRANSIT,
        ),
        str(RedxStatus.AGENT_RETURNING): _m(
            # "Parcel return-in-progress". On its way back, not back yet.
            str(RedxStatus.AGENT_RETURNING),
            ConsignmentStatus.RETURNING,
        ),
        str(RedxStatus.AGENT_AREA_CHANGE): _m(str(RedxStatus.AGENT_AREA_CHANGE), None),
        str(RedxStatus.PAID): _m(str(RedxStatus.PAID), None),
    }
)

#: Final statuses, keyed on (status, delivery type). Only these settle a
#: parcel; any other delivery type leaves a final status unsettled.
_FINAL_MAP: Final[MappingProxyType[tuple[str, str], RedxStatusMapping]] = MappingProxyType(
    {
        (str(RedxStatus.DELIVERED), str(DeliveryType.REGULAR)): _m(
            str(RedxStatus.DELIVERED),
            ConsignmentStatus.DELIVERED,
            is_final=True,
        ),
        (str(RedxStatus.DELIVERED), str(DeliveryType.PARTIAL_DELIVERY)): _m(
            str(RedxStatus.DELIVERED),
            ConsignmentStatus.PARTIAL_DELIVERED,
            is_final=True,
            # RedX attaches no quantities to a partial delivery. A person
            # supplies them, or the outcome is not recorded at all.
            needs_quantity_resolution=True,
            description="Part of the parcel was delivered.",
        ),
        (str(RedxStatus.RETURNED), str(DeliveryType.REGULAR)): _m(
            str(RedxStatus.RETURNED),
            ConsignmentStatus.RETURNED,
            is_final=True,
        ),
        (CANCEL_VALUE, str(DeliveryType.REGULAR)): _m(
            CANCEL_VALUE,
            ConsignmentStatus.CANCELLED,
            is_final=True,
            description="The parcel was cancelled at RedX.",
        ),
    }
)

#: The final statuses. Documented, but only settled together with a delivery
#: type in :data:`_FINAL_MAP`.
_FINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {str(RedxStatus.DELIVERED), str(RedxStatus.RETURNED), CANCEL_VALUE}
)


def map_parcel_status(
    raw_status: str | None, delivery_type: str | None = None
) -> RedxStatusMapping:
    """Map a RedX status, read with the parcel's delivery type.

    Never raises; never guesses.
    """
    if raw_status is None:
        return RedxStatusMapping(
            provider_status="",
            canonical=None,
            is_final=False,
            documented=False,
            description="The courier sent no status.",
        )

    mapping = STATUS_MAP.get(raw_status)
    if mapping is not None:
        return mapping

    if raw_status in _FINAL_STATUSES:
        settled = _FINAL_MAP.get((raw_status, delivery_type or ""))
        if settled is not None:
            return settled
        # A documented final status on a parcel whose delivery type does not
        # say which outcome it is — exchange, reverse, a partial return, or no
        # type at all. Stored exactly as sent, and nothing is settled.
        return RedxStatusMapping(
            provider_status=raw_status,
            canonical=None,
            is_final=False,
            documented=True,
            keep_polling=True,
            description=(
                "RedX reported a final status for a parcel whose delivery type "
                "does not say what it settles. Nothing was changed; check the "
                "parcel in RedX."
            ),
        )

    return RedxStatusMapping(
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


def assert_map_is_complete() -> None:
    """Fail loudly if a published RedX status has no mapping.

    Called by the test suite. A new member added to
    :class:`~app.couriers.redx.contract.RedxStatus` without a mapping would
    otherwise silently behave like an undocumented one.
    """
    missing = [
        str(status)
        for status in RedxStatus
        if str(status) not in STATUS_MAP and str(status) not in _FINAL_STATUSES
    ]
    if missing:
        raise AssertionError(f"published RedX statuses with no mapping: {missing}")
