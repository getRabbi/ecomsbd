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

from app.api.deps import DbSession, TenantPrincipal
from app.api.v1.money_schemas import (
    ConsignmentItemResponse,
    ConsignmentResponse,
    DeliveryOutcomePayload,
    DispatchPayload,
)
from app.consignments.models import Consignment
from app.consignments.service import ConsignmentService, DeliveryOutcome, ItemOutcome
from app.money.service import ReceivableService

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
        ),
    )
    return _to_response(consignment)


@router.get("/{consignment_id}", response_model=ConsignmentResponse, summary="Read a parcel")
async def get_consignment(
    consignment_id: uuid.UUID,
    principal: TenantPrincipal,
    consignments: ConsignmentsDep,
) -> ConsignmentResponse:
    return _to_response(await consignments.get(consignment_id))


__all__ = ["router"]
