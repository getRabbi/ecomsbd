"""V2.2 smart alerts: facts the seller can act on, raised once.

Two halves:

* **Detectors** read the shop's own data through the services that already own
  each definition — overdue COD through the Receivables rule, discrepancies
  through Reconciliation V2 cases, RTO through :mod:`app.analytics.rto` — and
  return the conditions that are true right now. None of them predicts
  anything; each condition carries the counts it was computed from.
* **The lifecycle** turns conditions into notifications. Every alert has a
  stable identity (kind + subject: a courier, a case bundle, an import), and
  for each identity it decides between *nothing*, *new*, *reminder after the
  cooldown*, *material worsening*, or *resolved*. Running the scan twice, or
  on two workers at once, therefore writes nothing the second time.

Every threshold and cooldown lives in :mod:`app.notifications.rules`.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.consignments.models import MANUAL_PROVIDER, Consignment, ConsignmentStatus
from app.core.clock import business_date, utc_now
from app.core.logging import get_logger
from app.db.types import TZDateTime
from app.notifications import rules
from app.notifications.models import Notification, NotificationKind, Severity
from app.notifications.service import NotificationService
from app.notifications.templates import render
from app.tenants.roles import Permission

__all__ = [
    "AlertCondition",
    "AlertLifecycle",
    "SmartAlerts",
    "last_moved_at",
    "stuck_parcel_clause",
]

log = get_logger(__name__)


def last_moved_at() -> sa.ColumnElement[datetime]:
    """When a parcel's status last changed, falling back to when it was made."""
    return sa.func.coalesce(
        Consignment.last_status_at,
        Consignment.provider_status_at,
        Consignment.booked_at,
        Consignment.created_at,
        type_=TZDateTime(),
    )


def stuck_parcel_clause(now: datetime) -> tuple[sa.ColumnElement[bool], ...]:
    """The one definition of a stuck parcel: the stuck alert and Insights share it.

    A courier parcel whose status has not changed for longer than
    :data:`rules.STUCK_AFTER` allows for that status. Manual parcels have no
    courier feed to be stuck in.
    """
    moved = last_moved_at()
    limits = [
        sa.and_(Consignment.status == status, moved <= now - after)
        for status, after in rules.STUCK_AFTER.items()
    ]
    return (Consignment.provider != MANUAL_PROVIDER, sa.or_(*limits))


@dataclass(frozen=True, slots=True)
class AlertCondition:
    """One thing that is true now and worth telling someone about."""

    kind: NotificationKind
    #: Identity within the kind, without any time window: ``shop``,
    #: ``provider:steadfast``, ``import:<id>:REJECTED_ROWS``.
    subject: str
    #: The facts, rendered by :mod:`app.notifications.templates`.
    params: dict[str, Any]
    #: The figure material worsening is judged on (paisa, a count, or bps).
    magnitude: int
    amount_paisa: int = 0
    item_count: int = 1
    #: The one record it is about, when it is about one.
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    #: Only this member sees it (an import's uploader).
    user_id: uuid.UUID | None = None
    audience: Permission | None = None
    severity: Severity | None = None
    #: Narrows the screen a shop-wide alert opens (a courier's receivables).
    target_params: dict[str, str] = field(default_factory=dict)


class AlertLifecycle:
    """Decides, per alert identity, whether anything should be written."""

    def __init__(self, session: AsyncSession, notifications: NotificationService) -> None:
        self._db = session
        self._notifications = notifications

    async def sync(
        self,
        kind: NotificationKind,
        conditions: list[AlertCondition],
        *,
        now: datetime | None = None,
        scope: str | None = None,
    ) -> int:
        """Make ``kind``'s alerts match ``conditions``. Returns how many were new.

        Identities with an open alert but no condition any more are resolved:
        the row stays as history, marked cleared, and nothing new is raised.
        ``scope`` limits resolution to subjects with that prefix, for a caller
        that only looked at part of the picture.
        """
        moment = now or utc_now()
        open_rows = await self._open(kind, prefix=scope)
        created = 0
        seen: set[str] = set()
        for condition in conditions:
            seen.add(condition.subject)
            latest = open_rows.get(condition.subject, [])
            if await self._raise_if_due(condition, latest[-1] if latest else None, moment):
                created += 1
        for subject, rows in open_rows.items():
            if subject not in seen:
                for row in rows:
                    row.resolved_at = moment
        await self._db.flush()
        return created

    async def raise_once(self, condition: AlertCondition) -> Notification | None:
        """For one-off events: raise unless this identity was ever raised."""
        earlier = await self._db.scalar(
            sa.select(sa.func.count())
            .select_from(Notification)
            .where(
                Notification.kind == str(condition.kind),
                Notification.subject_key == condition.subject,
            )
        )
        if earlier:
            return None
        return await self._write(condition, "NEW", utc_now())

    async def resolve(
        self, kind: NotificationKind, *, prefix: str, keep: str | None = None
    ) -> None:
        moment = utc_now()
        for subject, rows in (await self._open(kind, prefix=prefix)).items():
            if subject != keep:
                for row in rows:
                    row.resolved_at = moment
        await self._db.flush()

    # ------------------------------------------------------------ internals --

    async def _open(
        self, kind: NotificationKind, *, prefix: str | None
    ) -> dict[str, list[Notification]]:
        stmt = sa.select(Notification).where(
            Notification.kind == str(kind),
            Notification.resolved_at.is_(None),
            Notification.subject_key != "",
        )
        if prefix:
            stmt = stmt.where(Notification.subject_key.startswith(prefix, autoescape=True))
        rows = (await self._db.execute(stmt.order_by(Notification.created_at))).scalars()
        grouped: dict[str, list[Notification]] = {}
        for row in rows:
            grouped.setdefault(row.subject_key, []).append(row)
        return grouped

    async def _raise_if_due(
        self, condition: AlertCondition, latest: Notification | None, now: datetime
    ) -> bool:
        rule = rules.rule_for(condition.kind)
        reason = "NEW"
        if latest is not None:
            elapsed = now - latest.created_at
            previous = int((latest.payload or {}).get("magnitude") or 0)
            growth = condition.magnitude - previous
            if elapsed >= rule.cooldown:
                reason = "REMINDER"
            elif (
                growth >= rule.worsen_min_delta
                and condition.magnitude >= previous * (1 + rule.worsen_ratio)
                and elapsed >= rule.worsen_min_gap
            ):
                reason = "WORSENED"
            else:
                return False
        return await self._write(condition, reason, now) is not None

    async def _write(
        self, condition: AlertCondition, reason: str, now: datetime
    ) -> Notification | None:
        rule = rules.rule_for(condition.kind)
        # The sequence makes the dedupe key deterministic: two scans racing
        # for the same alert compute the same key and the unique constraint
        # lets exactly one of them write it.
        sequence = await self._db.scalar(
            sa.select(sa.func.count())
            .select_from(Notification)
            .where(
                Notification.kind == str(condition.kind),
                Notification.subject_key == condition.subject,
            )
        )
        title, body = render(condition.kind, condition.params, "en") or (str(condition.kind), "")
        target: dict[str, Any] = {"route": rule.route}
        if condition.entity_type and condition.entity_id:
            target = {"route": condition.entity_type, "id": str(condition.entity_id)}
        elif condition.target_params:
            target["params"] = dict(condition.target_params)
        try:
            async with self._db.begin_nested():
                return await self._notifications.notify(
                    kind=condition.kind,
                    severity=condition.severity or rule.severity,
                    title=title[:160],
                    body=body[:600],
                    dedupe_key=f"{condition.subject}#{int(sequence or 0) + 1}"[:120],
                    entity_type=condition.entity_type,
                    entity_id=condition.entity_id,
                    amount_paisa=condition.amount_paisa,
                    item_count=condition.item_count,
                    payload={
                        "params": condition.params,
                        "magnitude": condition.magnitude,
                        "reason": reason,
                        "target": target,
                    },
                    occurred_at=now,
                    category=rule.category,
                    audience=condition.audience or rules.audience_for(condition.kind),
                    subject_key=condition.subject,
                    user_id=condition.user_id,
                )
        except IntegrityError:
            return None


# --------------------------------------------------------------------------- #
# Detectors
# --------------------------------------------------------------------------- #


class SmartAlerts:
    """The V2.2 detectors, run for one shop on its tenant-scoped session."""

    def __init__(
        self, session: AsyncSession, *, notifications: NotificationService | None = None
    ) -> None:
        self._db = session
        self._notifications = notifications or NotificationService(session)
        self.lifecycle = AlertLifecycle(session, self._notifications)

    async def run(self) -> int:
        """Every detector, each isolated: one failing costs only its own alert."""
        detectors: list[tuple[NotificationKind, Callable[[], Awaitable[list[AlertCondition]]]]] = [
            (NotificationKind.FOLLOW_UP_DUE, self.followups_due),
            (NotificationKind.PAYOUT_OVERDUE, self.payout_overdue),
            (NotificationKind.RECONCILIATION_DISCREPANCY, self.reconciliation_discrepancies),
            (NotificationKind.COURIER_STATUS_STUCK, self.courier_stuck),
            (NotificationKind.HIGH_RTO, self.high_rto),
            (NotificationKind.LOW_STOCK, self.low_stock),
            (NotificationKind.NEGATIVE_MARGIN, self.negative_margin),
            (NotificationKind.COURIER_ACCOUNT_PROBLEM, self.courier_accounts),
            (NotificationKind.RETURNED_NOT_RESTOCKED, self.returned_not_restocked),
        ]
        created = 0
        for kind, detector in detectors:
            try:
                async with self._db.begin_nested():
                    created += await self.lifecycle.sync(kind, await detector())
            except Exception as exc:
                log.exception(
                    "smart alert detector failed",
                    extra={"kind": str(kind), "error": type(exc).__name__},
                )
        try:
            async with self._db.begin_nested():
                created += await self.scan_imports()
        except Exception as exc:
            log.exception("import alert scan failed", extra={"error": type(exc).__name__})
        return created

    # ---------------------------------------------------------- money --

    async def followups_due(self) -> list[AlertCondition]:
        """Count only; no customer name, phone or seller note reaches a push."""
        from app.customers.crm_models import CustomerFollowUp
        from app.customers.models import Customer

        count = int(
            await self._db.scalar(
                sa.select(sa.func.count(CustomerFollowUp.id))
                .join(
                    Customer,
                    Customer.id == CustomerFollowUp.customer_id,
                )
                .where(
                    Customer.deleted_at.is_(None),
                    CustomerFollowUp.completed_at.is_(None),
                    CustomerFollowUp.due_at <= utc_now(),
                )
            )
            or 0
        )
        return (
            [
                AlertCondition(
                    kind=NotificationKind.FOLLOW_UP_DUE,
                    subject="shop",
                    params={"count": count},
                    magnitude=count,
                    item_count=count,
                    target_params={"segment": "FOLLOW_UP_DUE"},
                )
            ]
            if count
            else []
        )

    async def payout_overdue(self) -> list[AlertCondition]:
        """COD past the Receivables overdue rule, one alert per courier.

        The Money screen's own figure (:class:`CashflowService`): a receivable
        with no known delivery date has no age and is never overdue, so no due
        date is ever invented here.
        """
        from app.money.cashflow import CashflowService
        from app.money.service import OVERDUE_AFTER_DAYS

        conditions = []
        for balance in await CashflowService(self._db).courier_balances():
            if balance.overdue_count <= 0 or balance.overdue_paisa <= 0:
                continue
            conditions.append(
                AlertCondition(
                    kind=NotificationKind.PAYOUT_OVERDUE,
                    subject=f"provider:{balance.provider}",
                    params={
                        "provider": balance.provider,
                        "count": balance.overdue_count,
                        "amount_paisa": balance.overdue_paisa,
                        "days": OVERDUE_AFTER_DAYS,
                    },
                    magnitude=balance.overdue_paisa,
                    amount_paisa=balance.overdue_paisa,
                    item_count=balance.overdue_count,
                    target_params={"provider": balance.provider},
                )
            )
        return conditions

    async def reconciliation_discrepancies(self) -> list[AlertCondition]:
        """Open Reconciliation V2 cases, summarised as one alert.

        One case already stands for each disputed parcel or line, so the alert
        counts cases rather than items, and never lists them one by one.
        """
        from app.reconciliation.models import CaseStatus, ReconciliationCase

        kinds = [str(kind) for kind in rules.DISCREPANCY_CASE_KINDS]
        active = [str(CaseStatus.OPEN), str(CaseStatus.IN_PROGRESS)]
        rows = await self._db.execute(
            sa.select(
                ReconciliationCase.kind,
                sa.func.count(),
                sa.func.coalesce(sa.func.sum(ReconciliationCase.amount_paisa), 0),
            )
            .where(ReconciliationCase.status.in_(active), ReconciliationCase.kind.in_(kinds))
            .group_by(ReconciliationCase.kind)
        )
        parsed = list(rows)
        by_kind = {kind: int(count) for kind, count, _ in parsed}
        count = sum(by_kind.values())
        if count == 0:
            return []
        amount = sum(int(total or 0) for _, _, total in parsed)
        entity_id = None
        if count == 1:
            entity_id = await self._db.scalar(
                sa.select(ReconciliationCase.id)
                .where(ReconciliationCase.status.in_(active), ReconciliationCase.kind.in_(kinds))
                .limit(1)
            )
        return [
            AlertCondition(
                kind=NotificationKind.RECONCILIATION_DISCREPANCY,
                subject="shop",
                params={"count": count, "amount_paisa": amount, "by_kind": by_kind},
                magnitude=count,
                amount_paisa=amount,
                item_count=count,
                entity_type="reconciliation_case" if entity_id else None,
                entity_id=entity_id,
            )
        ]

    async def negative_margin(self) -> list[AlertCondition]:
        """Delivered parcels whose fully measured profit is below zero.

        Only ``ACTUAL`` snapshots — every input measured. An estimated or
        missing-cost figure is never the basis of a loss warning.
        """
        from app.profit.models import ProfitQuality, ProfitSnapshot

        since = business_date(at=utc_now()) - timedelta(days=rules.NEGATIVE_MARGIN_WINDOW_DAYS - 1)
        where = (
            ProfitSnapshot.is_current.is_(True),
            ProfitSnapshot.quality == str(ProfitQuality.ACTUAL),
            ProfitSnapshot.outcome.in_(
                [str(ConsignmentStatus.DELIVERED), str(ConsignmentStatus.PARTIAL_DELIVERED)]
            ),
            ProfitSnapshot.business_date >= since,
            ProfitSnapshot.contribution_profit_paisa < 0,
        )
        count, total = (
            await self._db.execute(
                sa.select(
                    sa.func.count(),
                    sa.func.coalesce(sa.func.sum(ProfitSnapshot.contribution_profit_paisa), 0),
                ).where(*where)
            )
        ).one()
        count = int(count or 0)
        if count == 0:
            return []
        order_id = None
        if count == 1:
            order_id = await self._db.scalar(sa.select(ProfitSnapshot.order_id).where(*where))
        loss = -int(total or 0)
        return [
            AlertCondition(
                kind=NotificationKind.NEGATIVE_MARGIN,
                subject="shop",
                params={
                    "count": count,
                    "loss_paisa": loss,
                    "days": rules.NEGATIVE_MARGIN_WINDOW_DAYS,
                },
                magnitude=count,
                amount_paisa=loss,
                item_count=count,
                entity_type="order" if order_id else None,
                entity_id=order_id,
            )
        ]

    # -------------------------------------------------------- courier --

    async def courier_stuck(self) -> list[AlertCondition]:
        """Parcels with no status change for longer than their status allows.

        Measured from the last status change, per status, against the
        conservative limits in :data:`rules.STUCK_AFTER`. Manual parcels have no
        courier feed to be stuck in and are left out.
        """
        now = utc_now()
        moved = last_moved_at()
        where = stuck_parcel_clause(now)
        rows = list(
            await self._db.execute(
                sa.select(
                    Consignment.status,
                    sa.func.count(),
                    sa.func.coalesce(sa.func.sum(Consignment.cod_amount_paisa), 0),
                    sa.func.min(moved, type_=TZDateTime()),
                )
                .where(*where)
                .group_by(Consignment.status)
            )
        )
        by_status = {status: int(count) for status, count, _, _ in rows}
        count = sum(by_status.values())
        if count == 0:
            return []
        cod = sum(int(total or 0) for _, _, total, _ in rows)
        oldest = min(moment for *_, moment in rows if moment is not None)
        order_id = None
        if count == 1:
            order_id = await self._db.scalar(sa.select(Consignment.order_id).where(*where))
        return [
            AlertCondition(
                kind=NotificationKind.COURIER_STATUS_STUCK,
                subject="shop",
                params={
                    "count": count,
                    "cod_paisa": cod,
                    "oldest_days": max(0, (now - oldest).days),
                    "by_status": by_status,
                },
                magnitude=count,
                amount_paisa=cod,
                item_count=count,
                entity_type="order" if order_id else None,
                entity_id=order_id,
            )
        ]

    async def courier_accounts(self) -> list[AlertCondition]:
        """Accounts whose credentials the provider keeps rejecting.

        Only ``NEEDS_RECONNECT`` — reached after repeated conclusive
        rejections, never after a provider outage. A seller who disconnected an
        account on purpose is not told about it.
        """
        from app.couriers.models import CourierAccount, CourierAccountStatus

        rows = await self._db.execute(
            sa.select(CourierAccount.id, CourierAccount.provider).where(
                CourierAccount.status == str(CourierAccountStatus.NEEDS_RECONNECT)
            )
        )
        return [
            AlertCondition(
                kind=NotificationKind.COURIER_ACCOUNT_PROBLEM,
                subject=f"account:{account_id}",
                # Provider name only: no identifier, key or message from the
                # provider is ever copied into a notification.
                params={"provider": provider},
                magnitude=1,
                entity_type="courier_account",
                entity_id=account_id,
            )
            for account_id, provider in rows
        ]

    # -------------------------------------------------------- returns --

    async def high_rto(self) -> list[AlertCondition]:
        """RTO high or rising, by the one definition the RTO screen uses.

        Counts come from :class:`RtoService` — ``RETURNED`` *and* courier
        ``CANCELLED`` are RTO; pre-dispatch cancellations, parcels still moving
        and lost/damaged are not. No alert below the minimum sample.
        """
        from app.analytics.rto import RtoService, rate_bps

        limits = rules.RTO_THRESHOLDS
        service = RtoService(self._db)
        today = business_date(at=utc_now())
        current = (await service.summary(days=limits.window_days, today=today)).counts
        if current.completed < limits.min_completed:
            return []
        rate = current.rto_rate_bps or 0
        previous = await service.window_counts(
            days=limits.window_days, ending=today - timedelta(days=limits.window_days)
        )
        previous_rate = (
            previous.rto_rate_bps if previous.completed >= limits.min_completed else None
        )

        high = rate >= limits.high_rate_bps
        rising = previous_rate is not None and rate - previous_rate >= limits.rise_bps
        if not (high or rising):
            return []

        params: dict[str, Any] = {
            "rto": current.rto,
            "completed": current.completed,
            "returned": current.returned,
            "courier_cancelled": current.courier_cancelled,
            "rate_bps": rate,
            "days": limits.window_days,
        }
        if previous_rate is not None:
            params |= {
                "prev_rto": previous.rto,
                "prev_completed": previous.completed,
                "prev_rate_bps": rate_bps(previous.rto, previous.completed),
            }
        return [
            AlertCondition(
                kind=NotificationKind.HIGH_RTO,
                subject="shop",
                params=params,
                magnitude=rate,
                item_count=current.rto,
            )
        ]

    # ------------------------------------------------------ inventory --

    async def low_stock(self) -> list[AlertCondition]:
        """Products — or, for a product with variants, variants — at or below
        the low-stock level the seller set.

        A simple product is judged on its own threshold; a variant product on
        each active variant's (V2.2). Anything without a threshold is never
        judged low, and no default threshold is assumed. When restocking lifts
        every item above its level the condition disappears and the existing
        resolution rule clears the alert.
        """
        from app.products.models import Product, ProductVariant

        live = (
            Product.is_active.is_(True),
            Product.archived_at.is_(None),
            Product.stock_tracking_enabled.is_(True),
        )
        simple = (
            *live,
            Product.has_variants.is_(False),
            Product.low_stock_threshold.is_not(None),
            Product.stock_on_hand <= Product.low_stock_threshold,
        )
        variants = (
            *live,
            ProductVariant.is_active.is_(True),
            ProductVariant.low_stock_threshold.is_not(None),
            ProductVariant.stock_on_hand <= ProductVariant.low_stock_threshold,
        )
        count = int(await self._db.scalar(sa.select(sa.func.count(Product.id)).where(*simple)) or 0)
        count += int(
            await self._db.scalar(
                sa.select(sa.func.count(ProductVariant.id))
                .join(Product, Product.id == ProductVariant.product_id)
                .where(*variants)
            )
            or 0
        )
        if count == 0:
            return []
        listed: list[tuple[uuid.UUID, str, int, int]] = [
            (row[0], row[1], row[2], row[3])
            for row in await self._db.execute(
                sa.select(
                    Product.id, Product.name, Product.stock_on_hand, Product.low_stock_threshold
                )
                .where(*simple)
                .order_by(Product.stock_on_hand - Product.low_stock_threshold, Product.name)
                .limit(rules.MAX_LISTED)
            )
        ]
        listed += [
            (product_id, f"{name} ({variant})", stock, threshold)
            for product_id, name, variant, stock, threshold in await self._db.execute(
                sa.select(
                    Product.id,
                    Product.name,
                    ProductVariant.name,
                    ProductVariant.stock_on_hand,
                    ProductVariant.low_stock_threshold,
                )
                .join(Product, Product.id == ProductVariant.product_id)
                .where(*variants)
                .order_by(
                    ProductVariant.stock_on_hand - ProductVariant.low_stock_threshold,
                    Product.name,
                    ProductVariant.name,
                )
                .limit(rules.MAX_LISTED)
            )
        ]
        listed = sorted(listed, key=lambda row: (row[2] - row[3], row[1]))[: rules.MAX_LISTED]
        params: dict[str, Any] = {"count": count, "names": [name for _, name, _, _ in listed]}
        entity_id = None
        if count == 1:
            product_id, name, stock, threshold = listed[0]
            params["single"] = {"name": name, "stock": stock, "threshold": threshold}
            entity_id = product_id
        return [
            AlertCondition(
                kind=NotificationKind.LOW_STOCK,
                subject="shop",
                params=params,
                magnitude=count,
                item_count=count,
                entity_type="product" if entity_id else None,
                entity_id=entity_id,
            )
        ]

    async def returned_not_restocked(self) -> list[AlertCondition]:
        from app.reconciliation.models import CaseKind, CaseStatus, ReconciliationCase

        count = int(
            await self._db.scalar(
                sa.select(sa.func.count(ReconciliationCase.id)).where(
                    ReconciliationCase.kind == str(CaseKind.RETURNED_NOT_RESTOCKED),
                    ReconciliationCase.status.in_(
                        [str(CaseStatus.OPEN), str(CaseStatus.IN_PROGRESS)]
                    ),
                )
            )
            or 0
        )
        if count == 0:
            return []
        return [
            AlertCondition(
                kind=NotificationKind.RETURNED_NOT_RESTOCKED,
                subject="shop",
                params={"count": count},
                magnitude=count,
                item_count=count,
            )
        ]

    # -------------------------------------------------------- imports --

    async def import_alert(self, import_id: uuid.UUID) -> Notification | None:
        """Raise (or clear) the one alert an import deserves.

        Called when a commit finishes and by the stuck-commit sweep; the daily
        scan calls it too, for any event that was missed. The subject is the
        import and its outcome, so an import is announced once per outcome and
        a stopped import that later finishes clears its "did not finish".
        """
        from app.imports.models import ImportBatch, ImportStatus

        batch = await self._db.get(ImportBatch, import_id)
        if batch is None:
            return None
        finished = batch.committed_at or batch.updated_at
        if finished is not None and finished < utc_now() - rules.IMPORT_THRESHOLDS.lookback:
            # Old news: an import from last month is not worth a push today.
            return None

        status = ImportStatus(batch.status)
        rejected = int(batch.invalid_count or 0)
        reason: str | None = None
        if status is ImportStatus.FAILED:
            reason = "FAILED"
        elif status is ImportStatus.COMMITTING and batch.failure_reason:
            reason = "STOPPED"
        elif status is ImportStatus.COMMITTED and rejected > 0:
            limits = rules.IMPORT_THRESHOLDS
            share = rejected * 10_000 // max(1, int(batch.row_count or 0))
            if rejected >= limits.min_rejected_rows or share >= limits.min_rejected_share_bps:
                reason = "REJECTED_ROWS"

        prefix = f"import:{batch.id}:"
        subject = f"{prefix}{reason}" if reason else None
        await self.lifecycle.resolve(NotificationKind.IMPORT_FAILURE, prefix=prefix, keep=subject)
        if subject is None:
            return None

        condition = AlertCondition(
            kind=NotificationKind.IMPORT_FAILURE,
            subject=subject,
            params={
                "reason": reason,
                "template": batch.template,
                "file": (batch.original_filename or "")[:60],
                "rejected": rejected,
                "created": int(batch.created_count or 0),
                "row_count": int(batch.row_count or 0),
            },
            magnitude=rejected,
            item_count=max(1, rejected),
            entity_type="import",
            entity_id=batch.id,
            user_id=batch.created_by_user_id,
            audience=rules.IMPORT_AUDIENCE.get(batch.template),
        )
        return await self.lifecycle.raise_once(condition)

    async def scan_imports(self) -> int:
        """Imports finished (or stopped) recently, for events that were missed."""
        from app.imports.models import ImportBatch, ImportStatus

        since = utc_now() - rules.IMPORT_THRESHOLDS.lookback
        ids = (
            await self._db.execute(
                sa.select(ImportBatch.id).where(
                    ImportBatch.updated_at >= since,
                    sa.or_(
                        ImportBatch.status == str(ImportStatus.FAILED),
                        sa.and_(
                            ImportBatch.status == str(ImportStatus.COMMITTING),
                            ImportBatch.failure_reason.is_not(None),
                        ),
                        sa.and_(
                            ImportBatch.status == str(ImportStatus.COMMITTED),
                            ImportBatch.invalid_count > 0,
                        ),
                    ),
                )
            )
        ).scalars()
        created = 0
        for import_id in list(ids):
            if await self.import_alert(import_id) is not None:
                created += 1
        return created
