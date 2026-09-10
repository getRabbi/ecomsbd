"""Scoring a payout line against the parcels it might be paying for.

Master spec section 82, which exists to *replace unsafe greedy-only logic*:
"oldest-first may be used only as a suggestion when no stronger reference
exists." The weights below are the section's, used verbatim.

Section 112 states the governing preference plainly: **a reconciliation product
optimises precision before recall.** Missing an auto-match costs a seller a
click. Wrongly settling ৳14,050 against the wrong parcel costs them their
belief that the numbers are true, and there is no click that fixes that.

So the rules here are asymmetric on purpose. A single exact reference is enough
to match automatically. Everything softer — a matching amount, a plausible
date, a tie between two candidates — produces a *suggestion* and stops.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.consignments.models import Consignment, ConsignmentStatus
from app.money.models import CodReceivable, ReceivableStatus
from app.payouts.models import MatchConfidence, PayoutLine

__all__ = [
    "SCORE_WEIGHTS",
    "Candidate",
    "MatchDecision",
    "ScoringConfig",
    "decide",
    "score_candidate",
]


#: Section 82's table, as data. Keeping the weights here rather than inline
#: means a test can assert them against the spec, and a change to one is a
#: change to a visible constant rather than to a buried expression.
SCORE_WEIGHTS: dict[str, int] = {
    "exact_consignment_id": 100,
    "exact_tracking_code": 100,
    "exact_merchant_reference": 100,
    "exact_amount": 35,
    "amount_within_tolerance": 20,
    "delivery_date_in_window": 15,
    "same_phone": 10,
    "order_number_fragment": 20,
}

#: References strong enough to identify a parcel on their own.
_EXACT_REFERENCE_SIGNALS = frozenset(
    {"exact_consignment_id", "exact_tracking_code", "exact_merchant_reference"}
)


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    """The knobs section 16 and 81.5 require to be configurable and tested."""

    #: Absolute tolerance on an amount comparison. Providers round and deduct
    #: in small ways; ৳1 of slack avoids a case for every parcel.
    amount_tolerance_paisa: int = 100

    #: Proportional tolerance, in basis points. 100 = 1%.
    amount_tolerance_basis_points: int = 100

    #: How far after delivery a payment may plausibly arrive.
    delivery_window_days: int = 45

    #: The score a unique candidate must reach to be applied without a human.
    #: Set at the value of one exact reference: nothing softer auto-matches,
    #: which is section 82's "amount-only match → never auto-match" made
    #: structural rather than conditional.
    auto_match_threshold: int = 100

    #: Two candidates within this many points of each other are a tie, and a
    #: tie always goes to a human (section 82: "tie/near-tie → manual review").
    tie_margin: int = 20

    def tolerance_for(self, amount_paisa: int) -> int:
        """The slack allowed when comparing to ``amount_paisa``.

        The larger of the flat and proportional tolerances, so small parcels
        get a sensible floor and large ones scale.
        """
        proportional = abs(amount_paisa) * self.amount_tolerance_basis_points // 10_000
        return max(self.amount_tolerance_paisa, proportional)


@dataclass(frozen=True, slots=True)
class Candidate:
    """One parcel a payout line might be paying for, and how well it fits."""

    receivable_id: uuid.UUID
    consignment_id: uuid.UUID
    merchant_reference: str
    outstanding_paisa: int
    score: int
    signals: list[str] = field(default_factory=list)
    #: Set when the candidate is disqualified outright. A rejected candidate is
    #: kept rather than filtered away so the seller can see it was considered.
    rejected_because: str | None = None

    @property
    def is_eligible(self) -> bool:
        return self.rejected_because is None

    @property
    def has_exact_reference(self) -> bool:
        return any(signal in _EXACT_REFERENCE_SIGNALS for signal in self.signals)

    def as_dict(self) -> dict:
        return {
            "receivable_id": str(self.receivable_id),
            "consignment_id": str(self.consignment_id),
            "merchant_reference": self.merchant_reference,
            "outstanding_paisa": self.outstanding_paisa,
            "score": self.score,
            "signals": list(self.signals),
            "rejected_because": self.rejected_because,
        }


@dataclass(frozen=True, slots=True)
class MatchDecision:
    """What to do with a line, and why."""

    confidence: MatchConfidence
    chosen: Candidate | None
    considered: list[Candidate]
    reason: str

    @property
    def is_automatic(self) -> bool:
        return self.chosen is not None and self.confidence in (
            MatchConfidence.EXACT,
            MatchConfidence.HIGH,
        )


def _normalize(value: str | None) -> str:
    return (value or "").strip().casefold()


def score_candidate(
    line: PayoutLine,
    receivable: CodReceivable,
    consignment: Consignment,
    *,
    config: ScoringConfig,
    customer_phone_last4: str | None = None,
) -> Candidate:
    """Score one parcel against one statement line.

    Rejections come first and are absolute. A parcel that is already settled,
    or that came back, cannot be what this money is for — section 82 lists both
    as reject conditions, and scoring them anyway would let a high amount match
    override the fact that the parcel does not qualify.
    """
    signals: list[str] = []
    score = 0
    rejected: str | None = None

    if receivable.receivable_status is ReceivableStatus.SETTLED:
        rejected = "Already settled"
    elif receivable.receivable_status is ReceivableStatus.WRITTEN_OFF:
        rejected = "Written off"
    elif consignment.consignment_status in (
        ConsignmentStatus.RETURNED,
        ConsignmentStatus.CANCELLED,
    ):
        # Terminal return/cancel conflict. Money for a parcel that came back is
        # not a settlement; it is something to ask the provider about.
        rejected = f"Parcel was {consignment.consignment_status}"
    elif receivable.outstanding_paisa <= 0:
        rejected = "Nothing outstanding"

    # --- references ---------------------------------------------------------

    if line.provider_consignment_id and _normalize(line.provider_consignment_id) == _normalize(
        consignment.provider_consignment_id
    ):
        score += SCORE_WEIGHTS["exact_consignment_id"]
        signals.append("exact_consignment_id")

    if line.tracking_code and _normalize(line.tracking_code) == _normalize(
        consignment.tracking_code
    ):
        score += SCORE_WEIGHTS["exact_tracking_code"]
        signals.append("exact_tracking_code")

    line_reference = _normalize(line.merchant_reference)
    parcel_reference = _normalize(consignment.merchant_reference)
    if line_reference and line_reference == parcel_reference:
        score += SCORE_WEIGHTS["exact_merchant_reference"]
        signals.append("exact_merchant_reference")
    elif (
        line_reference
        and parcel_reference
        and _shares_number_fragment(line_reference, parcel_reference)
    ):
        # A partial reference is a hint, not an identification: statements
        # truncate, and two order numbers from the same day share a prefix.
        score += SCORE_WEIGHTS["order_number_fragment"]
        signals.append("order_number_fragment")

    # --- amount -------------------------------------------------------------

    expected = receivable.outstanding_paisa
    if line.amount_paisa and expected:
        difference = abs(line.amount_paisa - expected)
        if difference == 0:
            score += SCORE_WEIGHTS["exact_amount"]
            signals.append("exact_amount")
        elif difference <= config.tolerance_for(expected):
            score += SCORE_WEIGHTS["amount_within_tolerance"]
            signals.append("amount_within_tolerance")

    # --- circumstantial -----------------------------------------------------

    if line.delivered_on and _within_window(
        line.delivered_on, receivable, config.delivery_window_days
    ):
        score += SCORE_WEIGHTS["delivery_date_in_window"]
        signals.append("delivery_date_in_window")

    if (
        line.customer_phone_last4
        and customer_phone_last4
        and line.customer_phone_last4 == customer_phone_last4
    ):
        score += SCORE_WEIGHTS["same_phone"]
        signals.append("same_phone")

    return Candidate(
        receivable_id=receivable.id,
        consignment_id=consignment.id,
        merchant_reference=consignment.merchant_reference,
        outstanding_paisa=receivable.outstanding_paisa,
        score=score,
        signals=signals,
        rejected_because=rejected,
    )


def decide(candidates: list[Candidate], *, config: ScoringConfig) -> MatchDecision:
    """Choose a candidate, or refuse to.

    Section 82's rules, in order:

    * an exact unique reference auto-matches;
    * a unique candidate at or above the threshold may auto-match;
    * a tie or near-tie goes to a human;
    * an amount-only match never auto-matches.

    The last is enforced by the threshold rather than by a special case: the
    heaviest non-reference signal is worth 35, and three of them together still
    fall short of 100.
    """
    eligible = [
        candidate for candidate in candidates if candidate.is_eligible and candidate.score > 0
    ]
    if not eligible:
        return MatchDecision(
            confidence=MatchConfidence.MANUAL_REQUIRED,
            chosen=None,
            considered=candidates,
            reason="Nothing in this shop matches that line",
        )

    ranked = sorted(eligible, key=lambda candidate: candidate.score, reverse=True)
    best = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None

    exact = [candidate for candidate in ranked if candidate.has_exact_reference]
    if len(exact) == 1:
        return MatchDecision(
            confidence=MatchConfidence.EXACT,
            chosen=exact[0],
            considered=candidates,
            reason="Matched on an exact reference",
        )
    if len(exact) > 1:
        # Two parcels claiming the same reference is a data problem, not a
        # matching problem, and picking one would bury it.
        return MatchDecision(
            confidence=MatchConfidence.MANUAL_REQUIRED,
            chosen=None,
            considered=candidates,
            reason=f"{len(exact)} parcels share that reference",
        )

    if runner_up is not None and best.score - runner_up.score <= config.tie_margin:
        return MatchDecision(
            confidence=MatchConfidence.MANUAL_REQUIRED,
            chosen=None,
            considered=candidates,
            reason="Two parcels fit this line equally well",
        )

    if best.score >= config.auto_match_threshold:
        return MatchDecision(
            confidence=MatchConfidence.HIGH,
            chosen=best,
            considered=candidates,
            reason="One strong candidate",
        )

    return MatchDecision(
        confidence=(
            MatchConfidence.MEDIUM
            if best.score >= SCORE_WEIGHTS["exact_amount"]
            else MatchConfidence.MANUAL_REQUIRED
        ),
        chosen=best,
        considered=candidates,
        reason="Only a suggestion — check before applying",
    )


def _within_window(paid_for: date, receivable: CodReceivable, window_days: int) -> bool:
    """Whether a statement's delivery date is plausible for this receivable."""
    eligible_on = receivable.eligible_business_date
    if eligible_on is None:
        return False
    earliest = eligible_on - timedelta(days=2)
    latest = eligible_on + timedelta(days=window_days)
    return earliest <= paid_for <= latest


def _shares_number_fragment(left: str, right: str) -> bool:
    """Whether two references share a long enough run of digits.

    Order numbers look like ``CP-20260910-0042``; the date part is shared by
    every order that day, so only the trailing sequence is discriminating.
    """
    left_digits = "".join(char for char in left if char.isdigit())
    right_digits = "".join(char for char in right if char.isdigit())
    if len(left_digits) < 4 or len(right_digits) < 4:
        return False
    return left_digits[-4:] == right_digits[-4:]
