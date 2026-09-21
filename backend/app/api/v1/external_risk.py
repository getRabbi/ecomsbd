import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, Principal, require_permission
from app.customers.external_risk import capability
from app.customers.models import Customer
from app.messaging.service import required
from app.tenants.roles import Permission

router = APIRouter(prefix="/external-risk", tags=["external risk capability"])
Reader = Annotated[Principal, Depends(require_permission(Permission.CUSTOMER_RISK_VIEW))]


@router.get("/capability")
async def provider_capability(_: Reader):
    return capability()


@router.get("/customers/{customer_id}")
async def customer_facts(customer_id: uuid.UUID, db: DbSession, _: Reader):
    await required(db, Customer, customer_id)
    return {"customer_id": customer_id, **capability()}
