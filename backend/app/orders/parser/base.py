"""Parser contract.

Master spec section 96 fixes the response shape. Two rules from it drive the
design:

*   **the deterministic parser runs first**, and an LLM is only consulted below
    a confidence threshold or when the seller explicitly asks;
*   **a parse failure returns the seller's original text**, never loses it.

The second rule is why :class:`ParsedOrder` always carries ``source_text``. A
seller who pasted a Messenger conversation and got nothing useful back must
still be able to read what they pasted and fix it by hand.

Confidence is per field, not per parse, because these inputs are uneven: a
message often contains an unmistakable phone number and a completely ambiguous
address. Reporting one number for the whole parse would hide that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "CONFIDENCE_CONFIRM_THRESHOLD",
    "MAX_PARSE_INPUT_LENGTH",
    "EnhancedOrderParser",
    "FieldConfidence",
    "OrderTextParser",
    "ParsedItem",
    "ParsedOrder",
]

#: Section 96: "max input length". A pasted Messenger thread can be enormous;
#: this caps the work and the storage without truncating a realistic order.
MAX_PARSE_INPUT_LENGTH = 8_000

#: Below this a field is presented as a suggestion needing attention rather
#: than a filled-in value. It is *not* a threshold for accepting anything
#: automatically — every parsed order is confirmed by the seller regardless.
CONFIDENCE_CONFIRM_THRESHOLD = 0.6


class FieldConfidence:
    """Named confidence levels.

    Deliberately coarse. A deterministic parser knows whether a pattern matched
    exactly, matched loosely, or was guessed from position; inventing a
    continuous score would imply a precision the rules do not have.
    """

    #: An unambiguous match: a valid phone, a labelled field, a lone amount.
    EXACT = 1.0
    #: A strong pattern with one plausible alternative.
    HIGH = 0.8
    #: A positional or heuristic guess that is usually right.
    MEDIUM = 0.5
    #: A weak guess. Shown to the seller flagged for review.
    LOW = 0.25
    #: Nothing found. The field is left empty rather than filled with a guess.
    NONE = 0.0


@dataclass(slots=True)
class ParsedItem:
    """One line item extracted from the text."""

    name: str
    quantity: int = 1
    size: str | None = None
    color: str | None = None
    unit_price_paisa: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "quantity": self.quantity,
            "size": self.size,
            "color": self.color,
            "unit_price_paisa": self.unit_price_paisa,
        }


@dataclass(slots=True)
class ParsedOrder:
    """The normalized parse result from master spec section 96."""

    #: Always populated, even on total failure.
    source_text: str

    customer_name: str | None = None
    #: Every phone found, in order of appearance. Section 8 forbids silently
    #: choosing among several, so this is a list and the caller must ask.
    phones: list[str] = field(default_factory=list)
    #: Set only when exactly one phone was found.
    selected_phone: str | None = None
    address: str | None = None
    items: list[ParsedItem] = field(default_factory=list)
    cod_amount_paisa: int | None = None
    notes: str | None = None

    confidence: dict[str, float] = field(
        default_factory=lambda: {
            "phone": FieldConfidence.NONE,
            "address": FieldConfidence.NONE,
            "items": FieldConfidence.NONE,
            "amount": FieldConfidence.NONE,
            "name": FieldConfidence.NONE,
        }
    )

    #: Which parser produced this, for diagnostics and cost accounting.
    parser: str = "deterministic"
    #: Human-readable notes about what the parser could not resolve.
    warnings: list[str] = field(default_factory=list)

    @property
    def needs_phone_selection(self) -> bool:
        """Several numbers found and none chosen. Section 8: the seller picks."""
        return len(self.phones) > 1 and self.selected_phone is None

    @property
    def is_low_confidence(self) -> bool:
        """Whether an enhanced parse would be worth offering (section 96)."""
        scored = [
            self.confidence.get("phone", 0.0),
            self.confidence.get("address", 0.0),
            self.confidence.get("items", 0.0),
        ]
        return sum(scored) / len(scored) < CONFIDENCE_CONFIRM_THRESHOLD

    @property
    def is_empty(self) -> bool:
        return not (self.phones or self.items or self.address or self.cod_amount_paisa)

    def to_json(self) -> dict[str, Any]:
        return {
            "customer_name": self.customer_name,
            "phones": list(self.phones),
            "selected_phone": self.selected_phone,
            "address": self.address,
            "items": [item.to_json() for item in self.items],
            "cod_amount_paisa": self.cod_amount_paisa,
            "notes": self.notes,
            "confidence": dict(self.confidence),
            "parser": self.parser,
            "warnings": list(self.warnings),
            "needs_phone_selection": self.needs_phone_selection,
            "is_low_confidence": self.is_low_confidence,
        }


@runtime_checkable
class OrderTextParser(Protocol):
    """Anything that turns pasted text into a :class:`ParsedOrder`."""

    name: str

    def parse(self, text: str) -> ParsedOrder: ...


class EnhancedOrderParser:
    """UNIMPLEMENTED. The Layer 2 LLM fallback from master spec section 96.

    Deliberately not implemented in Phase B, and deliberately not a silent
    pass-through to the deterministic parser either. Section 97 requires:

    *   a configured model and provider;
    *   per-tenant monthly quota enforced by entitlement (``ai_parse_monthly``);
    *   recorded cost per parse in ``ai_usage``;
    *   strict schema validation of the model's JSON;
    *   pasted text treated as **data, never instruction** — these inputs are
        attacker-controlled by construction, since anyone can send a seller a
        Messenger message.

    None of that exists yet. Raising here keeps the boundary honest: order entry
    works fully without AI (section 97: "AI outage must not block business
    operations"), and this class documents exactly what enabling it requires.
    """

    name = "enhanced"

    def parse(self, text: str) -> ParsedOrder:
        raise NotImplementedError(
            "The enhanced (LLM) parser is not implemented. Order entry works "
            "without it; see master spec sections 96 and 97 for the model, "
            "quota, cost-tracking and prompt-injection requirements that must "
            "be satisfied first."
        )
