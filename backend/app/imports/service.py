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

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.outbox import OutboxTopic, enqueue
from app.core.clock import utc_now
from app.core.context import current_context
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.customers.service import CustomerService
from app.imports.models import (
    ImportBatch,
    ImportRow,
    ImportRowStatus,
    ImportStatus,
    ImportTemplate,
)
from app.imports.templates import TEMPLATES, autodetect_mapping, parse_row, row_fingerprint
from app.orders.models import OrderChannel
from app.orders.service import OrderDraft, OrderItemDraft, OrderService
from app.products.service import ProductService

__all__ = ["MAX_IMPORT_ROWS", "ImportService"]

#: Upper bound on one file. Large migrations are split, which also keeps a
#: mistake's blast radius small.
MAX_IMPORT_ROWS = 5_000

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
    ) -> None:
        self._db = session
        self._products = products
        self._orders = orders
        self._customers = customers

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
        if not content:
            raise ValidationError("The uploaded file is empty")

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

        headers, rows = self._read_csv(filename, content)
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
            original_filename=filename,
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

    def _read_csv(self, filename: str, content: bytes) -> tuple[list[str], list[dict]]:
        """Decode and parse a CSV.

        XLSX is detected and reported rather than half-parsed: the file starts
        with a ZIP signature, and feeding that to a CSV reader produces garbage
        rows instead of an error a seller can act on.
        """
        if content[:2] == b"PK":
            raise ValidationError(
                "This looks like an Excel file. Please export it as CSV and try "
                "again — XLSX import is not supported yet.",
                details={"filename": filename},
            )

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
                if await self._already_imported(batch, row.fingerprint):
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
        batch = await self.get(import_id)
        if batch.import_status is ImportStatus.COMMITTED:
            raise ConflictError(
                "This import was already committed",
                details={"created_count": batch.created_count},
            )
        if not batch.can_commit:
            raise ConflictError(
                "Run a dry run first, and make sure there is something to import",
                details={"status": batch.status},
            )

        template = ImportTemplate(batch.template)
        batch.status = ImportStatus.COMMITTING
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
                    {"field": "_row", "message": str(error)[:300]},
                ]
                batch.invalid_count += 1
                continue

            row.status = ImportRowStatus.CREATED
            row.created_entity_id = entity_id
            created += 1

        batch.created_count = created
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
            product = await self._products.create(
                name=values["name"],
                sku=values.get("sku"),
                description=values.get("description"),
                cost_paisa=values.get("cost_paisa", 0),
                default_selling_price_paisa=values.get("default_selling_price_paisa", 0),
                opening_stock=values.get("opening_stock", 0),
            )
            return product.id

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

    async def get(self, import_id: uuid.UUID) -> ImportBatch:
        batch = await self._db.get(ImportBatch, import_id)
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
