"""Offline sync endpoints (master spec sections 37, 38).

``POST /v1/sync/mutations`` accepts a device's queue; ``GET /v1/sync/changes``
serves the pull feed. Both are safe to call repeatedly — the network being
unreliable is the normal case for this app's users, not the exception.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import (
    DbSession,
    SettingsDep,
    TenantPrincipal,
    get_hasher,
    get_vault,
    require_permission,
)
from app.api.v1.commerce_schemas import (
    SyncChangeEntry,
    SyncChangesResponse,
    SyncMutationResult,
    SyncPushRequest,
    SyncPushResponse,
)
from app.core.clock import utc_now
from app.customers.service import CustomerService
from app.orders.service import OrderService
from app.products.service import ProductService
from app.sync.models import MutationStatus
from app.sync.service import SYNC_PAGE_SIZE, SyncService
from app.tenants.roles import Permission

router = APIRouter(prefix="/sync", tags=["sync"])


async def _sync(db: DbSession, settings: SettingsDep) -> SyncService:
    customers = CustomerService(db, hasher=get_hasher(settings), vault=get_vault(settings))
    return SyncService(
        db,
        products=ProductService(db),
        orders=OrderService(db, customers=customers),
        customers=customers,
    )


SyncServiceDep = Annotated[SyncService, Depends(_sync)]


@router.post(
    "/mutations",
    response_model=SyncPushResponse,
    summary="Push queued offline mutations",
    dependencies=[Depends(require_permission(Permission.ORDER_WRITE))],
)
async def push_mutations(
    payload: SyncPushRequest,
    principal: TenantPrincipal,
    sync: SyncServiceDep,
) -> SyncPushResponse:
    """Apply a device's queued changes.

    Idempotent by mutation id: resending a batch returns each mutation's
    original outcome rather than executing it again. A device on a bad
    connection will resend, and creating a second order from that is how a
    seller ends up with two parcels for one sale.

    Each mutation reports its own status, so one rejection does not discard the
    rest of the batch. A conflict returns the server's version of the record and
    applies nothing — the seller decides (master spec section 128).

    Only orders, products, customers and stock adjustments may be synced.
    Courier booking, risk lookups and payouts require a connection and are
    absent from this path by design (section 62.17).
    """
    outcomes = await sync.push(
        [mutation.model_dump(mode="json") for mutation in payload.mutations],
        device_id=payload.device_id,
    )

    results = [
        SyncMutationResult(
            mutation_id=outcome.mutation_id,
            status=str(outcome.status),
            entity_id=outcome.entity_id,
            server_version=outcome.server_version,
            error_code=outcome.error_code,
            error_message=outcome.error_message,
            server_state=outcome.server_state,
        )
        for outcome in outcomes
    ]

    def count(status: MutationStatus) -> int:
        return sum(1 for outcome in outcomes if outcome.status is status)

    return SyncPushResponse(
        results=results,
        applied_count=count(MutationStatus.APPLIED),
        conflict_count=count(MutationStatus.CONFLICT),
        rejected_count=count(MutationStatus.REJECTED),
        duplicate_count=count(MutationStatus.DUPLICATE),
    )


@router.get(
    "/changes",
    response_model=SyncChangesResponse,
    summary="Pull records changed since a cursor",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
async def get_changes(
    principal: TenantPrincipal,
    sync: SyncServiceDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = SYNC_PAGE_SIZE,
) -> SyncChangesResponse:
    """Records changed since ``cursor``, oldest first.

    Deleted and archived records come back as tombstones rather than simply
    disappearing, so a device that was offline when something was removed drops
    it instead of continuing to show a row the seller deleted.
    """
    entries, next_cursor, has_more = await sync.changes(cursor=cursor, limit=limit)
    return SyncChangesResponse(
        changes=[
            SyncChangeEntry(
                entity_type=entry.entity_type,
                entity_id=entry.entity_id,
                deleted=entry.deleted,
                version=entry.version,
                updated_at=entry.updated_at,
                data=entry.data,
            )
            for entry in entries
        ],
        next_cursor=next_cursor,
        has_more=has_more,
        # The client shows "last synced" from this rather than its own clock
        # (master spec section 69).
        server_time=utc_now(),
    )


__all__ = ["router"]
