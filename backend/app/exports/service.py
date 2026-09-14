"""Building a seller's export.

Master spec section 99. Five things hold for every export produced here:

*   **tenant-scoped** — every query runs on the tenant-scoped session, so the
    guards in :mod:`app.db.tenancy` apply exactly as they do to a screen;
*   **entitlement-gated** — ``csv_export`` is a plan feature (section 26), and
    the gate is here rather than at one endpoint, so a second export route
    cannot bypass it;
*   **escaped** — every cell goes through :mod:`app.common.safe_csv`, because a
    customer's name is text the seller did not write;
*   **audited** — requesting and downloading are separate audit entries;
*   **time-limited** — the download token expires.

Exports of *history* are never blocked by an expired subscription. Section 51
is explicit that a seller's own data stays theirs, and an export is the most
literal form of that. What ``csv_export`` gates is the convenience of the CSV
itself on the Free plan, not access to the underlying records, which stay
readable through the app on every plan.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.object_storage import ObjectStorage
from app.common.safe_csv import SafeCsvWriter
from app.core.clock import business_date, utc_now
from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.security import SecretHasher, generate_opaque_token
from app.customers.models import Customer
from app.entitlements.catalog import Entitlement
from app.entitlements.service import EntitlementService
from app.exports.models import ExportFormat, ExportJob, ExportKind, ExportStatus
from app.money.models import CodReceivable
from app.orders.models import Order, OrderItem
from app.payouts.models import Payout, PayoutLine
from app.products.models import Product
from app.profit.models import ProfitSnapshot
from app.tenants.roles import Permission

__all__ = ["ExportResult", "ExportService"]


@dataclass(frozen=True, slots=True)
class ExportResult:
    """A created export and, once, its download token."""

    job: ExportJob
    #: Returned exactly once, at creation. It is stored hashed, so it cannot be
    #: shown again — the seller downloads now or asks for a new export.
    download_token: str | None


#: Which permission each export needs. Customers are singled out because
#: section 88 makes exporting customer data an Owner-only action.
_EXPORT_PERMISSIONS: dict[ExportKind, Permission] = {
    ExportKind.ORDERS: Permission.DATA_EXPORT,
    ExportKind.CUSTOMERS: Permission.CUSTOMER_EXPORT,
    ExportKind.PRODUCTS: Permission.DATA_EXPORT,
    ExportKind.PAYOUTS: Permission.DATA_EXPORT,
    ExportKind.RECONCILIATION: Permission.DATA_EXPORT,
    ExportKind.PROFIT_SUMMARY: Permission.DATA_EXPORT,
}


def permission_for(kind: ExportKind) -> Permission:
    return _EXPORT_PERMISSIONS[kind]


class ExportService:
    """Creates, builds and serves exports."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        hasher: SecretHasher,
        entitlements: EntitlementService | None = None,
    ) -> None:
        self._db = session
        self._settings = settings
        self._hasher = hasher
        self._entitlements = entitlements or EntitlementService(session)

    # ------------------------------------------------------------- creating ---

    async def request(
        self,
        *,
        tenant_id: uuid.UUID,
        kind: ExportKind,
        user_id: uuid.UUID | None,
        since: date | None = None,
        until: date | None = None,
    ) -> ExportResult:
        """Create and build an export.

        Built synchronously when it is small and refused when it is not: the
        seller is told to narrow the range rather than handed a request that
        times out. The asynchronous path is the worker job below, which the
        same method feeds.
        """
        await self._entitlements.require(tenant_id, Entitlement.CSV_EXPORT)
        await self._enforce_concurrency(tenant_id)

        job = ExportJob(
            tenant_id=tenant_id,
            kind=str(kind),
            export_format=str(ExportFormat.CSV),
            status=str(ExportStatus.PENDING),
            requested_by_user_id=user_id,
            parameters={
                "since": since.isoformat() if since else None,
                "until": until.isoformat() if until else None,
            },
        )
        self._db.add(job)
        await self._db.flush()

        await record_audit(
            self._db,
            AuditAction.EXPORT_REQUESTED,
            entity_type="export_job",
            entity_id=job.id,
            context={"kind": str(kind), "since": job.parameters["since"]},
            tenant_id=tenant_id,
        )

        token = await self.build(job, tenant_id=tenant_id, since=since, until=until)
        return ExportResult(job=job, download_token=token)

    async def _enforce_concurrency(self, tenant_id: uuid.UUID) -> None:
        """Cap how many live exports one shop may hold.

        Not a rate limit for its own sake: each ready export is a copy of the
        shop's data sitting behind a token, and a hundred of them is a hundred
        chances for one to be forwarded.
        """
        live = int(
            (
                await self._db.execute(
                    sa.select(sa.func.count())
                    .select_from(ExportJob)
                    .where(
                        ExportJob.tenant_id == tenant_id,
                        ExportJob.status.in_(
                            [
                                str(ExportStatus.PENDING),
                                str(ExportStatus.RUNNING),
                                str(ExportStatus.READY),
                            ]
                        ),
                        sa.or_(ExportJob.expires_at.is_(None), ExportJob.expires_at > utc_now()),
                    )
                )
            ).scalar_one()
        )
        if live >= self._settings.export_max_active_per_tenant:
            raise ConflictError(
                f"You already have {live} exports waiting to be downloaded. "
                "Download or wait for one to expire before starting another.",
                details={"active": str(live)},
            )

    # ------------------------------------------------------------- building ---

    async def build(
        self,
        job: ExportJob,
        *,
        tenant_id: uuid.UUID,
        since: date | None = None,
        until: date | None = None,
    ) -> str | None:
        """Produce the file. Returns the download token on success."""
        job.status = str(ExportStatus.RUNNING)
        kind = ExportKind(job.kind)

        try:
            headers, rows = await self._collect(kind, tenant_id, since=since, until=until)
        except Exception as exc:
            job.status = str(ExportStatus.FAILED)
            job.error = f"{type(exc).__name__}: {exc}"[:400]
            job.completed_at = utc_now()
            await record_audit(
                self._db,
                AuditAction.EXPORT_FAILED,
                entity_type="export_job",
                entity_id=job.id,
                context={"kind": str(kind), "error": type(exc).__name__},
                tenant_id=tenant_id,
            )
            raise

        if len(rows) > self._settings.export_max_sync_rows:
            job.status = str(ExportStatus.FAILED)
            job.error = "too large for a single export"
            job.completed_at = utc_now()
            raise ValidationError(
                f"That range holds {len(rows)} rows; a single export is capped at "
                f"{self._settings.export_max_sync_rows}. Export a shorter period.",
                details={
                    "rows": str(len(rows)),
                    "max_rows": str(self._settings.export_max_sync_rows),
                },
            )

        writer = SafeCsvWriter(headers)
        writer.write_all(rows)
        content = writer.as_bytes()

        token = generate_opaque_token()
        if self._settings.r2_configured:
            storage = ObjectStorage(self._settings)
            job.storage_key = storage.key(tenant_id, "exports", job.id)
            await storage.put(job.storage_key, content, "text/csv; charset=utf-8")
            job.content = None
        else:
            job.content = content
        job.byte_size = len(content)
        job.row_count = writer.row_count
        job.filename = f"ecomsbd-{kind.lower()}-{business_date():%Y%m%d}.csv"
        job.status = str(ExportStatus.READY)
        job.download_token_hash = self._hasher.token_hash(token)
        job.expires_at = utc_now() + timedelta(seconds=self._settings.export_download_ttl_seconds)
        job.completed_at = utc_now()
        # Flushed inside the request, while the tenant is still in ambient
        # scope. `session_scope` commits after the principal dependency has torn
        # its context down, and the tenancy write guard refuses a flush that has
        # no tenant to check the rows against.
        await self._db.flush()
        return token

    # ---------------------------------------------------------- downloading ---

    async def download(
        self, *, tenant_id: uuid.UUID, job_id: uuid.UUID, token: str
    ) -> tuple[ExportJob, bytes]:
        """Serve a ready export to its owner.

        Both the tenant scope *and* the token must match. The session already
        proves who is asking; the token proves they hold the link that was
        issued, so a leaked job id alone is not enough.
        """
        job = (
            await self._db.execute(
                sa.select(ExportJob).where(ExportJob.id == job_id, ExportJob.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if job is None:
            raise NotFoundError("No such export")

        if job.download_token_hash is None or not self._hasher.verify_token(
            token, job.download_token_hash
        ):
            raise NotFoundError("No such export")

        if not job.is_downloadable():
            raise ConflictError(
                "That export has expired. Request a new one.",
                details={"status": job.status},
            )

        job.download_count += 1
        job.downloaded_at = utc_now()
        await self._db.flush()
        await record_audit(
            self._db,
            AuditAction.EXPORT_DOWNLOADED,
            entity_type="export_job",
            entity_id=job.id,
            context={"kind": job.kind, "rows": job.row_count, "count": job.download_count},
            tenant_id=tenant_id,
        )
        content = (
            await ObjectStorage(self._settings).get(job.storage_key)
            if job.storage_key
            else job.content
        )
        assert content is not None  # noqa: S101 - is_downloadable checked it
        return job, content

    async def expire_stale(self, *, now: datetime | None = None, limit: int = 200) -> int:
        """Drop the content of exports whose links have expired.

        The row stays; only the copy of the seller's data goes. That is the
        point: the audit record of who exported what must outlive the file.
        """
        moment = now or utc_now()
        rows = (
            (
                await self._db.execute(
                    sa.select(ExportJob)
                    .where(
                        ExportJob.status == str(ExportStatus.READY),
                        ExportJob.expires_at.is_not(None),
                        ExportJob.expires_at <= moment,
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for job in rows:
            if job.storage_key:
                await ObjectStorage(self._settings).delete(job.storage_key)
                job.storage_key = None
            job.status = str(ExportStatus.EXPIRED)
            job.content = None
            job.download_token_hash = None
        return len(rows)

    # ------------------------------------------------------------ collectors ---

    async def _collect(
        self,
        kind: ExportKind,
        tenant_id: uuid.UUID,
        *,
        since: date | None,
        until: date | None,
    ) -> tuple[Sequence[str], list[Sequence[Any]]]:
        match kind:
            case ExportKind.ORDERS:
                return await self._orders(tenant_id, since, until)
            case ExportKind.CUSTOMERS:
                return await self._customers(tenant_id)
            case ExportKind.PRODUCTS:
                return await self._products(tenant_id)
            case ExportKind.PAYOUTS:
                return await self._payouts(tenant_id, since, until)
            case ExportKind.RECONCILIATION:
                return await self._reconciliation(tenant_id)
            case ExportKind.PROFIT_SUMMARY:
                return await self._profit(tenant_id, since, until)
        raise ValidationError(f"'{kind}' is not an exportable dataset")

    async def _orders(
        self, tenant_id: uuid.UUID, since: date | None, until: date | None
    ) -> tuple[Sequence[str], list[Sequence[Any]]]:
        statement = sa.select(Order).where(Order.tenant_id == tenant_id)
        if since is not None:
            statement = statement.where(Order.business_date >= since)
        if until is not None:
            statement = statement.where(Order.business_date <= until)
        orders = list(
            (await self._db.execute(statement.order_by(Order.created_at))).scalars().all()
        )

        item_rows = (
            (
                await self._db.execute(
                    sa.select(OrderItem).where(
                        OrderItem.order_id.in_([o.id for o in orders] or [uuid.uuid4()])
                    )
                )
            )
            .scalars()
            .all()
        )
        items_by_order: dict[uuid.UUID, list[OrderItem]] = {}
        for item in item_rows:
            items_by_order.setdefault(item.order_id, []).append(item)

        headers = [
            "order_number",
            "business_date",
            "status",
            "customer_name",
            # The seller's own customer list, exported by the seller. Masked
            # would make the file useless for the thing sellers actually do
            # with it — calling people back.
            "customer_phone_masked",
            "delivery_district",
            "delivery_area",
            "items",
            "cod_amount_paisa",
            "discount_paisa",
            "delivery_fee_paisa",
            "created_at",
        ]
        rows: list[Sequence[Any]] = []
        for order in orders:
            items = items_by_order.get(order.id, [])
            summary = "; ".join(f"{i.quantity} x {i.product_name}" for i in items)
            rows.append(
                [
                    order.order_number,
                    order.business_date,
                    order.status,
                    order.customer_name,
                    order.customer_phone_masked,
                    order.delivery_district,
                    order.delivery_area,
                    summary,
                    order.cod_amount_paisa,
                    order.discount_paisa,
                    order.delivery_fee_paisa,
                    order.created_at,
                ]
            )
        return headers, rows

    async def _customers(self, tenant_id: uuid.UUID) -> tuple[Sequence[str], list[Sequence[Any]]]:
        customers = (
            (
                await self._db.execute(
                    sa.select(Customer)
                    .where(Customer.tenant_id == tenant_id)
                    .order_by(Customer.created_at)
                )
            )
            .scalars()
            .all()
        )
        headers = [
            "name",
            "phone_masked",
            "flag",
            "order_count",
            "delivered_count",
            "returned_count",
            "success_rate_basis_points",
            "created_at",
        ]
        return headers, [
            [
                customer.name,
                customer.phone_masked,
                customer.flag,
                customer.order_count,
                customer.delivered_count,
                customer.returned_count,
                customer.success_rate_basis_points,
                customer.created_at,
            ]
            for customer in customers
        ]

    async def _products(self, tenant_id: uuid.UUID) -> tuple[Sequence[str], list[Sequence[Any]]]:
        products = (
            (
                await self._db.execute(
                    sa.select(Product)
                    .where(Product.tenant_id == tenant_id)
                    .order_by(Product.created_at)
                )
            )
            .scalars()
            .all()
        )
        headers = [
            "sku",
            "name",
            "cost_paisa",
            "default_selling_price_paisa",
            "stock_on_hand",
            "low_stock_threshold",
            "is_archived",
        ]
        return headers, [
            [
                product.sku,
                product.name,
                product.cost_paisa,
                product.default_selling_price_paisa,
                product.stock_on_hand,
                product.low_stock_threshold,
                product.archived_at is not None,
            ]
            for product in products
        ]

    async def _payouts(
        self, tenant_id: uuid.UUID, since: date | None, until: date | None
    ) -> tuple[Sequence[str], list[Sequence[Any]]]:
        statement = sa.select(Payout).where(Payout.tenant_id == tenant_id)
        if since is not None:
            statement = statement.where(Payout.paid_on >= since)
        if until is not None:
            statement = statement.where(Payout.paid_on <= until)
        payouts = list(
            (await self._db.execute(statement.order_by(Payout.created_at))).scalars().all()
        )

        headers = [
            "provider",
            "paid_on",
            "reference",
            "status",
            "source",
            "total_paisa",
            "applied_paisa",
            "line_count",
            "matched_line_count",
        ]
        rows: list[Sequence[Any]] = []
        for payout in payouts:
            lines = (
                (
                    await self._db.execute(
                        sa.select(PayoutLine).where(PayoutLine.payout_id == payout.id)
                    )
                )
                .scalars()
                .all()
            )
            rows.append(
                [
                    payout.provider,
                    payout.paid_on,
                    payout.provider_reference,
                    payout.status,
                    payout.source,
                    payout.total_paisa,
                    payout.applied_paisa,
                    len(lines),
                    sum(1 for line in lines if line.receivable_id is not None),
                ]
            )
        return headers, rows

    async def _reconciliation(
        self, tenant_id: uuid.UUID
    ) -> tuple[Sequence[str], list[Sequence[Any]]]:
        receivables = (
            (
                await self._db.execute(
                    sa.select(CodReceivable)
                    .where(CodReceivable.tenant_id == tenant_id)
                    .order_by(CodReceivable.created_at)
                )
            )
            .scalars()
            .all()
        )
        headers = [
            "status",
            "provider",
            "collectible_paisa",
            "adjustment_paisa",
            "settled_paisa",
            "deduction_paisa",
            "outstanding_paisa",
            "eligible_at",
            "settled_at",
        ]
        return headers, [
            [
                row.status,
                row.provider,
                row.collectible_paisa,
                row.adjustment_paisa,
                row.settled_paisa,
                row.deduction_paisa,
                row.outstanding_paisa,
                row.eligible_at,
                row.settled_at,
            ]
            for row in receivables
        ]

    async def _profit(
        self, tenant_id: uuid.UUID, since: date | None, until: date | None
    ) -> tuple[Sequence[str], list[Sequence[Any]]]:
        """Current profit snapshots only — one row per parcel, not per revision.

        Exporting every revision would make the file a change log rather than a
        statement, and the seller asked for what their profit *is*.
        """
        statement = sa.select(ProfitSnapshot).where(
            ProfitSnapshot.tenant_id == tenant_id,
            ProfitSnapshot.is_current.is_(True),
        )
        if since is not None:
            statement = statement.where(ProfitSnapshot.business_date >= since)
        if until is not None:
            statement = statement.where(ProfitSnapshot.business_date <= until)

        snapshots = (
            (await self._db.execute(statement.order_by(ProfitSnapshot.business_date)))
            .scalars()
            .all()
        )
        headers = [
            "business_date",
            "revision",
            "realized_revenue_paisa",
            "item_cost_paisa",
            "delivery_charge_paisa",
            "cod_fee_paisa",
            "return_charge_paisa",
            "packaging_paisa",
            "ad_cost_paisa",
            "contribution_profit_paisa",
            "outcome",
            "data_quality",
        ]
        return headers, [
            [
                snapshot.business_date,
                snapshot.revision,
                snapshot.realized_revenue_paisa,
                snapshot.item_cost_paisa,
                snapshot.delivery_charge_paisa,
                snapshot.cod_fee_paisa,
                snapshot.return_charge_paisa,
                snapshot.packaging_paisa,
                snapshot.ad_cost_paisa,
                snapshot.contribution_profit_paisa,
                snapshot.outcome,
                snapshot.quality,
            ]
            for snapshot in snapshots
        ]
