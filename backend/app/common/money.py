"""Money.

Master spec sections 17 and 32:

*   money is stored and computed as integer **paisa**, never a float;
*   the currency is explicitly ``BDT``;
*   percentages are basis points (100 bp = 1%);
*   the rounding rule is centralised, with explicit test cases.

Everything downstream of this module — receivables, payouts, reconciliation,
profit snapshots, the ledger — depends on these being exact. A float would make
``0.1 + 0.2`` land two paisa off across a month of settlements, and the seller
would be right and the app would be wrong.

Display follows Bangladeshi grouping (``৳2,84,500``), with a compact lakh form
(``৳2.84L``) for dashboard tiles, matching the UI prototype.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Self

__all__ = [
    "BDT",
    "Money",
    "apply_basis_points",
    "format_bdt",
    "format_bdt_compact",
    "group_bd",
    "paisa_from_taka",
    "sum_money",
    "taka_from_paisa",
]

BDT = "BDT"
TAKA_SIGN = "৳"

_PAISA_PER_TAKA = 100
_BASIS_POINTS_DENOMINATOR = 10_000


def _quantize_to_int(value: Decimal) -> int:
    """The single rounding rule for the whole system: half-up, away from zero.

    Half-up matches how a seller reads an invoice and how courier statements are
    printed. Python's default banker's rounding would disagree with the provider
    on exactly the .5 cases that show up in COD percentage fees.
    """
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def paisa_from_taka(value: Decimal | int | str) -> int:
    """Convert taka to integer paisa.

    Accepts ``Decimal``/``int``/``str`` only. ``float`` is rejected: by the time
    a float reaches here the value may already be wrong.
    """
    if isinstance(value, float):
        raise TypeError("Refusing to convert a float to paisa; pass a Decimal, int or str")
    try:
        decimal_value = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Not a valid taka amount: {value!r}") from exc
    return _quantize_to_int(decimal_value * _PAISA_PER_TAKA)


def taka_from_paisa(paisa: int) -> Decimal:
    """Exact taka value of a paisa amount, for display and export only."""
    return (Decimal(paisa) / _PAISA_PER_TAKA).quantize(Decimal("0.01"))


def apply_basis_points(paisa: int, basis_points: int) -> int:
    """Apply a basis-point rate to a paisa amount (100 bp = 1%)."""
    return _quantize_to_int(Decimal(paisa) * Decimal(basis_points) / _BASIS_POINTS_DENOMINATOR)


def _truncate2(value: Decimal) -> str:
    """Two decimal places, truncated toward zero, for compact display only."""
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def group_bd(digits: str) -> str:
    """Bangladeshi digit grouping: last three, then pairs. ``284500`` -> ``2,84,500``."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts: list[str] = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join([*parts, tail])


def format_bdt(paisa: int, *, show_paisa: bool | None = None, signed: bool = False) -> str:
    """Render paisa as ``৳1,250`` / ``-৳80`` / ``+৳1,405``.

    Master spec section 123: exact paisa may be hidden when zero but is retained
    internally, and a negative amount must be visually explicit.
    """
    negative = paisa < 0
    magnitude = abs(paisa)
    whole, fraction = divmod(magnitude, _PAISA_PER_TAKA)

    include_paisa = fraction != 0 if show_paisa is None else show_paisa
    body = group_bd(str(whole))
    if include_paisa:
        body = f"{body}.{fraction:02d}"

    if negative:
        return f"-{TAKA_SIGN}{body}"
    if signed:
        return f"+{TAKA_SIGN}{body}"
    return f"{TAKA_SIGN}{body}"


def format_bdt_compact(paisa: int) -> str:
    """Dashboard-tile form: ``৳87,450`` under a lakh, ``৳2.84L`` above it.

    Truncated rather than rounded, so the abbreviation never claims a larger
    figure than the exact amount behind it. Matches the UI prototype.
    """
    negative = paisa < 0
    taka = abs(paisa) // _PAISA_PER_TAKA
    if taka >= 10_000_000:
        body = f"{TAKA_SIGN}{_truncate2(Decimal(taka) / 10_000_000)}Cr"
    elif taka >= 100_000:
        body = f"{TAKA_SIGN}{_truncate2(Decimal(taka) / 100_000)}L"
    elif taka >= 1_000:
        body = f"{TAKA_SIGN}{group_bd(str(taka))}"
    else:
        body = f"{TAKA_SIGN}{taka}"
    return f"-{body}" if negative else body


@dataclass(frozen=True, slots=True, order=False)
class Money:
    """An exact amount of money.

    Immutable, so a value handed to the profit engine cannot be mutated behind a
    snapshot's back. Arithmetic between different currencies raises rather than
    coercing.
    """

    paisa: int
    currency: str = BDT

    def __post_init__(self) -> None:
        if not isinstance(self.paisa, int) or isinstance(self.paisa, bool):
            raise TypeError(f"Money.paisa must be an int, got {type(self.paisa).__name__}")
        if self.currency != BDT:
            raise ValueError(f"Unsupported currency {self.currency!r}; only {BDT} is supported")

    # ------------------------------------------------------------ builders --

    @classmethod
    def zero(cls, currency: str = BDT) -> Self:
        return cls(0, currency)

    @classmethod
    def from_taka(cls, value: Decimal | int | str, currency: str = BDT) -> Self:
        return cls(paisa_from_taka(value), currency)

    # ---------------------------------------------------------- properties --

    @property
    def taka(self) -> Decimal:
        return taka_from_paisa(self.paisa)

    @property
    def is_zero(self) -> bool:
        return self.paisa == 0

    @property
    def is_negative(self) -> bool:
        return self.paisa < 0

    # ---------------------------------------------------------- arithmetic --

    def _check(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(f"Cannot combine {self.currency} and {other.currency} amounts")

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.paisa + other.paisa, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.paisa - other.paisa, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.paisa, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.paisa), self.currency)

    def __mul__(self, factor: int) -> Money:
        if not isinstance(factor, int) or isinstance(factor, bool):
            raise TypeError(
                "Money can only be multiplied by an int (use of_basis_points otherwise)"
            )
        return Money(self.paisa * factor, self.currency)

    __rmul__ = __mul__

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.paisa < other.paisa

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.paisa <= other.paisa

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self.paisa > other.paisa

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self.paisa >= other.paisa

    def of_basis_points(self, basis_points: int) -> Money:
        """A basis-point share of this amount, rounded by the central rule."""
        return Money(apply_basis_points(self.paisa, basis_points), self.currency)

    def basis_points_of(self, total: Money) -> int:
        """This amount expressed as basis points of ``total``. Zero total -> 0 bp."""
        self._check(total)
        if total.paisa == 0:
            return 0
        return _quantize_to_int(
            Decimal(self.paisa) * _BASIS_POINTS_DENOMINATOR / Decimal(total.paisa)
        )

    def allocate(self, weights: list[int]) -> list[Money]:
        """Split this amount across ``weights`` losing no paisa.

        Largest-remainder distribution: the parts always sum back to the exact
        original amount, which is what makes ad-cost allocation and partial
        delivery splits reconcile to the penny.
        """
        if not weights:
            raise ValueError("allocate() needs at least one weight")
        if any(w < 0 for w in weights):
            raise ValueError("allocate() weights must be non-negative")

        total_weight = sum(weights)
        if total_weight == 0:
            # Degenerate case: spread evenly rather than dropping the money.
            weights = [1] * len(weights)
            total_weight = len(weights)

        # Work on the magnitude so a negative total (a refund, a reversal)
        # distributes exactly like the positive amount it reverses.
        sign = -1 if self.paisa < 0 else 1
        magnitude = abs(self.paisa)

        shares = [magnitude * weight // total_weight for weight in weights]
        remainders = [(magnitude * weight) % total_weight for weight in weights]
        leftover = magnitude - sum(shares)

        # Largest remainder first; index breaks ties so the split is stable
        # across runs and reproducible in a golden financial fixture.
        order = sorted(range(len(weights)), key=lambda i: (-remainders[i], i))
        for position in range(leftover):
            shares[order[position]] += 1

        return [Money(sign * share, self.currency) for share in shares]

    # ------------------------------------------------------------- display --

    def format(self, *, show_paisa: bool | None = None, signed: bool = False) -> str:
        return format_bdt(self.paisa, show_paisa=show_paisa, signed=signed)

    def format_compact(self) -> str:
        return format_bdt_compact(self.paisa)

    def __str__(self) -> str:
        return self.format()

    def __repr__(self) -> str:
        return f"Money({self.paisa}, {self.currency!r})"

    # ---------------------------------------------------------- interchange --

    def as_dict(self) -> dict[str, Any]:
        """Wire representation. Always paisa; the client never parses a string."""
        return {"amount_paisa": self.paisa, "currency": self.currency}


def sum_money(amounts: list[Money], currency: str = BDT) -> Money:
    """Sum a list of amounts, returning zero for an empty list."""
    total = Money.zero(currency)
    for amount in amounts:
        total = total + amount
    return total
