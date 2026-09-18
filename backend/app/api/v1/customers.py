"""Customer endpoints (master spec section 39).

Every response carries the masked phone. The full number is available only from
``/customers/{id}/reveal-phone``, which requires a reason and writes an audit
entry (sections 101, 133).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.analytics.rto import CustomerHistory, RtoService
from app.api.deps import (
    DbSession,
    EntitlementsDep,
    SettingsDep,
    TenantPrincipal,
    get_hasher,
    get_vault,
    require_permission,
)
from app.api.v1.commerce_schemas import (
    CustomerAddressResponse,
    CustomerCreatePayload,
    CustomerDetailResponse,
    CustomerResponse,
    CustomerUpdatePayload,
)
from app.api.v1.rto_schemas import (
    ObservationResponse,
    OutcomeCountsResponse,
    ParcelEventResponse,
)
from app.common.pagination import Page, decode_cursor
from app.customers import risk as risk_rules
from app.customers.models import Customer, CustomerFlag
from app.customers.service import CustomerService
from app.entitlements.catalog import Entitlement
from app.tenants.roles import Permission

router = APIRouter(prefix="/customers", tags=["customers"])


async def _customers(db: DbSession, settings: SettingsDep) -> CustomerService:
    return CustomerService(db, hasher=get_hasher(settings), vault=get_vault(settings))


CustomerServiceDep = Annotated[CustomerService, Depends(_customers)]


def _to_response(customer: Customer, history: CustomerHistory | None = None) -> CustomerResponse:
    """A customer row, with counts derived from the shop's parcels.

    The denormalised counters on the row were only ever maintained for orders
    placed; delivered and returned never moved. ``history`` comes from the
    shared RTO classification, the same numbers the Risk Check bands on.
    """
    history = history or CustomerHistory(order_count=customer.order_count)
    return CustomerResponse(
        id=customer.id,
        name=customer.name,
        phone_masked=customer.phone_masked,
        phone_last4=customer.phone_last4,
        flag=customer.flag,
        is_repeat_buyer=customer.is_repeat_buyer,
        order_count=history.order_count,
        delivered_count=history.delivered_count,
        returned_count=history.returned_count,
        cancelled_count=history.cancelled_count,
        success_rate_basis_points=history.success_rate_basis_points,
        realized_revenue_paisa=customer.realized_revenue_paisa,
        first_order_at=history.first_order_at or customer.first_order_at,
        last_order_at=history.last_order_at or customer.last_order_at,
        created_at=customer.created_at,
    )


def _to_detail(
    customer: Customer, history: CustomerHistory | None = None
) -> CustomerDetailResponse:
    base = _to_response(customer, history)
    return CustomerDetailResponse(
        **base.model_dump(),
        notes=customer.notes,
        flag_reason=customer.flag_reason,
        addresses=[
            CustomerAddressResponse.model_validate(address) for address in customer.addresses
        ],
    )


async def _history(db: DbSession, customer: Customer) -> CustomerHistory:
    return (await RtoService(db).customer_counts([customer.id]))[customer.id]


@router.get(
    "",
    response_model=Page[CustomerResponse],
    summary="List customers",
    dependencies=[Depends(require_permission(Permission.CUSTOMER_VIEW))],
)
async def list_customers(
    principal: TenantPrincipal,
    db: DbSession,
    customers: CustomerServiceDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    search: Annotated[str | None, Query(max_length=120)] = None,
    repeat_only: Annotated[bool, Query()] = False,
    flag: Annotated[CustomerFlag | None, Query()] = None,
) -> Page[CustomerResponse]:
    """List this shop's customers.

    Search accepts a name, a full phone or the last four digits. A full phone
    resolves through the keyed HMAC, so it is an exact match — there is no
    partial-number search, because supporting one would mean storing the number
    in a form that could be scanned.
    """
    rows = await customers.list_customers(
        limit=limit,
        cursor=decode_cursor(cursor) if cursor else None,
        search=search,
        repeat_only=repeat_only,
        flag=flag,
    )
    # One grouped read for the whole page, not one per row.
    histories = await RtoService(db).customer_counts(row.id for row in rows)
    return Page[CustomerResponse].build(
        rows, limit=limit, serializer=lambda row: _to_response(row, histories.get(row.id))
    )


@router.post(
    "",
    response_model=CustomerDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a customer",
    dependencies=[Depends(require_permission(Permission.ORDER_WRITE))],
)
async def create_customer(
    payload: CustomerCreatePayload,
    principal: TenantPrincipal,
    customers: CustomerServiceDep,
) -> CustomerDetailResponse:
    customer = await customers.create(
        phone=payload.phone, name=payload.name, address=payload.address
    )
    return _to_detail(customer)


@router.get(
    "/lookup",
    response_model=CustomerDetailResponse | None,
    summary="Find a customer by phone",
    dependencies=[Depends(require_permission(Permission.CUSTOMER_VIEW))],
)
async def lookup_customer(
    principal: TenantPrincipal,
    db: DbSession,
    customers: CustomerServiceDep,
    phone: Annotated[str, Query(min_length=6, max_length=24)],
) -> CustomerDetailResponse | None:
    """Phone-first lookup for the order screen.

    Returns ``null`` rather than 404 for an unknown number: a new customer is
    the normal case at this point in the flow, not an error.
    """
    customer = await customers.find_by_phone(phone)
    if customer is None:
        return None
    return _to_detail(customer, await _history(db, customer))


class RiskCheckResponse(BaseModel):
    """A customer's delivery history with this shop, and the band it implies.

    Deliberately flat and deliberately complete: every number the app needs to
    justify ``state`` on screen is in the same payload, so the seller is never
    shown a verdict they cannot check. ``reasons`` are machine codes the app
    localises — the API ships no user-facing prose.
    """

    found: bool
    customer_id: uuid.UUID | None = None
    name: str | None = None
    phone_masked: str | None = None
    phone_last4: str | None = None

    state: str
    order_count: int
    delivered_count: int
    returned_count: int
    cancelled_count: int
    terminal_count: int
    success_rate_basis_points: int | None = None
    first_order_at: datetime | None = None
    last_order_at: datetime | None = None
    reasons: list[str]

    #: Checks left in today's quota, or ``None`` when the plan is unlimited.
    checks_remaining: int | None = None

    # --- V2.2 return intelligence ------------------------------------------
    #: Completed parcels by outcome (RTO split into returned / courier
    #: cancelled) and the RTO rate over completed parcels only.
    parcels: OutcomeCountsResponse | None = None
    in_transit_count: int = 0
    #: Latest parcels, newest first (at most ten).
    recent: list[ParcelEventResponse] = Field(default_factory=list)
    #: Factual repeat patterns, each with the counts that make it true.
    observations: list[ObservationResponse] = Field(default_factory=list)


@router.get(
    "/risk-check",
    response_model=RiskCheckResponse,
    summary="Delivery-risk band from this shop's own history",
    dependencies=[Depends(require_permission(Permission.CUSTOMER_RISK_VIEW))],
)
async def risk_check(
    principal: TenantPrincipal,
    db: DbSession,
    customers: CustomerServiceDep,
    entitlements: EntitlementsDep,
    phone: Annotated[str, Query(min_length=6, max_length=24)],
) -> RiskCheckResponse:
    """Band a phone number against this shop's own finished orders.

    First-party only. The lookup is the same keyed-HMAC path the order screen
    uses, and the ORM tenancy guard scopes it, so a seller can only ever reach
    customers they have sold to themselves — there is no aggregator here and no
    cross-shop view (master spec sections 24, 121, 130).

    Metered before the lookup runs: the quota exists to stop the endpoint being
    used to probe numbers, so an unknown number has to cost a check too.
    Otherwise "not found" would be free and the meter would measure nothing.

    A number with no history returns ``found: false`` and
    ``INSUFFICIENT_DATA`` rather than 404 — the seller asked a reasonable
    question and "we have never sold to this number" is the answer, not an
    error.
    """
    usage = await entitlements.consume(principal.require_tenant(), Entitlement.RISK_CHECKS_DAILY)

    # The number itself is never logged: it reaches the hasher and nothing else.
    customer = await customers.find_by_phone(phone)
    # Parcel-derived, through the shared RTO classification. The V1 rule is
    # unchanged; what changed is that delivered and returned are now real.
    history = await RtoService(db).customer_history(customer.id) if customer else None
    assessment = risk_rules.assess(history) if history else risk_rules.UNKNOWN

    return RiskCheckResponse(
        found=customer is not None,
        customer_id=customer.id if customer else None,
        name=customer.name if customer else None,
        phone_masked=customer.phone_masked if customer else None,
        phone_last4=customer.phone_last4 if customer else None,
        state=str(assessment.state),
        order_count=assessment.order_count,
        delivered_count=assessment.delivered_count,
        returned_count=assessment.returned_count,
        cancelled_count=assessment.cancelled_count,
        terminal_count=assessment.terminal_count,
        success_rate_basis_points=assessment.success_rate_basis_points,
        first_order_at=assessment.first_order_at,
        last_order_at=assessment.last_order_at,
        reasons=[str(reason) for reason in assessment.reasons],
        checks_remaining=usage.remaining,
        parcels=OutcomeCountsResponse.of(history.counts) if history else None,
        in_transit_count=history.counts.in_transit if history else 0,
        recent=[ParcelEventResponse.of(event) for event in history.recent] if history else [],
        observations=(
            [ObservationResponse.of(obs) for obs in history.observations] if history else []
        ),
    )


@router.get(
    "/{customer_id}",
    response_model=CustomerDetailResponse,
    summary="Read a customer",
    dependencies=[Depends(require_permission(Permission.CUSTOMER_VIEW))],
)
async def get_customer(
    customer_id: uuid.UUID,
    principal: TenantPrincipal,
    db: DbSession,
    customers: CustomerServiceDep,
) -> CustomerDetailResponse:
    customer = await customers.get(customer_id)
    return _to_detail(customer, await _history(db, customer))


@router.patch(
    "/{customer_id}",
    response_model=CustomerDetailResponse,
    summary="Update a customer",
    dependencies=[Depends(require_permission(Permission.ORDER_WRITE))],
)
async def update_customer(
    customer_id: uuid.UUID,
    payload: CustomerUpdatePayload,
    principal: TenantPrincipal,
    db: DbSession,
    customers: CustomerServiceDep,
) -> CustomerDetailResponse:
    customer = await customers.update(
        customer_id,
        name=payload.name,
        notes=payload.notes,
        flag=payload.flag,
        flag_reason=payload.flag_reason,
    )
    return _to_detail(customer, await _history(db, customer))


class RevealPhoneRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


class RevealPhoneResponse(BaseModel):
    phone: str


@router.post(
    "/{customer_id}/reveal-phone",
    response_model=RevealPhoneResponse,
    summary="Reveal the full phone number",
    dependencies=[Depends(require_permission(Permission.CUSTOMER_EXPORT))],
)
async def reveal_phone(
    customer_id: uuid.UUID,
    payload: RevealPhoneRequest,
    principal: TenantPrincipal,
    customers: CustomerServiceDep,
) -> RevealPhoneResponse:
    """Decrypt and return the customer's number.

    The one endpoint that returns an unmasked phone. It requires a stated reason
    and writes a ``privacy.phone_revealed`` audit entry, so the seller's own
    access to their customers' numbers is traceable (master spec section 101).
    """
    return RevealPhoneResponse(
        phone=await customers.reveal_phone(customer_id, reason=payload.reason)
    )


__all__ = ["router"]
