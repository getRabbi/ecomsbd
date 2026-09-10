"""Money endpoints (master spec section 39).

``GET /v1/money/summary`` is the Money screen's headline. Everything in it is
derived from the ledger and the receivables rather than from a maintained
counter, so section 81.10 — the dashboard reconciles to the ledger — holds by
construction rather than by discipline.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, TenantPrincipal, require_permission
from app.api.v1.money_schemas import (
    AgingBandResponse,
    DisputePayload,
    LedgerEntryResponse,
    ManualCorrectionPayload,
    MoneySummaryResponse,
    ReceivableResponse,
    WriteOffPayload,
)
from app.common.pagination import Page, decode_cursor
from app.core.clock import utc_now
from app.ledger.models import LedgerBucket, LedgerDirection, LedgerEventType, LedgerSource
from app.ledger.service import LedgerService
from app.money.models import CodReceivable, ReceivableStatus
from app.money.service import ReceivableService
from app.orders.models import Order
from app.payouts.models import Payout
from app.reconciliation.models import CaseStatus, ReconciliationCase
from app.tenants.roles import Permission

router = APIRouter(prefix="/money", tags=["money"])


async def _receivables(db: DbSession) -> ReceivableService:
    return ReceivableService(db)


async def _ledger(db: DbSession) -> LedgerService:
    return LedgerService(db)


ReceivablesDep = Annotated[ReceivableService, Depends(_receivables)]
LedgerDep = Annotated[LedgerService, Depends(_ledger)]


def _to_response(
    receivable: CodReceivable, *, order_number: str | None = None
) -> ReceivableResponse:
    return ReceivableResponse(
        id=receivable.id,
        consignment_id=receivable.consignment_id,
        order_id=receivable.order_id,
        provider=receivable.provider,
        status=receivable.status,
        collectible_paisa=receivable.collectible_paisa,
        settled_paisa=receivable.settled_paisa,
        deduction_paisa=receivable.deduction_paisa,
        adjustment_paisa=receivable.adjustment_paisa,
        outstanding_paisa=receivable.outstanding_paisa,
        eligible_at=receivable.eligible_at,
        settled_at=receivable.settled_at,
        status_reason=receivable.status_reason,
        version=receivable.version,
        created_at=receivable.created_at,
        age_days=receivable.age_in_days(as_of=utc_now()),
        order_number=order_number or receivable.metadata_json.get("order_number"),
    )


@router.get(
    "/summary",
    response_model=MoneySummaryResponse,
    summary="Money headline",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def money_summary(
    principal: TenantPrincipal,
    db: DbSession,
    receivables: ReceivablesDep,
    ledger: LedgerDep,
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
) -> MoneySummaryResponse:
    """What the courier owes, what arrived, and what was taken off.

    The deduction buckets are reported separately — courier charge, COD fee,
    return charge, and the ones we could not classify. Section 84 forbids
    folding an unknown deduction into a familiar category, and a summary that
    reported one "fees" total would do exactly that.
    """
    balances = await ledger.balances(since=since, until=until)
    aging = await receivables.aging()

    unpaid_count = await db.execute(
        sa.select(sa.func.count())
        .select_from(CodReceivable)
        .where(CodReceivable.status == str(ReceivableStatus.ELIGIBLE))
    )
    open_cases = await db.execute(
        sa.select(sa.func.count())
        .select_from(ReconciliationCase)
        .where(ReconciliationCase.status.in_([str(CaseStatus.OPEN), str(CaseStatus.IN_PROGRESS)]))
    )
    unexplained = await db.execute(
        sa.select(sa.func.coalesce(sa.func.sum(Payout.total_paisa - Payout.applied_paisa), 0))
    )

    return MoneySummaryResponse(
        outstanding_paisa=sum(amount for _, _, amount in aging),
        settled_paisa=balances[LedgerBucket.COD_SETTLED].net_paisa,
        unpaid_parcel_count=int(unpaid_count.scalar_one()),
        courier_charge_paisa=-balances[LedgerBucket.COURIER_CHARGE].net_paisa,
        cod_fee_paisa=-balances[LedgerBucket.COD_FEE].net_paisa,
        return_charge_paisa=-balances[LedgerBucket.RETURN_CHARGE].net_paisa,
        unknown_deduction_paisa=-balances[LedgerBucket.OTHER].net_paisa,
        write_off_paisa=-balances[LedgerBucket.WRITE_OFF].net_paisa,
        unexplained_payout_paisa=max(0, int(unexplained.scalar_one() or 0)),
        open_case_count=int(open_cases.scalar_one()),
        aging=[
            AgingBandResponse(
                label=bucket.label,
                min_days=bucket.min_days,
                max_days=bucket.max_days,
                parcel_count=count,
                outstanding_paisa=amount,
            )
            for bucket, count, amount in aging
        ],
        since=since,
        until=until,
    )


@router.get(
    "/receivables",
    response_model=Page[ReceivableResponse],
    summary="COD receivables",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def list_receivables(
    principal: TenantPrincipal,
    db: DbSession,
    receivables: ReceivablesDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    status: Annotated[ReceivableStatus | None, Query()] = None,
    provider: Annotated[str | None, Query(max_length=40)] = None,
    open_only: Annotated[bool, Query()] = False,
) -> Page[ReceivableResponse]:
    """What each parcel still owes.

    ``open_only`` restricts this to money a courier is actually holding.
    Parcels in transit are deliberately excluded from that: nobody owes
    anything until a parcel has been delivered, and counting them would inflate
    a seller's expected cash by their entire pipeline.
    """
    rows = await receivables.list_receivables(
        limit=limit,
        cursor=decode_cursor(cursor) if cursor else None,
        status=status,
        provider=provider,
        open_only=open_only,
    )

    numbers: dict[uuid.UUID, str] = {}
    if rows:
        result = await db.execute(
            sa.select(Order.id, Order.order_number).where(
                Order.id.in_([row.order_id for row in rows])
            )
        )
        numbers = {row[0]: row[1] for row in result.all()}

    return Page[ReceivableResponse].build(
        rows,
        limit=limit,
        serializer=lambda row: _to_response(row, order_number=numbers.get(row.order_id)),
    )


@router.get(
    "/aging",
    response_model=list[AgingBandResponse],
    summary="COD aging",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def cod_aging(
    principal: TenantPrincipal,
    receivables: ReceivablesDep,
    provider: Annotated[str | None, Query(max_length=40)] = None,
) -> list[AgingBandResponse]:
    """Outstanding money grouped by how long it has been waiting.

    Days rather than weeks: for a Bangladeshi courier the difference between
    four days and eight is the difference between normal and worth chasing.
    """
    aging = await receivables.aging(provider=provider)
    return [
        AgingBandResponse(
            label=bucket.label,
            min_days=bucket.min_days,
            max_days=bucket.max_days,
            parcel_count=count,
            outstanding_paisa=amount,
        )
        for bucket, count, amount in aging
    ]


@router.get(
    "/receivables/{receivable_id}/ledger",
    response_model=list[LedgerEntryResponse],
    summary="Explain a receivable",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def receivable_ledger(
    receivable_id: uuid.UUID,
    principal: TenantPrincipal,
    ledger: LedgerDep,
    receivables: ReceivablesDep,
) -> list[LedgerEntryResponse]:
    """Every money event behind one parcel, oldest first.

    This is the answer to "why does this say ৳0 outstanding?". Both the
    consignment's entries (the delivery) and the receivable's (the payments and
    deductions) are returned, because the seller is asking about the parcel,
    not about our internal split.
    """
    receivable = await receivables.get(receivable_id)
    entries = await ledger.entries_for("consignment", receivable.consignment_id)
    entries += await ledger.entries_for("cod_receivable", receivable.id)
    entries.sort(key=lambda entry: (entry.occurred_at, entry.id))
    return [LedgerEntryResponse.model_validate(entry) for entry in entries]


@router.post(
    "/receivables/{receivable_id}/correction",
    response_model=ReceivableResponse,
    summary="Correct a balance by hand",
    dependencies=[Depends(require_permission(Permission.MONEY_CORRECT))],
)
async def correct_receivable(
    receivable_id: uuid.UUID,
    payload: ManualCorrectionPayload,
    principal: TenantPrincipal,
    receivables: ReceivablesDep,
    ledger: LedgerDep,
) -> ReceivableResponse:
    """Adjust what a parcel is owed, with a reason.

    Master spec section 134. The correction states a *change*, never a new
    balance — "never directly update settled totals" — and it writes a ledger
    entry with the actor and the reason attached, so the resulting balance can
    always be traced back to the person who decided it.
    """
    receivable = await receivables.get(receivable_id)
    delta = payload.amount_paisa if payload.increases_balance else -payload.amount_paisa
    receivable.adjustment_paisa += delta
    receivable.version += 1

    await ledger.record(
        event_type=LedgerEventType.MANUAL_ADJUSTMENT,
        entity_type="cod_receivable",
        entity_id=receivable.id,
        amount_paisa=payload.amount_paisa,
        direction=(LedgerDirection.CREDIT if payload.increases_balance else LedgerDirection.DEBIT),
        source=LedgerSource.SELLER,
        reason=payload.reason,
        source_ref=str(receivable.consignment_id),
    )
    return _to_response(receivable)


@router.post(
    "/receivables/{receivable_id}/write-off",
    response_model=ReceivableResponse,
    summary="Give up on collecting",
    dependencies=[Depends(require_permission(Permission.MONEY_CORRECT))],
)
async def write_off_receivable(
    receivable_id: uuid.UUID,
    payload: WriteOffPayload,
    principal: TenantPrincipal,
    receivables: ReceivablesDep,
) -> ReceivableResponse:
    """Write off what a courier will not pay.

    Terminal and audited. The outstanding amount is written to the
    ``WRITE_OFF`` ledger bucket rather than simply disappearing from the
    outstanding total — a loss the seller cannot see is a loss they cannot
    learn from.
    """
    receivable = await receivables.write_off(receivable_id, reason=payload.reason)
    return _to_response(receivable)


@router.post(
    "/receivables/{receivable_id}/dispute",
    response_model=ReceivableResponse,
    summary="Mark a receivable disputed",
    dependencies=[Depends(require_permission(Permission.MONEY_RECONCILE))],
)
async def dispute_receivable(
    receivable_id: uuid.UUID,
    payload: DisputePayload,
    principal: TenantPrincipal,
    receivables: ReceivablesDep,
) -> ReceivableResponse:
    """Record that the seller has raised this with the provider.

    A disputed receivable is still money owed and still appears in the
    outstanding total. Raising a case does not make the money stop existing.
    """
    receivable = await receivables.mark_disputed(receivable_id, reason=payload.reason)
    return _to_response(receivable)


__all__ = ["router"]
