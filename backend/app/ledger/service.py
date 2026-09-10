"""Writing to and reading from the financial ledger.

The only write path is :meth:`LedgerService.record`, and the only way to undo
something is :meth:`LedgerService.reverse`, which writes a *new* entry pointing
back at the old one. There is no update and no delete anywhere in this module —
master spec section 80: "ledger entry is never edited".

Balances come from :meth:`LedgerService.balances`, which sums the ledger rather
than reading a maintained total. Section 81.10 requires the dashboard to
reconcile to the ledger; the cheapest way to guarantee that is for the
dashboard to *be* the ledger.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.money import BDT
from app.core.clock import business_date, utc_now
from app.core.context import current_context
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.ledger.models import (
    EVENT_SHAPE,
    LedgerBucket,
    LedgerDirection,
    LedgerEntry,
    LedgerEventType,
    LedgerSource,
)

__all__ = ["BucketBalance", "LedgerService"]


@dataclass(frozen=True, slots=True)
class BucketBalance:
    """The net of one bucket over a period."""

    bucket: LedgerBucket
    credit_paisa: int
    debit_paisa: int

    @property
    def net_paisa(self) -> int:
        return self.credit_paisa - self.debit_paisa


class LedgerService:
    """Append-only access to a shop's money history."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def record(
        self,
        *,
        event_type: LedgerEventType,
        entity_type: str,
        entity_id: uuid.UUID,
        amount_paisa: int,
        source: LedgerSource,
        occurred_at: datetime | None = None,
        source_ref: str | None = None,
        reason: str | None = None,
        metadata: dict | None = None,
        bucket: LedgerBucket | None = None,
        direction: LedgerDirection | None = None,
    ) -> LedgerEntry:
        """Append one entry.

        ``bucket`` and ``direction`` default to the pairing declared for the
        event in ``EVENT_SHAPE``. They can be overridden — a manual adjustment
        can be a debit as easily as a credit — but the default means a caller
        cannot accidentally file a delivery under ``COD_SETTLED``.

        A zero amount is refused rather than stored. A ledger full of zero rows
        is noise a seller has to read past, and nothing that produces one is
        describing a real money event.
        """
        if amount_paisa < 0:
            raise ValidationError(
                "A ledger amount is always positive; direction carries the sign",
                details={"amount_paisa": amount_paisa},
            )
        if amount_paisa == 0:
            raise ValidationError(
                "A ledger entry must move a non-zero amount",
                details={"event_type": str(event_type)},
            )

        default_direction, default_bucket = EVENT_SHAPE[event_type]
        moment = occurred_at or utc_now()
        context = current_context()

        entry = LedgerEntry(
            occurred_at=moment,
            business_date=business_date(at=moment),
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=str(event_type),
            currency=BDT,
            amount_paisa=amount_paisa,
            direction=str(direction or default_direction),
            bucket=str(bucket or default_bucket),
            source=str(source),
            source_ref=source_ref,
            reason=reason,
            actor_user_id=context.user_id if context else None,
            metadata_json=metadata or {},
        )
        self._db.add(entry)
        await self._db.flush()
        return entry

    async def reverse(
        self, entry_id: uuid.UUID, *, reason: str, source: LedgerSource
    ) -> LedgerEntry:
        """Undo an entry by writing its mirror image.

        The original row is left exactly as it was. What changes is that a
        second row now exists with the opposite direction and a ``reversal_of``
        pointer, so both the mistake and the fix are visible — which is what
        lets support explain a balance that moved.
        """
        original = await self._db.get(LedgerEntry, entry_id)
        if original is None:
            raise NotFoundError("Ledger entry not found")

        existing = await self._db.execute(
            sa.select(sa.func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.reversal_of == entry_id)
        )
        if existing.scalar_one() > 0:
            # Reversing twice would credit the money back a second time.
            raise ConflictError(
                "That entry has already been reversed",
                details={"entry_id": str(entry_id)},
            )

        opposite = (
            LedgerDirection.DEBIT
            if original.ledger_direction is LedgerDirection.CREDIT
            else LedgerDirection.CREDIT
        )
        context = current_context()
        now = utc_now()

        reversal = LedgerEntry(
            occurred_at=now,
            business_date=business_date(at=now),
            entity_type=original.entity_type,
            entity_id=original.entity_id,
            event_type=str(LedgerEventType.REVERSAL),
            currency=original.currency,
            amount_paisa=original.amount_paisa,
            direction=str(opposite),
            # The reversal lands in the same bucket it is undoing, so the
            # bucket's net returns to where it was.
            bucket=original.bucket,
            source=str(source),
            source_ref=original.source_ref,
            reversal_of=original.id,
            reason=reason,
            actor_user_id=context.user_id if context else None,
            metadata_json={"reversed_event_type": original.event_type},
        )
        self._db.add(reversal)
        await self._db.flush()
        return reversal

    async def balances(
        self,
        *,
        since: date | None = None,
        until: date | None = None,
    ) -> dict[LedgerBucket, BucketBalance]:
        """Net every bucket over an optional business-date range.

        Reversals need no special handling: a reversal is a row with the
        opposite direction in the same bucket, so summing the bucket already
        accounts for it.
        """
        stmt = sa.select(
            LedgerEntry.bucket,
            LedgerEntry.direction,
            sa.func.coalesce(sa.func.sum(LedgerEntry.amount_paisa), 0),
        ).group_by(LedgerEntry.bucket, LedgerEntry.direction)

        if since is not None:
            stmt = stmt.where(LedgerEntry.business_date >= since)
        if until is not None:
            stmt = stmt.where(LedgerEntry.business_date <= until)

        credits: dict[str, int] = {}
        debits: dict[str, int] = {}
        for bucket, direction, total in (await self._db.execute(stmt)).all():
            target = credits if direction == str(LedgerDirection.CREDIT) else debits
            target[str(bucket)] = int(total or 0)

        return {
            bucket: BucketBalance(
                bucket=bucket,
                credit_paisa=credits.get(str(bucket), 0),
                debit_paisa=debits.get(str(bucket), 0),
            )
            for bucket in LedgerBucket
        }

    async def entries_for(self, entity_type: str, entity_id: uuid.UUID) -> list[LedgerEntry]:
        """Everything the ledger knows about one thing, oldest first.

        This is the "explain this number" query: a seller asking why a parcel
        shows ৳0 outstanding gets the delivery, the payout and any deduction in
        the order they happened.
        """
        result = await self._db.execute(
            sa.select(LedgerEntry)
            .where(
                LedgerEntry.entity_type == entity_type,
                LedgerEntry.entity_id == entity_id,
            )
            .order_by(LedgerEntry.occurred_at.asc(), LedgerEntry.id.asc())
        )
        return list(result.scalars().all())
