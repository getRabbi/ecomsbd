"""Reconciliation endpoints (master spec section 39).

The engine matches; people decide the rest. Every endpoint here that moves
money requires either an exact reference the engine found on its own, or a
stated reason from a person (section 81.7).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, TenantPrincipal
from app.api.v1.money_schemas import (
    CaseResponse,
    CaseUpdatePayload,
    ManualMatchPayload,
    PayoutLineResponse,
    ReconcileResponse,
    UnmatchPayload,
)
from app.common.pagination import Page, decode_cursor
from app.reconciliation.models import CaseKind, CaseStatus
from app.reconciliation.service import ReconciliationService

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


async def _engine(db: DbSession) -> ReconciliationService:
    return ReconciliationService(db)


EngineDep = Annotated[ReconciliationService, Depends(_engine)]


@router.post(
    "/payouts/{payout_id}/reconcile",
    response_model=ReconcileResponse,
    summary="Match a payout against outstanding parcels",
)
async def reconcile_payout(
    payout_id: uuid.UUID,
    principal: TenantPrincipal,
    engine: EngineDep,
    shadow: Annotated[bool, Query()] = False,
) -> ReconcileResponse:
    """Run the engine over a payout's lines.

    Only lines the engine is *certain* about are applied: an exact provider
    reference, or a single candidate scoring at or above the threshold.
    Everything else becomes a suggestion or a case, because section 112 puts
    precision ahead of recall — a missed match costs a click, a wrong one costs
    the seller their trust in the figures.

    ``shadow=true`` runs the whole thing and writes nothing. That is how a
    matching rule is measured before it is allowed to move money.
    """
    report = await engine.reconcile(payout_id, shadow=shadow)
    return ReconcileResponse(
        payout_id=report.payout_id,
        shadow=report.shadow,
        exact_matches=report.exact_matches,
        suggested=report.suggested,
        unresolved=report.unresolved,
        applied_paisa=report.applied_paisa,
        cases_opened=report.cases_opened,
    )


@router.post(
    "/lines/{line_id}/match",
    response_model=PayoutLineResponse,
    summary="Apply a payout line by hand",
)
async def match_line(
    line_id: uuid.UUID,
    payload: ManualMatchPayload,
    principal: TenantPrincipal,
    engine: EngineDep,
) -> PayoutLineResponse:
    """Settle a parcel because a person decided this line pays for it.

    The reason is mandatory and stored with the actor (section 81.7). This is
    the other half of the engine refusing to guess: the decision moves to
    somebody who can pick up a phone and ask.
    """
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
)
async def unmatch_line(
    line_id: uuid.UUID,
    payload: UnmatchPayload,
    principal: TenantPrincipal,
    engine: EngineDep,
) -> PayoutLineResponse:
    """Reverse a settlement.

    Section 81.8: a reversal, not a destructive overwrite. The original ledger
    entries stay and two more undo them, so the history of the mistake survives
    alongside its correction.
    """
    line = await engine.unmatch(line_id, reason=payload.reason)
    return PayoutLineResponse.model_validate(line)


@router.post("/scan", response_model=dict, summary="Look for problems")
async def scan(principal: TenantPrincipal, engine: EngineDep) -> dict:
    """Find the problems no statement would surface.

    Delivered parcels whose money never arrived, parcels stuck in transit, and
    returns whose stock was never restored. These come from the shop's own
    data rather than from a payout, so they are looked for on demand and on a
    schedule rather than on import.
    """
    return {"cases_opened": await engine.scan_for_cases()}


@router.get("/cases", response_model=Page[CaseResponse], summary="Open cases")
async def list_cases(
    principal: TenantPrincipal,
    engine: EngineDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    case_status: Annotated[CaseStatus | None, Query(alias="status")] = None,
    kind: Annotated[CaseKind | None, Query()] = None,
) -> Page[CaseResponse]:
    rows = await engine.list_cases(
        limit=limit,
        cursor=decode_cursor(cursor) if cursor else None,
        status=case_status,
        kind=kind,
    )
    return Page[CaseResponse].build(rows, limit=limit, serializer=CaseResponse.model_validate)


@router.patch("/cases/{case_id}", response_model=CaseResponse, summary="Move a case on")
async def update_case(
    case_id: uuid.UUID,
    payload: CaseUpdatePayload,
    principal: TenantPrincipal,
    engine: EngineDep,
) -> CaseResponse:
    """Progress, resolve or dismiss a case.

    Closing one requires a note. A case closed with no explanation teaches
    nobody anything, and the same problem returns next month with nothing
    recorded about what happened last time.
    """
    case = await engine.update_case(case_id, status=payload.status, resolution=payload.resolution)
    return CaseResponse.model_validate(case)


__all__ = ["router"]
