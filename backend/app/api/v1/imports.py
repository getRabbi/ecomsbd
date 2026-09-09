"""Import endpoints (master spec sections 39, 98).

``POST /v1/imports`` uploads and detects, ``/dry-run`` validates without
writing, ``/commit`` creates. The dry run is mandatory: an import is a bulk
write a seller cannot easily undo, so nothing is created before they have seen
the counts.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from pydantic import BaseModel, Field

from app.api.deps import DbSession, SettingsDep, TenantPrincipal, get_hasher, get_vault
from app.api.v1.commerce_schemas import (
    ImportCommitResponse,
    ImportResponse,
    ImportRowResponse,
)
from app.customers.service import CustomerService
from app.imports.models import ImportBatch, ImportRowStatus, ImportTemplate
from app.imports.service import ImportService
from app.orders.service import OrderService
from app.products.service import ProductService

router = APIRouter(prefix="/imports", tags=["imports"])

#: Guards memory on the single VPS this runs on. A 5,000-row CSV is well under.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


async def _imports(db: DbSession, settings: SettingsDep) -> ImportService:
    customers = CustomerService(db, hasher=get_hasher(settings), vault=get_vault(settings))
    return ImportService(
        db,
        products=ProductService(db),
        orders=OrderService(db, customers=customers),
        customers=customers,
    )


ImportServiceDep = Annotated[ImportService, Depends(_imports)]


def _to_response(batch: ImportBatch) -> ImportResponse:
    return ImportResponse(
        id=batch.id,
        template=ImportTemplate(batch.template),
        status=batch.import_status,
        original_filename=batch.original_filename,
        source_sha256=batch.source_sha256,
        detected_headers=list(batch.detected_headers),
        column_mapping=dict(batch.column_mapping),
        row_count=batch.row_count,
        ready_count=batch.ready_count,
        warning_count=batch.warning_count,
        duplicate_count=batch.duplicate_count,
        invalid_count=batch.invalid_count,
        created_count=batch.created_count,
        can_commit=batch.can_commit,
        dry_run_at=batch.dry_run_at,
        committed_at=batch.committed_at,
        failure_reason=batch.failure_reason,
        created_at=batch.created_at,
    )


@router.post(
    "",
    response_model=ImportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a file to import",
)
async def create_import(
    principal: TenantPrincipal,
    imports: ImportServiceDep,
    file: Annotated[UploadFile, File()],
    template: Annotated[ImportTemplate, Form()],
) -> ImportResponse:
    """Accept a CSV, detect its headers and suggest a column mapping.

    Nothing is created yet. Re-uploading a file that was already committed is
    refused with a pointer to the earlier import, rather than silently doubling
    the seller's data (master spec section 98).
    """
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        from app.core.errors import ValidationError

        raise ValidationError(f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)}MB")

    batch = await imports.create(
        template=template,
        filename=file.filename or "upload.csv",
        content=content,
        content_type=file.content_type,
    )
    return _to_response(batch)


class DryRunPayload(BaseModel):
    """Optional mapping override before validating."""

    column_mapping: dict[str, str] | None = Field(
        default=None,
        description="field -> column header. Overrides the detected mapping.",
    )


@router.post(
    "/{import_id}/dry-run",
    response_model=ImportResponse,
    summary="Validate every row without writing",
)
async def dry_run_import(
    import_id: uuid.UUID,
    payload: DryRunPayload,
    principal: TenantPrincipal,
    imports: ImportServiceDep,
) -> ImportResponse:
    """Parse and validate the file, reporting ready/warning/duplicate/invalid.

    Writes nothing outside the import tables. An unparseable phone or amount is
    reported against its row with the offending value, never coerced into
    something importable (master spec section 98).
    """
    return _to_response(await imports.dry_run(import_id, column_mapping=payload.column_mapping))


@router.post(
    "/{import_id}/commit",
    response_model=ImportCommitResponse,
    summary="Create the records",
)
async def commit_import(
    import_id: uuid.UUID,
    principal: TenantPrincipal,
    imports: ImportServiceDep,
) -> ImportCommitResponse:
    """Create everything the dry run marked importable.

    A row that fails at creation is marked invalid with its reason and the rest
    continue: one bad row out of two hundred should not cost the seller the
    other hundred and ninety-nine.
    """
    batch = await imports.commit(import_id)
    return ImportCommitResponse(
        import_batch=_to_response(batch),
        created_count=batch.created_count,
        skipped_count=batch.duplicate_count + batch.invalid_count,
    )


@router.get("/{import_id}", response_model=ImportResponse, summary="Read an import")
async def get_import(
    import_id: uuid.UUID,
    principal: TenantPrincipal,
    imports: ImportServiceDep,
) -> ImportResponse:
    return _to_response(await imports.get(import_id))


@router.get(
    "/{import_id}/rows",
    response_model=list[ImportRowResponse],
    summary="Row-by-row report",
)
async def list_import_rows(
    import_id: uuid.UUID,
    principal: TenantPrincipal,
    imports: ImportServiceDep,
    row_status: Annotated[ImportRowStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ImportRowResponse]:
    """The per-row outcome, including the raw values the file contained.

    This is the downloadable error report master spec section 7.3 asks for: a
    seller fixing twelve bad rows needs to see which twelve and why.
    """
    await imports.get(import_id)
    rows = await imports.rows(import_id, status=row_status, limit=limit)
    return [ImportRowResponse.model_validate(row) for row in rows]


__all__ = ["router"]
