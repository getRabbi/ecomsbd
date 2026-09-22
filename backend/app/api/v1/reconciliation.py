"""Reconciliation endpoints (master spec section 39).

The engine matches; people decide the rest. Every endpoint here that moves
money requires either an exact reference the engine found on its own, or a
stated reason from a person (section 81.7).

Entitlements (section 26): reconciliation *actions* need the ``reconciliation``
entitlement; **reading cases and results does not**. Section 51 is explicit that
a seller's own money history is never locked behind a plan, so a downgraded shop
can still open every case it has and see what happened — it simply cannot run
the engine again or move a settlement.

Permissions: reading needs ``money.view``; anything that writes — matching,
accepting charges, closing, reopening or annotating a case — needs
``money.reconcile``, which only the Owner and Finance roles hold.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, EntitlementsDep, TenantPrincipal, require_permission
from app.api.v1.money_schemas import (
    AcceptChargesPayload,
    CaseDetailResponse,
    CaseEventResponse,
    CaseNotePayload,
    CaseResponse,
    CaseUpdatePayload,
    ChargeAcceptanceResponse,
    ManualMatchPayload,
    PayoutAdjustmentResponse,
    PayoutLineResponse,
    ReconcileResponse,
    ReconciliationItemDetailResponse,
    ReconciliationItemResponse,
    ReconciliationSummaryResponse,
    UnmatchPayload,
)
from app.common.pagination import Page, decode_cursor
from app.entitlements.catalog import Entitlement
from app.reconciliation.models import (
    CaseKind,
    CasePriority,
    CaseStatus,
    ItemStatus,
    ReconciliationItem,
)
from app.reconciliation.service import ChargeAcceptance, ReconciliationService
from app.tenants.roles import Permission

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


async def _engine(db: DbSession) -> ReconciliationService:
    return ReconciliationService(db)


EngineDep = Annotated[ReconciliationService, Depends(_engine)]


def _item(
    item: ReconciliationItem, case_statuses: dict[uuid.UUID, str]
) -> ReconciliationItemResponse:
    response = ReconciliationItemResponse.model_validate(item)
    if item.case_id is not None and item.case_id in case_statuses:
        response.case_status = CaseStatus(case_statuses[item.case_id])
    return response


def _acceptance(result: ChargeAcceptance) -> ChargeAcceptanceResponse:
    return ChargeAcceptanceResponse(
        accepted_paisa=result.accepted_paisa,
        adjustments=result.adjustments,
        items=result.items,
    )


@router.post(
    "/payouts/{payout_id}/reconcile",
    response_model=ReconcileResponse,
    summary="Match a payout against outstanding parcels",
    dependencies=[Depends(require_permission(Permission.MONEY_RECONCILE))],
)
async def reconcile_payout(
    payout_id: uuid.UUID,
    principal: TenantPrincipal,
    engine: EngineDep,
    entitlements: EntitlementsDep,
    shadow: Annotated[bool, Query()] = False,
) -> ReconcileResponse:
    """Run the engine over a payout's lines.

    Only lines the engine is *certain* about are applied: an exact provider
    reference, or a single candidate scoring at or above the threshold.
    Everything else becomes a suggestion or a case, because section 112 puts
    precision ahead of recall — a missed match costs a click, a wrong one costs
    the seller their trust in the figures.

    Safe to repeat: resolved lines are skipped, cases are de-duplicated, and a
    row already paid in another statement is flagged rather than applied again.

    ``shadow=true`` runs the whole thing and writes nothing. That is how a
    matching rule is measured before it is allowed to move money.
    """
    await entitlements.require(principal.require_tenant(), Entitlement.RECONCILIATION)
    report = await engine.reconcile(payout_id, shadow=shadow)
    return ReconcileResponse(
        payout_id=report.payout_id,
        shadow=report.shadow,
        exact_matches=report.exact_matches,
        suggested=report.suggested,
        unresolved=report.unresolved,
        duplicates=report.duplicates,
        charge_only=report.charge_only,
        applied_paisa=report.applied_paisa,
        cases_opened=report.cases_opened,
    )


@router.post(
    "/payouts/{payout_id}/accept-charges",
    response_model=ChargeAcceptanceResponse,
    summary="Accept the courier's charges on every agreeing parcel in a payout",
    dependencies=[Depends(require_permission(Permission.MONEY_RECONCILE))],
)
async def accept_payout_charges(
    payout_id: uuid.UUID,
    payload: AcceptChargesPayload,
    principal: TenantPrincipal,
    engine: EngineDep,
    entitlements: EntitlementsDep,
) -> ChargeAcceptanceResponse:
    """Write the courier's deductions into the ledger for matched parcels.

    Only parcels whose COD agrees and whose deductions are all named. A charge
    that differs from the one on record, or one nobody could name, is left for
    the seller to look at individually. Repeating the call accepts nothing new.
    """
    await entitlements.require(principal.require_tenant(), Entitlement.RECONCILIATION)
    return _acceptance(await engine.accept_payout_charges(payout_id, reason=payload.reason))


@router.post(
    "/lines/{line_id}/match",
    response_model=PayoutLineResponse,
    summary="Apply a payout line by hand",
    dependencies=[Depends(require_permission(Permission.MONEY_RECONCILE))],
)
async def match_line(
    line_id: uuid.UUID,
    payload: ManualMatchPayload,
    principal: TenantPrincipal,
    engine: EngineDep,
    entitlements: EntitlementsDep,
) -> PayoutLineResponse:
    """Settle a parcel because a person decided this line pays for it.

    The reason is mandatory and stored with the actor (section 81.7). This is
    the other half of the engine refusing to guess: the decision moves to
    somebody who can pick up a phone and ask.
    """
    await entitlements.require(principal.require_tenant(), Entitlement.RECONCILIATION)
    line = await engine.match_manually(
        line_id,
        payload.receivable_id,
        reason=payload.reason,
        amount_paisa=payload.amount_paisa,
    )
    return PayoutLineResponse.model_validate(line)


@router.post(
    "/lines/{line_id}/unmatch",
    response_model=PayoutLineResponse,
    summary="Undo a match",
    dependencies=[Depends(require_permission(Permission.MONEY_RECONCILE))],
)
async def unmatch_line(
    line_id: uuid.UUID,
    payload: UnmatchPayload,
    principal: TenantPrincipal,
    engine: EngineDep,
    entitlements: EntitlementsDep,
) -> PayoutLineResponse:
    """Reverse a settlement.

    Section 81.8: a reversal, not a destructive overwrite. The original ledger
    entries stay and two more undo them, so the history of the mistake survives
    alongside its correction.
    """
    await entitlements.require(principal.require_tenant(), Entitlement.RECONCILIATION)
    line = await engine.unmatch(line_id, reason=payload.reason)
    return PayoutLineResponse.model_validate(line)


@router.post(
    "/scan",
    response_model=dict,
    summary="Look for problems",
    dependencies=[Depends(require_permission(Permission.MONEY_RECONCILE))],
)
async def scan(
    principal: TenantPrincipal, engine: EngineDep, entitlements: EntitlementsDep
) -> dict:
    """Find the problems no statement would surface.

    Delivered parcels whose money never arrived, parcels stuck in transit, and
    returns whose stock was never restored. These come from the shop's own
    data rather than from a payout, so they are looked for on demand and on a
    schedule rather than on import.
    """
    await entitlements.require(principal.require_tenant(), Entitlement.RECONCILIATION)
    return {"cases_opened": await engine.scan_for_cases()}


# ------------------------------------------------------------------ items --


@router.get(
    "/summary",
    response_model=ReconciliationSummaryResponse,
    summary="Expected, actual and the difference",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def summary(
    principal: TenantPrincipal,
    engine: EngineDep,
    provider: Annotated[str | None, Query(max_length=40)] = None,
    payout_id: Annotated[uuid.UUID | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> ReconciliationSummaryResponse:
    """Headline figures over the same filters the review table uses."""
    result = await engine.summary(
        provider=provider, payout_id=payout_id, date_from=date_from, date_to=date_to
    )
    return ReconciliationSummaryResponse(
        counts=result.counts,
        matched=result.matched,
        discrepancies=result.discrepancies,
        unmatched=result.unmatched,
        expected_paisa=result.expected_paisa,
        actual_paisa=result.actual_paisa,
        difference_paisa=result.difference_paisa,
        unmatched_paisa=result.unmatched_paisa,
        duplicate_paisa=result.duplicate_paisa,
        charges_pending_paisa=result.charges_pending_paisa,
        open_cases=result.open_cases,
        open_case_paisa=result.open_case_paisa,
    )


@router.get(
    "/items",
    response_model=Page[ReconciliationItemResponse],
    summary="Expected against actual, parcel by parcel",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def list_items(
    principal: TenantPrincipal,
    engine: EngineDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    item_status: Annotated[list[ItemStatus] | None, Query(alias="status")] = None,
    discrepancies_only: Annotated[bool, Query()] = False,
    provider: Annotated[str | None, Query(max_length=40)] = None,
    payout_id: Annotated[uuid.UUID | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    case_status: Annotated[CaseStatus | None, Query()] = None,
) -> Page[ReconciliationItemResponse]:
    """Server-side filtered and cursor-paged. A client never holds the whole set."""
    rows = await engine.list_items(
        limit=limit,
        cursor=decode_cursor(cursor) if cursor else None,
        statuses=item_status,
        discrepancies_only=discrepancies_only,
        provider=provider,
        payout_id=payout_id,
        search=q,
        date_from=date_from,
        date_to=date_to,
        case_status=case_status,
    )
    statuses = await engine.case_statuses(row.case_id for row in rows)
    return Page[ReconciliationItemResponse].build(
        rows, limit=limit, serializer=lambda row: _item(row, statuses)
    )


@router.get(
    "/items/{item_id}",
    response_model=ReconciliationItemDetailResponse,
    summary="One parcel's reconciliation, with its statement rows",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def get_item(
    item_id: uuid.UUID,
    principal: TenantPrincipal,
    engine: EngineDep,
) -> ReconciliationItemDetailResponse:
    item = await engine.get_item(item_id)
    lines, adjustments = await engine.item_evidence(item)
    case = await engine.get_case(item.case_id) if item.case_id else None
    base = _item(item, {case.id: case.status} if case else {})
    return ReconciliationItemDetailResponse(
        **base.model_dump(),
        lines=[PayoutLineResponse.model_validate(line) for line in lines],
        adjustments=[PayoutAdjustmentResponse.model_validate(a) for a in adjustments],
        case=CaseResponse.model_validate(case) if case else None,
    )


@router.post(
    "/items/{item_id}/accept-charges",
    response_model=ChargeAcceptanceResponse,
    summary="Accept the courier's charges on one parcel",
    dependencies=[Depends(require_permission(Permission.MONEY_RECONCILE))],
)
async def accept_item_charges(
    item_id: uuid.UUID,
    payload: AcceptChargesPayload,
    principal: TenantPrincipal,
    engine: EngineDep,
    entitlements: EntitlementsDep,
) -> ChargeAcceptanceResponse:
    """Record what the courier kept on this parcel, once, in the ledger.

    Appends ledger entries; never edits one. Repeating the call finds nothing
    left to accept and returns zero.
    """
    await entitlements.require(principal.require_tenant(), Entitlement.RECONCILIATION)
    return _acceptance(await engine.accept_charges(item_id, reason=payload.reason))


# ------------------------------------------------------------------ cases --


@router.get(
    "/cases",
    response_model=Page[CaseResponse],
    summary="Open cases",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def list_cases(
    principal: TenantPrincipal,
    engine: EngineDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    case_status: Annotated[CaseStatus | None, Query(alias="status")] = None,
    kind: Annotated[CaseKind | None, Query()] = None,
    priority: Annotated[CasePriority | None, Query()] = None,
    payout_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[CaseResponse]:
    rows = await engine.list_cases(
        limit=limit,
        cursor=decode_cursor(cursor) if cursor else None,
        status=case_status,
        kind=kind,
        priority=priority,
        payout_id=payout_id,
    )
    return Page[CaseResponse].build(rows, limit=limit, serializer=CaseResponse.model_validate)


@router.get(
    "/cases/{case_id}",
    response_model=CaseDetailResponse,
    summary="A case with its history",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def get_case(
    case_id: uuid.UUID,
    principal: TenantPrincipal,
    engine: EngineDep,
) -> CaseDetailResponse:
    case = await engine.get_case(case_id)
    events = await engine.case_events(case_id)
    item = await engine.item_for_case(case)
    return CaseDetailResponse(
        **CaseResponse.model_validate(case).model_dump(),
        events=[CaseEventResponse.model_validate(event) for event in events],
        item=_item(item, {case.id: case.status}) if item else None,
    )


@router.patch(
    "/cases/{case_id}",
    response_model=CaseResponse,
    summary="Move a case on",
    dependencies=[Depends(require_permission(Permission.MONEY_RECONCILE))],
)
async def update_case(
    case_id: uuid.UUID,
    payload: CaseUpdatePayload,
    principal: TenantPrincipal,
    engine: EngineDep,
) -> CaseResponse:
    """Progress, resolve, dismiss or reopen a case.

    Closing one requires a note. A case closed with no explanation teaches
    nobody anything, and the same problem returns next month with nothing
    recorded about what happened last time. Every change is a case event; no
    change here touches the ledger.
    """
    case = await engine.update_case(case_id, status=payload.status, resolution=payload.resolution)
    return CaseResponse.model_validate(case)


@router.post(
    "/cases/{case_id}/notes",
    response_model=CaseEventResponse,
    summary="Add a note to a case",
    dependencies=[Depends(require_permission(Permission.MONEY_RECONCILE))],
)
async def add_case_note(
    case_id: uuid.UUID,
    payload: CaseNotePayload,
    principal: TenantPrincipal,
    engine: EngineDep,
) -> CaseEventResponse:
    event = await engine.add_note(case_id, note=payload.note)
    return CaseEventResponse.model_validate(event)


__all__ = ["router"]
