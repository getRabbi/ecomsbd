"""Recording payouts.

Three ways money arrives (master spec section 16): a provider payment API, an
uploaded statement, or a figure the seller types in. None of them settles
anything on its own — recording a payout and reconciling it are separate steps,
because a statement is evidence and applying it is a decision.

The invariants this module carries are section 81's: a payout has a unique
provider identity where one exists (81.1), re-importing the same statement is
idempotent (81.2), an unmatched line does not block the rest (81.3), and the
source file is never deleted (81.4).
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.object_storage import ObjectStorage
from app.common.pagination import Cursor, apply_cursor
from app.common.uploads import DetectedFormat, UploadCheck, validate_upload
from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.context import current_context
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.imports.spreadsheet import read_xlsx
from app.payouts.models import (
    Payout,
    PayoutAdjustment,
    PayoutLine,
    PayoutLineStatus,
    PayoutSource,
    PayoutSourceFile,
    PayoutStatus,
)
from app.payouts.statements import (
    ParsedStatement,
    autodetect_columns,
    classify_adjustment,
    parse_rows,
    parse_statement,
    sha256_of,
)

__all__ = ["MAX_STATEMENT_BYTES", "MAX_STATEMENT_ROWS", "PayoutService"]

#: Guards memory on the single VPS this runs on. A courier statement with more
#: lines than this is not a statement, it is an export of a year.
MAX_STATEMENT_ROWS = 5_000

#: Statements are small. A larger file is almost certainly the wrong file.
MAX_STATEMENT_BYTES = 5 * 1024 * 1024

_XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class PayoutService:
    """Creates payouts and keeps their source evidence."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    # --------------------------------------------------------------- manual --

    async def record_manual(
        self,
        *,
        provider: str,
        total_paisa: int,
        paid_on: date | None = None,
        reference: str | None = None,
        note: str | None = None,
        received_at: datetime | None = None,
    ) -> Payout:
        """Record a lump sum the seller says arrived.

        Creates one line covering the whole amount, with no reference. Section
        82: *"manual payout with only lump total may generate proposed
        allocation, not irreversible settlement"* — so this line can be
        suggested against parcels but can never auto-match, because there is
        nothing to match on but the number.
        """
        if total_paisa <= 0:
            raise ValidationError("A payout must be a positive amount")

        payout = await self._create_payout(
            provider=provider,
            source=PayoutSource.MANUAL,
            total_paisa=total_paisa,
            paid_on=paid_on,
            reference=reference,
            note=note,
            received_at=received_at,
        )
        self._db.add(
            PayoutLine(
                payout_id=payout.id,
                row_number=1,
                amount_paisa=total_paisa,
                status=str(PayoutLineStatus.UNMATCHED),
                raw={"entered_by_hand": True, "note": note},
            )
        )
        await self._db.flush()
        await self._db.refresh(payout, attribute_names=["lines"])

        await record_audit(
            self._db,
            action=AuditAction.PAYOUT_RECORDED,
            entity_type="payout",
            entity_id=payout.id,
            context={"provider": provider, "total_paisa": total_paisa},
        )
        return payout

    # ------------------------------------------------------------ statement --

    async def preview_statement(
        self,
        content: bytes,
        *,
        mapping: dict[str, str] | None = None,
        filename: str | None = None,
    ) -> ParsedStatement:
        """Read a statement without saving anything.

        The seller sees the detected columns and the parsed rows — including
        the ones that could not be read — before any of it exists in their
        shop's data. A mapping the seller corrected is validated against the
        file's own headers before it is used to read a single amount.
        """
        checked = self._guard_size(content, filename=filename)
        parsed = self._parse(checked, content, mapping)
        if len(parsed.rows) > MAX_STATEMENT_ROWS:
            raise ValidationError(
                f"That file has more than {MAX_STATEMENT_ROWS:,} rows",
                details={"row_count": len(parsed.rows)},
            )
        return parsed

    async def import_statement(
        self,
        content: bytes,
        *,
        provider: str,
        filename: str,
        mapping: dict[str, str] | None = None,
        paid_on: date | None = None,
        reference: str | None = None,
        note: str | None = None,
    ) -> Payout:
        """Import a statement into a payout with one line per row.

        Re-importing the same file is refused by content hash (section 81.2):
        a seller who uploads Tuesday's statement twice would otherwise double
        their settled total, and the second import would look exactly as
        legitimate as the first.

        Rows that could not be parsed are still imported, marked ``UNMAPPABLE``
        and carrying the text the file contained. Dropping them would hide
        money that genuinely arrived.
        """
        checked = self._guard_size(content, filename=filename)
        digest = sha256_of(content)

        await self._refuse_reimport(digest)

        parsed = await self.preview_statement(content, mapping=mapping, filename=filename)
        if not parsed.rows:
            raise ValidationError("That file has no rows we could read")

        now = utc_now()
        context = current_context()
        source_file = PayoutSourceFile(
            provider=provider,
            # The sanitised name, never the client's. It reaches storage
            # keys and log lines.
            original_filename=checked.safe_name,
            sha256=digest,
            size_bytes=len(content),
            # Retain parsed evidence; preserve the original bytes privately in R2.
            storage_key=None,
            raw_content=checked.text or _as_csv(parsed),
            uploaded_by=context.user_id if context else None,
            imported_at=now,
            created_at=now,
        )
        try:
            # Two uploads of the same file racing each other: the unique
            # (tenant, sha256) key lets exactly one through, and the loser gets
            # the same answer a sequential re-upload would.
            async with self._db.begin_nested():
                self._db.add(source_file)
                await self._db.flush()
        except IntegrityError as exc:
            await self._refuse_reimport(digest)
            raise ConflictError("That statement has already been imported") from exc

        settings = get_settings()
        if settings.r2_configured:
            storage = ObjectStorage(settings)
            source_file.storage_key = storage.key(source_file.tenant_id, "payouts", source_file.id)
            await storage.put(
                source_file.storage_key,
                content,
                _XLSX_TYPE if checked.detected is DetectedFormat.XLSX else "text/csv",
            )

        payout = await self._create_payout(
            provider=provider,
            source=PayoutSource.STATEMENT,
            total_paisa=parsed.total_paisa,
            paid_on=paid_on,
            reference=reference,
            note=note,
            received_at=now,
            source_file_id=source_file.id,
            metadata={
                "detected_headers": parsed.headers,
                "column_mapping": parsed.mapping,
                "row_count": len(parsed.rows),
                "invalid_row_count": len(parsed.invalid_rows),
            },
        )

        seen_references: dict[str, int] = {}
        for row in parsed.rows:
            status = PayoutLineStatus.UNMATCHED
            if not row.is_valid:
                status = PayoutLineStatus.UNMAPPABLE

            reference_key = row.consignment_id or row.tracking_code or row.merchant_reference
            if reference_key:
                # Section 16 lists "duplicate payout line" as its own
                # reconciliation case. Flagging it here means the second
                # occurrence never gets applied by accident.
                if reference_key in seen_references:
                    status = PayoutLineStatus.DUPLICATE
                seen_references[reference_key] = row.row_number

            line = PayoutLine(
                payout_id=payout.id,
                row_number=row.row_number,
                amount_paisa=row.amount_paisa or 0,
                status=str(status),
                provider_consignment_id=row.consignment_id,
                tracking_code=row.tracking_code,
                merchant_reference=row.merchant_reference,
                customer_phone_last4=row.phone_last4,
                delivered_on=row.delivered_on,
                raw={**row.raw, "errors": row.errors},
            )
            self._db.add(line)
            await self._db.flush()

            if row.fee_paisa:
                adjustment_type, rule = classify_adjustment(row.fee_label)
                self._db.add(
                    PayoutAdjustment(
                        payout_id=payout.id,
                        payout_line_id=line.id,
                        type=str(adjustment_type),
                        amount_paisa=row.fee_paisa,
                        provider_label=row.fee_label,
                        raw_text=row.fee_label,
                        recognized_rule=rule,
                    )
                )

        await self._db.flush()
        await self._db.refresh(payout, attribute_names=["lines"])

        await record_audit(
            self._db,
            action=AuditAction.PAYOUT_IMPORTED,
            entity_type="payout",
            entity_id=payout.id,
            context={
                "provider": provider,
                "filename": filename,
                "sha256": digest,
                "row_count": len(parsed.rows),
                "total_paisa": parsed.total_paisa,
            },
        )
        return payout

    def detect_columns(self, content: bytes) -> dict[str, str]:
        parsed = parse_statement(content)
        return autodetect_columns(parsed.headers)

    def _parse(
        self, checked: UploadCheck, content: bytes, mapping: dict[str, str] | None
    ) -> ParsedStatement:
        """CSV or XLSX into lines, through the one row parser both share."""
        if checked.detected is DetectedFormat.XLSX:
            headers, rows = read_xlsx(content, max_rows=MAX_STATEMENT_ROWS)
            return parse_rows(headers, rows, mapping=mapping)
        return parse_statement(content, mapping=mapping)

    async def _refuse_reimport(self, digest: str) -> None:
        """Section 81.2: the same statement bytes are never imported twice."""
        existing = await self._db.execute(
            sa.select(PayoutSourceFile).where(PayoutSourceFile.sha256 == digest)
        )
        duplicate = existing.scalar_one_or_none()
        if duplicate is not None:
            payout_id = (
                await self._db.execute(
                    sa.select(Payout.id).where(Payout.source_file_id == duplicate.id).limit(1)
                )
            ).scalar_one_or_none()
            raise ConflictError(
                "That statement has already been imported",
                details={
                    "imported_at": duplicate.imported_at.isoformat(),
                    "original_filename": duplicate.original_filename,
                    "payout_id": str(payout_id) if payout_id else None,
                },
            )

    # -------------------------------------------------------------- reading --

    async def get(self, payout_id: uuid.UUID) -> Payout:
        payout = await self._db.get(Payout, payout_id)
        if payout is None:
            raise NotFoundError("Payout not found")
        return payout

    async def list_payouts(
        self,
        *,
        limit: int = 30,
        cursor: Cursor | None = None,
        provider: str | None = None,
        status: PayoutStatus | None = None,
    ) -> list[Payout]:
        stmt = sa.select(Payout)
        if provider is not None:
            stmt = stmt.where(Payout.provider == provider)
        if status is not None:
            stmt = stmt.where(Payout.status == str(status))
        stmt = apply_cursor(stmt, Payout, cursor)
        stmt = stmt.order_by(Payout.created_at.desc(), Payout.id.desc()).limit(limit + 1)
        return list((await self._db.execute(stmt)).scalars().all())

    async def lines(
        self,
        payout_id: uuid.UUID,
        *,
        status: PayoutLineStatus | None = None,
        limit: int | None = 500,
    ) -> list[PayoutLine]:
        """A payout's lines in file order.

        ``limit=None`` reads every line. Reconciliation and the totals need all
        of them: a statement may hold up to ``MAX_STATEMENT_ROWS``, and capping
        the read would silently leave the tail of a long statement unmatched.
        """
        stmt = sa.select(PayoutLine).where(PayoutLine.payout_id == payout_id)
        if status is not None:
            stmt = stmt.where(PayoutLine.status == str(status))
        stmt = stmt.order_by(PayoutLine.row_number.asc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self._db.execute(stmt)).scalars().all())

    async def adjustments(self, payout_id: uuid.UUID) -> list[PayoutAdjustment]:
        result = await self._db.execute(
            sa.select(PayoutAdjustment)
            .where(PayoutAdjustment.payout_id == payout_id)
            .order_by(PayoutAdjustment.created_at.asc())
        )
        return list(result.scalars().all())

    async def source_file(self, payout: Payout) -> PayoutSourceFile | None:
        if payout.source_file_id is None:
            return None
        return await self._db.get(PayoutSourceFile, payout.source_file_id)

    # ------------------------------------------------------------ internals --

    async def refresh_totals(self, payout_id: uuid.UUID) -> Payout:
        """Recompute what has been applied, and the payout's status.

        Derived from the lines rather than incremented as they are matched, so
        a reversal cannot leave the header disagreeing with its own rows.
        """
        payout = await self.get(payout_id)
        rows = await self.lines(payout_id, limit=None)

        payout.applied_paisa = sum(line.applied_paisa for line in rows)
        if not rows:
            payout.status = str(PayoutStatus.RECEIVED)
        elif all(line.line_status.is_resolved for line in rows):
            payout.status = str(PayoutStatus.RECONCILED)
        elif any(line.line_status.is_applied for line in rows):
            payout.status = str(PayoutStatus.PARTIALLY_RECONCILED)
        else:
            payout.status = str(PayoutStatus.RECEIVED)

        await self._db.flush()
        return payout

    async def _create_payout(
        self,
        *,
        provider: str,
        source: PayoutSource,
        total_paisa: int,
        paid_on: date | None,
        reference: str | None,
        note: str | None,
        received_at: datetime | None = None,
        source_file_id: uuid.UUID | None = None,
        metadata: dict | None = None,
    ) -> Payout:
        if reference:
            existing = await self._db.execute(
                sa.select(Payout).where(
                    Payout.provider == provider,
                    Payout.provider_reference == reference,
                )
            )
            if existing.scalar_one_or_none() is not None:
                raise ConflictError(
                    "A payout with that reference already exists",
                    details={"provider": provider, "reference": reference},
                )

        payout = Payout(
            provider=provider,
            provider_reference=reference,
            source=str(source),
            status=str(PayoutStatus.RECEIVED),
            total_paisa=total_paisa,
            paid_on=paid_on,
            received_at=received_at or utc_now(),
            note=note,
            source_file_id=source_file_id,
            metadata_json=metadata or {},
        )
        self._db.add(payout)
        await self._db.flush()
        return payout

    def _guard_size(self, content: bytes, *, filename: str | None = None) -> UploadCheck:
        """Validate a statement upload before it is parsed.

        Phase F section 29: size, extension, **content sniffing**, encoding and
        row count. An XLSX renamed to ``.csv`` is caught by its ZIP magic
        number, not by its name — and refused with a sentence the seller can
        act on rather than parsed into a payout full of nonsense amounts.
        """
        return validate_upload(
            content,
            filename=filename,
            max_bytes=MAX_STATEMENT_BYTES,
            max_rows=MAX_STATEMENT_ROWS,
            # Read safely by the import module's spreadsheet reader: streamed,
            # formulas off, every cell back to the text a CSV would carry.
            allow_spreadsheet=True,
        )


def _as_csv(parsed: ParsedStatement) -> str:
    """A spreadsheet's rows as CSV text, kept as the statement's evidence.

    Used only when the original was an XLSX and there is no text to keep
    verbatim; the original bytes also go to R2 when it is configured.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(parsed.headers)
    for row in parsed.rows:
        writer.writerow([row.raw.get(header, "") for header in parsed.headers])
    return buffer.getvalue()
