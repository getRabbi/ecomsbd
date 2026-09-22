"""Import pipeline service.

Master spec section 98: upload, detect, map, preview, validate, dry run, commit,
report.

The dry run exists because an import is a bulk write a seller cannot easily
undo. Nothing is created until the seller has seen the counts — ready, warnings,
duplicates, invalid — and said go.
"""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.object_storage import ObjectStorage
from app.common.outbox import OutboxTopic, enqueue
from app.common.uploads import DetectedFormat, UploadCheck, validate_upload
from app.core.clock import utc_now
from app.core.config import Settings, get_settings
from app.core.context import current_context
from app.core.errors import AppError, ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.customers.service import CustomerService
from app.imports.models import (
    ImportBatch,
    ImportMapping,
    ImportRow,
    ImportRowStatus,
    ImportStatus,
    ImportTemplate,
)
from app.imports.spreadsheet import read_xlsx
from app.imports.templates import TEMPLATES, autodetect_mapping, parse_row, row_fingerprint
from app.orders.models import OrderChannel
from app.orders.service import OrderDraft, OrderItemDraft, OrderService
from app.products.service import ProductService

__all__ = [
    "ASYNC_COMMIT_THRESHOLD_ROWS",
    "COMMIT_CHECKPOINT_ROWS",
    "MAX_IMPORT_ROWS",
    "ImportService",
    "build_import_service",
]

log = get_logger(__name__)

#: Upper bound on one file. Large migrations are split, which also keeps a
#: mistake's blast radius small.
MAX_IMPORT_ROWS = 5_000

#: Appended to the error export. Named with a leading underscore so they are
#: obvious to delete before re-uploading the fixed file.
ERROR_ROW_COLUMN = "_row_number"
ERROR_REASON_COLUMN = "_why_it_failed"

#: Rows committed before the session is flushed. A crash costs at most this
#: much work, and the rows already created stay created so the resume skips
#: them rather than making them twice.
COMMIT_CHECKPOINT_ROWS = 50

#: Imports at or above this many rows are handed to the ARQ worker instead of
#: being committed inside the request. Creating this many orders through the
#: normal order service takes far longer than a request should be held open,
#: and a client that times out mid-commit must never be able to retry it.
ASYNC_COMMIT_THRESHOLD_ROWS = 200

#: Byte-order marks and encodings real spreadsheet exports produce.
_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


class ImportService:
    """Drives one import from upload to committed report."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        products: ProductService,
        orders: OrderService,
        customers: CustomerService,
        settings: Settings | None = None,
    ) -> None:
        self._db = session
        self._products = products
        self._orders = orders
        self._customers = customers
        self._settings = settings or get_settings()

    # ------------------------------------------------------------- upload ---

    async def create(
        self,
        *,
        template: ImportTemplate,
        filename: str,
        content: bytes,
        content_type: str | None = None,
    ) -> ImportBatch:
        """Accept a file, detect its headers and suggest a column mapping."""
        if template not in TEMPLATES:
            raise ValidationError(
                f"{template} imports are not supported yet",
                details={"template": str(template)},
            )
        # Section 98 / Phase F section 29. Size, extension, content sniffing,
        # encoding and row count, in that order, before anything is parsed.
        # The declared content type is recorded and never believed.
        checked = validate_upload(
            content,
            filename=filename,
            content_type=content_type,
            max_rows=MAX_IMPORT_ROWS,
            # Safe here specifically because `_read_file` turns every cell back
            # into the text a CSV would have carried, so the same money and
            # phone parsers run either way.
            allow_spreadsheet=True,
        )

        digest = hashlib.sha256(content).hexdigest()

        # Section 98's idempotent duplicate handling: the identical file, already
        # committed, is refused rather than silently doubling the seller's data.
        previous = (
            await self._db.execute(
                sa.select(ImportBatch).where(
                    ImportBatch.source_sha256 == digest,
                    ImportBatch.template == template,
                    ImportBatch.status == ImportStatus.COMMITTED,
                )
            )
        ).scalar_one_or_none()
        if previous is not None:
            raise ConflictError(
                "This exact file has already been imported",
                details={
                    "import_id": str(previous.id),
                    "committed_at": previous.committed_at.isoformat()
                    if previous.committed_at
                    else None,
                    "created_count": previous.created_count,
                },
            )

        headers, rows = self._read_file(checked, filename, content)
        if not headers:
            raise ValidationError("Could not read a header row from this file")
        if len(rows) > MAX_IMPORT_ROWS:
            raise ValidationError(
                f"This file has {len(rows)} rows; the limit is {MAX_IMPORT_ROWS}. Please split it."
            )

        spec = TEMPLATES[template]
        batch = ImportBatch(
            template=template,
            status=ImportStatus.UPLOADED,
            original_filename=checked.safe_name,
            source_sha256=digest,
            content_type=content_type,
            byte_size=len(content),
            detected_headers=headers,
            column_mapping=autodetect_mapping(spec, headers),
            row_count=len(rows),
            created_by_user_id=current_context().user_id,
        )
        self._db.add(batch)
        await self._db.flush()

        if self._settings.r2_configured:
            storage = ObjectStorage(self._settings)
            batch.storage_key = storage.key(batch.tenant_id, "imports", batch.id)
            await storage.put(batch.storage_key, content, "text/csv")

        for index, raw in enumerate(rows, start=1):
            self._db.add(
                ImportRow(
                    tenant_id=batch.tenant_id,
                    import_id=batch.id,
                    row_number=index,
                    # Kept verbatim: the evidence behind every imported record.
                    raw=raw,
                    status=ImportRowStatus.PENDING,
                )
            )

        await record_audit(
            self._db,
            AuditAction.IMPORT_CREATED,
            entity_type="import",
            entity_id=batch.id,
            context={
                "template": str(template),
                "filename": filename,
                "rows": len(rows),
                "sha256": digest,
            },
        )
        await self._db.flush()
        return batch

    def _read_file(
        self, checked: UploadCheck, filename: str, content: bytes
    ) -> tuple[list[str], list[dict]]:
        """Read a CSV or an XLSX into headers and row dicts.

        The two formats converge here and nowhere else. An XLSX cell is
        rendered back to the string a CSV export of the same sheet would have
        carried, so everything downstream — mapping, money parsing, phone
        normalisation, duplicate fingerprints — is the identical code path.
        That is what stops the spreadsheet route quietly growing its own,
        looser, number handling.
        """
        if checked.detected is DetectedFormat.XLSX:
            return read_xlsx(content, max_rows=MAX_IMPORT_ROWS)
        return self._read_csv(filename, content)

    def _read_csv(self, filename: str, content: bytes) -> tuple[list[str], list[dict]]:
        """Decode and parse a CSV."""
        text: str | None = None
        for encoding in _ENCODINGS:
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValidationError("Could not read this file's text encoding")

        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel

        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        headers = [header.strip() for header in (reader.fieldnames or []) if header]
        rows = [
            {key.strip(): value for key, value in row.items() if key}
            for row in reader
            if any((value or "").strip() for value in row.values())
        ]
        return headers, rows

    # ------------------------------------------------------------ dry run ---

    async def dry_run(
        self, import_id: uuid.UUID, *, column_mapping: dict[str, str] | None = None
    ) -> ImportBatch:
        """Parse and validate every row. Writes nothing outside the import tables."""
        batch = await self.get(import_id)
        if batch.import_status.is_terminal:
            raise ConflictError("This import is already finished", details={"status": batch.status})

        spec = TEMPLATES[ImportTemplate(batch.template)]
        if column_mapping is not None:
            batch.column_mapping = column_mapping

        missing = [
            field_name for field_name in spec.required if field_name not in batch.column_mapping
        ]
        if missing:
            raise ValidationError(
                f"These columns still need mapping: {', '.join(missing)}",
                details={"missing": missing, "headers": batch.detected_headers},
            )

        rows = list(
            (
                await self._db.execute(
                    sa.select(ImportRow)
                    .where(ImportRow.import_id == batch.id)
                    .order_by(ImportRow.row_number)
                )
            )
            .scalars()
            .all()
        )

        counts = dict.fromkeys(("ready", "warning", "duplicate", "invalid"), 0)
        seen_in_file: set[str] = set()

        for row in rows:
            parsed = parse_row(spec, row.raw, batch.column_mapping)
            row.parsed = parsed.values
            row.errors = parsed.errors
            row.warnings = parsed.warnings
            row.fingerprint = row_fingerprint(spec, parsed) if parsed.is_valid else None

            if not parsed.is_valid:
                row.status = ImportRowStatus.INVALID
                counts["invalid"] += 1
                continue

            if row.fingerprint is not None and row.fingerprint in seen_in_file:
                # The same logical record twice in one file.
                row.status = ImportRowStatus.DUPLICATE
                row.warnings = [
                    *row.warnings,
                    {"field": "_row", "message": "Duplicate of an earlier row in this file"},
                ]
                counts["duplicate"] += 1
                continue

            if row.fingerprint is not None:
                seen_in_file.add(row.fingerprint)
                if await self._already_imported(
                    batch, row.fingerprint
                ) and not await self._is_new_count(spec.template, parsed.values):
                    row.status = ImportRowStatus.DUPLICATE
                    row.warnings = [
                        *row.warnings,
                        {"field": "_row", "message": "Already imported previously"},
                    ]
                    counts["duplicate"] += 1
                    continue

            if parsed.warnings:
                row.status = ImportRowStatus.WARNING
                counts["warning"] += 1
            else:
                row.status = ImportRowStatus.READY
                counts["ready"] += 1

        batch.ready_count = counts["ready"]
        batch.warning_count = counts["warning"]
        batch.duplicate_count = counts["duplicate"]
        batch.invalid_count = counts["invalid"]
        batch.dry_run_at = utc_now()
        batch.status = ImportStatus.VALIDATED

        await self._db.flush()
        return batch

    async def _is_new_count(self, template: ImportTemplate, values: dict[str, Any]) -> bool:
        """A re-imported product row that states a *different* stock count.

        Such a row is a stock take, not a duplicate: committing it records the
        difference as one adjustment. The same count again is still a duplicate.
        """
        if template is not ImportTemplate.PRODUCTS or not values.get("stock_given"):
            return False
        change = await self._products.counted_change(values.get("sku"), values.get("opening_stock"))
        return bool(change)

    async def _already_imported(self, batch: ImportBatch, fingerprint: str) -> bool:
        """Whether a previous committed import already created this record."""
        found = (
            await self._db.execute(
                sa.select(ImportRow.id)
                .join(ImportBatch, ImportBatch.id == ImportRow.import_id)
                .where(
                    ImportRow.fingerprint == fingerprint,
                    ImportRow.status == ImportRowStatus.CREATED,
                    ImportBatch.template == batch.template,
                    ImportRow.import_id != batch.id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        return found is not None

    # ------------------------------------------------------------- commit ---

    async def commit(self, import_id: uuid.UUID) -> ImportBatch:
        """Create the records for every importable row.

        A row that fails at creation is marked invalid with the reason and the
        rest continue. An import is not all-or-nothing: one bad row out of two
        hundred should not cost the seller the other hundred and ninety-nine.
        """
        batch = await self.get(import_id, lock=True)
        if batch.import_status is ImportStatus.COMMITTED:
            raise ConflictError(
                "This import was already committed",
                details={"created_count": batch.created_count},
            )
        # A batch left in COMMITTING by a worker that died is resumed rather
        # than refused: the row selection below only takes rows that are still
        # READY or WARNING, so anything already created is skipped instead of
        # being made twice. Without this the batch is stranded, because
        # `can_commit` requires VALIDATED and it can never return to that.
        if not (batch.can_commit or batch.is_resumable):
            raise ConflictError(
                "Run a dry run first, and make sure there is something to import",
                details={"status": batch.status},
            )

        template = ImportTemplate(batch.template)
        batch.status = ImportStatus.COMMITTING
        if batch.started_at is None:
            batch.started_at = utc_now()
        await self._db.flush()

        rows = list(
            (
                await self._db.execute(
                    sa.select(ImportRow)
                    .where(
                        ImportRow.import_id == batch.id,
                        ImportRow.status.in_([ImportRowStatus.READY, ImportRowStatus.WARNING]),
                    )
                    .order_by(ImportRow.row_number)
                )
            )
            .scalars()
            .all()
        )

        created = 0
        for row in rows:
            try:
                entity_id = await self._create_entity(template, row)
            except Exception as error:
                row.status = ImportRowStatus.INVALID
                row.errors = [
                    *row.errors,
                    {"field": "_row", "message": _row_failure_message(error)},
                ]
                batch.invalid_count += 1
                continue

            row.status = ImportRowStatus.CREATED
            row.created_entity_id = entity_id
            created += 1
            batch.processed_count += 1

            # Checkpoint. A crash then costs at most one chunk of work rather
            # than the whole import, and the rows already created stay created
            # so the resume does not repeat them.
            if created % COMMIT_CHECKPOINT_ROWS == 0:
                await self._db.flush()

        # `+=` rather than `=`: a resumed commit adds to what the previous
        # attempt already created instead of reporting only its own share.
        batch.created_count += created
        batch.committed_at = utc_now()
        batch.status = ImportStatus.COMMITTED

        await record_audit(
            self._db,
            AuditAction.IMPORT_COMMITTED,
            entity_type="import",
            entity_id=batch.id,
            context={"template": str(template), "created": created},
        )
        await enqueue(
            self._db,
            OutboxTopic.IMPORT_COMMITTED,
            {"import_id": str(batch.id), "created": created},
        )
        await self._db.flush()
        return batch

    async def _create_entity(self, template: ImportTemplate, row: ImportRow) -> uuid.UUID:
        values = row.parsed

        if template is ImportTemplate.PRODUCTS:
            # Keyed on the import row, so a resumed or retried commit can never
            # move the same row's stock twice.
            return await self._products.import_row(
                values, idempotency_key=f"import:{row.import_id}:{row.row_number}"
            )

        order, _ = await self._orders.create(
            OrderDraft(
                phone=values["phone"],
                customer_name=values.get("customer_name"),
                address=values.get("address"),
                district=values.get("district"),
                area=values.get("area"),
                items=[
                    OrderItemDraft(
                        name=values["product"],
                        quantity=values.get("quantity", 1),
                        unit_price_paisa=values.get("cod_amount_paisa", 0)
                        // max(1, values.get("quantity", 1)),
                    )
                ],
                cod_amount_paisa=values.get("cod_amount_paisa", 0),
                note=values.get("note"),
                channel=OrderChannel.CSV_IMPORT,
            ),
            # The file is the seller's own history; warning on every row would
            # be noise. Duplicates are already handled by the fingerprint pass.
            check_duplicates=False,
        )
        return order.id

    # --------------------------------------------------------------- read ---

    async def get(self, import_id: uuid.UUID, *, lock: bool = False) -> ImportBatch:
        """Read a batch. ``lock`` takes the row for the rest of the transaction.

        The commit paths lock, so a retry that races a still-running request
        waits for it and then finds the batch committed or claimed, instead of
        both reading ``VALIDATED`` and creating every row twice.
        """
        batch = await self._db.get(
            ImportBatch,
            import_id,
            with_for_update=True if lock else None,
            populate_existing=lock,
        )
        if batch is None:
            raise NotFoundError("Import not found")
        return batch

    async def rows(
        self,
        import_id: uuid.UUID,
        *,
        status: ImportRowStatus | None = None,
        limit: int = 100,
    ) -> list[ImportRow]:
        stmt = (
            sa.select(ImportRow)
            .where(ImportRow.import_id == import_id)
            .order_by(ImportRow.row_number)
            .limit(limit)
        )
        if status is not None:
            stmt = stmt.where(ImportRow.status == status)
        return list((await self._db.execute(stmt)).scalars().all())

    async def begin_async_commit(self, import_id: uuid.UUID) -> ImportBatch:
        """Mark an import as committing and hand the work to the worker.

        Both halves happen in the caller's transaction, which is the point: the
        job is enqueued through the outbox rather than straight onto a queue, so
        it exists if and only if this transaction commits. A direct enqueue
        would leave an orphan job whenever the request rolled back — a worker
        committing an import the seller never confirmed.

        The batch moves to ``COMMITTING`` here so a second tap finds it already
        claimed instead of enqueueing a second job.
        """
        batch = await self.get(import_id, lock=True)
        if batch.import_status is ImportStatus.COMMITTED:
            raise ConflictError(
                "This import was already committed",
                details={"created_count": batch.created_count},
            )
        if batch.import_status is ImportStatus.COMMITTING:
            # Already claimed. Returning it rather than raising keeps a
            # double-tap idempotent from the client's point of view.
            return batch
        if not batch.can_commit:
            raise ConflictError(
                "Run a dry run first, and make sure there is something to import",
                details={"status": batch.status},
            )

        batch.status = ImportStatus.COMMITTING
        batch.started_at = utc_now()
        await self._db.flush()

        event = await enqueue(
            self._db,
            OutboxTopic.IMPORT_COMMIT_REQUESTED,
            {"import_id": str(batch.id)},
            # One job per import, whatever else happens upstream.
            dedupe_key=f"import-commit:{batch.id}",
        )
        batch.job_id = str(event.id)[:64]
        await self._db.flush()
        return batch

    async def history(
        self,
        *,
        template: ImportTemplate | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ImportBatch]:
        """Past imports, newest first.

        Tenant-scoped by the session, so this is every import *this shop* has
        run — including the failed and cancelled ones, which are the ones a
        seller comes looking for.
        """
        statement = sa.select(ImportBatch).order_by(ImportBatch.created_at.desc())
        if template is not None:
            statement = statement.where(ImportBatch.template == str(template))
        statement = statement.offset(max(0, offset)).limit(max(1, min(limit, 200)))
        return list((await self._db.execute(statement)).scalars().all())

    async def error_rows(self, import_id: uuid.UUID) -> list[ImportRow]:
        """Every row that could not be created, in file order.

        Deliberately not paginated: this is what the error export is built
        from, and an export that silently stopped at a hundred rows would be
        worse than no export at all.
        """
        batch = await self.get(import_id)
        return list(
            (
                await self._db.execute(
                    sa.select(ImportRow)
                    .where(
                        ImportRow.import_id == batch.id,
                        ImportRow.status.in_([ImportRowStatus.INVALID, ImportRowStatus.DUPLICATE]),
                    )
                    .order_by(ImportRow.row_number)
                )
            )
            .scalars()
            .all()
        )

    async def error_csv(self, import_id: uuid.UUID) -> str:
        """The failed rows as a CSV the seller can fix and re-upload.

        The seller's **original columns** are preserved, in their original
        order, with two appended: the row number from the file they uploaded,
        and why it was refused. That is what makes the download usable as an
        input — fix the flagged rows, delete the two added columns, upload
        again — rather than only a report to read.
        """
        batch = await self.get(import_id)
        rows = await self.error_rows(import_id)

        headers = [str(header) for header in (batch.detected_headers or []) if header]
        if not headers:
            # A batch whose headers were never recorded still produces a useful
            # file: take the columns from the rows themselves.
            seen: list[str] = []
            for row in rows:
                for key in row.raw or {}:
                    if str(key) not in seen:
                        seen.append(str(key))
            headers = seen

        buffer = io.StringIO()
        # QUOTE_ALL so a Bangla address containing a comma survives the round
        # trip, and CRLF because that is what Excel expects.
        writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
        writer.writerow([*headers, ERROR_ROW_COLUMN, ERROR_REASON_COLUMN])

        for row in rows:
            raw = row.raw or {}
            reasons = "; ".join(
                str(error.get("message", "")).strip()
                for error in (row.errors or [])
                if str(error.get("message", "")).strip()
            )
            if not reasons and row.status == ImportRowStatus.DUPLICATE:
                reasons = "Already imported"
            writer.writerow(
                [*(str(raw.get(header, "")) for header in headers), row.row_number, reasons]
            )

        # A BOM so Excel opens Bangla text in the right encoding rather than
        # showing mojibake, which is the first thing a seller would report.
        return "﻿" + buffer.getvalue()

    # ------------------------------------------------------ saved mappings ---

    async def save_mapping(
        self,
        *,
        name: str,
        template: ImportTemplate,
        mapping: dict[str, str],
        source_headers: list[str] | None = None,
    ) -> ImportMapping:
        """Save a column mapping for reuse, or update one of the same name.

        Updating rather than duplicating: a seller who saves "Daily orders"
        twice means "this is what Daily orders is now", not "I would like two
        mappings with the same name to choose between".
        """
        cleaned_name = (name or "").strip()
        if not cleaned_name:
            raise ValidationError("Give this mapping a name so you can find it again")
        if not mapping:
            raise ValidationError("Map at least one column before saving")
        if template not in TEMPLATES:
            raise ValidationError(f"{template} imports are not supported yet")

        existing = (
            await self._db.execute(
                sa.select(ImportMapping).where(
                    ImportMapping.template == str(template),
                    ImportMapping.name == cleaned_name,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.mapping = dict(mapping)
            existing.source_headers = list(source_headers or [])
            await self._db.flush()
            return existing

        saved = ImportMapping(
            name=cleaned_name,
            template=str(template),
            mapping=dict(mapping),
            source_headers=list(source_headers or []),
            created_by_user_id=current_context().user_id,
        )
        self._db.add(saved)
        await self._db.flush()
        return saved

    async def list_mappings(
        self, *, template: ImportTemplate | None = None, headers: list[str] | None = None
    ) -> list[ImportMapping]:
        """Saved mappings, most recently used first.

        When ``headers`` is given, only mappings whose columns are all present
        in that file are returned. Offering one that cannot apply is worse than
        offering none: the seller picks it and every row fails.
        """
        statement = sa.select(ImportMapping).order_by(
            ImportMapping.last_used_at.desc().nullslast(),
            ImportMapping.created_at.desc(),
        )
        if template is not None:
            statement = statement.where(ImportMapping.template == str(template))
        rows = list((await self._db.execute(statement)).scalars().all())
        if headers is None:
            return rows
        return [row for row in rows if row.matches(headers)]

    async def apply_mapping(self, import_id: uuid.UUID, mapping_id: uuid.UUID) -> ImportBatch:
        """Put a saved mapping onto an import, and record that it was used."""
        batch = await self.get(import_id)
        saved = (
            await self._db.execute(sa.select(ImportMapping).where(ImportMapping.id == mapping_id))
        ).scalar_one_or_none()
        if saved is None:
            raise NotFoundError("That saved mapping does not exist")
        if saved.template != batch.template:
            raise ValidationError(
                "That mapping is for a different kind of import",
                details={
                    "mapping_template": saved.template,
                    "import_template": batch.template,
                },
            )

        batch.column_mapping = dict(saved.mapping)
        batch.status = ImportStatus.MAPPED
        saved.last_used_at = utc_now()
        saved.use_count += 1
        await self._db.flush()
        return batch

    async def delete_mapping(self, mapping_id: uuid.UUID) -> None:
        saved = (
            await self._db.execute(sa.select(ImportMapping).where(ImportMapping.id == mapping_id))
        ).scalar_one_or_none()
        if saved is None:
            raise NotFoundError("That saved mapping does not exist")
        await self._db.delete(saved)
        await self._db.flush()


def _row_failure_message(error: Exception) -> str:
    """What a seller is told about a row that failed at creation.

    This text reaches the row report and the downloadable error CSV. An
    ``AppError`` carries copy written for a seller; anything else is an internal
    exception whose text can name tables, constraints or values, and is logged
    rather than shown.
    """
    if isinstance(error, AppError):
        return error.message_en[:300]
    log.warning("import row failed", extra={"error": type(error).__name__})
    return "This row could not be created. Check its values and import it again."


def build_import_service(session: AsyncSession, settings: Settings | None = None) -> ImportService:
    """Assemble the service with its collaborators.

    One builder for both callers — the API route and the worker job — so a
    background commit runs through the identical object graph a request does.
    Two assemblies would be two chances for the worker to construct a slightly
    different service and import under slightly different rules.
    """
    from app.api.deps import get_hasher, get_vault
    from app.customers.service import CustomerService
    from app.orders.service import OrderService
    from app.products.service import ProductService

    resolved = settings or get_settings()
    customers = CustomerService(session, hasher=get_hasher(resolved), vault=get_vault(resolved))
    return ImportService(
        session,
        products=ProductService(session),
        orders=OrderService(session, customers=customers),
        customers=customers,
        settings=resolved,
    )
