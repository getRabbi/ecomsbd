"""Delivery-risk banding from this shop's own order history.

Master spec sections 24, 121 and 130. Three constraints shape everything here:

* **First-party data only.** The inputs are the tenant's own parcel and order
  outcomes (:class:`app.analytics.rto.CustomerHistory`). No licensed aggregator,
  no cross-tenant view, nothing scraped. A seller sees how *their* customers
  behaved with *them*, which is the only history they are entitled to.
* **Explainable, not scored.** The band is a function of two integers the
  screen already shows. A seller who disagrees can check the arithmetic; there
  is no opaque model to appeal to.
* **Silent under a small sample.** Below :data:`MIN_TERMINAL_ORDERS` finished
  orders the answer is ``INSUFFICIENT_DATA``, never ``LOW``. Calling a customer
  low-risk on one delivery is a judgement the data does not support, and
  section 130 requires the wording to be about the order, never the person.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

#: Finished orders needed before a band is claimed at all. Two deliveries and a
#: return is the smallest history where the ratio says anything; below that the
#: swing from one more order is larger than the gap between the bands.
MIN_TERMINAL_ORDERS = 3

#: Delivered share, in basis points, at or above which the order is banded low.
LOW_RISK_BPS = 8_000
#: ...and above which it is banded medium rather than high.
MEDIUM_RISK_BPS = 5_000


class RiskState(StrEnum):
    """The band, in the spelling the order model and the app already use."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class RiskReason(StrEnum):
    """Why the band came out the way it did.

    Machine codes, not sentences: the app renders them in the seller's own
    language from its own catalogue, so the API never ships user-facing copy
    that would have to be translated twice.
    """

    NO_ORDERS = "NO_ORDERS"
    SMALL_SAMPLE = "SMALL_SAMPLE"
    STRONG_DELIVERY_RATE = "STRONG_DELIVERY_RATE"
    MIXED_DELIVERY_RATE = "MIXED_DELIVERY_RATE"
    WEAK_DELIVERY_RATE = "WEAK_DELIVERY_RATE"
    HAS_RETURNS = "HAS_RETURNS"
    HAS_CANCELLATIONS = "HAS_CANCELLATIONS"
    #: Always present. The screen shows it as a caveat, because a seller must
    #: not read this band as the customer's reputation anywhere else.
    OWN_SHOP_HISTORY_ONLY = "OWN_SHOP_HISTORY_ONLY"


class RiskInputs(Protocol):
    """The counts the band reads.

    Satisfied by :class:`app.analytics.rto.CustomerHistory`, which derives them
    from the shop's parcels with the shared RTO classification: delivered
    includes partial deliveries, returned is every RTO parcel (returned or
    cancelled at the courier), cancelled is orders cancelled before dispatch.
    """

    @property
    def order_count(self) -> int: ...
    @property
    def delivered_count(self) -> int: ...
    @property
    def returned_count(self) -> int: ...
    @property
    def cancelled_count(self) -> int: ...
    @property
    def terminal_count(self) -> int: ...
    @property
    def success_rate_basis_points(self) -> int | None: ...
    @property
    def first_order_at(self) -> datetime | None: ...
    @property
    def last_order_at(self) -> datetime | None: ...


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """One customer's delivery history and the band it implies."""

    state: RiskState
    order_count: int
    delivered_count: int
    returned_count: int
    cancelled_count: int
    terminal_count: int
    success_rate_basis_points: int | None
    first_order_at: datetime | None
    last_order_at: datetime | None
    reasons: tuple[RiskReason, ...]


def assess(customer: RiskInputs) -> RiskAssessment:
    """Band ``customer`` from their finished orders with this shop.

    The denominator is ``terminal_count`` — delivered, returned and cancelled —
    and the numerator is deliveries. The API feeds it the parcel-derived
    history the customers list also shows, so the two never disagree.
    """
    terminal = customer.terminal_count
    rate = customer.success_rate_basis_points
    reasons: list[RiskReason] = []

    if terminal < MIN_TERMINAL_ORDERS:
        reasons.append(RiskReason.NO_ORDERS if terminal == 0 else RiskReason.SMALL_SAMPLE)
        state = RiskState.INSUFFICIENT_DATA
    else:
        # ``rate`` is not None here: terminal_count > 0 is exactly the condition
        # under which the model computes it.
        assert rate is not None  # noqa: S101 - guarded by the branch above
        if rate >= LOW_RISK_BPS:
            state = RiskState.LOW
            reasons.append(RiskReason.STRONG_DELIVERY_RATE)
        elif rate >= MEDIUM_RISK_BPS:
            state = RiskState.MEDIUM
            reasons.append(RiskReason.MIXED_DELIVERY_RATE)
        else:
            state = RiskState.HIGH
            reasons.append(RiskReason.WEAK_DELIVERY_RATE)

        if customer.returned_count > 0:
            reasons.append(RiskReason.HAS_RETURNS)
        if customer.cancelled_count > 0:
            reasons.append(RiskReason.HAS_CANCELLATIONS)

    reasons.append(RiskReason.OWN_SHOP_HISTORY_ONLY)

    return RiskAssessment(
        state=state,
        order_count=customer.order_count,
        delivered_count=customer.delivered_count,
        returned_count=customer.returned_count,
        cancelled_count=customer.cancelled_count,
        terminal_count=terminal,
        success_rate_basis_points=rate,
        first_order_at=customer.first_order_at,
        last_order_at=customer.last_order_at,
        reasons=tuple(reasons),
    )


#: What to answer for a number this shop has never sold to. Not a band: an
#: unknown customer is not a safe one, and it is not a risky one either.
UNKNOWN = RiskAssessment(
    state=RiskState.INSUFFICIENT_DATA,
    order_count=0,
    delivered_count=0,
    returned_count=0,
    cancelled_count=0,
    terminal_count=0,
    success_rate_basis_points=None,
    first_order_at=None,
    last_order_at=None,
    reasons=(RiskReason.NO_ORDERS, RiskReason.OWN_SHOP_HISTORY_ONLY),
)

__all__ = [
    "LOW_RISK_BPS",
    "MEDIUM_RISK_BPS",
    "MIN_TERMINAL_ORDERS",
    "UNKNOWN",
    "RiskAssessment",
    "RiskInputs",
    "RiskReason",
    "RiskState",
    "assess",
]
