"""Consignment endpoints — the manual courier flow.

No provider is contacted anywhere in this module. Master spec section 140
forbids inventing a courier's endpoints, and the Steadfast adapter is Phase C,
blocked on documentation. What exists here is manual mode, which the product
supports in its own right: the seller records that a parcel went out and,
later, what happened to it.

These two calls are what make the money core real rather than fixture-driven —
a COD receivable needs a delivery event to exist.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import DbSession, TenantPrincipal, require_permission
from app.api.v1.analytics_schemas import ChargeCreatePayload, ChargeResponse
from app.api.v1.money_schemas import (
    ConsignmentItemResponse,
    ConsignmentResponse,
    DeliveryOutcomePayload,
    DispatchPayload,
    ReturnReceiptPayload,
)
from app.consignments.models import Consignment
from app.consignments.service import (
    ConsignmentService,
    DeliveryOutcome,
    ItemOutcome,
    ReturnReceiptLine,
)
from app.money.service import ReceivableService
from app.profit.service import ProfitService
from app.tenants.roles import Permission

router = APIRouter(prefix="/consignments", tags=["consignments"])


async def _consignments(db: DbSession) -> ConsignmentService:
    return ConsignmentService(db, receivables=ReceivableService(db))


ConsignmentsDep = Annotated[ConsignmentService, Depends(_consignments)]


def _to_response(consignment: Consignment) -> ConsignmentResponse:
    return ConsignmentResponse(
        id=consignment.id,
        order_id=consignment.order_id,
        provider=consignment.provider,
        provider_consignment_id=consignment.provider_consignment_id,
        tracking_code=consignment.tracking_code,
        merchant_reference=consignment.merchant_reference,
        status=consignment.status,
        cod_amount_paisa=consignment.cod_amount_paisa,
        collectible_paisa=consignment.collectible_paisa,
        booked_at=consignment.booked_at,
        delivered_at=consignment.delivered_at,
        returned_at=consignment.returned_at,
        items=[ConsignmentItemResponse.model_validate(item) for item in consignment.items],
    )


@router.post(
    "/orders/{order_id}/dispatch",
    response_model=ConsignmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record that a parcel has gone out",
    dependencies=[Depends(require_permission(Permission.ORDER_BOOK))],
)
async def dispatch(
    order_id: uuid.UUID,
    payload: DispatchPayload,
    principal: TenantPrincipal,
    consignments: ConsignmentsDep,
) -> ConsignmentResponse:
    """Hand a parcel to a courier, by hand.

    Decrements stock and opens the receivable as ``EXPECTED`` — expected, not
    collectible. Nobody owes anything until the parcel is delivered, and
    crediting at dispatch would show a seller money for every parcel in
    transit, including the ones that come back.
    """
    consignment = await consignments.dispatch_manual(
        order_id,
        provider=payload.provider,
        tracking_code=payload.tracking_code,
        cod_amount_paisa=payload.cod_amount_paisa,
    )
    return _to_response(consignment)


@router.post(
    "/{consignment_id}/outcome",
    response_model=ConsignmentResponse,
    summary="Record what happened to a parcel",
    dependencies=[Depends(require_permission(Permission.ORDER_BOOK))],
)
async def record_outcome(
    consignment_id: uuid.UUID,
    payload: DeliveryOutcomePayload,
    principal: TenantPrincipal,
    consignments: ConsignmentsDep,
) -> ConsignmentResponse:
    """Delivered, partly delivered, or returned.

    Everything that follows happens in the same transaction: the collectible
    amount, the receivable's state, the returned units going back on the shelf,
    and the ledger entries. A status that changed without those would be a lie
    the Money screen then repeats.

    A partial delivery requires the per-item quantities — section 17.8 forbids
    assuming the original COD, and the quantities are the only honest way to
    know what was collected.
    """
    consignment = await consignments.record_outcome(
        consignment_id,
        DeliveryOutcome(
            status=payload.status,
            occurred_at=payload.occurred_at,
            items=[
                ItemOutcome(
                    consignment_item_id=item.consignment_item_id,
                    qty_delivered=item.qty_delivered,
                    qty_returned=item.qty_returned,
                )
                for item in payload.items
            ],
            note=payload.note,
            return_reason=payload.return_reason,
        ),
    )
    return _to_response(consignment)


@router.get(
    "/{consignment_id}",
    response_model=ConsignmentResponse,
    summary="Read a parcel",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
async def get_consignment(
    consignment_id: uuid.UUID,
    principal: TenantPrincipal,
    consignments: ConsignmentsDep,
) -> ConsignmentResponse:
    return _to_response(await consignments.get(consignment_id))


@router.post(
    "/{consignment_id}/return-receipt",
    response_model=ConsignmentResponse,
    summary="Receive a returned parcel back into stock",
    dependencies=[Depends(require_permission(Permission.INVENTORY_ADJUST))],
)
async def receive_return(
    consignment_id: uuid.UUID,
    payload: ReturnReceiptPayload,
    principal: TenantPrincipal,
    consignments: ConsignmentsDep,
) -> ConsignmentResponse:
    """Say what physically came back: restock it, or not (damaged / missing).

    A courier marking a parcel RETURNED never restocks anything by itself;
    only this does, and only for the units the seller says are sellable. Once
    per line — repeating the same decision is a no-op.
    """
    consignment = await consignments.get(consignment_id)
    if payload.decision == "PARTIAL":
        lines = [
            ReturnReceiptLine(
                consignment_item_id=item.consignment_item_id,
                qty_restocked=item.qty_restocked,
                qty_not_restocked=item.qty_not_restocked,
            )
            for item in payload.items
        ]
    else:
        restock = payload.decision == "RESTOCK_ALL"
        lines = [
            ReturnReceiptLine(
                consignment_item_id=item.id,
                qty_restocked=item.qty_returned if restock else 0,
                qty_not_restocked=0 if restock else item.qty_returned,
            )
            for item in consignment.items
            if item.qty_returned > 0 and item.return_received_at is None
        ]
    return _to_response(await consignments.receive_return(consignment_id, lines, note=payload.note))


__all__ = ["router"]


@router.post(
    "/{consignment_id}/charges",
    response_model=ChargeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record what a parcel cost",
    dependencies=[Depends(require_permission(Permission.ORDER_BOOK))],
)
async def record_charge(
    consignment_id: uuid.UUID,
    payload: ChargeCreatePayload,
    principal: TenantPrincipal,
    db: DbSession,
) -> ChargeResponse:
    """Enter a courier charge, COD fee, packaging cost or return charge.

    This is how profit becomes real in manual courier mode: without it every
    parcel's figure is revenue minus goods, which flatters every margin in the
    shop by whatever the courier actually charged.

    Recording a charge writes a new profit revision for the parcel, so the
    Insights screen moves the moment the seller enters the figure and the
    number they read before it is still there.
    """
    profit = ProfitService(db)
    charge = await profit.record_charge(
        consignment_id,
        kind=payload.kind,
        amount_paisa=payload.amount_paisa,
        source=payload.source,
        provider_label=payload.provider_label,
        source_ref=payload.source_ref,
        reason=payload.reason,
        occurred_at=payload.occurred_at,
    )
    if await profit.current_snapshot(consignment_id) is not None:
        await profit.snapshot(consignment_id, reason=payload.reason or f"{payload.kind} recorded")
    await db.commit()
    return ChargeResponse.model_validate(charge)


@router.get(
    "/{consignment_id}/charges",
    response_model=list[ChargeResponse],
    summary="What a parcel cost",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def list_charges(
    consignment_id: uuid.UUID,
    principal: TenantPrincipal,
    db: DbSession,
) -> list[ChargeResponse]:
    """Only the charges still in force.

    A superseded estimate is kept in the table but not listed here: the
    seller asking what a parcel cost wants the current answer, and the history
    belongs in the parcel's own audit view.
    """
    charges = await ProfitService(db).charges_for(consignment_id)
    return [ChargeResponse.model_validate(charge) for charge in charges]
