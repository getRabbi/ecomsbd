"""Importing provider payments into the money engine.

Brief sections 21–27; master spec sections 16, 81, 82, 84, 85.

This is the part of the integration where the documentation runs out. Steadfast
V1 gives ``GET /payments`` and ``GET /payments/{payment_id}`` — a path and a
method each — and **no request parameter, no response schema, no field list and
no sample body**. The brief's section 55 says what to do about that, and it is
what this module does:

*   the endpoint integration is complete — real calls, real pagination handling,
    real persistence, real reconciliation;
*   typed fields are *looked for* rather than assumed, and which ones the
    provider actually sent is recorded on the row, so the first live response
    turns ``UNVERIFIED`` into documented fact;
*   every unrecognised key is preserved verbatim;
*   nothing is invented — in particular, an aggregate figure is never split
    into a fabricated breakdown.

Four safety properties, all of which cost a seller money if they break:

**One payment, one payout, forever.** ``courier_provider_payments`` has a unique
key on ``(tenant, provider, provider_payment_id)``. A re-run of the sync finds
the row and updates ``last_seen_at``; it does not create a second payout or a
second set of ledger entries.

**A changed payment is never silently rewritten.** If the provider's copy hashes
differently after we imported it, the row moves to ``CHANGED`` and a person
decides. Quietly rewriting a settled ledger entry would make a seller's
reconciled month stop reconciling, with no event that explains why.

**Matching uses identity, not amounts.** A payment line matches a parcel on the
provider's consignment id, its tracking code or our invoice. An amount alone
never auto-matches — that is master spec section 82, and it is enforced by the
existing reconciliation engine, which this module feeds rather than bypasses.

**An unexplained deduction stays unexplained.** Where the payload itemises
charges, each is mapped to its own adjustment type. Where it gives only a net
figure, the difference is recorded as one ``UNKNOWN_DEDUCTION`` carrying the
provider's own words. Section 84 is explicit that these must stay visible rather
than being folded into a familiar-looking category.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.provider_health import ProviderHealthService, ProviderKind
from app.consignments.models import Consignment
from app.core.clock import ensure_utc, utc_now
from app.core.config import Settings, get_settings
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.couriers.accounts import CourierAccountService
from app.couriers.capabilities import Capability
from app.couriers.metrics import CourierMetric, record_metric
from app.couriers.models import (
    CourierAccount,
    CourierRawPayload,
    CourierSyncCursor,
    CourierSyncKind,
    PaymentSyncState,
    ProviderPayment,
    RawPayloadKind,
    payload_hash,
)
from app.couriers.steadfast.dto import (
    PaymentConsignmentRecord,
    PaymentDetailResponse,
    PaymentSummary,
)
from app.couriers.steadfast.errors import SteadfastError
from app.payouts.models import (
    AdjustmentType,
    Payout,
    PayoutAdjustment,
    PayoutLine,
    PayoutLineStatus,
    PayoutSource,
    PayoutStatus,
)
from app.profit.models import ChargeKind, ChargeSource
from app.profit.service import ProfitService

__all__ = ["PaymentSyncReport", "PaymentSyncService"]

log = get_logger(__name__)

#: Which itemised field maps to which adjustment type. Only fields whose
#: *meaning* is unambiguous from the key name are mapped; anything else stays
#: an unknown deduction (section 84).
_CHARGE_FIELDS: tuple[tuple[str, AdjustmentType, ChargeKind], ...] = (
    ("delivery_charge_paisa", AdjustmentType.DELIVERY_FEE, ChargeKind.DELIVERY),
    ("cod_fee_paisa", AdjustmentType.COD_FEE, ChargeKind.COD_FEE),
    ("return_charge_paisa", AdjustmentType.RETURN_FEE, ChargeKind.RETURN),
)


@dataclass(slots=True)
class PaymentSyncReport:
    """What one payment sync run did."""

    provider: str
    seen: int = 0
    imported: int = 0
    changed: int = 0
    errors: int = 0
    pages: int = 0
    matched_lines: int = 0
    suggested_lines: int = 0
    unresolved_lines: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "seen": self.seen,
            "imported": self.imported,
            "changed": self.changed,
            "errors": self.errors,
            "pages": self.pages,
            "matched_lines": self.matched_lines,
            "suggested_lines": self.suggested_lines,
            "unresolved_lines": self.unresolved_lines,
        }


class PaymentSyncService:
    """Pulls provider payments and turns them into payouts."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        accounts: CourierAccountService,
        settings: Settings | None = None,
        health: ProviderHealthService | None = None,
    ) -> None:
        self._db = session
        self._accounts = accounts
        self._settings = settings or get_settings()
        self._health = health or ProviderHealthService(session)

    # ----------------------------------------------------------------- sync --

    async def sync(self, *, provider: str = "steadfast") -> PaymentSyncReport:
        """Fetch payments, import the new ones, reconcile what they explain."""
        report = PaymentSyncReport(provider=provider)

        account = await self._accounts.usable_account(provider)
        if account is None:
            return report
        adapter = self._accounts.adapter_for(provider)
        if adapter is None:
            return report
        if not await self._health.allows(
            provider, capability=str(Capability.PAYMENTS), tenant_id=account.tenant_id
        ):
            report.notes.append("circuit breaker open")
            return report

        cursor = await self._cursor(account, provider)
        credentials = self._accounts.credentials_for(account)
        summaries = await self._fetch_all(adapter, credentials, account, report)

        for summary in summaries:
            if summary.provider_payment_id is None:
                # A payment we cannot identify cannot be deduplicated, and an
                # import that cannot be deduplicated will eventually double a
                # seller's settled total. Recorded and skipped, not guessed at.
                report.errors += 1
                log.warning(
                    "provider payment has no identifier",
                    extra={
                        "provider": provider,
                        "operation": "sync_payments",
                        "observed_fields": summary.observed_fields,
                    },
                )
                continue
            try:
                await self._ingest(adapter, credentials, account, summary, report)
            except ConflictError as exc:
                report.errors += 1
                log.warning(
                    "provider payment ingestion conflicted",
                    extra={
                        "provider": provider,
                        "operation": "ingest_payment",
                        "provider_payment_id": summary.provider_payment_id,
                        "reason": str(exc),
                    },
                )

        now = utc_now()
        cursor.last_run_at = now
        cursor.last_success_at = now
        cursor.consecutive_failures = 0
        cursor.watermark_at = now
        cursor.next_run_at = now + timedelta(
            minutes=self._settings.courier_payment_sync_interval_minutes
        )
        cursor.stats_json = report.as_dict()
        await self._db.flush()

        record_metric(CourierMetric.PAYMENT_SYNC_COUNT, value=report.imported, provider=provider)
        if report.errors:
            record_metric(CourierMetric.PAYMENT_SYNC_ERROR, value=report.errors, provider=provider)
        return report

    async def _fetch_all(
        self,
        adapter: object,
        credentials: object,
        account: CourierAccount,
        report: PaymentSyncReport,
    ) -> list[PaymentSummary]:
        """Read the payments list, following pagination **only if declared**.

        No pagination parameter is documented, so none is invented. If the
        response declares Laravel-style paging, the next page is requested with
        the page number the response itself named; otherwise one page is all
        there is. Inventing ``?page=2`` against an endpoint that ignores it
        would return page one forever and make this loop either infinite or
        silently truncating.
        """
        summaries: list[PaymentSummary] = []
        page: int | None = None

        for _ in range(self._settings.courier_payment_sync_max_pages):
            query = {"page": page} if page is not None else None
            try:
                call = await adapter.client.list_payments(credentials, query=query)  # type: ignore[attr-defined]
            except SteadfastError as exc:
                report.errors += 1
                await self._health.record_failure(
                    ProviderKind.COURIER,
                    account.provider,
                    capability=str(Capability.PAYMENTS),
                    tenant_id=account.tenant_id,
                    error_code=str(exc.kind),
                    is_auth_failure=exc.is_auth_failure,
                )
                break

            await self._health.record_success(
                ProviderKind.COURIER,
                account.provider,
                capability=str(Capability.PAYMENTS),
                tenant_id=account.tenant_id,
                latency_ms=call.elapsed_ms,
            )
            listing = call.value
            report.pages += 1
            summaries.extend(listing.payments)
            report.seen += len(listing.payments)

            await self._store_raw(
                account,
                kind=RawPayloadKind.PAYMENT_LIST_RESPONSE,
                payload={"payments": [p.raw for p in listing.payments]},
                correlation_id=call.correlation_id,
                endpoint="/payments",
                http_status=call.http_status,
            )

            if not listing.has_declared_pagination:
                break
            if listing.current_page is None or listing.last_page is None:
                break
            if listing.current_page >= listing.last_page:
                break
            page = listing.current_page + 1

        return summaries

    # -------------------------------------------------------------- ingest --

    async def _ingest(
        self,
        adapter: object,
        credentials: object,
        account: CourierAccount,
        summary: PaymentSummary,
        report: PaymentSyncReport,
    ) -> None:
        """Import one provider payment, exactly once."""
        assert summary.provider_payment_id is not None  # noqa: S101 - checked by caller
        provider = account.provider
        digest = payload_hash(summary.raw)

        record = await self._existing(provider, summary.provider_payment_id)
        now = utc_now()

        if record is not None:
            record.last_seen_at = now
            if record.state is PaymentSyncState.IMPORTED:
                if record.payload_sha256 and record.payload_sha256 != digest:
                    # The provider's copy moved after we settled against it.
                    # Flagged for a controlled re-examination; never rewritten
                    # in place (brief section 23).
                    record.sync_state = str(PaymentSyncState.CHANGED)
                    record.error_message = (
                        "The courier's copy of this payment changed after it was imported."
                    )
                    report.changed += 1
                    log.warning(
                        "provider payment changed after import",
                        extra={
                            "provider": provider,
                            "operation": "ingest_payment",
                            "provider_payment_id": summary.provider_payment_id,
                        },
                    )
                await self._db.flush()
                return
            if record.state is PaymentSyncState.CHANGED:
                # Waiting on a person. Seen again, not re-imported.
                await self._db.flush()
                return
        else:
            record = ProviderPayment(
                provider=provider,
                courier_account_id=account.id,
                provider_payment_id=summary.provider_payment_id,
                provider_reference=summary.reference,
                sync_state=str(PaymentSyncState.SEEN),
                total_paisa=summary.amount_paisa,
                provider_status=summary.status,
                paid_at=summary.paid_at,
                consignment_count=summary.consignment_count,
                payload_sha256=digest,
                first_seen_at=now,
                last_seen_at=now,
                observed_fields=summary.observed_fields,
            )
            self._db.add(record)
            await self._db.flush()

        detail = await self._fetch_detail(adapter, credentials, account, record, report)
        if detail is None:
            return

        payout = await self._build_payout(account, record, detail)
        record.payout_id = payout.id
        record.sync_state = str(PaymentSyncState.IMPORTED)
        record.imported_at = utc_now()
        record.payload_sha256 = payload_hash(detail.raw)
        record.observed_fields = detail.payment.observed_fields
        await self._db.flush()

        report.imported += 1
        await record_audit(
            self._db,
            AuditAction.COURIER_PAYMENT_IMPORTED,
            entity_type="payout",
            entity_id=payout.id,
            context={
                "provider": provider,
                "provider_payment_id": summary.provider_payment_id,
                "total_paisa": payout.total_paisa,
                "line_count": len(detail.consignments),
                "schema_undocumented": True,
            },
        )
        await self._reconcile(payout, report)

    async def _fetch_detail(
        self,
        adapter: object,
        credentials: object,
        account: CourierAccount,
        record: ProviderPayment,
        report: PaymentSyncReport,
    ) -> PaymentDetailResponse | None:
        try:
            call = await adapter.client.get_payment(  # type: ignore[attr-defined]
                credentials, record.provider_payment_id
            )
        except SteadfastError as exc:
            record.sync_state = str(PaymentSyncState.FAILED)
            record.error_message = f"Could not read this payment's detail ({exc.kind})."
            report.errors += 1
            await self._db.flush()
            await self._health.record_failure(
                ProviderKind.COURIER,
                account.provider,
                capability=str(Capability.PAYMENT_CONSIGNMENTS),
                tenant_id=account.tenant_id,
                error_code=str(exc.kind),
                is_auth_failure=exc.is_auth_failure,
            )
            return None

        detail: PaymentDetailResponse = call.value
        raw = await self._store_raw(
            account,
            kind=RawPayloadKind.PAYMENT_DETAIL_RESPONSE,
            payload=detail.raw if isinstance(detail.raw, dict) else {"data": detail.raw},
            correlation_id=call.correlation_id,
            endpoint=f"/payments/{record.provider_payment_id}",
            http_status=call.http_status,
        )
        record.raw_payload_id = raw.id
        record.detail_fetched_at = utc_now()
        record.sync_state = str(PaymentSyncState.DETAILED)
        if detail.payment.amount_paisa is not None:
            record.total_paisa = detail.payment.amount_paisa
        if detail.payment.paid_at is not None:
            record.paid_at = detail.payment.paid_at
        record.consignment_count = len(detail.consignments)
        await self._db.flush()
        return detail

    # -------------------------------------------------------------- payout --

    async def _build_payout(
        self, account: CourierAccount, record: ProviderPayment, detail: PaymentDetailResponse
    ) -> Payout:
        """Turn a provider payment into a payout with one line per parcel.

        The payout total comes from the provider's own figure where it gave
        one, and from the sum of the lines only where it did not. Preferring
        the stated total matters: if the two disagree, the difference is real
        and belongs in front of the seller as unexplained money, not smoothed
        away by recomputing the header from the rows.
        """
        now = utc_now()
        line_total = sum(
            row.payable_amount_paisa or 0
            for row in detail.consignments
            if (row.payable_amount_paisa or 0) > 0
        )
        total = record.total_paisa if record.total_paisa is not None else line_total

        payout = Payout(
            provider=account.provider,
            provider_reference=record.provider_payment_id,
            source=str(PayoutSource.API),
            status=str(PayoutStatus.RECEIVED),
            total_paisa=max(0, total),
            paid_on=record.paid_at.date() if record.paid_at else None,
            received_at=now,
            note=None,
            metadata_json={
                "provider_payment_id": record.provider_payment_id,
                "provider_reference": record.provider_reference,
                "provider_status": record.provider_status,
                "consignment_count": len(detail.consignments),
                # Marked so a support engineer reading this payout months later
                # knows its field names were inferred, not documented.
                "schema_undocumented": True,
                "observed_fields": detail.payment.observed_fields,
            },
        )
        self._db.add(payout)
        await self._db.flush()

        for index, row in enumerate(detail.consignments, start=1):
            await self._build_line(payout, row, index)

        await self._db.flush()
        await self._db.refresh(payout, attribute_names=["lines"])
        return payout

    async def _build_line(
        self, payout: Payout, row: PaymentConsignmentRecord, row_number: int
    ) -> PayoutLine:
        """One parcel's line, plus whatever the provider said it deducted."""
        amount = row.payable_amount_paisa
        status = PayoutLineStatus.UNMATCHED
        if amount is None:
            # The provider named a parcel but not what it paid for it. Kept as
            # an unmappable line rather than dropped: the parcel is evidence
            # even when the amount is missing, and dropping it would hide it.
            amount = 0
            status = PayoutLineStatus.UNMAPPABLE
        if not row.has_identity:
            status = PayoutLineStatus.UNMAPPABLE

        line = PayoutLine(
            payout_id=payout.id,
            row_number=row_number,
            amount_paisa=max(0, amount),
            status=str(status),
            provider_consignment_id=row.provider_consignment_id,
            tracking_code=row.tracking_code,
            merchant_reference=row.invoice,
            delivered_on=row.delivered_at.date() if row.delivered_at else None,
            raw={**row.raw, "schema_undocumented": True},
        )
        self._db.add(line)
        await self._db.flush()

        await self._build_adjustments(payout, line, row)
        return line

    async def _build_adjustments(
        self, payout: Payout, line: PayoutLine, row: PaymentConsignmentRecord
    ) -> None:
        """Record what the provider took off.

        Itemised where it itemised. Where it gave only a COD amount and a net
        payable, the difference is **one** unclassified adjustment carrying the
        provider's own words — never a fabricated split into delivery charge
        and COD fee, which is exactly what master spec section 84 forbids.
        """
        itemised = 0
        for attribute, adjustment_type, _charge_kind in _CHARGE_FIELDS:
            amount = getattr(row, attribute, None)
            if amount is None or amount <= 0:
                continue
            itemised += amount
            self._db.add(
                PayoutAdjustment(
                    payout_id=payout.id,
                    payout_line_id=line.id,
                    type=str(adjustment_type),
                    amount_paisa=amount,
                    provider_label=attribute.replace("_paisa", ""),
                    raw_text=attribute.replace("_paisa", ""),
                    recognized_rule="steadfast.payment_detail",
                )
            )

        if row.discount_paisa:
            itemised += row.discount_paisa
            self._db.add(
                PayoutAdjustment(
                    payout_id=payout.id,
                    payout_line_id=line.id,
                    type=str(AdjustmentType.MANUAL_ADJUSTMENT),
                    amount_paisa=row.discount_paisa,
                    provider_label="discount",
                    raw_text="discount",
                    recognized_rule="steadfast.payment_detail",
                )
            )

        cod = row.cod_amount_paisa
        payable = row.payable_amount_paisa
        if cod is None or payable is None:
            return

        gap = cod - payable - itemised
        if gap > 0:
            # Money the courier kept and did not explain. Section 84: it stays
            # visible as an unknown deduction rather than being folded into a
            # category that looks familiar.
            self._db.add(
                PayoutAdjustment(
                    payout_id=payout.id,
                    payout_line_id=line.id,
                    type=str(AdjustmentType.UNKNOWN_DEDUCTION),
                    amount_paisa=gap,
                    provider_label="unexplained difference",
                    raw_text=(
                        f"COD {cod} paisa less payable {payable} paisa leaves "
                        f"{gap} paisa the courier did not itemise"
                    ),
                    recognized_rule=None,
                )
            )
            await self._db.flush()

    # --------------------------------------------------------- reconciling --

    async def _reconcile(self, payout: Payout, report: PaymentSyncReport) -> None:
        """Hand the payout to the existing reconciliation engine.

        Deliberately not a second matcher. Section 82's precision rules, the
        scoring weights, the tie handling, the case creation and the shadow
        mode all live in :class:`~app.reconciliation.service.ReconciliationService`,
        and a provider-specific matcher here would be a divergent copy of the
        most safety-critical logic in the product.
        """
        from app.reconciliation.service import ReconciliationService

        engine = ReconciliationService(self._db)
        result = await engine.reconcile(payout.id)
        report.matched_lines += result.exact_matches
        report.suggested_lines += result.suggested
        report.unresolved_lines += result.unresolved

        unexplained = payout.total_paisa - payout.applied_paisa
        if unexplained > 0:
            record_metric(
                CourierMetric.UNMATCHED_PAYMENT_AMOUNT,
                value=unexplained,
                provider=payout.provider,
            )

        await self._record_actual_charges(payout)

    async def _record_actual_charges(self, payout: Payout) -> None:
        """Promote a parcel's charges from estimate to settled fact.

        Master spec section 85's truth hierarchy: a provider-settled figure
        outranks a booking quote, a configured rate and an estimate. This is
        the only place a Steadfast parcel ever gets a ``SETTLED`` charge —
        nothing is recorded at booking, because ``cod_amount`` is documented as
        the collect amount "including all charges" with no decomposition, and
        calling part of it a delivery fee would be an invention (brief
        section 26).
        """
        profit = ProfitService(self._db)
        adjustments = list(
            (
                await self._db.execute(
                    sa.select(PayoutAdjustment).where(PayoutAdjustment.payout_id == payout.id)
                )
            )
            .scalars()
            .all()
        )
        if not adjustments:
            return

        lines = {line.id: line for line in payout.lines}
        kinds = {
            str(adjustment_type): charge_kind
            for _attribute, adjustment_type, charge_kind in _CHARGE_FIELDS
        }

        for adjustment in adjustments:
            charge_kind = kinds.get(adjustment.type)
            if charge_kind is None or adjustment.payout_line_id is None:
                # An unknown deduction has no charge kind by definition. It
                # stays on the payout as a visible adjustment and is not
                # guessed into a profit line.
                continue
            line = lines.get(adjustment.payout_line_id)
            if line is None:
                continue
            consignment = await self._consignment_for(line)
            if consignment is None:
                continue
            adjustment.consignment_id = consignment.id
            await profit.record_charge(
                consignment_id=consignment.id,
                kind=charge_kind,
                source=ChargeSource.SETTLED,
                amount_paisa=adjustment.amount_paisa,
                provider_label=adjustment.provider_label,
                source_ref=f"payout:{payout.id}",
                occurred_at=payout.received_at,
            )
        await self._db.flush()

    async def _consignment_for(self, line: PayoutLine) -> Consignment | None:
        """The parcel a payout line names, by provider identity only.

        Identity, never amount. An amount-only guess would attach one parcel's
        settled delivery charge to another's profit snapshot, which is both
        wrong and invisible.
        """
        conditions = []
        if line.provider_consignment_id:
            conditions.append(Consignment.provider_consignment_id == line.provider_consignment_id)
        if line.tracking_code:
            conditions.append(Consignment.tracking_code == line.tracking_code)
        if line.merchant_reference:
            conditions.append(Consignment.merchant_reference == line.merchant_reference)
        if not conditions:
            return None
        result = await self._db.execute(sa.select(Consignment).where(sa.or_(*conditions)))
        return result.scalars().first()

    # ----------------------------------------------------------- internals --

    async def _existing(self, provider: str, payment_id: str) -> ProviderPayment | None:
        result = await self._db.execute(
            sa.select(ProviderPayment).where(
                ProviderPayment.provider == provider,
                ProviderPayment.provider_payment_id == payment_id,
            )
        )
        return result.scalar_one_or_none()

    async def _cursor(self, account: CourierAccount, provider: str) -> CourierSyncCursor:
        """The payment sync watermark for this shop.

        Locked on PostgreSQL so two workers cannot both run a shop's sync at
        once. The watermark deliberately overlaps on resume: re-seeing a
        payment is free — the unique payment id absorbs it — while missing one
        because the provider's clock and ours disagreed is money that never
        reaches the seller's screen.
        """
        stmt = sa.select(CourierSyncCursor).where(
            CourierSyncCursor.provider == provider,
            CourierSyncCursor.courier_account_id == account.id,
            CourierSyncCursor.kind == str(CourierSyncKind.PAYMENT_SYNC),
        )
        dialect = self._db.bind.dialect.name if self._db.bind is not None else ""
        if dialect == "postgresql":
            stmt = stmt.with_for_update()
        cursor = (await self._db.execute(stmt)).scalar_one_or_none()
        if cursor is not None:
            return cursor

        cursor = CourierSyncCursor(
            provider=provider,
            courier_account_id=account.id,
            kind=str(CourierSyncKind.PAYMENT_SYNC),
        )
        self._db.add(cursor)
        await self._db.flush()
        return cursor

    def resume_from(self, cursor: CourierSyncCursor) -> datetime | None:
        """Where a resumed sync should start reading from, with overlap."""
        if cursor.watermark_at is None:
            return None
        return ensure_utc(cursor.watermark_at) - timedelta(
            minutes=self._settings.courier_payment_sync_overlap_minutes
        )

    async def _store_raw(
        self,
        account: CourierAccount,
        *,
        kind: RawPayloadKind,
        payload: dict,
        correlation_id: str | None,
        endpoint: str,
        http_status: int | None,
    ) -> CourierRawPayload:
        row = CourierRawPayload(
            provider=account.provider,
            courier_account_id=account.id,
            kind=str(kind),
            endpoint=endpoint,
            http_status=http_status,
            correlation_id=correlation_id,
            response_payload=payload,
            payload_sha256=payload_hash(payload),
            # These two endpoints have no documented response schema, so the
            # flag travels with the evidence.
            schema_undocumented=True,
            received_at=utc_now(),
        )
        self._db.add(row)
        await self._db.flush()
        return row

    # -------------------------------------------------------------- reading --

    async def payments(
        self, *, provider: str = "steadfast", limit: int = 50
    ) -> list[ProviderPayment]:
        result = await self._db.execute(
            sa.select(ProviderPayment)
            .where(ProviderPayment.provider == provider)
            .order_by(ProviderPayment.first_seen_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def needs_attention(self, *, provider: str = "steadfast") -> list[ProviderPayment]:
        """Payments a person has to look at: changed after import, or failed."""
        result = await self._db.execute(
            sa.select(ProviderPayment)
            .where(
                ProviderPayment.provider == provider,
                ProviderPayment.sync_state.in_(
                    [str(PaymentSyncState.CHANGED), str(PaymentSyncState.FAILED)]
                ),
            )
            .order_by(ProviderPayment.last_seen_at.desc())
        )
        return list(result.scalars().all())
