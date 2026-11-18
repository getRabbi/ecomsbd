"""Printable order documents (V4 WP0.5).

HTML, not PDF: Bangla needs complex-script shaping that PDF libraries get wrong,
while browsers and the phone's print system get it right. Responses are
``text/html; charset=utf-8`` with ``Cache-Control: no-store`` (a label carries
the customer's phone). ``order.view``; behind ``order_documents_enabled``.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.api.deps import DbSession, SettingsDep, TenantPrincipal, get_vault, require_permission
from app.common.feature_flags import FeatureFlagService, FlagKey
from app.core.errors import AppError, ErrorCode
from app.documents.render import LabelSize
from app.documents.service import MAX_BULK, DocumentService
from app.tenants.roles import Permission

router = APIRouter(prefix="/orders", tags=["orders"])

_HEADERS = {"Cache-Control": "no-store"}
_VIEW = [Depends(require_permission(Permission.ORDER_VIEW))]


class BulkDocumentsRequest(BaseModel):
    order_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_BULK)
    kind: Literal["invoice", "label"]
    size: LabelSize = LabelSize.FOUR_BY_SIX
    mask_phone: bool = False


async def _service(
    db: DbSession, settings: SettingsDep, principal: TenantPrincipal
) -> DocumentService:
    if not await FeatureFlagService(db).is_enabled(
        FlagKey.ORDER_DOCUMENTS_ENABLED, tenant_id=principal.require_tenant()
    ):
        raise AppError("Printing documents is not enabled", code=ErrorCode.FEATURE_DISABLED)
    return DocumentService(db, vault=get_vault(settings))


def _html(body: str) -> HTMLResponse:
    return HTMLResponse(body, headers=_HEADERS, media_type="text/html; charset=utf-8")


@router.get(
    "/{order_id}/documents/invoice",
    response_class=HTMLResponse,
    summary="A4 invoice for one order",
    dependencies=_VIEW,
)
async def order_invoice(
    order_id: uuid.UUID,
    principal: TenantPrincipal,
    service: DocumentService = Depends(_service),
) -> HTMLResponse:
    return _html(await service.invoices(principal.require_tenant(), [order_id]))


@router.get(
    "/{order_id}/documents/label",
    response_class=HTMLResponse,
    summary="Shipping label for one order",
    dependencies=_VIEW,
)
async def order_label(
    order_id: uuid.UUID,
    principal: TenantPrincipal,
    size: LabelSize = Query(default=LabelSize.FOUR_BY_SIX),
    mask_phone: bool = Query(default=False),
    service: DocumentService = Depends(_service),
) -> HTMLResponse:
    return _html(
        await service.labels(
            principal.require_tenant(), [order_id], size=size, mask_phone=mask_phone
        )
    )


@router.post(
    "/documents/bulk",
    response_class=HTMLResponse,
    summary="Invoices or labels for up to 100 orders in one document",
    dependencies=_VIEW,
)
async def bulk_documents(
    payload: BulkDocumentsRequest,
    principal: TenantPrincipal,
    service: DocumentService = Depends(_service),
) -> HTMLResponse:
    tenant_id = principal.require_tenant()
    if payload.kind == "invoice":
        return _html(await service.invoices(tenant_id, payload.order_ids))
    return _html(
        await service.labels(
            tenant_id, payload.order_ids, size=payload.size, mask_phone=payload.mask_phone
        )
    )


__all__ = ["router"]
