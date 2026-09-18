"""Import endpoints (master spec sections 39, 98).

``POST /v1/imports`` uploads and detects, ``/dry-run`` validates without
writing, ``/commit`` creates. The dry run is mandatory: an import is a bulk
write a seller cannot easily undo, so nothing is created before they have seen
the counts.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
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
    ImportCommitResponse,
    ImportResponse,
    ImportRowResponse,
)
from app.common.uploads import safe_filename
from app.customers.service import CustomerService
from app.imports.models import (
    ImportBatch,
    ImportMapping,
    ImportRowStatus,
    ImportTemplate,
)
from app.imports.service import ASYNC_COMMIT_THRESHOLD_ROWS, ImportService
from app.orders.service import OrderService
from app.products.service import ProductService
from app.tenants.roles import Permission

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
        settings=settings,
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
    dependencies=[Depends(require_permission(Permission.ORDER_WRITE))],
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
    dependencies=[Depends(require_permission(Permission.ORDER_WRITE))],
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
    dependencies=[Depends(require_permission(Permission.ORDER_WRITE))],
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

    **Large imports are committed in the background.** Each row goes through the
    normal order service — customer normalisation, stock, the financial ledger —
    so a few thousand rows is minutes of work, far longer than a request should
    be held open. Past the threshold the batch is marked ``COMMITTING`` and the
    work is handed to the worker through the outbox, inside this request's own
    transaction: the job therefore exists if and only if this request committed,
    and a request that rolls back leaves no orphan behind.

    The response says which happened, so the client polls rather than assuming
    a returned count means finished.
    """
    batch = await imports.get(import_id)
    pending = batch.ready_count + batch.warning_count

    if pending >= ASYNC_COMMIT_THRESHOLD_ROWS:
        batch = await imports.begin_async_commit(import_id)
        return ImportCommitResponse(
            import_batch=_to_response(batch),
            created_count=0,
            skipped_count=batch.duplicate_count + batch.invalid_count,
            queued=True,
        )

    batch = await imports.commit(import_id)
    return ImportCommitResponse(
        import_batch=_to_response(batch),
        created_count=batch.created_count,
        skipped_count=batch.duplicate_count + batch.invalid_count,
    )


class SaveMappingPayload(BaseModel):
    """A column mapping the seller wants to reuse."""

    name: str = Field(min_length=1, max_length=80)
    template: ImportTemplate
    mapping: dict[str, str]
    source_headers: list[str] = Field(default_factory=list)


class MappingResponse(BaseModel):
    id: uuid.UUID
    name: str
    template: str
    mapping: dict[str, str]
    source_headers: list[str]
    use_count: int
    last_used_at: str | None


def _mapping_response(saved: ImportMapping) -> MappingResponse:
    return MappingResponse(
        id=saved.id,
        name=saved.name,
        template=saved.template,
        mapping=dict(saved.mapping),
        source_headers=[str(header) for header in (saved.source_headers or [])],
        use_count=saved.use_count,
        last_used_at=saved.last_used_at.isoformat() if saved.last_used_at else None,
    )


@router.get(
    "",
    response_model=list[ImportResponse],
    summary="Past imports",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
async def list_imports(
    principal: TenantPrincipal,
    imports: ImportServiceDep,
    template: Annotated[ImportTemplate | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ImportResponse]:
    """Import history for this shop, newest first.

    Includes the failed and cancelled ones, because those are the imports a
    seller comes looking for.
    """
    batches = await imports.history(template=template, limit=limit, offset=offset)
    return [_to_response(batch) for batch in batches]


@router.get(
    "/mappings",
    response_model=list[MappingResponse],
    summary="Saved column mappings",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
async def list_mappings(
    principal: TenantPrincipal,
    imports: ImportServiceDep,
    template: Annotated[ImportTemplate | None, Query()] = None,
    import_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[MappingResponse]:
    """Mappings this shop has saved.

    With ``import_id``, only the mappings that can actually be applied to that
    file are returned. Offering one whose columns are missing is worse than
    offering none: the seller picks it and every row fails.
    """
    headers: list[str] | None = None
    if import_id is not None:
        batch = await imports.get(import_id)
        headers = [str(header) for header in (batch.detected_headers or [])]
    rows = await imports.list_mappings(template=template, headers=headers)
    return [_mapping_response(row) for row in rows]


@router.post(
    "/mappings",
    response_model=MappingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save a column mapping for reuse",
    dependencies=[Depends(require_permission(Permission.ORDER_WRITE))],
)
async def save_mapping(
    payload: SaveMappingPayload,
    principal: TenantPrincipal,
    imports: ImportServiceDep,
) -> MappingResponse:
    """Save a mapping, or update the one with the same name.

    Sellers import the same weekly export with the same oddly-named columns.
    Re-mapping it every time is the difference between a feature they use and
    one they try once.
    """
    saved = await imports.save_mapping(
        name=payload.name,
        template=payload.template,
        mapping=payload.mapping,
        source_headers=payload.source_headers,
    )
    return _mapping_response(saved)


@router.delete(
    "/mappings/{mapping_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a saved mapping",
    dependencies=[Depends(require_permission(Permission.ORDER_WRITE))],
)
async def delete_mapping(
    mapping_id: uuid.UUID,
    principal: TenantPrincipal,
    imports: ImportServiceDep,
) -> None:
    await imports.delete_mapping(mapping_id)


@router.post(
    "/{import_id}/mapping/{mapping_id}",
    response_model=ImportResponse,
    summary="Apply a saved mapping to this import",
    dependencies=[Depends(require_permission(Permission.ORDER_WRITE))],
)
async def apply_saved_mapping(
    import_id: uuid.UUID,
    mapping_id: uuid.UUID,
    principal: TenantPrincipal,
    imports: ImportServiceDep,
) -> ImportResponse:
    return _to_response(await imports.apply_mapping(import_id, mapping_id))


@router.get(
    "/{import_id}/errors.csv",
    summary="Download the rows that failed",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
async def download_error_rows(
    import_id: uuid.UUID,
    principal: TenantPrincipal,
    imports: ImportServiceDep,
) -> Response:
    """The failed rows, as a CSV the seller can fix and upload again.

    Their original columns in their original order, plus the row number from
    the file and why it was refused. That makes the download an *input* — fix
    the flagged rows, delete the two added columns, re-upload — rather than
    only a report to read.
    """
    batch = await imports.get(import_id)
    body = await imports.error_csv(import_id)
    name = f"{batch.original_filename.rsplit('.', 1)[0]}-errors.csv"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            # The filename is already sanitised on the batch, so it is safe to
            # put in a header.
            "Content-Disposition": f'attachment; filename="{safe_filename(name)}"',
            # An error report is about one moment; a cached copy would show a
            # seller yesterday's failures after they fixed them.
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "/{import_id}",
    response_model=ImportResponse,
    summary="Read an import",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
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
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
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
