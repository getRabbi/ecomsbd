"""The reconciliation engine.

Master spec section 16 calls this "the primary paid feature", and section 112
says how to build it: *precision before recall*. Every decision here leans the
same way — when the evidence is short of certain, the engine produces a
suggestion and a case, and stops.

Three entry points:

* :meth:`ReconciliationService.reconcile` matches a payout's lines against
  outstanding receivables and applies the ones that are certain;
* the same method with ``shadow=True`` runs the whole thing and changes
  nothing, which is section 112's shadow mode;
* :meth:`ReconciliationService.match_manually` applies a line because a person
  said so, recording who and why (section 81.7).

Unmatching is :meth:`ReconciliationService.unmatch`, which reverses rather than
deletes (section 81.8).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.pagination import Cursor, apply_cursor
from app.consignments.models import Consignment, ConsignmentStatus
from app.core.clock import utc_now
from app.core.context import current_context
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.customers.models import Customer
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
)
from app.payouts.service import PayoutService
from app.reconciliation.models import (
    CaseKind,
    CaseStatus,
    ReconciliationCase,
    priority_for,
)
from app.reconciliation.scoring import (
    Candidate,
    MatchDecision,
    ScoringConfig,
    decide,
    score_candidate,
)

__all__ = ["STALE_IN_TRANSIT_DAYS", "ReconciliationReport", "ReconciliationService"]

#: How long a parcel may sit in transit before it is worth asking about.
#: Section 87 forbids hard-coding "a return takes N days" for settlement; this
#: is an *alert* threshold, which the same section says to make configurable.
#: Default only — a provider median replaces it once there is history.
STALE_IN_TRANSIT_DAYS = 14

#: How long after delivery money may be outstanding before it becomes a case.
UNPAID_ALERT_DAYS = 10


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
    applied_paisa: int = 0
    cases_opened: int = 0
    decisions: list[tuple[uuid.UUID, MatchDecision]] = field(default_factory=list)

    @property
    def total_lines(self) -> int:
        return self.exact_matches + self.suggested + self.unresolved


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

    @property
    def config(self) -> ScoringConfig:
        return self._config

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
        payout = await self._payouts.get(payout_id)
        lines = await self._payouts.lines(payout_id)
        report = ReconciliationReport(payout_id=payout_id, shadow=shadow)

        for line in lines:
            if line.line_status.is_resolved or line.line_status is PayoutLineStatus.REVERSED:
                continue

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

            decision = await self._decide_for(line, payout)
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
                applied = await self._apply(line, decision.chosen, payout)
                report.applied_paisa += applied
                report.cases_opened += await self._raise_amount_cases(line, decision.chosen, payout)
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
            report.cases_opened += await self._raise_duplicate_cases(payout_id, lines)
            report.cases_opened += await self._raise_unknown_deduction_cases(payout_id)
            await self._payouts.refresh_totals(payout_id)
            await record_audit(
                self._db,
                action=AuditAction.PAYOUT_RECONCILED,
                entity_type="payout",
                entity_id=payout_id,
                context={
                    "exact_matches": report.exact_matches,
                    "suggested": report.suggested,
                    "unresolved": report.unresolved,
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
        """
        if not reason.strip():
            raise ValidationError("A manual match needs a reason")

        line = await self._get_line(line_id)
        if line.line_status.is_applied:
            raise ConflictError(
                "That line is already matched",
                details={"status": line.status},
            )

        receivable = await self._db.get(CodReceivable, receivable_id)
        if receivable is None:
            raise NotFoundError("Receivable not found")

        payout = await self._payouts.get(line.payout_id)
        amount = amount_paisa if amount_paisa is not None else line.unapplied_paisa
        applied = await self._settle(line, receivable, payout, amount)

        context = current_context()
        line.status = str(PayoutLineStatus.MANUAL_MATCHED)
        line.confidence = str(MatchConfidence.MANUAL_REQUIRED)
        line.matched_by = context.user_id if context else None
        line.match_reason = reason
        line.matched_at = utc_now()
        await self._db.flush()

        await self._payouts.refresh_totals(line.payout_id)
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
        if not line.line_status.is_applied:
            raise ConflictError("That line is not matched", details={"status": line.status})
        if line.receivable_id is None:
            raise ConflictError("That line has no receivable to unmatch from")

        await self._receivables.reverse_settlement(
            line.receivable_id,
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
        return line

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

    # ---------------------------------------------------------------- cases --

    async def list_cases(
        self,
        *,
        limit: int = 30,
        cursor: Cursor | None = None,
        status: CaseStatus | None = None,
        kind: CaseKind | None = None,
    ) -> list[ReconciliationCase]:
        stmt = sa.select(ReconciliationCase)
        if status is not None:
            stmt = stmt.where(ReconciliationCase.status == str(status))
        if kind is not None:
            stmt = stmt.where(ReconciliationCase.kind == str(kind))
        stmt = apply_cursor(stmt, ReconciliationCase, cursor)
        stmt = stmt.order_by(
            ReconciliationCase.created_at.desc(), ReconciliationCase.id.desc()
        ).limit(limit + 1)
        return list((await self._db.execute(stmt)).scalars().all())

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
        """
        case = await self._db.get(ReconciliationCase, case_id)
        if case is None:
            raise NotFoundError("Case not found")

        if status in (CaseStatus.RESOLVED, CaseStatus.DISMISSED) and not (resolution or "").strip():
            raise ValidationError("Say what happened before closing a case")

        context = current_context()
        case.status = str(status)
        case.resolution = resolution
        if not status.is_open:
            case.resolved_at = utc_now()
            case.resolved_by = context.user_id if context else None
        await self._db.flush()

        await record_audit(
            self._db,
            action=AuditAction.RECONCILIATION_CASE_RESOLVED,
            entity_type="reconciliation_case",
            entity_id=case.id,
            reason=resolution,
            context={"kind": case.kind, "status": str(status)},
        )
        return case

    # ------------------------------------------------------------ internals --

    async def _decide_for(self, line: PayoutLine, payout: Payout) -> MatchDecision:
        candidates = await self._candidates_for(line, payout)
        return decide(candidates, config=self._config)

    async def _candidates_for(self, line: PayoutLine, payout: Payout) -> list[Candidate]:
        """Every parcel from this provider that could be the one.

        Same provider is mandatory (section 82), which also keeps the candidate
        set small: a shop reconciling a Steadfast statement never scores its
        Pathao parcels.
        """
        rows = await self._db.execute(
            sa.select(CodReceivable, Consignment)
            .join(Consignment, Consignment.id == CodReceivable.consignment_id)
            .where(CodReceivable.provider == payout.provider)
            .where(
                CodReceivable.status.in_(
                    [str(status) for status in ReceivableStatus if status.is_collectible]
                )
            )
        )

        pairs = list(rows.all())
        phones = await self._phone_last4_for([receivable.order_id for receivable, _ in pairs])

        return [
            score_candidate(
                line,
                receivable,
                consignment,
                config=self._config,
                customer_phone_last4=phones.get(receivable.order_id),
            )
            for receivable, consignment in pairs
        ]

    async def _phone_last4_for(self, order_ids: list[uuid.UUID]) -> dict[uuid.UUID, str | None]:
        """Last four digits of each order's customer number.

        The last four is all the matcher needs and all a statement ever gives.
        Nothing here reads a full number, so a reconciliation run cannot become
        a way to bulk-read customer phones.
        """
        if not order_ids:
            return {}
        rows = await self._db.execute(
            sa.select(Order.id, Customer.phone_last4)
            .join(Customer, Customer.id == Order.customer_id)
            .where(Order.id.in_(order_ids))
        )
        return {row[0]: row[1] for row in rows.all()}

    async def _apply(self, line: PayoutLine, candidate: Candidate, payout: Payout) -> int:
        receivable = await self._db.get(CodReceivable, candidate.receivable_id)
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
        """Open a case unless one just like it is already open.

        Returns 1 if a case was created, 0 otherwise. Re-running the engine
        must not grow the list — a case list that doubles on every scan is one
        the seller stops reading, and then the engine has achieved nothing.
        """
        existing = await self._db.execute(
            sa.select(ReconciliationCase).where(
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
            summary=summary,
            detail=detail,
            receivable_id=receivable_id,
            payout_id=payout_id,
            payout_line_id=payout_line_id,
            consignment_id=consignment_id,
            opened_at=utc_now(),
        )
        self._db.add(case)
        await self._db.flush()

        await record_audit(
            self._db,
            action=AuditAction.RECONCILIATION_CASE_OPENED,
            entity_type="reconciliation_case",
            entity_id=case.id,
            context={"kind": str(kind), "amount_paisa": amount_paisa},
        )
        return 1

    async def _raise_amount_cases(
        self, line: PayoutLine, candidate: Candidate, payout: Payout
    ) -> int:
        """Open a case when the money and the parcel disagree.

        Both directions matter. An underpayment is money the seller has not
        received; an overpayment is usually somebody else's money, and
        absorbing it quietly makes it the seller's problem when the provider
        notices.
        """
        opened = 0
        receivable = await self._db.get(CodReceivable, candidate.receivable_id)
        if receivable is None:
            return 0

        tolerance = self._config.tolerance_for(candidate.outstanding_paisa)
        difference = line.amount_paisa - candidate.outstanding_paisa

        if difference < -tolerance:
            shortfall = -difference
            await self._receivables.mark_mismatched(
                receivable.id, reason=f"Short by {shortfall} paisa"
            )
            opened += await self._open_case(
                kind=CaseKind.UNDERPAID,
                subject_type="cod_receivable",
                subject_id=receivable.id,
                amount_paisa=shortfall,
                summary=(
                    f"{candidate.merchant_reference} was paid short by {shortfall / 100:.2f} taka"
                ),
                detail={
                    "expected_paisa": candidate.outstanding_paisa,
                    "received_paisa": line.amount_paisa,
                    "tolerance_paisa": tolerance,
                },
                dedupe_key=str(shortfall),
                receivable_id=receivable.id,
                payout_id=payout.id,
                payout_line_id=line.id,
            )
        elif difference > tolerance:
            excess = difference
            opened += await self._open_case(
                kind=CaseKind.OVERPAID,
                subject_type="cod_receivable",
                subject_id=receivable.id,
                amount_paisa=excess,
                summary=(
                    f"{candidate.merchant_reference} was paid "
                    f"{excess / 100:.2f} taka more than it was owed"
                ),
                detail={
                    "expected_paisa": candidate.outstanding_paisa,
                    "received_paisa": line.amount_paisa,
                    "tolerance_paisa": tolerance,
                },
                dedupe_key=str(excess),
                receivable_id=receivable.id,
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
        """Returned parcels whose units never went back on the shelf.

        Section 16's last case. The dispatch flow restores stock as part of
        recording the return, so this should stay empty — which is exactly why
        it is worth scanning for: a non-empty result means something bypassed
        that path.
        """
        from app.products.models import StockMovement, StockMovementReason

        rows = await self._db.execute(
            sa.select(Consignment).where(
                Consignment.status.in_(
                    [
                        str(ConsignmentStatus.RETURNED),
                        str(ConsignmentStatus.PARTIAL_DELIVERED),
                    ]
                )
            )
        )
        restore_reasons = [
            str(StockMovementReason.RETURN_RESTORE),
            str(StockMovementReason.PARTIAL_RETURN_RESTORE),
            str(StockMovementReason.CANCEL_RESTORE),
        ]

        opened = 0
        for consignment in rows.scalars().all():
            restored = await self._db.execute(
                sa.select(sa.func.count())
                .select_from(StockMovement)
                .where(
                    StockMovement.consignment_id == consignment.id,
                    StockMovement.reason.in_(restore_reasons),
                )
            )
            if restored.scalar_one() > 0:
                continue
            opened += await self._open_case(
                kind=CaseKind.RETURNED_NOT_RESTOCKED,
                subject_type="consignment",
                subject_id=consignment.id,
                amount_paisa=0,
                summary=(
                    f"{consignment.merchant_reference} came back but its stock was never restored"
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
