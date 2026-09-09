"""Seller-facing order numbers.

Master spec section 70::

    CP-20260909-0042

Requirements: unique within the tenant, human searchable, never reused — not
even for a cancelled order, because a seller who cancels ``CP-20260909-0042``
and later sees that number again on a courier statement has no way to tell which
order it meant.

The ``CP-`` prefix is a **stored technical identifier**, not branding. Renaming
it because the product is now called ecomsbd would rewrite every existing order
reference, break the seller's own paper trail and break searches against courier
statements that carry the old string. Section 70 already allows a
tenant-configured prefix for shops that want their own.

Allocation is per tenant per business date. Concurrency is handled by the
``uq_orders_tenant_id_order_number`` constraint plus a bounded retry: two
devices creating an order in the same second both compute ``…-0043``, one wins,
the loser retries and gets ``…-0044``. That is cheaper and more robust than a
counter table, which would serialise every order creation in the shop.
"""

from __future__ import annotations

import re
from datetime import date

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError

__all__ = [
    "DEFAULT_ORDER_PREFIX",
    "MAX_ALLOCATION_ATTEMPTS",
    "allocate_order_number",
    "format_order_number",
    "parse_order_number",
]

#: Historical default. Not changed to an ecomsbd-derived prefix: see module docs.
DEFAULT_ORDER_PREFIX = "CP"

#: How many collisions to absorb before giving up. Each retry means another
#: device won the same sequence number in the same instant.
MAX_ALLOCATION_ATTEMPTS = 8

_ORDER_NUMBER_RE = re.compile(r"^(?P<prefix>[A-Z0-9]{1,8})-(?P<date>\d{8})-(?P<seq>\d{4,})$")


def format_order_number(prefix: str, business_date: date, sequence: int) -> str:
    """``CP`` + ``2026-09-09`` + ``42`` -> ``CP-20260909-0042``."""
    return f"{prefix}-{business_date:%Y%m%d}-{sequence:04d}"


def parse_order_number(value: str) -> tuple[str, date, int] | None:
    """Split an order number back into its parts, or ``None`` if malformed.

    Used by search so a seller can paste a full order number and have it
    recognised rather than treated as a free-text term.
    """
    match = _ORDER_NUMBER_RE.match(value.strip().upper())
    if match is None:
        return None
    raw_date = match.group("date")
    try:
        parsed = date(int(raw_date[0:4]), int(raw_date[4:6]), int(raw_date[6:8]))
    except ValueError:
        return None
    return match.group("prefix"), parsed, int(match.group("seq"))


async def allocate_order_number(
    session: AsyncSession,
    *,
    business_date: date,
    prefix: str = DEFAULT_ORDER_PREFIX,
    attempt: int = 0,
) -> str:
    """Next free order number for this tenant and business date.

    Reads the highest sequence already used for the date and adds one. Because
    the read is not locked, two concurrent creates can produce the same
    candidate; the caller flushes inside a savepoint and calls this again with
    ``attempt + 1`` on a unique violation.

    Numbers are never reused: the maximum only ever moves up, so a cancelled
    order's number stays retired.
    """
    from app.orders.models import Order

    if attempt >= MAX_ALLOCATION_ATTEMPTS:
        raise ConflictError(
            "Could not allocate an order number after repeated collisions. Please try again.",
            details={"business_date": business_date.isoformat()},
        )

    like_pattern = f"{prefix}-{business_date:%Y%m%d}-%"
    highest = (
        await session.execute(
            sa.select(sa.func.max(Order.order_number)).where(Order.order_number.like(like_pattern))
        )
    ).scalar_one_or_none()

    if highest is None:
        next_sequence = 1
    else:
        parsed = parse_order_number(highest)
        # A malformed stored value must not stall order creation; start a fresh
        # sequence above it rather than failing the seller's order.
        next_sequence = (parsed[2] if parsed else 0) + 1

    # Each retry skips ahead, so two racing writers converge instead of
    # colliding on the same candidate repeatedly.
    return format_order_number(prefix, business_date, next_sequence + attempt)
