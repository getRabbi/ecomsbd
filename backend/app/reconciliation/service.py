"""The reconciliation engine.

Master spec section 16 calls this "the primary paid feature", and section 112
says how to build it: *precision before recall*. Every decision here leans the
same way — when the evidence is short of certain, the engine produces a
suggestion and a case, and stops.

Entry points:

* :meth:`ReconciliationService.reconcile` matches a payout's lines against
  outstanding receivables and applies the ones that are certain;
* the same method with ``shadow=True`` runs the whole thing and changes
  nothing, which is section 112's shadow mode;
* :meth:`ReconciliationService.match_manually` applies a line because a person
  said so, recording who and why (section 81.7);
* :meth:`ReconciliationService.unmatch` reverses rather than deletes
  (section 81.8);
* :meth:`ReconciliationService.accept_charges` records the courier's deductions
  on a parcel in the ledger, once, because a person accepted them.

**V2.** After every change the engine rebuilds *reconciliation items*: for each
parcel a statement touched, what the courier should have paid, what it did pay,
and the difference (:mod:`app.reconciliation.evaluation`). Items explain the
ledger; they never move money and are never read back as money.

Three V2 safety properties:

* **Row identity across statements.** A row already paid in an earlier
  statement — same courier, same reference, same amount — is a duplicate, not a
  second payment, however the file around it changed.
* **Serialised money.** A reconcile run locks its payout, and every settlement
  locks its receivable, so two runs cannot settle the same parcel twice.
* **Resolution is an event.** Closing a case, reopening it, or the engine
  closing it because the money arrived, each appends a case event. Nothing
  about a resolution edits a ledger entry.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.pagination import Cursor, apply_cursor
from app.consignments.models import Consignment, ConsignmentItem, ConsignmentStatus
from app.core.clock import ensure_utc, utc_now
from app.core.context import current_context
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.customers.models import Customer
from app.ledger.models import LedgerEventType, LedgerSource
from app.ledger.service import LedgerService
from app.money.models import CodReceivable, ReceivableStatus
from app.money.service import ReceivableService
from app.orders.models import Order
from app.payouts.models import (
    AdjustmentType,
    MatchConfidence,
    Payout,
    PayoutAdjustment,
    PayoutLine,
    PayoutLineStatus,
    PayoutSource,
)
from app.payouts.service import PayoutService
from app.profit.models import ChargeKind, ChargeSource, ConsignmentCharge
from app.profit.service import ProfitService
from app.reconciliation.evaluation import (
    Evaluation,
    ExpectedCharge,
    LineEvidence,
    evaluate_omitted,
    evaluate_parcel,
    evaluate_return,
    evaluate_unplaced,
    expected_charge_from,
)
from app.reconciliation.models import (
    DISCREPANCY_STATUSES,
    CaseEvent,
    CaseEventAction,
    CaseKind,
    CasePriority,
    CaseStatus,
    ItemStatus,
    ReconciliationCase,
    ReconciliationItem,
    priority_for,
)
from app.reconciliation.scoring import (
    Candidate,
    MatchDecision,
    ScoringConfig,
    decide,
    score_candidate,
)

__all__ = [
    "STALE_IN_TRANSIT_DAYS",
    "ChargeAcceptance",
    "ReconciliationReport",
    "ReconciliationService",
    "ReconciliationSummary",
    "resolve_returned_not_restocked",
]

#: How long a parcel may sit in transit before it is worth asking about.
#: Section 87 forbids hard-coding "a return takes N days" for settlement; this
#: is an *alert* threshold, which the same section says to make configurable.
#: Default only — a provider median replaces it once there is history.
STALE_IN_TRANSIT_DAYS = 14

#: How long after delivery money may be outstanding before it becomes a case.
UNPAID_ALERT_DAYS = 10

#: A delivered parcel is "left out of a payout" only when it was delivered
#: comfortably before everything the payout covers. Two days of grace absorbs
#: a courier's cut-off; thirty bounds the look-back so one statement does not
#: raise a case for every old parcel in the shop.
OMISSION_GRACE_DAYS = 2
OMISSION_LOOKBACK_DAYS = 30
OMISSION_MAX_PER_PAYOUT = 500

#: Cases the engine may close on its own once the parcel is fully settled —
#: each is *about* money not having arrived, and it now has.
_SETTLED_CLOSES: frozenset[CaseKind] = frozenset(
    {CaseKind.DELIVERED_BUT_UNPAID, CaseKind.MISSING_COD, CaseKind.UNDERPAID}
)

#: Cases a person closes by accepting the courier's charge.
_ACCEPTANCE_CLOSES: frozenset[CaseKind] = frozenset(
    {CaseKind.CHARGE_MISMATCH, CaseKind.RETURN_CHARGE_MISMATCH, CaseKind.UNKNOWN_DEDUCTION}
)

_DEDUCTION_EVENTS: dict[AdjustmentType, LedgerEventType] = {
    AdjustmentType.COD_FEE: LedgerEventType.COD_FEE_APPLIED,
    AdjustmentType.DELIVERY_FEE: LedgerEventType.COURIER_CHARGE_APPLIED,
    AdjustmentType.RETURN_FEE: LedgerEventType.RETURN_FEE_APPLIED,
}

_PROFIT_KINDS: dict[AdjustmentType, ChargeKind] = {
    AdjustmentType.COD_FEE: ChargeKind.COD_FEE,
    AdjustmentType.DELIVERY_FEE: ChargeKind.DELIVERY,
    AdjustmentType.RETURN_FEE: ChargeKind.RETURN,
}

#: Lines tied to a parcel for the purposes of expected-vs-actual.
_LINKED_STATUSES = (
    str(PayoutLineStatus.MATCHED),
    str(PayoutLineStatus.MANUAL_MATCHED),
    str(PayoutLineStatus.CHARGE_ONLY),
)

#: Parameters per ``IN`` clause. Well under SQLite's and PostgreSQL's limits.
_CHUNK = 500


def _chunks[T](values: Sequence[T], size: int = _CHUNK) -> Iterable[Sequence[T]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _norm(value: str | None) -> str:
    return (value or "").strip().casefold()


@dataclass(slots=True)
class ReconciliationReport:
    """What a reconciliation run did, or would have done.

    The three counts are section 82's own summary format: *"38 exact matches /
    2 suggested matches / 1 unresolved"*.
    """

    payout_id: uuid.UUID
    shadow: bool
    exact_matches: int = 0
    suggested: int = 0
    unresolved: int = 0
    duplicates: int = 0
    charge_only: int = 0
    applied_paisa: int = 0
    cases_opened: int = 0
    decisions: list[tuple[uuid.UUID, MatchDecision]] = field(default_factory=list)

    @property
    def total_lines(self) -> int:
        return (
            self.exact_matches
            + self.suggested
            + self.unresolved
            + self.duplicates
            + self.charge_only
        )


@dataclass(slots=True)
class ReconciliationSummary:
    """Headline figures for the review screen, from the items table."""

    counts: dict[str, int]
    expected_paisa: int
    actual_paisa: int
    difference_paisa: int
    unmatched_paisa: int
    duplicate_paisa: int
    charges_pending_paisa: int
    open_cases: int
    open_case_paisa: int
    #: Summed ``difference_paisa`` per item status, so a caller can say how
    #: much sits in MISSING_COD or CHARGE_MISMATCH without a second query.
    difference_by_status: dict[str, int] = field(default_factory=dict)

    @property
    def matched(self) -> int:
        return self.counts.get(str(ItemStatus.MATCHED), 0)

    @property
    def discrepancies(self) -> int:
        return sum(self.counts.get(str(status), 0) for status in DISCREPANCY_STATUSES)

    @property
    def unmatched(self) -> int:
        return self.counts.get(str(ItemStatus.UNMATCHED), 0) + self.counts.get(
            str(ItemStatus.NEEDS_REVIEW), 0
        )


@dataclass(slots=True)
class ChargeAcceptance:
    accepted_paisa: int = 0
    adjustments: int = 0
    items: int = 0


@dataclass(slots=True)
class _Pool:
    """Every parcel a statement from one courier could be paying for.

    Loaded once per run rather than once per line, and indexed by the three
    exact references, so a line naming its parcel is scored against that
    parcel alone. A line with no exact hit is still scored against the whole
    pool, which is how suggestions are found.
    """

    pairs: list[tuple[CodReceivable, Consignment]]
    phones: dict[uuid.UUID, str | None]
    by_consignment_id: dict[str, list[int]] = field(default_factory=dict)
    by_tracking: dict[str, list[int]] = field(default_factory=dict)
    by_reference: dict[str, list[int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for index, (_receivable, consignment) in enumerate(self.pairs):
            for key, bucket in (
                (consignment.provider_consignment_id, self.by_consignment_id),
                (consignment.tracking_code, self.by_tracking),
                (consignment.merchant_reference, self.by_reference),
            ):
                if _norm(key):
                    bucket.setdefault(_norm(key), []).append(index)

    def exact_for(self, line: PayoutLine) -> list[int]:
        hits: set[int] = set()
        for key, bucket in (
            (line.provider_consignment_id, self.by_consignment_id),
            (line.tracking_code, self.by_tracking),
            (line.merchant_reference, self.by_reference),
        ):
            if _norm(key):
                hits.update(bucket.get(_norm(key), []))
        return sorted(hits)


class ReconciliationService:
    """Matches money to parcels, and raises a case when it cannot."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        receivables: ReceivableService | None = None,
        payouts: PayoutService | None = None,
        config: ScoringConfig | None = None,
    ) -> None:
        self._db = session
        self._receivables = receivables or ReceivableService(session)
        self._payouts = payouts or PayoutService(session)
        self._config = config or ScoringConfig()
        self._ledger = LedgerService(session)
        self._profit = ProfitService(session)

    @property
    def config(self) -> ScoringConfig:
        return self._config

    @property
    def _postgres(self) -> bool:
        bind = self._db.bind
        return bind is not None and bind.dialect.name == "postgresql"

    # ---------------------------------------------------------- reconciling --

    async def reconcile(
        self, payout_id: uuid.UUID, *, shadow: bool = False
    ) -> ReconciliationReport:
        """Match a payout's lines and apply the certain ones.

        With ``shadow=True`` nothing is written: no settlement, no case, no
        ledger entry. Section 112 requires exactly this before a new matching
        rule is trusted — run it, compare it with what people did by hand,
        measure the false-match risk, and only then let it move money.
        """
        payout = await (self._payouts.get(payout_id) if shadow else self._lock_payout(payout_id))
        lines = await self._payouts.lines(payout_id, limit=None)
        report = ReconciliationReport(payout_id=payout_id, shadow=shadow)

        open_lines = [
            line
            for line in lines
            if not (line.line_status.is_resolved or line.line_status is PayoutLineStatus.REVERSED)
        ]
        evidence = await self._evidence_for(open_lines)
        pool = await self._pool(payout.provider)
        prior = await self._prior_applications(payout, open_lines)

        for line in open_lines:
            if line.line_status is PayoutLineStatus.UNMAPPABLE:
                report.unresolved += 1
                if not shadow:
                    report.cases_opened += await self._open_case(
                        kind=CaseKind.UNMAPPABLE_PAYOUT,
                        subject_type="payout_line",
                        subject_id=line.id,
                        amount_paisa=line.amount_paisa,
                        summary=(f"Row {line.row_number} of this statement could not be read"),
                        detail={"raw": line.raw},
                        payout_id=payout_id,
                        payout_line_id=line.id,
                    )
                continue

            earlier = prior.get(line.id)
            if earlier is not None:
                report.duplicates += 1
                if not shadow:
                    report.cases_opened += await self._mark_prior_duplicate(line, earlier, payout)
                continue

            line_evidence = evidence.get(line.id) or LineEvidence(line=line)
            if line.amount_paisa == 0:
                # Never scored: a zero line has nothing to settle, and an exact
                # reference would otherwise try to apply ৳0 to a parcel.
                if await self._charge_only(line, line_evidence, payout, shadow=shadow):
                    report.charge_only += 1
                    continue
                report.unresolved += 1
                if not shadow:
                    line.status = str(PayoutLineStatus.UNMATCHED)
                    report.cases_opened += await self._open_case(
                        kind=CaseKind.UNMAPPABLE_PAYOUT,
                        subject_type="payout_line",
                        subject_id=line.id,
                        amount_paisa=line_evidence.charge_paisa,
                        summary=f"No parcel matches row {line.row_number}",
                        detail={"reason": "Charge row naming no parcel in this shop"},
                        payout_id=payout_id,
                        payout_line_id=line.id,
                    )
                continue

            decision = self._decide_for(line, pool)
            report.decisions.append((line.id, decision))

            if decision.confidence is MatchConfidence.EXACT:
                report.exact_matches += 1
            elif decision.chosen is not None and not decision.is_automatic:
                report.suggested += 1
            else:
                report.unresolved += 1

            if shadow:
                continue

            line.candidates = [candidate.as_dict() for candidate in decision.considered[:10]]
            line.confidence = str(decision.confidence)

            if decision.is_automatic and decision.chosen is not None:
                outstanding_before = decision.chosen.outstanding_paisa
                applied = await self._apply(line, decision.chosen, payout)
                report.applied_paisa += applied
                report.cases_opened += await self._raise_amount_cases(
                    line,
                    receivable_id=decision.chosen.receivable_id,
                    outstanding_before=outstanding_before,
                    reference=decision.chosen.merchant_reference,
                    payout=payout,
                    gross_paisa=line_evidence.actual_cod_paisa,
                )
            elif decision.chosen is not None:
                line.status = str(PayoutLineStatus.SUGGESTED)
            else:
                line.status = str(PayoutLineStatus.UNMATCHED)
                report.cases_opened += await self._open_case(
                    kind=CaseKind.UNMAPPABLE_PAYOUT,
                    subject_type="payout_line",
                    subject_id=line.id,
                    amount_paisa=line.amount_paisa,
                    summary=f"No parcel matches row {line.row_number}",
                    detail={"reason": decision.reason},
                    payout_id=payout_id,
                    payout_line_id=line.id,
                )

        if not shadow:
            await self._db.flush()
            report.cases_opened += await self._raise_duplicate_cases(payout_id, lines)
            report.cases_opened += await self._raise_unknown_deduction_cases(payout_id)
            await self._payouts.refresh_totals(payout_id)
            report.cases_opened += await self.refresh_items(payout)
            await record_audit(
                self._db,
                action=AuditAction.PAYOUT_RECONCILED,
                entity_type="payout",
                entity_id=payout_id,
                context={
                    "exact_matches": report.exact_matches,
                    "suggested": report.suggested,
                    "unresolved": report.unresolved,
                    "duplicates": report.duplicates,
                    "charge_only": report.charge_only,
                    "applied_paisa": report.applied_paisa,
                },
            )
        return report

    async def match_manually(
        self,
        line_id: uuid.UUID,
        receivable_id: uuid.UUID,
        *,
        reason: str,
        amount_paisa: int | None = None,
    ) -> PayoutLine:
        """Apply a line because a person decided to.

        Records who and why (section 81.7). The seller can do this for any
        candidate the engine refused, which is the point of refusing rather
        than guessing: the decision moves to somebody who can pick up a phone.

        Still refused where it cannot be safe: a line already applied, a row
        that duplicates one already paid, or a parcel from a different courier
        (section 82: same provider is mandatory).
        """
        if not reason.strip():
            raise ValidationError("A manual match needs a reason")

        line = await self._get_line(line_id)
        payout = await self._lock_payout(line.payout_id)
        if line.line_status.is_applied:
            raise ConflictError(
                "That line is already matched",
                details={"status": line.status},
            )
        if line.line_status in (PayoutLineStatus.DUPLICATE, PayoutLineStatus.CHARGE_ONLY):
            raise ConflictError(
                "That line cannot be matched to a parcel",
                details={"status": line.status},
            )

        receivable = await self._lock_receivable(receivable_id)
        if receivable is None:
            raise NotFoundError("Receivable not found")
        if receivable.provider != payout.provider:
            raise ConflictError(
                "That parcel was sent with a different courier",
                details={"line_provider": payout.provider, "parcel_provider": receivable.provider},
            )

        outstanding_before = receivable.outstanding_paisa
        amount = amount_paisa if amount_paisa is not None else line.unapplied_paisa
        applied = await self._settle(line, receivable, payout, amount)

        context = current_context()
        line.status = str(PayoutLineStatus.MANUAL_MATCHED)
        line.confidence = str(MatchConfidence.MANUAL_REQUIRED)
        line.matched_by = context.user_id if context else None
        line.match_reason = reason
        line.matched_at = utc_now()
        await self._db.flush()

        await self._close_cases(
            await self._open_cases_for(line_ids=[line.id], kinds={CaseKind.UNMAPPABLE_PAYOUT}),
            action=CaseEventAction.MANUAL_MATCH,
            note=reason,
        )
        consignment = await self._db.get(Consignment, receivable.consignment_id)
        evidence = (await self._evidence_for([line])).get(line.id) or LineEvidence(line=line)
        await self._raise_amount_cases(
            line,
            receivable_id=receivable.id,
            outstanding_before=outstanding_before,
            reference=consignment.merchant_reference if consignment else "",
            payout=payout,
            gross_paisa=evidence.actual_cod_paisa,
        )

        await self._payouts.refresh_totals(line.payout_id)
        await self.refresh_items(payout)
        await record_audit(
            self._db,
            action=AuditAction.RECONCILIATION_MANUAL_MATCH,
            entity_type="payout_line",
            entity_id=line.id,
            reason=reason,
            context={
                "receivable_id": str(receivable_id),
                "amount_paisa": applied,
            },
        )
        return line

    async def unmatch(self, line_id: uuid.UUID, *, reason: str) -> PayoutLine:
        """Undo a match.

        Section 81.8: a reversal, not a destructive overwrite. The line keeps
        its history and the receivable's ledger gains the two entries that undo
        the settlement.
        """
        line = await self._get_line(line_id)
        payout = await self._lock_payout(line.payout_id)
        if not line.line_status.is_applied:
            raise ConflictError("That line is not matched", details={"status": line.status})
        if line.receivable_id is None:
            raise ConflictError("That line has no receivable to unmatch from")

        receivable_id = line.receivable_id
        await self._lock_receivable(receivable_id)
        await self._receivables.reverse_settlement(
            receivable_id,
            amount_paisa=line.applied_paisa,
            reason=reason,
            source_ref=self._source_ref(line),
        )

        line.status = str(PayoutLineStatus.REVERSED)
        line.applied_paisa = 0
        line.receivable_id = None
        line.match_reason = reason
        await self._db.flush()
        await self._payouts.refresh_totals(line.payout_id)
        await self.refresh_items(payout, extra_receivable_ids={receivable_id})
        return line

    # -------------------------------------------------------------- charges --

    async def accept_charges(
        self, item_id: uuid.UUID, *, reason: str | None = None
    ) -> ChargeAcceptance:
        """Accept what the courier kept on one parcel, into the ledger.

        Each deduction on the parcel's statement rows is written once: the
        adjustment row is locked and stamped, so a retried request, a double
        tap or a second device finds nothing left to accept. For a delivered
        parcel the receivable shrinks by the charge; for a returned one the
        charge is recorded as a cost against the parcel.
        """
        item = await self._get_item(item_id)
        result = await self._accept_item(item, reason=reason or "Courier charges accepted")
        if result.adjustments:
            payout = await self._payouts.get(item.payout_id) if item.payout_id else None
            if payout is not None:
                await self.refresh_items(payout)
        return result

    async def accept_payout_charges(
        self, payout_id: uuid.UUID, *, reason: str | None = None
    ) -> ChargeAcceptance:
        """Accept the charges on every parcel in a payout that agrees.

        Only ``MATCHED`` parcels with nothing unexplained: a charge that
        differs from the one on record, or a deduction nobody could name, is
        looked at one by one.
        """
        payout = await self._lock_payout(payout_id)
        rows = await self._db.execute(
            sa.select(ReconciliationItem).where(
                ReconciliationItem.payout_id == payout_id,
                ReconciliationItem.status == str(ItemStatus.MATCHED),
                ReconciliationItem.charges_pending_paisa > 0,
            )
        )
        total = ChargeAcceptance()
        for item in rows.scalars().all():
            if item.detail.get("unknown_deduction"):
                continue
            result = await self._accept_item(item, reason=reason or "Courier charges accepted")
            total.accepted_paisa += result.accepted_paisa
            total.adjustments += result.adjustments
            total.items += result.items
        if total.adjustments:
            await self.refresh_items(payout)
        return total

    async def _accept_item(self, item: ReconciliationItem, *, reason: str) -> ChargeAcceptance:
        line_ids = [uuid.UUID(value) for value in item.detail.get("lines", [])]
        if not line_ids:
            return ChargeAcceptance()

        stmt = (
            sa.select(PayoutAdjustment)
            .where(
                PayoutAdjustment.payout_line_id.in_(line_ids),
                PayoutAdjustment.accepted_at.is_(None),
                PayoutAdjustment.type != str(AdjustmentType.BONUS),
            )
            .order_by(PayoutAdjustment.created_at.asc())
            .execution_options(populate_existing=True)
        )
        if self._postgres:
            stmt = stmt.with_for_update()
        pending = list((await self._db.execute(stmt)).scalars().all())
        if not pending:
            return ChargeAcceptance()

        total = sum(adjustment.amount_paisa for adjustment in pending)
        receivable: CodReceivable | None = None
        if item.receivable_id is not None:
            receivable = await self._lock_receivable(item.receivable_id)
        if receivable is not None and receivable.receivable_status.is_collectible:
            remaining = (
                receivable.expected_paisa - receivable.settled_paisa - receivable.deduction_paisa
            )
            if total > remaining:
                raise ConflictError(
                    "Those charges are more than this parcel still owes",
                    details={"charges_paisa": total, "outstanding_paisa": remaining},
                )
        elif item.consignment_id is None:
            raise ConflictError("That row is not tied to a parcel")

        lines = {
            line.id: line
            for line in (
                await self._db.execute(sa.select(PayoutLine).where(PayoutLine.id.in_(line_ids)))
            )
            .scalars()
            .all()
        }
        payouts = {
            payout.id: payout
            for payout in (
                await self._db.execute(
                    sa.select(Payout).where(
                        Payout.id.in_({line.payout_id for line in lines.values()})
                    )
                )
            )
            .scalars()
            .all()
        }

        context = current_context()
        now = utc_now()
        consignment_id = item.consignment_id or (receivable.consignment_id if receivable else None)
        for adjustment in pending:
            line = lines.get(adjustment.payout_line_id) if adjustment.payout_line_id else None
            payout = payouts.get(line.payout_id) if line else None
            occurred_at = payout.received_at if payout else now
            adjustment_type = AdjustmentType(adjustment.type)
            event = _DEDUCTION_EVENTS.get(adjustment_type, LedgerEventType.PROVIDER_DEDUCTION)
            source_ref = f"payout_adjustment:{adjustment.id}"
            if receivable is not None and receivable.receivable_status.is_collectible:
                await self._receivables.record_deduction(
                    receivable.id,
                    amount_paisa=adjustment.amount_paisa,
                    event_type=event,
                    source_ref=source_ref,
                    label=adjustment.provider_label,
                    occurred_at=occurred_at,
                )
            elif consignment_id is not None:
                # Nothing was receivable — a return — so the charge is a cost
                # against the parcel rather than money off a balance.
                await self._ledger.record(
                    event_type=event,
                    entity_type="consignment",
                    entity_id=consignment_id,
                    amount_paisa=adjustment.amount_paisa,
                    source=LedgerSource.RECONCILIATION,
                    occurred_at=occurred_at,
                    source_ref=source_ref,
                    reason=reason,
                    metadata={"provider_label": adjustment.provider_label},
                )
            adjustment.accepted_at = now
            adjustment.accepted_by = context.user_id if context else None
            if consignment_id is not None:
                adjustment.consignment_id = consignment_id
                await self._record_settled_charge(consignment_id, adjustment, occurred_at)
        await self._db.flush()

        if receivable is not None:
            await self._receivables.refresh_profit(receivable, reason="Courier charges accepted")

        cases = await self._open_cases_for(
            receivable_ids=[item.receivable_id] if item.receivable_id else [],
            line_ids=line_ids,
        )
        await self._close_cases(
            [case for case in cases if case.case_kind in _ACCEPTANCE_CLOSES],
            action=CaseEventAction.CHARGES_ACCEPTED,
            note=reason,
        )
        if receivable is not None and receivable.receivable_status is ReceivableStatus.SETTLED:
            await self._close_cases(
                [case for case in cases if case.case_kind in _SETTLED_CLOSES],
                action=CaseEventAction.AUTO_RESOLVED,
                note="Fully explained once the courier's charges were accepted",
            )

        await record_audit(
            self._db,
            action=AuditAction.RECONCILIATION_CHARGES_ACCEPTED,
            entity_type="reconciliation_item",
            entity_id=item.id,
            reason=reason,
            context={
                "amount_paisa": total,
                "adjustments": [str(adjustment.id) for adjustment in pending],
                "receivable_id": str(item.receivable_id) if item.receivable_id else None,
            },
        )
        return ChargeAcceptance(accepted_paisa=total, adjustments=len(pending), items=1)

    async def _record_settled_charge(
        self, consignment_id: uuid.UUID, adjustment: PayoutAdjustment, occurred_at: datetime
    ) -> None:
        """Promote the accepted charge to the parcel's settled charge.

        Section 85's truth hierarchy: the courier's own figure, accepted by the
        seller, outranks a booking quote or an estimate. Skipped when a settled
        figure is already on record — the payment API may have recorded it.
        """
        kind = _PROFIT_KINDS.get(AdjustmentType(adjustment.type))
        if kind is None:
            return
        current = (
            (
                await self._db.execute(
                    sa.select(ConsignmentCharge).where(
                        ConsignmentCharge.consignment_id == consignment_id,
                        ConsignmentCharge.kind == str(kind),
                        ConsignmentCharge.superseded_by.is_(None),
                    )
                )
            )
            .scalars()
            .first()
        )
        if current is not None and current.source == str(ChargeSource.SETTLED):
            return
        await self._profit.record_charge(
            consignment_id,
            kind=kind,
            source=ChargeSource.SETTLED,
            amount_paisa=adjustment.amount_paisa,
            provider_label=adjustment.provider_label,
            source_ref=f"payout_adjustment:{adjustment.id}",
            occurred_at=occurred_at,
        )

    # ---------------------------------------------------------------- scans --

    async def scan_for_cases(self) -> int:
        """Find the problems no payout run would surface.

        Delivered parcels whose money never arrived, parcels stuck in transit,
        and returns whose stock was never restored. Section 16 lists all three;
        they are found by looking at the shop's own data rather than at a
        statement, so they run on a schedule instead of on import.
        """
        opened = 0
        opened += await self._scan_delivered_but_unpaid()
        opened += await self._scan_stale_in_transit()
        opened += await self._scan_returned_not_restocked()
        return opened

    # ---------------------------------------------------------------- items --

    async def list_items(
        self,
        *,
        limit: int = 50,
        cursor: Cursor | None = None,
        statuses: list[ItemStatus] | None = None,
        discrepancies_only: bool = False,
        provider: str | None = None,
        payout_id: uuid.UUID | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        case_status: CaseStatus | None = None,
    ) -> list[ReconciliationItem]:
        stmt = self._filtered_items(
            statuses=statuses,
            discrepancies_only=discrepancies_only,
            provider=provider,
            payout_id=payout_id,
            search=search,
            date_from=date_from,
            date_to=date_to,
            case_status=case_status,
        )
        stmt = apply_cursor(stmt, ReconciliationItem, cursor)
        stmt = stmt.order_by(
            ReconciliationItem.created_at.desc(), ReconciliationItem.id.desc()
        ).limit(limit + 1)
        return list((await self._db.execute(stmt)).scalars().all())

    async def summary(
        self,
        *,
        provider: str | None = None,
        payout_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> ReconciliationSummary:
        """Expected, actual and the difference, over the filtered items.

        One grouped query. ``DUPLICATE`` rows and rows with no parcel are kept
        out of expected/actual — they have nothing to be compared with — and
        reported as their own totals instead.
        """
        filtered = self._filtered_items(
            provider=provider, payout_id=payout_id, date_from=date_from, date_to=date_to
        ).subquery()
        rows = await self._db.execute(
            sa.select(
                filtered.c.status,
                sa.func.count(),
                sa.func.coalesce(sa.func.sum(filtered.c.expected_net_paisa), 0),
                sa.func.coalesce(
                    sa.func.sum(
                        sa.case(
                            (
                                filtered.c.expected_net_paisa.is_not(None),
                                filtered.c.actual_net_paisa,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                sa.func.coalesce(sa.func.sum(filtered.c.difference_paisa), 0),
                sa.func.coalesce(sa.func.sum(filtered.c.actual_net_paisa), 0),
                sa.func.coalesce(sa.func.sum(filtered.c.charges_pending_paisa), 0),
            ).group_by(filtered.c.status)
        )
        counts: dict[str, int] = {}
        by_status: dict[str, int] = {}
        expected = actual = difference = unmatched = duplicate = pending = 0
        for status, count, expected_sum, actual_sum, diff_sum, net_sum, pending_sum in rows.all():
            counts[status] = int(count)
            by_status[status] = int(diff_sum)
            pending += int(pending_sum)
            if status == str(ItemStatus.DUPLICATE):
                duplicate += int(net_sum)
            elif status in (str(ItemStatus.UNMATCHED), str(ItemStatus.NEEDS_REVIEW)):
                unmatched += int(net_sum)
            else:
                expected += int(expected_sum)
                actual += int(actual_sum)
                difference += int(diff_sum)

        case_stmt = sa.select(
            sa.func.count(), sa.func.coalesce(sa.func.sum(ReconciliationCase.amount_paisa), 0)
        ).where(ReconciliationCase.status.in_([str(CaseStatus.OPEN), str(CaseStatus.IN_PROGRESS)]))
        if payout_id is not None:
            case_stmt = case_stmt.where(ReconciliationCase.payout_id == payout_id)
        open_cases, open_paisa = (await self._db.execute(case_stmt)).one()

        return ReconciliationSummary(
            counts=counts,
            expected_paisa=expected,
            actual_paisa=actual,
            difference_paisa=difference,
            unmatched_paisa=unmatched,
            duplicate_paisa=duplicate,
            charges_pending_paisa=pending,
            open_cases=int(open_cases),
            open_case_paisa=int(open_paisa),
            difference_by_status=by_status,
        )

    async def get_item(self, item_id: uuid.UUID) -> ReconciliationItem:
        return await self._get_item(item_id)

    async def item_evidence(
        self, item: ReconciliationItem
    ) -> tuple[list[PayoutLine], list[PayoutAdjustment]]:
        line_ids = [uuid.UUID(value) for value in item.detail.get("lines", [])]
        if not line_ids:
            return [], []
        lines = list(
            (
                await self._db.execute(
                    sa.select(PayoutLine)
                    .where(PayoutLine.id.in_(line_ids))
                    .order_by(PayoutLine.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        adjustments = list(
            (
                await self._db.execute(
                    sa.select(PayoutAdjustment)
                    .where(PayoutAdjustment.payout_line_id.in_(line_ids))
                    .order_by(PayoutAdjustment.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        return lines, adjustments

    async def case_statuses(self, case_ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]:
        ids = [case_id for case_id in set(case_ids) if case_id is not None]
        if not ids:
            return {}
        rows = await self._db.execute(
            sa.select(ReconciliationCase.id, ReconciliationCase.status).where(
                ReconciliationCase.id.in_(ids)
            )
        )
        return {row[0]: row[1] for row in rows.all()}

    def _filtered_items(
        self,
        *,
        statuses: list[ItemStatus] | None = None,
        discrepancies_only: bool = False,
        provider: str | None = None,
        payout_id: uuid.UUID | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        case_status: CaseStatus | None = None,
    ) -> sa.Select[tuple[ReconciliationItem]]:
        stmt = sa.select(ReconciliationItem)
        if statuses:
            stmt = stmt.where(ReconciliationItem.status.in_([str(status) for status in statuses]))
        if discrepancies_only:
            stmt = stmt.where(
                ReconciliationItem.status.in_([str(status) for status in DISCREPANCY_STATUSES])
            )
        if provider:
            stmt = stmt.where(ReconciliationItem.provider == provider)
        if payout_id is not None:
            stmt = stmt.where(ReconciliationItem.payout_id == payout_id)
        if date_from is not None:
            stmt = stmt.where(ReconciliationItem.settlement_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(ReconciliationItem.settlement_date <= date_to)
        if search and search.strip():
            # A prefix match on the references a seller actually types. Not a
            # contains-match: that cannot use an index and a seller searching
            # "CP-0042" does not mean "anything with 42 in it".
            needle = search.strip().replace("%", "").replace("_", "").lower() + "%"
            stmt = stmt.where(
                sa.or_(
                    sa.func.lower(ReconciliationItem.merchant_reference).like(needle),
                    sa.func.lower(ReconciliationItem.tracking_code).like(needle),
                )
            )
        if case_status is not None:
            stmt = stmt.join(
                ReconciliationCase, ReconciliationCase.id == ReconciliationItem.case_id
            ).where(ReconciliationCase.status == str(case_status))
        return stmt

    async def refresh_items(
        self,
        payout: Payout,
        *,
        extra_receivable_ids: set[uuid.UUID] | None = None,
    ) -> int:
        """Rebuild expected-vs-actual for everything this payout touches.

        Batched: a fixed number of queries whatever the statement's length.
        Returns the number of cases opened, so the run's report counts them.
        """
        lines = await self._payouts.lines(payout.id, limit=None)
        linked_ids = {
            line.receivable_id
            for line in lines
            if line.receivable_id is not None and line.status in _LINKED_STATUSES
        }
        receivable_ids = set(linked_ids) | set(extra_receivable_ids or set())
        omitted = await self._omitted_receivables(payout, lines)
        receivable_ids |= {receivable.id for receivable in omitted}

        # Every line tied to these parcels, from any statement: a parcel paid
        # in two statements is judged on both.
        all_lines: dict[uuid.UUID, PayoutLine] = {line.id: line for line in lines}
        for chunk in _chunks(list(receivable_ids)):
            rows = await self._db.execute(
                sa.select(PayoutLine).where(
                    PayoutLine.receivable_id.in_(chunk), PayoutLine.status.in_(_LINKED_STATUSES)
                )
            )
            for line in rows.scalars().all():
                all_lines[line.id] = line

        evidence = await self._evidence_for(list(all_lines.values()))
        receivables = await self._by_ids(CodReceivable, receivable_ids)

        return_consignments: dict[uuid.UUID, uuid.UUID] = {}
        for line in lines:
            if line.status == str(PayoutLineStatus.CHARGE_ONLY) and line.receivable_id is None:
                consignment_id = next(
                    (
                        adjustment.consignment_id
                        for adjustment in evidence[line.id].adjustments
                        if adjustment.consignment_id is not None
                    ),
                    None,
                )
                if consignment_id is not None:
                    return_consignments[line.id] = consignment_id

        consignment_ids = {r.consignment_id for r in receivables.values()} | set(
            return_consignments.values()
        )
        consignments = await self._by_ids(Consignment, consignment_ids)
        expected_charges = await self._expected_charges(consignment_ids)
        payouts = await self._by_ids(Payout, {line.payout_id for line in all_lines.values()})
        payouts[payout.id] = payout

        subject_keys = {f"line:{line.id}" for line in lines} | {
            f"receivable:{receivable_id}" for receivable_id in receivable_ids
        }
        existing = await self._existing_items(subject_keys)
        cases = await self._cases_for(receivable_ids, set(all_lines))

        opened = 0
        now = utc_now()

        # --- rows with no parcel, or a returned one ---------------------------
        for line in lines:
            key = f"line:{line.id}"
            line_evidence = evidence.get(line.id) or LineEvidence(line=line)
            if line.receivable_id is not None and line.status in _LINKED_STATUSES:
                stale = existing.get(key)
                if stale is not None:
                    await self._db.delete(stale)
                continue

            consignment_id = return_consignments.get(line.id)
            consignment = consignments.get(consignment_id) if consignment_id else None
            if consignment is not None:
                evaluation, mismatch = evaluate_return(
                    line_evidence,
                    expected_charge=expected_charges.get(consignment.id),
                    config=self._config,
                )
                if mismatch:
                    gap = evaluation.charge_gap_paisa or 0
                    new_case = await self._open_case(
                        kind=CaseKind.RETURN_CHARGE_MISMATCH,
                        subject_type="payout_line",
                        subject_id=line.id,
                        amount_paisa=abs(gap),
                        summary=(
                            f"{consignment.merchant_reference} was charged "
                            f"{evaluation.actual_charge_paisa / 100:.2f} taka for the return; "
                            f"{(evaluation.expected_charge_paisa or 0) / 100:.2f} was expected"
                        ),
                        detail={
                            "expected_charge_paisa": evaluation.expected_charge_paisa,
                            "actual_charge_paisa": evaluation.actual_charge_paisa,
                        },
                        dedupe_key=str(evaluation.actual_charge_paisa),
                        payout_id=line.payout_id,
                        payout_line_id=line.id,
                        consignment_id=consignment.id,
                    )
                    if new_case:
                        opened += new_case
                        cases = cases + await self._cases_for(set(), {line.id})
            elif line.status == str(PayoutLineStatus.DUPLICATE):
                evaluation = evaluate_unplaced(
                    line_evidence, status=ItemStatus.DUPLICATE, reason="duplicate_row"
                )
            elif line.status == str(PayoutLineStatus.SUGGESTED):
                evaluation = evaluate_unplaced(
                    line_evidence, status=ItemStatus.NEEDS_REVIEW, reason="suggestion"
                )
            else:
                evaluation = evaluate_unplaced(
                    line_evidence, status=ItemStatus.UNMATCHED, reason="no_parcel"
                )

            await self._upsert_item(
                existing,
                key,
                evaluation,
                now=now,
                provider=payout.provider,
                payout=payout,
                line_id=line.id,
                receivable_id=None,
                consignment=consignment,
                reference=line.merchant_reference or line.provider_consignment_id,
                tracking=line.tracking_code,
                case=self._case_for(cases, receivable_id=None, line_ids={line.id}),
            )

        # --- parcels -----------------------------------------------------------
        omitted_ids = {receivable.id for receivable in omitted}
        lines_by_receivable: dict[uuid.UUID, list[LineEvidence]] = {}
        for line in all_lines.values():
            if line.receivable_id is not None and line.status in _LINKED_STATUSES:
                lines_by_receivable.setdefault(line.receivable_id, []).append(
                    evidence.get(line.id) or LineEvidence(line=line)
                )

        for receivable_id in receivable_ids:
            key = f"receivable:{receivable_id}"
            receivable = receivables.get(receivable_id)
            if receivable is None:
                continue
            consignment = consignments.get(receivable.consignment_id)
            expected_charge = expected_charges.get(receivable.consignment_id)
            parcel_lines = sorted(
                lines_by_receivable.get(receivable_id, []),
                key=lambda ev: (payouts[ev.line.payout_id].received_at, ev.line.row_number),
            )

            if parcel_lines:
                evaluation = evaluate_parcel(
                    expected_cod_paisa=receivable.expected_paisa,
                    lines=parcel_lines,
                    expected_charge=expected_charge,
                    config=self._config,
                )
                latest = parcel_lines[-1].line
                item_payout = payouts[latest.payout_id]
                line_id = latest.id
            elif receivable_id in omitted_ids:
                current = existing.get(key)
                if current is not None and current.line_count > 0:
                    continue
                evaluation = evaluate_omitted(
                    expected_cod_paisa=receivable.expected_paisa, expected_charge=expected_charge
                )
                item_payout = payout
                line_id = None
            else:
                # Nothing ties this parcel to a statement any more — the match
                # was undone. The item goes; the reversal stays in the ledger.
                stale = existing.get(key)
                if stale is not None:
                    await self._db.delete(stale)
                continue

            parcel_line_ids = {ev.line.id for ev in parcel_lines}
            new_cases = await self._raise_item_cases(
                evaluation, receivable, consignment, item_payout, line_id, cases
            )
            if new_cases:
                opened += new_cases
                cases = cases + await self._cases_for({receivable_id}, parcel_line_ids)
            await self._upsert_item(
                existing,
                key,
                evaluation,
                now=now,
                provider=receivable.provider,
                payout=item_payout,
                line_id=line_id,
                receivable_id=receivable_id,
                consignment=consignment,
                reference=consignment.merchant_reference if consignment else None,
                tracking=consignment.tracking_code if consignment else None,
                case=self._case_for(cases, receivable_id=receivable_id, line_ids=parcel_line_ids),
            )

        await self._db.flush()
        return opened

    async def _raise_item_cases(
        self,
        evaluation: Evaluation,
        receivable: CodReceivable,
        consignment: Consignment | None,
        payout: Payout,
        line_id: uuid.UUID | None,
        cases: list[ReconciliationCase],
    ) -> int:
        reference = consignment.merchant_reference if consignment else str(receivable.id)
        if evaluation.status is ItemStatus.CHARGE_MISMATCH:
            gap = evaluation.charge_gap_paisa or 0
            return await self._open_case(
                kind=CaseKind.CHARGE_MISMATCH,
                subject_type="cod_receivable",
                subject_id=receivable.id,
                amount_paisa=abs(gap),
                summary=(
                    f"{reference} was charged {evaluation.actual_charge_paisa / 100:.2f} taka; "
                    f"{(evaluation.expected_charge_paisa or 0) / 100:.2f} was expected"
                ),
                detail={
                    "expected_charge_paisa": evaluation.expected_charge_paisa,
                    "expected_charge_source": evaluation.expected_charge_source,
                    "actual_charge_paisa": evaluation.actual_charge_paisa,
                    "charges": evaluation.detail.get("actual_charges", {}),
                },
                dedupe_key=str(evaluation.actual_charge_paisa),
                receivable_id=receivable.id,
                payout_id=payout.id,
                payout_line_id=line_id,
                consignment_id=receivable.consignment_id,
            )
        if evaluation.status is ItemStatus.MISSING_COD:
            already_chased = any(
                case.receivable_id == receivable.id
                and case.case_kind is CaseKind.DELIVERED_BUT_UNPAID
                and case.case_status.is_open
                for case in cases
            )
            if already_chased:
                return 0
            left_out = evaluation.detail.get("reason") == "not_in_payout"
            return await self._open_case(
                kind=CaseKind.MISSING_COD,
                subject_type="cod_receivable",
                subject_id=receivable.id,
                amount_paisa=evaluation.expected_cod_paisa or 0,
                summary=(
                    f"{reference} was delivered before everything in this payout but is not in it"
                    if left_out
                    else f"The courier charged for {reference} but paid no COD for it"
                ),
                detail={
                    "reason": evaluation.detail.get("reason"),
                    "expected_cod_paisa": evaluation.expected_cod_paisa,
                    "actual_charge_paisa": evaluation.actual_charge_paisa,
                },
                receivable_id=receivable.id,
                payout_id=payout.id,
                payout_line_id=line_id,
                consignment_id=receivable.consignment_id,
            )
        if evaluation.status is ItemStatus.MATCHED and receivable.receivable_status is (
            ReceivableStatus.SETTLED
        ):
            await self._close_cases(
                [
                    case
                    for case in cases
                    if case.receivable_id == receivable.id
                    and case.case_kind in _SETTLED_CLOSES
                    and case.case_status.is_open
                ],
                action=CaseEventAction.AUTO_RESOLVED,
                note="The money for this parcel has arrived",
            )
        return 0

    async def _upsert_item(
        self,
        existing: dict[str, ReconciliationItem],
        key: str,
        evaluation: Evaluation,
        *,
        now: datetime,
        provider: str,
        payout: Payout,
        line_id: uuid.UUID | None,
        receivable_id: uuid.UUID | None,
        consignment: Consignment | None,
        reference: str | None,
        tracking: str | None,
        case: ReconciliationCase | None,
    ) -> None:
        values = {
            "status": str(evaluation.status),
            "provider": provider,
            "payout_id": payout.id,
            "payout_line_id": line_id,
            "receivable_id": receivable_id,
            "consignment_id": consignment.id if consignment else None,
            "case_id": case.id if case else None,
            "merchant_reference": (reference or None) and reference[:120],
            "tracking_code": (tracking or None) and tracking[:120],
            "settlement_date": payout.paid_on or payout.received_at.date(),
            "expected_cod_paisa": evaluation.expected_cod_paisa,
            "actual_cod_paisa": evaluation.actual_cod_paisa,
            "expected_charge_paisa": evaluation.expected_charge_paisa,
            "expected_charge_source": evaluation.expected_charge_source,
            "actual_charge_paisa": evaluation.actual_charge_paisa,
            "expected_net_paisa": evaluation.expected_net_paisa,
            "actual_net_paisa": evaluation.actual_net_paisa,
            "difference_paisa": evaluation.difference_paisa,
            "charges_pending_paisa": evaluation.charges_pending_paisa,
            "line_count": evaluation.line_count,
            "detail": evaluation.detail,
            "evaluated_at": now,
        }
        item = existing.get(key)
        if item is None:
            item = ReconciliationItem(subject_key=key, **values)
            try:
                async with self._db.begin_nested():
                    self._db.add(item)
                    await self._db.flush()
            except IntegrityError:
                # Another run created it first. Update theirs instead.
                item = (
                    await self._db.execute(
                        sa.select(ReconciliationItem).where(ReconciliationItem.subject_key == key)
                    )
                ).scalar_one()
                for name, value in values.items():
                    setattr(item, name, value)
            existing[key] = item
            return
        for name, value in values.items():
            setattr(item, name, value)

    # ---------------------------------------------------------------- cases --

    async def list_cases(
        self,
        *,
        limit: int = 30,
        cursor: Cursor | None = None,
        status: CaseStatus | None = None,
        kind: CaseKind | None = None,
        priority: CasePriority | None = None,
        payout_id: uuid.UUID | None = None,
    ) -> list[ReconciliationCase]:
        stmt = sa.select(ReconciliationCase)
        if status is not None:
            stmt = stmt.where(ReconciliationCase.status == str(status))
        if kind is not None:
            stmt = stmt.where(ReconciliationCase.kind == str(kind))
        if priority is not None:
            stmt = stmt.where(ReconciliationCase.priority == str(priority))
        if payout_id is not None:
            stmt = stmt.where(ReconciliationCase.payout_id == payout_id)
        stmt = apply_cursor(stmt, ReconciliationCase, cursor)
        stmt = stmt.order_by(
            ReconciliationCase.created_at.desc(), ReconciliationCase.id.desc()
        ).limit(limit + 1)
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_case(self, case_id: uuid.UUID) -> ReconciliationCase:
        case = await self._db.get(ReconciliationCase, case_id)
        if case is None:
            raise NotFoundError("Case not found")
        return case

    async def case_events(self, case_id: uuid.UUID) -> list[CaseEvent]:
        rows = await self._db.execute(
            sa.select(CaseEvent)
            .where(CaseEvent.case_id == case_id)
            .order_by(CaseEvent.created_at.asc(), CaseEvent.id.asc())
        )
        return list(rows.scalars().all())

    async def item_for_case(self, case: ReconciliationCase) -> ReconciliationItem | None:
        keys = []
        if case.receivable_id is not None:
            keys.append(f"receivable:{case.receivable_id}")
        if case.payout_line_id is not None:
            keys.append(f"line:{case.payout_line_id}")
        if not keys:
            return None
        rows = await self._db.execute(
            sa.select(ReconciliationItem).where(ReconciliationItem.subject_key.in_(keys))
        )
        return rows.scalars().first()

    async def update_case(
        self,
        case_id: uuid.UUID,
        *,
        status: CaseStatus,
        resolution: str | None = None,
    ) -> ReconciliationCase:
        """Move a case on.

        Resolving or dismissing needs a note. A case closed with no explanation
        is one nobody can learn from, and the same problem will be re-raised
        next month with nothing recorded about what happened last time.

        Reopening clears the resolution from the case row; the event history
        keeps it, so the earlier decision is never lost.
        """
        case = await self.get_case(case_id)

        if status in (CaseStatus.RESOLVED, CaseStatus.DISMISSED) and not (resolution or "").strip():
            raise ValidationError("Say what happened before closing a case")

        context = current_context()
        previous = case.case_status
        reopening = not previous.is_open and status.is_open
        case.status = str(status)
        if status.is_open:
            if reopening:
                case.resolved_at = None
                case.resolved_by = None
                case.resolution = None
        else:
            case.resolution = resolution
            case.resolved_at = utc_now()
            case.resolved_by = context.user_id if context else None
        await self._db.flush()

        await self._append_event(
            case,
            CaseEventAction.REOPENED if reopening else CaseEventAction.STATUS_CHANGED,
            from_status=str(previous),
            to_status=str(status),
            note=resolution,
        )
        await record_audit(
            self._db,
            action=(
                AuditAction.RECONCILIATION_CASE_REOPENED
                if reopening
                else AuditAction.RECONCILIATION_CASE_RESOLVED
            ),
            entity_type="reconciliation_case",
            entity_id=case.id,
            reason=resolution,
            context={"kind": case.kind, "status": str(status), "from": str(previous)},
        )
        return case

    async def add_note(self, case_id: uuid.UUID, *, note: str) -> CaseEvent:
        if not note.strip():
            raise ValidationError("A note cannot be empty")
        case = await self.get_case(case_id)
        event = await self._append_event(case, CaseEventAction.NOTE, note=note.strip())
        await record_audit(
            self._db,
            action=AuditAction.RECONCILIATION_CASE_NOTE,
            entity_type="reconciliation_case",
            entity_id=case.id,
            reason=note.strip()[:400],
            context={"kind": case.kind},
        )
        return event

    # ------------------------------------------------------------ internals --

    async def _lock_payout(self, payout_id: uuid.UUID) -> Payout:
        """The payout, locked for the rest of the transaction on PostgreSQL.

        Two reconcile runs over the same statement — a double tap, a retry, a
        second device — queue behind one another instead of both reading the
        same unmatched lines and both settling them.
        """
        stmt = (
            sa.select(Payout)
            .where(Payout.id == payout_id)
            .execution_options(populate_existing=True)
        )
        if self._postgres:
            stmt = stmt.with_for_update()
        payout = (await self._db.execute(stmt)).scalar_one_or_none()
        if payout is None:
            raise NotFoundError("Payout not found")
        return payout

    async def _lock_receivable(self, receivable_id: uuid.UUID) -> CodReceivable | None:
        stmt = (
            sa.select(CodReceivable)
            .where(CodReceivable.id == receivable_id)
            .execution_options(populate_existing=True)
        )
        if self._postgres:
            stmt = stmt.with_for_update()
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def _pool(self, provider: str) -> _Pool:
        """Every collectible parcel from this courier, loaded once.

        Same provider is mandatory (section 82), which also keeps the pool
        small: a shop reconciling a Steadfast statement never scores its Pathao
        parcels.
        """
        rows = await self._db.execute(
            sa.select(CodReceivable, Consignment)
            .join(Consignment, Consignment.id == CodReceivable.consignment_id)
            .where(CodReceivable.provider == provider)
            .where(
                CodReceivable.status.in_(
                    [str(status) for status in ReceivableStatus if status.is_collectible]
                )
            )
        )
        pairs = [(receivable, consignment) for receivable, consignment in rows.all()]
        phones = await self._phone_last4_for([receivable.order_id for receivable, _ in pairs])
        return _Pool(pairs=pairs, phones=phones)

    def _decide_for(self, line: PayoutLine, pool: _Pool) -> MatchDecision:
        """Score a line against its exact hits, or the whole pool if none fit.

        Scoring only the exact hits changes no outcome: one eligible exact
        reference always wins and two always tie, whatever else scores. Falling
        back to the whole pool when no exact hit is eligible keeps suggestions.
        """
        exact = [pool.pairs[index] for index in pool.exact_for(line)]
        if exact:
            candidates = self._score(line, exact, pool)
            if any(candidate.is_eligible for candidate in candidates):
                return decide(candidates, config=self._config)
        return decide(self._score(line, pool.pairs, pool), config=self._config)

    def _score(
        self,
        line: PayoutLine,
        pairs: list[tuple[CodReceivable, Consignment]],
        pool: _Pool,
    ) -> list[Candidate]:
        return [
            score_candidate(
                line,
                receivable,
                consignment,
                config=self._config,
                customer_phone_last4=pool.phones.get(receivable.order_id),
            )
            for receivable, consignment in pairs
        ]

    async def _phone_last4_for(self, order_ids: list[uuid.UUID]) -> dict[uuid.UUID, str | None]:
        """Last four digits of each order's customer number.

        The last four is all the matcher needs and all a statement ever gives.
        Nothing here reads a full number, so a reconciliation run cannot become
        a way to bulk-read customer phones.
        """
        phones: dict[uuid.UUID, str | None] = {}
        for chunk in _chunks(order_ids):
            rows = await self._db.execute(
                sa.select(Order.id, Customer.phone_last4)
                .join(Customer, Customer.id == Order.customer_id)
                .where(Order.id.in_(chunk))
            )
            phones.update({row[0]: row[1] for row in rows.all()})
        return phones

    async def _evidence_for(self, lines: list[PayoutLine]) -> dict[uuid.UUID, LineEvidence]:
        evidence = {line.id: LineEvidence(line=line) for line in lines}
        for chunk in _chunks(list(evidence)):
            rows = await self._db.execute(
                sa.select(PayoutAdjustment)
                .where(PayoutAdjustment.payout_line_id.in_(chunk))
                .order_by(PayoutAdjustment.created_at.asc())
            )
            for adjustment in rows.scalars().all():
                if adjustment.payout_line_id in evidence:
                    evidence[adjustment.payout_line_id].adjustments.append(adjustment)
        return evidence

    async def _prior_applications(
        self, payout: Payout, lines: list[PayoutLine]
    ) -> dict[uuid.UUID, PayoutLine]:
        """Rows already paid in another statement from the same courier.

        Row identity is (courier, reference, amount). A re-exported statement
        hashes differently from the original, so the file-level check cannot
        see it; this can. Same reference with a *different* amount is not a
        duplicate — it may be the second half of a split payment — and is left
        to the matcher.
        """
        wanted: set[str] = set()
        for line in lines:
            if line.amount_paisa <= 0 or line.line_status is PayoutLineStatus.UNMAPPABLE:
                continue
            for value in (
                line.provider_consignment_id,
                line.tracking_code,
                line.merchant_reference,
            ):
                if _norm(value):
                    wanted.add(_norm(value))
        if not wanted:
            return {}

        applied = [str(PayoutLineStatus.MATCHED), str(PayoutLineStatus.MANUAL_MATCHED)]
        seen: dict[tuple[str, str, int], PayoutLine] = {}
        for chunk in _chunks(sorted(wanted)):
            rows = await self._db.execute(
                sa.select(PayoutLine)
                .join(Payout, Payout.id == PayoutLine.payout_id)
                .where(
                    Payout.provider == payout.provider,
                    PayoutLine.payout_id != payout.id,
                    PayoutLine.status.in_(applied),
                    sa.or_(
                        sa.func.lower(PayoutLine.provider_consignment_id).in_(chunk),
                        sa.func.lower(PayoutLine.tracking_code).in_(chunk),
                        sa.func.lower(PayoutLine.merchant_reference).in_(chunk),
                    ),
                )
            )
            for earlier in rows.scalars().all():
                for field_name in (
                    "provider_consignment_id",
                    "tracking_code",
                    "merchant_reference",
                ):
                    value = _norm(getattr(earlier, field_name))
                    if value:
                        seen.setdefault((field_name, value, earlier.amount_paisa), earlier)

        found: dict[uuid.UUID, PayoutLine] = {}
        for line in lines:
            if line.amount_paisa <= 0:
                continue
            for field_name in ("provider_consignment_id", "tracking_code", "merchant_reference"):
                value = _norm(getattr(line, field_name))
                prior = seen.get((field_name, value, line.amount_paisa)) if value else None
                if prior is not None:
                    found[line.id] = prior
                    break
        return found

    async def _mark_prior_duplicate(
        self, line: PayoutLine, earlier: PayoutLine, payout: Payout
    ) -> int:
        line.status = str(PayoutLineStatus.DUPLICATE)
        line.match_reason = "Already paid in an earlier statement"
        return await self._open_case(
            kind=CaseKind.DUPLICATE_PAYOUT_LINE,
            subject_type="payout_line",
            subject_id=line.id,
            amount_paisa=line.amount_paisa,
            summary=f"Row {line.row_number} was already paid in an earlier statement",
            detail={
                "reference": self._source_ref(line),
                "duplicate_of_payout_id": str(earlier.payout_id),
                "duplicate_of_line_id": str(earlier.id),
            },
            payout_id=payout.id,
            payout_line_id=line.id,
        )

    async def _charge_only(
        self, line: PayoutLine, evidence: LineEvidence, payout: Payout, *, shadow: bool
    ) -> bool:
        """Place a row that pays nothing and only carries a charge.

        By exact identity only — the same rule the payment import uses to
        attach a settled charge. A return charge names a parcel that came back;
        a charge on a delivered parcel with no COD beside it is a missing COD.
        Anything else is left for a person.
        """
        if evidence.charge_paisa <= 0:
            return False
        conditions = []
        if line.provider_consignment_id:
            conditions.append(Consignment.provider_consignment_id == line.provider_consignment_id)
        if line.tracking_code:
            conditions.append(Consignment.tracking_code == line.tracking_code)
        if line.merchant_reference:
            conditions.append(Consignment.merchant_reference == line.merchant_reference)
        if not conditions:
            return False
        rows = await self._db.execute(
            sa.select(Consignment)
            .where(Consignment.provider == payout.provider)
            .where(sa.or_(*conditions))
            .limit(2)
        )
        found = list(rows.scalars().all())
        if len(found) != 1:
            return False
        consignment = found[0]

        receivable = await self._receivables.for_consignment(consignment.id)
        returned = consignment.consignment_status in (
            ConsignmentStatus.RETURNED,
            ConsignmentStatus.CANCELLED,
        )
        delivered = receivable is not None and receivable.receivable_status.is_collectible
        if not (returned or delivered):
            return False
        if shadow:
            return True

        line.status = str(PayoutLineStatus.CHARGE_ONLY)
        line.confidence = str(MatchConfidence.EXACT)
        line.match_reason = "Charge only"
        line.matched_at = utc_now()
        if delivered and not returned and receivable is not None:
            line.receivable_id = receivable.id
        for adjustment in evidence.adjustments:
            adjustment.consignment_id = consignment.id
        await self._db.flush()
        return True

    async def _omitted_receivables(
        self, payout: Payout, lines: list[PayoutLine]
    ) -> list[CodReceivable]:
        """Delivered parcels this payout plausibly should have included.

        Only for a statement or API payout that says when its parcels were
        delivered: anything from the same courier delivered comfortably before
        the earliest of them, still wholly unpaid and on no statement at all.
        A manual lump sum says nothing about which parcels it covers.
        """
        if payout.source == str(PayoutSource.MANUAL):
            return []
        dates = [
            line.delivered_on
            for line in lines
            if line.delivered_on is not None and line.line_status.is_applied
        ]
        if not dates:
            return []
        earliest = min(dates)
        before = earliest - timedelta(days=OMISSION_GRACE_DAYS)
        since = earliest - timedelta(days=OMISSION_LOOKBACK_DAYS)
        on_a_statement = sa.select(PayoutLine.id).where(
            PayoutLine.receivable_id == CodReceivable.id
        )
        rows = await self._db.execute(
            sa.select(CodReceivable)
            .where(
                CodReceivable.provider == payout.provider,
                CodReceivable.status == str(ReceivableStatus.ELIGIBLE),
                CodReceivable.settled_paisa == 0,
                CodReceivable.eligible_business_date.is_not(None),
                CodReceivable.eligible_business_date >= since,
                CodReceivable.eligible_business_date < before,
                ~on_a_statement.exists(),
            )
            .order_by(CodReceivable.eligible_business_date.asc())
            .limit(OMISSION_MAX_PER_PAYOUT)
        )
        return [
            receivable for receivable in rows.scalars().all() if receivable.outstanding_paisa > 0
        ]

    async def _expected_charges(
        self, consignment_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, ExpectedCharge]:
        grouped: dict[uuid.UUID, list[ConsignmentCharge]] = {}
        for chunk in _chunks(list(consignment_ids)):
            rows = await self._db.execute(
                sa.select(ConsignmentCharge).where(ConsignmentCharge.consignment_id.in_(chunk))
            )
            for charge in rows.scalars().all():
                grouped.setdefault(charge.consignment_id, []).append(charge)
        result: dict[uuid.UUID, ExpectedCharge] = {}
        for consignment_id, charges in grouped.items():
            expected = expected_charge_from(charges)
            if expected is not None:
                result[consignment_id] = expected
        return result

    async def _by_ids(self, model: Any, ids: set[uuid.UUID]) -> dict:
        found: dict = {}
        for chunk in _chunks(list(ids)):
            rows = await self._db.execute(sa.select(model).where(model.id.in_(chunk)))
            for row in rows.scalars().all():
                found[row.id] = row
        return found

    async def _existing_items(self, keys: set[str]) -> dict[str, ReconciliationItem]:
        found: dict[str, ReconciliationItem] = {}
        for chunk in _chunks(sorted(keys)):
            rows = await self._db.execute(
                sa.select(ReconciliationItem).where(ReconciliationItem.subject_key.in_(chunk))
            )
            for item in rows.scalars().all():
                found[item.subject_key] = item
        return found

    async def _cases_for(
        self, receivable_ids: set[uuid.UUID], line_ids: set[uuid.UUID]
    ) -> list[ReconciliationCase]:
        found: dict[uuid.UUID, ReconciliationCase] = {}
        for chunk in _chunks(list(receivable_ids)):
            rows = await self._db.execute(
                sa.select(ReconciliationCase).where(ReconciliationCase.receivable_id.in_(chunk))
            )
            found.update({case.id: case for case in rows.scalars().all()})
        for chunk in _chunks(list(line_ids)):
            rows = await self._db.execute(
                sa.select(ReconciliationCase).where(ReconciliationCase.payout_line_id.in_(chunk))
            )
            found.update({case.id: case for case in rows.scalars().all()})
        return list(found.values())

    def _case_for(
        self,
        cases: list[ReconciliationCase],
        *,
        receivable_id: uuid.UUID | None,
        line_ids: set[uuid.UUID],
    ) -> ReconciliationCase | None:
        """The case a reviewer should open from this row: open first, then the
        most urgent, then the newest."""
        related = [
            case
            for case in cases
            if (receivable_id is not None and case.receivable_id == receivable_id)
            or (case.payout_line_id is not None and case.payout_line_id in line_ids)
        ]
        if not related:
            return None
        rank = {CasePriority.HIGH: 0, CasePriority.MEDIUM: 1, CasePriority.LOW: 2}
        related.sort(
            key=lambda case: (
                0 if case.case_status.is_open else 1,
                rank.get(case.case_priority, 3),
                -case.opened_at.timestamp(),
            )
        )
        return related[0]

    async def _open_cases_for(
        self,
        *,
        receivable_ids: list[uuid.UUID] | None = None,
        line_ids: list[uuid.UUID] | None = None,
        kinds: set[CaseKind] | None = None,
    ) -> list[ReconciliationCase]:
        cases = await self._cases_for(set(receivable_ids or []), set(line_ids or []))
        return [
            case
            for case in cases
            if case.case_status.is_open and (kinds is None or case.case_kind in kinds)
        ]

    async def _close_cases(
        self,
        cases: list[ReconciliationCase],
        *,
        action: CaseEventAction,
        note: str,
    ) -> None:
        """Close cases the evidence has answered, with an event saying why.

        ``resolved_by`` stays empty for the engine's own closures, so a
        reviewer can tell a person's decision from the system's.
        """
        context = current_context()
        by_person = action is not CaseEventAction.AUTO_RESOLVED
        for case in cases:
            if not case.case_status.is_open:
                continue
            previous = case.status
            case.status = str(CaseStatus.RESOLVED)
            case.resolution = note[:400]
            case.resolved_at = utc_now()
            case.resolved_by = context.user_id if (context and by_person) else None
            await self._append_event(
                case, action, from_status=previous, to_status=case.status, note=note
            )
        await self._db.flush()

    async def _append_event(
        self,
        case: ReconciliationCase,
        action: CaseEventAction,
        *,
        from_status: str | None = None,
        to_status: str | None = None,
        note: str | None = None,
    ) -> CaseEvent:
        context = current_context()
        # A case's history is read in time order, and two events written in
        # the same clock tick (a note then a status change, say) would
        # otherwise tie — with ids that are not ordered within a millisecond.
        # Each event is stamped strictly after the case's previous one.
        moment = utc_now()
        latest = (
            await self._db.execute(
                sa.select(sa.func.max(CaseEvent.created_at)).where(CaseEvent.case_id == case.id)
            )
        ).scalar_one_or_none()
        if latest is not None and moment <= ensure_utc(latest):
            moment = ensure_utc(latest) + timedelta(microseconds=1)
        event = CaseEvent(
            case_id=case.id,
            action=str(action),
            from_status=from_status,
            to_status=to_status,
            note=(note or None) and note[:1000],
            actor_user_id=(
                context.user_id if context and action is not CaseEventAction.AUTO_RESOLVED else None
            ),
            created_at=moment,
        )
        self._db.add(event)
        await self._db.flush()
        return event

    async def _apply(self, line: PayoutLine, candidate: Candidate, payout: Payout) -> int:
        receivable = await self._lock_receivable(candidate.receivable_id)
        if receivable is None:
            return 0
        applied = await self._settle(line, receivable, payout, line.unapplied_paisa)
        line.status = str(PayoutLineStatus.MATCHED)
        await self._db.flush()
        return applied

    async def _settle(
        self,
        line: PayoutLine,
        receivable: CodReceivable,
        payout: Payout,
        amount_paisa: int,
    ) -> int:
        """Apply money from one line to one receivable.

        Capped at what the line still has (section 17.1) and at what the parcel
        is still owed (section 17.2). The smaller of the two wins, and the
        remainder stays visible on whichever side it belongs to rather than
        being written off quietly.
        """
        if amount_paisa <= 0:
            raise ValidationError("There is nothing left on that line to apply")

        available = min(amount_paisa, line.unapplied_paisa)
        owed = receivable.expected_paisa - receivable.settled_paisa
        applied = min(available, owed)

        if applied <= 0:
            raise ConflictError(
                "That parcel is already fully settled",
                details={"receivable_id": str(receivable.id)},
            )

        await self._receivables.apply_settlement(
            receivable.id,
            amount_paisa=applied,
            source_ref=self._source_ref(line),
            payout_line_id=line.id,
            occurred_at=payout.received_at,
        )
        line.applied_paisa += applied
        line.receivable_id = receivable.id
        await self._db.flush()
        return applied

    def _source_ref(self, line: PayoutLine) -> str:
        return (
            line.provider_consignment_id
            or line.tracking_code
            or line.merchant_reference
            or f"line:{line.row_number}"
        )

    # ------------------------------------------------------------ case-work --

    async def _open_case(
        self,
        *,
        kind: CaseKind,
        subject_type: str,
        subject_id: uuid.UUID,
        amount_paisa: int,
        summary: str,
        detail: dict,
        dedupe_key: str = "",
        receivable_id: uuid.UUID | None = None,
        payout_id: uuid.UUID | None = None,
        payout_line_id: uuid.UUID | None = None,
        consignment_id: uuid.UUID | None = None,
    ) -> int:
        """Open a case unless one just like it already exists.

        Returns 1 if a case was created, 0 otherwise. Re-running the engine
        must not grow the list — a case list that doubles on every scan is one
        the seller stops reading, and then the engine has achieved nothing. A
        dismissed case stays dismissed for the same reason.

        Two runs racing to open the same case both pass the lookup; the unique
        key lets one insert through and the other is treated as "exists".
        """
        existing = await self._db.execute(
            sa.select(ReconciliationCase.id).where(
                ReconciliationCase.kind == str(kind),
                ReconciliationCase.subject_type == subject_type,
                ReconciliationCase.subject_id == subject_id,
                ReconciliationCase.dedupe_key == dedupe_key,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return 0

        case = ReconciliationCase(
            kind=str(kind),
            status=str(CaseStatus.OPEN),
            priority=str(priority_for(kind, amount_paisa)),
            subject_type=subject_type,
            subject_id=subject_id,
            dedupe_key=dedupe_key,
            amount_paisa=amount_paisa,
            summary=summary[:400],
            detail=detail,
            receivable_id=receivable_id,
            payout_id=payout_id,
            payout_line_id=payout_line_id,
            consignment_id=consignment_id,
            opened_at=utc_now(),
        )
        try:
            async with self._db.begin_nested():
                self._db.add(case)
                await self._db.flush()
        except IntegrityError:
            return 0

        await self._append_event(case, CaseEventAction.OPENED, to_status=case.status)
        await record_audit(
            self._db,
            action=AuditAction.RECONCILIATION_CASE_OPENED,
            entity_type="reconciliation_case",
            entity_id=case.id,
            context={"kind": str(kind), "amount_paisa": amount_paisa},
        )
        return 1

    async def _raise_amount_cases(
        self,
        line: PayoutLine,
        *,
        receivable_id: uuid.UUID,
        outstanding_before: int,
        reference: str,
        payout: Payout,
        gross_paisa: int,
    ) -> int:
        """Open a case when the money and the parcel disagree.

        Both directions matter. An underpayment is money the seller has not
        received; an overpayment is usually somebody else's money, and
        absorbing it quietly makes it the seller's problem when the provider
        notices.

        V2 compares what the courier *collected* — the row's amount plus the
        charges it itemised — rather than the net. A ৳1,325 row with an ৳80 COD
        fee against ৳1,405 owed is not an underpayment; it is a charge, and it
        is reviewed as one.
        """
        opened = 0
        tolerance = self._config.tolerance_for(outstanding_before)
        difference = gross_paisa - outstanding_before

        if difference < -tolerance:
            shortfall = -difference
            await self._receivables.mark_mismatched(
                receivable_id, reason=f"Short by {shortfall} paisa"
            )
            opened += await self._open_case(
                kind=CaseKind.UNDERPAID,
                subject_type="cod_receivable",
                subject_id=receivable_id,
                amount_paisa=shortfall,
                summary=(f"{reference} was paid short by {shortfall / 100:.2f} taka"),
                detail={
                    "expected_paisa": outstanding_before,
                    "received_paisa": line.amount_paisa,
                    "collected_paisa": gross_paisa,
                    "tolerance_paisa": tolerance,
                },
                dedupe_key=str(shortfall),
                receivable_id=receivable_id,
                payout_id=payout.id,
                payout_line_id=line.id,
            )
        elif difference > tolerance:
            excess = difference
            opened += await self._open_case(
                kind=CaseKind.OVERPAID,
                subject_type="cod_receivable",
                subject_id=receivable_id,
                amount_paisa=excess,
                summary=(f"{reference} was paid {excess / 100:.2f} taka more than it was owed"),
                detail={
                    "expected_paisa": outstanding_before,
                    "received_paisa": line.amount_paisa,
                    "collected_paisa": gross_paisa,
                    "tolerance_paisa": tolerance,
                },
                dedupe_key=str(excess),
                receivable_id=receivable_id,
                payout_id=payout.id,
                payout_line_id=line.id,
            )
        return opened

    async def _raise_duplicate_cases(self, payout_id: uuid.UUID, lines: list[PayoutLine]) -> int:
        opened = 0
        for line in lines:
            if line.line_status is not PayoutLineStatus.DUPLICATE:
                continue
            opened += await self._open_case(
                kind=CaseKind.DUPLICATE_PAYOUT_LINE,
                subject_type="payout_line",
                subject_id=line.id,
                amount_paisa=line.amount_paisa,
                summary=(f"Row {line.row_number} repeats a reference already in this statement"),
                detail={"reference": self._source_ref(line), "raw": line.raw},
                payout_id=payout_id,
                payout_line_id=line.id,
            )
        return opened

    async def _raise_unknown_deduction_cases(self, payout_id: uuid.UUID) -> int:
        """Surface every deduction we could not name.

        Section 84: unknown deductions must remain visible. A case is how they
        stay visible after the import screen is closed.
        """
        rows = await self._db.execute(
            sa.select(PayoutAdjustment).where(
                PayoutAdjustment.payout_id == payout_id,
                PayoutAdjustment.type == str(AdjustmentType.UNKNOWN_DEDUCTION),
            )
        )
        opened = 0
        for adjustment in rows.scalars().all():
            opened += await self._open_case(
                kind=CaseKind.UNKNOWN_DEDUCTION,
                subject_type="payout_adjustment",
                subject_id=adjustment.id,
                amount_paisa=adjustment.amount_paisa,
                summary=(
                    "The courier deducted "
                    f"{adjustment.amount_paisa / 100:.2f} taka and we could not "
                    "tell what for"
                ),
                detail={
                    "provider_label": adjustment.provider_label,
                    "raw_text": adjustment.raw_text,
                },
                payout_id=payout_id,
                payout_line_id=adjustment.payout_line_id,
            )
        return opened

    async def _scan_delivered_but_unpaid(self) -> int:
        cutoff = utc_now() - timedelta(days=UNPAID_ALERT_DAYS)
        rows = await self._db.execute(
            sa.select(CodReceivable, Consignment)
            .join(Consignment, Consignment.id == CodReceivable.consignment_id)
            .where(
                CodReceivable.status == str(ReceivableStatus.ELIGIBLE),
                CodReceivable.eligible_at.is_not(None),
                CodReceivable.eligible_at <= cutoff,
            )
        )
        opened = 0
        for receivable, consignment in rows.all():
            outstanding = receivable.outstanding_paisa
            if outstanding <= 0:
                continue
            days = receivable.age_in_days(as_of=utc_now()) or 0
            opened += await self._open_case(
                kind=CaseKind.DELIVERED_BUT_UNPAID,
                subject_type="cod_receivable",
                subject_id=receivable.id,
                amount_paisa=outstanding,
                summary=(
                    f"{consignment.merchant_reference} was delivered {days} days "
                    f"ago and {outstanding / 100:.2f} taka has not arrived"
                ),
                detail={"days_outstanding": days, "provider": receivable.provider},
                receivable_id=receivable.id,
                consignment_id=consignment.id,
            )
        return opened

    async def _scan_stale_in_transit(self) -> int:
        cutoff = utc_now() - timedelta(days=STALE_IN_TRANSIT_DAYS)
        rows = await self._db.execute(
            sa.select(Consignment).where(
                Consignment.status.in_(
                    [
                        str(ConsignmentStatus.BOOKED),
                        str(ConsignmentStatus.PICKED_UP),
                        str(ConsignmentStatus.IN_TRANSIT),
                        str(ConsignmentStatus.OUT_FOR_DELIVERY),
                    ]
                ),
                Consignment.booked_at.is_not(None),
                Consignment.booked_at <= cutoff,
            )
        )
        opened = 0
        for consignment in rows.scalars().all():
            days = (utc_now() - consignment.booked_at).days if consignment.booked_at else 0
            opened += await self._open_case(
                kind=CaseKind.STALE_IN_TRANSIT,
                subject_type="consignment",
                subject_id=consignment.id,
                amount_paisa=consignment.cod_amount_paisa,
                summary=(f"{consignment.merchant_reference} has been in transit for {days} days"),
                detail={"status": consignment.status, "days": days},
                consignment_id=consignment.id,
            )
        return opened

    async def _scan_returned_not_restocked(self) -> int:
        """Returned parcels whose units nobody has received back yet.

        Section 16's last case. Since V2.2 a courier's RETURNED no longer
        restocks by itself — the seller records what physically came back
        (restocked, or damaged and not restocked). A returned line with no such
        decision is the gap this surfaces; recording the decision resolves the
        case (:func:`resolve_returned_not_restocked`).
        """
        undecided = (
            sa.select(ConsignmentItem.consignment_id)
            .where(
                ConsignmentItem.qty_returned > 0,
                ConsignmentItem.return_received_at.is_(None),
            )
            .distinct()
        )
        rows = await self._db.execute(
            sa.select(Consignment).where(
                Consignment.status.in_(
                    [
                        str(ConsignmentStatus.RETURNED),
                        str(ConsignmentStatus.PARTIAL_DELIVERED),
                    ]
                ),
                Consignment.id.in_(undecided),
            )
        )

        opened = 0
        for consignment in rows.scalars().all():
            opened += await self._open_case(
                kind=CaseKind.RETURNED_NOT_RESTOCKED,
                subject_type="consignment",
                subject_id=consignment.id,
                amount_paisa=0,
                summary=(
                    f"{consignment.merchant_reference} came back but has not been "
                    "received into stock"
                ),
                detail={"status": consignment.status},
                consignment_id=consignment.id,
            )
        return opened

    async def _get_line(self, line_id: uuid.UUID) -> PayoutLine:
        line = await self._db.get(PayoutLine, line_id)
        if line is None:
            raise NotFoundError("Payout line not found")
        return line

    async def _get_item(self, item_id: uuid.UUID) -> ReconciliationItem:
        item = await self._db.get(ReconciliationItem, item_id)
        if item is None:
            raise NotFoundError("Reconciliation item not found")
        return item


async def resolve_returned_not_restocked(db: AsyncSession, consignment_id: uuid.UUID) -> None:
    """Close a parcel's "returned but not restocked" case once it is received.

    Called by the return-receipt flow. The Smart Alerts rule counts open cases
    of this kind, so closing it here is also what clears that alert.
    """
    cases = (
        (
            await db.execute(
                sa.select(ReconciliationCase).where(
                    ReconciliationCase.kind == str(CaseKind.RETURNED_NOT_RESTOCKED),
                    ReconciliationCase.subject_type == "consignment",
                    ReconciliationCase.subject_id == consignment_id,
                )
            )
        )
        .scalars()
        .all()
    )
    still_open = [case for case in cases if case.case_status.is_open]
    if not still_open:
        return
    await ReconciliationService(db)._close_cases(
        still_open,
        action=CaseEventAction.AUTO_RESOLVED,
        note="The returned items were received and a stock decision recorded",
    )
