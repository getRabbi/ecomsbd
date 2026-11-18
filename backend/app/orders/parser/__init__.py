"""Order text parsing.

Layer 1 is deterministic and ships here. Layer 2 — model-assisted extraction —
is :class:`app.orders.parser.enhanced.EnhancedOrderParser` (V4 WP0.3): behind a
flag and a monthly quota, and never a dependency of order entry (master spec
section 97). It is imported from its module, not here, because it needs the
database layer and this package must stay importable without it.
"""

from app.orders.parser.base import (
    FieldConfidence,
    OrderTextParser,
    ParsedItem,
    ParsedOrder,
)
from app.orders.parser.deterministic import DeterministicOrderParser

__all__ = [
    "DeterministicOrderParser",
    "FieldConfidence",
    "OrderTextParser",
    "ParsedItem",
    "ParsedOrder",
]
