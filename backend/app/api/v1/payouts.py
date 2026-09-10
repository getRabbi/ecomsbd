"""Payout endpoints (master spec section 39).

Recording money and applying it are separate calls on purpose. A statement is
evidence; deciding which parcel each line pays for is the reconciliation step,
and merging the two would mean an upload could silently settle forty parcels
before anybody looked at it.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status

from app.api.deps import DbSession, TenantPrincipal, require_permission
from app.api.v1.money_schemas import (
    ManualPayoutPayload,
    PayoutAdjustmentResponse,
    PayoutDetailResponse,
    PayoutLineResponse,
    PayoutResponse,
    StatementPreviewResponse,
    StatementRowPreview,
)
from app.common.pagination import Page, decode_cursor
from app.core.errors import ValidationError
from app.payouts.models import Payout, PayoutStatus
from app.payouts.service import MAX_STATEMENT_BYTES, PayoutService
from app.tenants.roles import Permission

router = APIRouter(prefix="/payouts", tags=["payouts"])


async def _payouts(db: DbSession) -> PayoutService:
    return PayoutService(db)


PayoutsDep = Annotated[PayoutService, Depends(_payouts)]


def _to_response(payout: Payout) -> PayoutResponse:
    return PayoutResponse(
        id=payout.id,
        provider=payout.provider,
        provider_reference=payout.provider_reference,
        source=payout.source,
        status=PayoutStatus(payout.status),
        total_paisa=payout.total_paisa,
        applied_paisa=payout.applied_paisa,
        unexplained_paisa=payout.unexplained_paisa,
        paid_on=payout.paid_on,
        received_at=payout.received_at,
        note=payout.note,
        source_file_id=payout.source_file_id,
        created_at=payout.created_at,
    )


@router.get(
    "",
    response_model=Page[PayoutResponse],
    summary="List payouts",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def list_payouts(
    principal: TenantPrincipal,
    payouts: PayoutsDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    provider: Annotated[str | None, Query(max_length=40)] = None,
    payout_status: Annotated[PayoutStatus | None, Query(alias="status")] = None,
) -> Page[PayoutResponse]:
    rows = await payouts.list_payouts(
        limit=limit,
        cursor=decode_cursor(cursor) if cursor else None,
        provider=provider,
        status=payout_status,
    )
    return Page[PayoutResponse].build(rows, limit=limit, serializer=_to_response)


@router.post(
    "/manual",
    response_model=PayoutDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a payout by hand",
    dependencies=[Depends(require_permission(Permission.MONEY_RECONCILE))],
)
async def record_manual_payout(
    payload: ManualPayoutPayload,
    principal: TenantPrincipal,
    payouts: PayoutsDep,
) -> PayoutDetailResponse:
    """Record a lump sum the seller says arrived.

    It becomes one unmatched line. Master spec section 82: a manual payout with
    only a lump total may generate a *proposed allocation*, never an
    irreversible settlement — there is nothing to match on but the number.
    """
    payout = await payouts.record_manual(
        provider=payload.provider,
        total_paisa=payload.total_paisa,
        paid_on=payload.paid_on,
        reference=payload.reference,
        note=payload.note,
    )
    return await _detail(payouts, payout)


@router.post(
    "/preview",
    response_model=StatementPreviewResponse,
    summary="Read a statement without saving it",
    dependencies=[Depends(require_permission(Permission.MONEY_RECONCILE))],
)
async def preview_statement(
    principal: TenantPrincipal,
    payouts: PayoutsDep,
    file: Annotated[UploadFile, File()],
) -> StatementPreviewResponse:
    """Parse a statement and show what is in it.

    Nothing is created. The seller sees the detected columns and every row —
    including the ones that could not be read, with the text the file actually
    contained — before a single paisa moves.
    """
    content = await file.read()
    if len(content) > MAX_STATEMENT_BYTES:
        raise ValidationError(f"That file is larger than {MAX_STATEMENT_BYTES // (1024 * 1024)}MB")
    parsed = await payouts.preview_statement(content)
    return StatementPreviewResponse(
        detected_headers=parsed.headers,
        column_mapping=parsed.mapping,
        row_count=len(parsed.rows),
        invalid_row_count=len(parsed.invalid_rows),
        total_paisa=parsed.total_paisa,
        rows=[
            StatementRowPreview(
                row_number=row.row_number,
                amount_paisa=row.amount_paisa,
                consignment_id=row.consignment_id,
                tracking_code=row.tracking_code,
                merchant_reference=row.merchant_reference,
                delivered_on=row.delivered_on,
                fee_paisa=row.fee_paisa,
                fee_label=row.fee_label,
                errors=row.errors,
                raw=row.raw,
            )
            for row in parsed.rows
        ],
    )


@router.post(
    "/import",
    response_model=PayoutDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import a courier statement",
    dependencies=[Depends(require_permission(Permission.MONEY_RECONCILE))],
)
async def import_statement(
    principal: TenantPrincipal,
    payouts: PayoutsDep,
    file: Annotated[UploadFile, File()],
    provider: Annotated[str, Form()],
    reference: Annotated[str | None, Form()] = None,
    paid_on: Annotated[date | None, Form()] = None,
    note: Annotated[str | None, Form()] = None,
) -> PayoutDetailResponse:
    """Import a statement as a payout with one line per row.

    Re-uploading the same file is refused by content hash (section 81.2). The
    source file is kept, because section 81.4 forbids reconciliation from ever
    deleting statement data — it is what lets support explain a result months
    later.

    Nothing is matched here. Reconciliation is the next, separate call.
    """
    content = await file.read()
    payout = await payouts.import_statement(
        content,
        provider=provider,
        filename=file.filename or "statement.csv",
        reference=reference,
        paid_on=paid_on,
        note=note,
    )
    return await _detail(payouts, payout)


@router.get(
    "/{payout_id}",
    response_model=PayoutDetailResponse,
    summary="Read a payout",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def get_payout(
    payout_id: uuid.UUID,
    principal: TenantPrincipal,
    payouts: PayoutsDep,
) -> PayoutDetailResponse:
    return await _detail(payouts, await payouts.get(payout_id))


async def _detail(payouts: PayoutService, payout: Payout) -> PayoutDetailResponse:
    lines = await payouts.lines(payout.id)
    adjustments = await payouts.adjustments(payout.id)
    return PayoutDetailResponse(
        **_to_response(payout).model_dump(),
        lines=[PayoutLineResponse.model_validate(line) for line in lines],
        adjustments=[
            PayoutAdjustmentResponse.model_validate(adjustment) for adjustment in adjustments
        ],
    )


__all__ = ["router"]
