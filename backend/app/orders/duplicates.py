"""Duplicate order detection.

Master spec section 9. Warn when, within the same tenant, an order shares a
normalized phone with a recent order, has a close COD amount, falls inside a
configurable window, and is not cancelled.

The rule that matters most is the last line of section 9: **"this is a warning,
not a hard block."** F-commerce sellers legitimately take two orders from the
same customer in one day — a repeat purchase, a corrected order, a friend
ordering on the same phone. Blocking them would break a real workflow to prevent
a mistake the seller can see for themselves. So this returns candidates and
reasons, and the seller decides.

Nothing here merges orders. Section 9 does not ask for it, and a silent merge
would destroy one of the two orders' item lines.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.orders.models import Order, OrderStatus

__all__ = [
    "DEFAULT_AMOUNT_TOLERANCE_BASIS_POINTS",
    "DEFAULT_WINDOW_HOURS",
    "DuplicateCandidate",
    "DuplicateCheck",
    "DuplicateReason",
    "detect_duplicates",
]

#: Section 9: "Initial window: 24 hours." Configurable per call.
DEFAULT_WINDOW_HOURS = 24

#: How close two COD amounts must be to count as "close". 500 bp = 5%, so a
#: ৳1,250 order matches ৳1,187–৳1,312. Wide enough to catch a re-typed order
#: with a slightly different delivery charge, narrow enough that a genuinely
#: different purchase does not trip it.
DEFAULT_AMOUNT_TOLERANCE_BASIS_POINTS = 500

#: Below this the percentage tolerance is uselessly small, so an absolute floor
#: applies instead.
_MIN_AMOUNT_TOLERANCE_PAISA = 5_000  # ৳50


class DuplicateReason(StrEnum):
    """Why a candidate was flagged. Returned so the UI can explain itself."""

    SAME_PHONE = "SAME_PHONE"
    SIMILAR_AMOUNT = "SIMILAR_AMOUNT"
    IDENTICAL_AMOUNT = "IDENTICAL_AMOUNT"
    SIMILAR_ITEMS = "SIMILAR_ITEMS"
    SAME_CUSTOMER = "SAME_CUSTOMER"


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    """A recent order that resembles the one being created."""

    order_id: uuid.UUID
    order_number: str
    status: OrderStatus
    cod_amount_paisa: int
    created_at: object
    hours_ago: float
    reasons: tuple[DuplicateReason, ...]
    matching_item_names: tuple[str, ...] = ()

    @property
    def is_strong(self) -> bool:
        """Same phone plus the same amount, or the same items.

        Strong enough that the UI leads with it, still not enough to block.
        """
        return DuplicateReason.IDENTICAL_AMOUNT in self.reasons or (
            DuplicateReason.SIMILAR_ITEMS in self.reasons
            and DuplicateReason.SIMILAR_AMOUNT in self.reasons
        )


@dataclass(frozen=True, slots=True)
class DuplicateCheck:
    """Result of a duplicate scan."""

    possible_duplicate: bool
    candidates: list[DuplicateCandidate] = field(default_factory=list)
    window_hours: int = DEFAULT_WINDOW_HOURS

    @property
    def message(self) -> str:
        """Seller-facing wording. A warning, never a refusal."""
        if not self.possible_duplicate:
            return ""
        count = len(self.candidates)
        if count == 1:
            return "Similar order found recently."
        return f"{count} similar orders found recently."


def _amount_tolerance_paisa(amount_paisa: int, basis_points: int) -> int:
    proportional = abs(amount_paisa) * basis_points // 10_000
    return max(proportional, _MIN_AMOUNT_TOLERANCE_PAISA)


def _normalize_item_name(name: str) -> str:
    return " ".join(name.lower().split())


async def detect_duplicates(
    session: AsyncSession,
    *,
    phone_hmac: str | None,
    cod_amount_paisa: int,
    item_names: list[str] | None = None,
    customer_id: uuid.UUID | None = None,
    exclude_order_id: uuid.UUID | None = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    amount_tolerance_basis_points: int = DEFAULT_AMOUNT_TOLERANCE_BASIS_POINTS,
) -> DuplicateCheck:
    """Scan recent orders for something that looks like this one.

    Keyed on the phone HMAC, which is what makes the scan cheap: it is indexed
    (``ix_orders_dup_scan``) and needs no decryption. With no phone there is
    nothing reliable to compare, so the check returns clean rather than guessing
    from amounts alone — an amount-only match across different customers is
    noise, and a warning sellers learn to dismiss is worse than no warning.
    """
    if not phone_hmac:
        return DuplicateCheck(possible_duplicate=False, window_hours=window_hours)

    since = utc_now() - timedelta(hours=window_hours)

    stmt = (
        sa.select(Order)
        .where(
            Order.customer_phone_hmac == phone_hmac,
            Order.created_at >= since,
            # Section 9: a cancelled order is not a duplicate. Re-entering an
            # order the seller just cancelled is the correct action.
            Order.status != OrderStatus.CANCELLED,
            Order.deleted_at.is_(None),
        )
        .order_by(Order.created_at.desc())
        .limit(20)
    )
    if exclude_order_id is not None:
        stmt = stmt.where(Order.id != exclude_order_id)

    recent = list((await session.execute(stmt)).scalars().all())
    if not recent:
        return DuplicateCheck(possible_duplicate=False, window_hours=window_hours)

    tolerance = _amount_tolerance_paisa(cod_amount_paisa, amount_tolerance_basis_points)
    wanted_items = {_normalize_item_name(name) for name in (item_names or []) if name}

    now = utc_now()
    candidates: list[DuplicateCandidate] = []

    for order in recent:
        # Identity signals say *who* this is. They are informational: every row
        # in `recent` already shares the phone, and the customer is identified
        # *by* that phone, so neither can corroborate the other.
        identity: list[DuplicateReason] = [DuplicateReason.SAME_PHONE]
        if customer_id is not None and order.customer_id == customer_id:
            identity.append(DuplicateReason.SAME_CUSTOMER)

        # Corroborating signals say this looks like the *same order*. At least
        # one is required. Without this split, every repeat customer trips the
        # warning, and a warning that fires constantly is one sellers learn to
        # dismiss — which is worse than no warning at all.
        corroborating: list[DuplicateReason] = []

        difference = abs(order.cod_amount_paisa - cod_amount_paisa)
        if difference == 0:
            corroborating.append(DuplicateReason.IDENTICAL_AMOUNT)
        elif difference <= tolerance:
            corroborating.append(DuplicateReason.SIMILAR_AMOUNT)

        overlap: tuple[str, ...] = ()
        if wanted_items:
            existing_items = {_normalize_item_name(item.product_name) for item in order.items}
            shared = wanted_items & existing_items
            if shared:
                corroborating.append(DuplicateReason.SIMILAR_ITEMS)
                overlap = tuple(sorted(shared))

        if not corroborating:
            continue

        reasons = identity + corroborating

        elapsed = (now - order.created_at).total_seconds() / 3600
        candidates.append(
            DuplicateCandidate(
                order_id=order.id,
                order_number=order.order_number,
                status=order.order_status,
                cod_amount_paisa=order.cod_amount_paisa,
                created_at=order.created_at,
                hours_ago=round(elapsed, 2),
                reasons=tuple(reasons),
                matching_item_names=overlap,
            )
        )

    candidates.sort(key=lambda c: (not c.is_strong, c.hours_ago))
    return DuplicateCheck(
        possible_duplicate=bool(candidates),
        candidates=candidates,
        window_hours=window_hours,
    )
