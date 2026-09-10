"""The profit calculation.

Master spec section 18.1's formula, as one pure function over one input
dataclass. Deliberately free of database access: section 55 requires that *"old
order profit does not change after new rate rule"*, and the cheapest way to
prove a formula behaves is to be able to call it with numbers and compare the
answer to a fixture.

Section 1.5 also applies here more than anywhere: *AI must never be the source
of truth for profit.* This is arithmetic on integer paisa, and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.profit.models import ChargeKind, ChargeSource, ProfitQuality

__all__ = [
    "ChargeInput",
    "ProfitInput",
    "ProfitResult",
    "calculate_profit",
    "margin_basis_points",
]


@dataclass(frozen=True, slots=True)
class ChargeInput:
    """One cost, with where its figure came from."""

    kind: ChargeKind
    amount_paisa: int
    source: ChargeSource = ChargeSource.ESTIMATE


@dataclass(frozen=True, slots=True)
class ProfitInput:
    """Everything the formula needs about one parcel."""

    #: What the customer actually paid. For a partial delivery this is the
    #: delivered units only; for a return it is zero.
    realized_revenue_paisa: int

    #: Cost of the units that stayed with the customer.
    item_cost_paisa: int

    #: Cost of units that came back damaged and were written off. A return that
    #: comes back sellable costs nothing in goods — the stock is on the shelf.
    write_off_cost_paisa: int = 0

    charges: list[ChargeInput] = field(default_factory=list)

    #: Advertising apportioned to this parcel, and how.
    ad_cost_paisa: int = 0
    ad_source: ChargeSource = ChargeSource.UNKNOWN
    allocation_method: str | None = None
    allocation_version: int | None = None

    #: The discount the customer received. **Not subtracted** — see
    #: :func:`calculate_profit`.
    discount_paisa: int = 0

    #: How trustworthy the revenue figure is. Settled once a payout has been
    #: matched; the collected amount before that.
    revenue_source: ChargeSource = ChargeSource.SETTLED

    #: Whether the item cost came from a real snapshot.
    item_cost_source: ChargeSource = ChargeSource.SETTLED


@dataclass(frozen=True, slots=True)
class ProfitResult:
    """The answer, with its own honesty rating."""

    realized_revenue_paisa: int
    item_cost_paisa: int
    delivery_charge_paisa: int
    cod_fee_paisa: int
    return_charge_paisa: int
    packaging_paisa: int
    payment_fee_paisa: int
    other_cost_paisa: int
    ad_cost_paisa: int
    write_off_cost_paisa: int
    discount_paisa: int
    contribution_profit_paisa: int
    margin_basis_points: int | None
    quality: ProfitQuality
    missing_inputs: list[str]

    @property
    def total_cost_paisa(self) -> int:
        return (
            self.item_cost_paisa
            + self.delivery_charge_paisa
            + self.cod_fee_paisa
            + self.return_charge_paisa
            + self.packaging_paisa
            + self.payment_fee_paisa
            + self.other_cost_paisa
            + self.ad_cost_paisa
            + self.write_off_cost_paisa
        )

    @property
    def is_loss(self) -> bool:
        return self.contribution_profit_paisa < 0


#: Which input each charge kind lands in.
_KIND_FIELD: dict[ChargeKind, str] = {
    ChargeKind.DELIVERY: "delivery_charge_paisa",
    ChargeKind.COD_FEE: "cod_fee_paisa",
    ChargeKind.RETURN: "return_charge_paisa",
    ChargeKind.PACKAGING: "packaging_paisa",
    ChargeKind.PAYMENT_FEE: "payment_fee_paisa",
    ChargeKind.OTHER: "other_cost_paisa",
}

#: Seller-facing names for the inputs that can be missing, so section 135's
#: "Estimated (ad cost missing)" can name the actual gap.
_MISSING_LABEL: dict[str, str] = {
    "delivery_charge_paisa": "delivery charge",
    "cod_fee_paisa": "COD fee",
    "return_charge_paisa": "return charge",
    "packaging_paisa": "packaging",
    "payment_fee_paisa": "payment fee",
    "other_cost_paisa": "other cost",
    "ad_cost_paisa": "ad cost",
    "item_cost_paisa": "product cost",
    "realized_revenue_paisa": "collected amount",
}


def margin_basis_points(profit_paisa: int, revenue_paisa: int) -> int | None:
    """Margin against revenue, in basis points (100 = 1%).

    ``None`` when nothing was collected. A returned parcel has a real loss and
    no margin; reporting 0% would read as break-even, which is the opposite of
    what happened.
    """
    if revenue_paisa <= 0:
        return None
    # Integer arithmetic throughout: a float here would put a fraction of a
    # basis point into a figure the seller compares between products.
    return round(profit_paisa * 10_000 / revenue_paisa)


def calculate_profit(profit_input: ProfitInput) -> ProfitResult:
    """Contribution profit for one parcel (master spec section 18.1).

    ``discount_paisa`` is carried through as a memo and **not** subtracted. The
    discount is already inside ``realized_revenue_paisa`` — that field is what
    the customer handed over, after the discount. Subtracting it again is a
    double count that would quietly understate every discounted order's profit,
    which is exactly the kind of silent error section 1.1 exists to prevent.

    Section 17.9 is honoured by construction: a returned parcel has zero
    revenue and can still carry a return charge and packaging, so the result is
    negative. Nothing clamps it.
    """
    totals: dict[str, int] = dict.fromkeys(_KIND_FIELD.values(), 0)
    # Revenue and product cost are the two terms profit is *made of*; the rest
    # adjust it. An unknown among that pair leaves nothing worth showing, so
    # they are weighed separately when rating the figure.
    essential: list[ChargeSource] = [
        profit_input.revenue_source,
        profit_input.item_cost_source,
    ]
    peripheral: list[ChargeSource] = []
    missing: list[str] = []

    for charge in profit_input.charges:
        totals[_KIND_FIELD[charge.kind]] += charge.amount_paisa
        peripheral.append(charge.source)
        if charge.source is ChargeSource.UNKNOWN:
            missing.append(_MISSING_LABEL[_KIND_FIELD[charge.kind]])

    peripheral.append(profit_input.ad_source)
    if profit_input.ad_source is ChargeSource.UNKNOWN:
        missing.append(_MISSING_LABEL["ad_cost_paisa"])
    if profit_input.item_cost_source is ChargeSource.UNKNOWN:
        missing.append(_MISSING_LABEL["item_cost_paisa"])
    if profit_input.revenue_source is ChargeSource.UNKNOWN:
        missing.append(_MISSING_LABEL["realized_revenue_paisa"])

    cost = (
        profit_input.item_cost_paisa
        + profit_input.write_off_cost_paisa
        + sum(totals.values())
        + profit_input.ad_cost_paisa
    )
    profit = profit_input.realized_revenue_paisa - cost

    return ProfitResult(
        realized_revenue_paisa=profit_input.realized_revenue_paisa,
        item_cost_paisa=profit_input.item_cost_paisa,
        delivery_charge_paisa=totals["delivery_charge_paisa"],
        cod_fee_paisa=totals["cod_fee_paisa"],
        return_charge_paisa=totals["return_charge_paisa"],
        packaging_paisa=totals["packaging_paisa"],
        payment_fee_paisa=totals["payment_fee_paisa"],
        other_cost_paisa=totals["other_cost_paisa"],
        ad_cost_paisa=profit_input.ad_cost_paisa,
        write_off_cost_paisa=profit_input.write_off_cost_paisa,
        discount_paisa=profit_input.discount_paisa,
        contribution_profit_paisa=profit,
        margin_basis_points=margin_basis_points(profit, profit_input.realized_revenue_paisa),
        quality=ProfitQuality.from_sources(essential=essential, peripheral=peripheral),
        # Deduplicated but order-preserving, so the UI lists them the way the
        # formula reads.
        missing_inputs=list(dict.fromkeys(missing)),
    )
