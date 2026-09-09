"""Order text parsing.

Layer 1 is deterministic and ships here. Layer 2 (an LLM fallback) is an
interface only — master spec section 96 permits it below a confidence threshold,
and section 97 requires it to be optional, quota-limited and never a dependency
of normal order entry.
"""

from app.orders.parser.base import (
    EnhancedOrderParser,
    FieldConfidence,
    OrderTextParser,
    ParsedItem,
    ParsedOrder,
)
from app.orders.parser.deterministic import DeterministicOrderParser

__all__ = [
    "DeterministicOrderParser",
    "EnhancedOrderParser",
    "FieldConfidence",
    "OrderTextParser",
    "ParsedItem",
    "ParsedOrder",
]
