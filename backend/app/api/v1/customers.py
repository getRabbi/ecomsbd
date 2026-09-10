"""Customer endpoints (master spec section 39).

Every response carries the masked phone. The full number is available only from
``/customers/{id}/reveal-phone``, which requires a reason and writes an audit
entry (sections 101, 133).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.api.deps import (
    DbSession,
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
from app.common.pagination import Page, decode_cursor
from app.customers.models import Customer, CustomerFlag
from app.customers.service import CustomerService
from app.tenants.roles import Permission

router = APIRouter(prefix="/customers", tags=["customers"])


async def _customers(db: DbSession, settings: SettingsDep) -> CustomerService:
    return CustomerService(db, hasher=get_hasher(settings), vault=get_vault(settings))


CustomerServiceDep = Annotated[CustomerService, Depends(_customers)]


def _to_response(customer: Customer) -> CustomerResponse:
    return CustomerResponse(
        id=customer.id,
        name=customer.name,
        phone_masked=customer.phone_masked,
        phone_last4=customer.phone_last4,
        flag=customer.flag,
        is_repeat_buyer=customer.is_repeat_buyer,
        order_count=customer.order_count,
        delivered_count=customer.delivered_count,
        returned_count=customer.returned_count,
        cancelled_count=customer.cancelled_count,
        success_rate_basis_points=customer.success_rate_basis_points,
        realized_revenue_paisa=customer.realized_revenue_paisa,
        first_order_at=customer.first_order_at,
        last_order_at=customer.last_order_at,
        created_at=customer.created_at,
    )


def _to_detail(customer: Customer) -> CustomerDetailResponse:
    base = _to_response(customer)
    return CustomerDetailResponse(
        **base.model_dump(),
        notes=customer.notes,
        flag_reason=customer.flag_reason,
        addresses=[
            CustomerAddressResponse.model_validate(address) for address in customer.addresses
        ],
    )


@router.get(
    "",
    response_model=Page[CustomerResponse],
    summary="List customers",
    dependencies=[Depends(require_permission(Permission.CUSTOMER_VIEW))],
)
async def list_customers(
    principal: TenantPrincipal,
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
    return Page[CustomerResponse].build(rows, limit=limit, serializer=_to_response)


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
    customers: CustomerServiceDep,
    phone: Annotated[str, Query(min_length=6, max_length=24)],
) -> CustomerDetailResponse | None:
    """Phone-first lookup for the order screen.

    Returns ``null`` rather than 404 for an unknown number: a new customer is
    the normal case at this point in the flow, not an error.
    """
    customer = await customers.find_by_phone(phone)
    return _to_detail(customer) if customer else None


@router.get(
    "/{customer_id}",
    response_model=CustomerDetailResponse,
    summary="Read a customer",
    dependencies=[Depends(require_permission(Permission.CUSTOMER_VIEW))],
)
async def get_customer(
    customer_id: uuid.UUID,
    principal: TenantPrincipal,
    customers: CustomerServiceDep,
) -> CustomerDetailResponse:
    return _to_detail(await customers.get(customer_id))


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
    customers: CustomerServiceDep,
) -> CustomerDetailResponse:
    customer = await customers.update(
        customer_id,
        name=payload.name,
        notes=payload.notes,
        flag=payload.flag,
        flag_reason=payload.flag_reason,
    )
    return _to_detail(customer)


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
