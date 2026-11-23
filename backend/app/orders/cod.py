"""COD calculation and the check against an amount stated in chat (V4 WP0.4).

``COD = sum(catalog price x quantity) + zone charge - discount``, in integer
paisa, never below zero. A stated amount (the customer or seller wrote a total)
is never replaced by the calculation; when the two differ by more than the zone
charge — more than "did the total include delivery or not" can explain — it is
reported as a mismatch for the seller to resolve.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

__all__ = ["CodLine", "calculate_cod", "is_chat_mismatch"]


@dataclass(frozen=True, slots=True)
class CodLine:
    unit_price_paisa: int
    quantity: int


def calculate_cod(
    lines: Iterable[CodLine], *, zone_charge_paisa: int = 0, discount_paisa: int = 0
) -> int | None:
    """The calculated COD, or ``None`` when any line has no usable catalog price."""
    total = 0
    count = 0
    for line in lines:
        if not isinstance(line.unit_price_paisa, int) or line.unit_price_paisa <= 0:
            return None
        if not isinstance(line.quantity, int) or line.quantity <= 0:
            return None
        total += line.unit_price_paisa * line.quantity
        count += 1
    if count == 0:
        return None
    if zone_charge_paisa < 0 or discount_paisa < 0:
        raise ValueError("zone charge and discount must be non-negative paisa")
    return max(0, total + zone_charge_paisa - discount_paisa)


def is_chat_mismatch(stated_paisa: int, calculated_paisa: int, *, zone_charge_paisa: int) -> bool:
    """Whether a stated COD is further from the calculation than the zone charge."""
    return abs(stated_paisa - calculated_paisa) > zone_charge_paisa
