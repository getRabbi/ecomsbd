"""Expected against actual, for one parcel.

The question a seller asks is three questions: *what should the courier have
paid me*, *what did it pay me*, and *what is different*. This module answers
them from records that already exist — the receivable (what was owed), the
statement lines and their adjustments (what arrived and what was kept), and
the charges on record for the parcel (what the courier was expected to keep).

Pure functions over those records. Nothing is written here, nothing is
estimated, and nothing computed here is ever read back as money: the ledger
stays the only financial truth, and these figures only explain it.

Two rules carry the design:

*   **A statement line is net.** ``amount_paisa`` is what the courier paid for
    the parcel; its adjustments are what it kept. The COD the courier
    collected is therefore the two added together — which is also how the
    Steadfast payment import records an unexplained gap, so both routes agree.
*   **Missing is not zero.** When no charge is on record for a parcel, the
    expected charge is *unknown*, and the expected payout falls back to the
    courier's own charge with ``charge_verified`` false. The COD comparison
    still happens; only the charge is unverified. Treating an unknown charge
    as ৳0 would turn every ordinary delivery fee into a "discrepancy".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from app.payouts.models import AdjustmentType, PayoutAdjustment, PayoutLine
from app.profit.models import ChargeKind, ChargeSource, ConsignmentCharge
from app.reconciliation.models import ItemStatus
from app.reconciliation.scoring import ScoringConfig

__all__ = [
    "COURIER_CHARGE_KINDS",
    "Evaluation",
    "ExpectedCharge",
    "LineEvidence",
    "evaluate_omitted",
    "evaluate_parcel",
    "evaluate_return",
    "evaluate_unplaced",
    "expected_charge_from",
]

#: The charges a courier keeps out of COD. Packaging and payment fees are the
#: seller's own costs and never appear on a courier statement.
COURIER_CHARGE_KINDS: frozenset[ChargeKind] = frozenset(
    {ChargeKind.DELIVERY, ChargeKind.COD_FEE, ChargeKind.RETURN}
)

#: Sources that describe what was *expected*. ``SETTLED`` is the courier's own
#: figure off a statement — the actual, not the expectation — and ``UNKNOWN``
#: is a recorded absence rather than a figure.
_EXPECTATION_SOURCES: frozenset[ChargeSource] = frozenset(
    {ChargeSource.BOOKED, ChargeSource.SELLER, ChargeSource.ESTIMATE}
)


@dataclass(frozen=True, slots=True)
class ExpectedCharge:
    amount_paisa: int
    #: The weakest source among the kinds summed, so a total built partly from
    #: an estimate is labelled an estimate.
    source: ChargeSource
    by_kind: dict[str, int] = field(default_factory=dict)


def expected_charge_from(charges: Iterable[ConsignmentCharge]) -> ExpectedCharge | None:
    """The charge a parcel was expected to incur, or ``None`` if nothing is known.

    Superseded rows are included on purpose: once a statement's settled figure
    supersedes a booking quote, the quote is still what was *expected*, and it
    is exactly the number the settled one should be compared with. Per kind the
    most trustworthy expectation wins (section 85's order), then the latest.
    """
    best: dict[ChargeKind, ConsignmentCharge] = {}
    for charge in charges:
        try:
            kind = ChargeKind(charge.kind)
            source = ChargeSource(charge.source)
        except ValueError:
            continue
        if kind not in COURIER_CHARGE_KINDS or source not in _EXPECTATION_SOURCES:
            continue
        current = best.get(kind)
        if current is None:
            best[kind] = charge
            continue
        current_rank = ChargeSource(current.source).rank
        if source.rank < current_rank or (
            source.rank == current_rank and charge.occurred_at > current.occurred_at
        ):
            best[kind] = charge

    if not best:
        return None
    weakest = max((ChargeSource(charge.source) for charge in best.values()), key=lambda s: s.rank)
    return ExpectedCharge(
        amount_paisa=sum(charge.amount_paisa for charge in best.values()),
        source=weakest,
        by_kind={str(kind): charge.amount_paisa for kind, charge in best.items()},
    )


@dataclass(slots=True)
class LineEvidence:
    """One statement line with the adjustments that belong to it."""

    line: PayoutLine
    adjustments: list[PayoutAdjustment] = field(default_factory=list)

    @property
    def charge_paisa(self) -> int:
        """What the courier kept for this parcel, net of anything it added."""
        kept = sum(a.amount_paisa for a in self.adjustments if a.is_deduction)
        added = sum(a.amount_paisa for a in self.adjustments if not a.is_deduction)
        return kept - added

    @property
    def pending_paisa(self) -> int:
        """Deductions not yet accepted into the ledger."""
        return sum(
            a.amount_paisa for a in self.adjustments if a.is_deduction and a.accepted_at is None
        )

    @property
    def is_charge_only(self) -> bool:
        return self.line.amount_paisa == 0 and self.charge_paisa > 0

    @property
    def actual_cod_paisa(self) -> int:
        """What the courier collected, as the statement implies it.

        A charge-only row collected nothing: the charge came out of other money.
        """
        if self.line.amount_paisa <= 0:
            return 0
        return self.line.amount_paisa + self.charge_paisa

    @property
    def actual_net_paisa(self) -> int:
        """What this row put in (or, for a charge-only row, took from) the payout."""
        if self.line.amount_paisa <= 0:
            return -self.charge_paisa
        return self.line.amount_paisa

    def charges_by_type(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for adjustment in self.adjustments:
            signed = (
                adjustment.amount_paisa if adjustment.is_deduction else -adjustment.amount_paisa
            )
            totals[adjustment.type] = totals.get(adjustment.type, 0) + signed
        return totals

    @property
    def has_unknown_deduction(self) -> bool:
        return any(a.type == str(AdjustmentType.UNKNOWN_DEDUCTION) for a in self.adjustments)


@dataclass(slots=True)
class Evaluation:
    status: ItemStatus
    expected_cod_paisa: int | None
    actual_cod_paisa: int
    expected_charge_paisa: int | None
    expected_charge_source: str | None
    actual_charge_paisa: int
    expected_net_paisa: int | None
    actual_net_paisa: int
    difference_paisa: int | None
    charges_pending_paisa: int
    line_count: int
    detail: dict

    @property
    def charge_gap_paisa(self) -> int | None:
        if self.expected_charge_paisa is None:
            return None
        return self.actual_charge_paisa - self.expected_charge_paisa


def _charges_detail(lines: list[LineEvidence]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for evidence in lines:
        for kind, amount in evidence.charges_by_type().items():
            merged[kind] = merged.get(kind, 0) + amount
    return merged


def evaluate_parcel(
    *,
    expected_cod_paisa: int,
    lines: list[LineEvidence],
    expected_charge: ExpectedCharge | None,
    config: ScoringConfig,
) -> Evaluation:
    """Compare everything the statements say about one delivered parcel.

    All lines tied to the parcel are summed, so a parcel paid in two parts is
    judged on the total — the second half arriving turns ``PARTIAL`` into
    ``MATCHED`` rather than leaving two half-mismatches behind.
    """
    applied = [evidence for evidence in lines if evidence.line.line_status.is_applied]
    actual_cod = sum(evidence.actual_cod_paisa for evidence in lines)
    actual_charge = sum(evidence.charge_paisa for evidence in lines)
    actual_net = sum(evidence.actual_net_paisa for evidence in lines)

    charge_basis = expected_charge.amount_paisa if expected_charge else actual_charge
    expected_net = expected_cod_paisa - charge_basis
    difference = actual_net - expected_net

    cod_gap = actual_cod - expected_cod_paisa
    cod_tolerance = config.tolerance_for(expected_cod_paisa)
    reason: str
    if not applied and expected_cod_paisa > 0:
        status = ItemStatus.MISSING_COD
        reason = "charges_without_cod"
    elif cod_gap > cod_tolerance:
        status = ItemStatus.AMOUNT_MISMATCH
        reason = "cod_over"
    elif cod_gap < -cod_tolerance:
        # One short payment is a mismatch. Several that do not yet add up is a
        # parcel being paid in parts.
        status = ItemStatus.PARTIAL if len(applied) > 1 else ItemStatus.AMOUNT_MISMATCH
        reason = "cod_short"
    elif expected_charge is not None and abs(
        actual_charge - expected_charge.amount_paisa
    ) > config.tolerance_for(expected_charge.amount_paisa):
        status = ItemStatus.CHARGE_MISMATCH
        reason = "charge_differs"
    else:
        status = ItemStatus.MATCHED
        reason = "agrees"

    return Evaluation(
        status=status,
        expected_cod_paisa=expected_cod_paisa,
        actual_cod_paisa=actual_cod,
        expected_charge_paisa=expected_charge.amount_paisa if expected_charge else None,
        expected_charge_source=str(expected_charge.source) if expected_charge else None,
        actual_charge_paisa=actual_charge,
        expected_net_paisa=expected_net,
        actual_net_paisa=actual_net,
        difference_paisa=difference,
        charges_pending_paisa=sum(evidence.pending_paisa for evidence in lines),
        line_count=len(lines),
        detail={
            "reason": reason,
            "charge_verified": expected_charge is not None,
            "cod_gap_paisa": cod_gap,
            "cod_tolerance_paisa": cod_tolerance,
            "actual_charges": _charges_detail(lines),
            "expected_charges": dict(expected_charge.by_kind) if expected_charge else {},
            "unknown_deduction": any(evidence.has_unknown_deduction for evidence in lines),
            "lines": [str(evidence.line.id) for evidence in lines],
        },
    )


def evaluate_omitted(
    *, expected_cod_paisa: int, expected_charge: ExpectedCharge | None
) -> Evaluation:
    """A delivered parcel a payout should plausibly have included, and did not."""
    charge = expected_charge.amount_paisa if expected_charge else None
    expected_net = expected_cod_paisa - (charge or 0)
    return Evaluation(
        status=ItemStatus.MISSING_COD,
        expected_cod_paisa=expected_cod_paisa,
        actual_cod_paisa=0,
        expected_charge_paisa=charge,
        expected_charge_source=str(expected_charge.source) if expected_charge else None,
        actual_charge_paisa=0,
        expected_net_paisa=expected_net,
        actual_net_paisa=0,
        difference_paisa=-expected_net,
        charges_pending_paisa=0,
        line_count=0,
        detail={
            "reason": "not_in_payout",
            "charge_verified": expected_charge is not None,
            "expected_charges": dict(expected_charge.by_kind) if expected_charge else {},
            "actual_charges": {},
            "lines": [],
        },
    )


def evaluate_return(
    evidence: LineEvidence, *, expected_charge: ExpectedCharge | None, config: ScoringConfig
) -> tuple[Evaluation, bool]:
    """A charge-only row for a parcel that came back.

    Returns the evaluation and whether the charge disagrees with the one on
    record. With nothing on record there is nothing to disagree with.
    """
    actual_charge = evidence.charge_paisa
    expected = expected_charge.amount_paisa if expected_charge else None
    expected_net = -(expected if expected is not None else actual_charge)
    actual_net = evidence.actual_net_paisa
    mismatch = expected is not None and abs(actual_charge - expected) > config.tolerance_for(
        expected
    )
    return (
        Evaluation(
            status=ItemStatus.RETURN_ADJUSTMENT,
            expected_cod_paisa=0,
            actual_cod_paisa=0,
            expected_charge_paisa=expected,
            expected_charge_source=str(expected_charge.source) if expected_charge else None,
            actual_charge_paisa=actual_charge,
            expected_net_paisa=expected_net,
            actual_net_paisa=actual_net,
            difference_paisa=actual_net - expected_net,
            charges_pending_paisa=evidence.pending_paisa,
            line_count=1,
            detail={
                "reason": "return_charge_differs" if mismatch else "return_charge",
                "charge_verified": expected_charge is not None,
                "expected_charges": dict(expected_charge.by_kind) if expected_charge else {},
                "actual_charges": evidence.charges_by_type(),
                "lines": [str(evidence.line.id)],
            },
        ),
        mismatch,
    )


def evaluate_unplaced(evidence: LineEvidence, *, status: ItemStatus, reason: str) -> Evaluation:
    """A row with no parcel behind it: unmatched, awaiting review, or a duplicate.

    There is nothing to expect, so expected and difference stay null. Its money
    is reported separately as unplaced rather than as a surplus.
    """
    return Evaluation(
        status=status,
        expected_cod_paisa=None,
        actual_cod_paisa=evidence.actual_cod_paisa,
        expected_charge_paisa=None,
        expected_charge_source=None,
        actual_charge_paisa=evidence.charge_paisa,
        expected_net_paisa=None,
        actual_net_paisa=evidence.actual_net_paisa,
        difference_paisa=None,
        charges_pending_paisa=0,
        line_count=1,
        detail={
            "reason": reason,
            "charge_verified": False,
            "actual_charges": evidence.charges_by_type(),
            "expected_charges": {},
            "lines": [str(evidence.line.id)],
        },
    )
