"""Opt-in anonymous benchmarks. No phone, customer, or shop-level result leaves this module.

Two releases per closed month, both written once by the worker and never
recomputed on request:

* the V2 headline (:class:`NetworkBenchmark`): parcel RTO and delivery rates
  across every eligible shop;
* the V3.7 cohort cells (:class:`NetworkBenchmarkCell`): factual operational
  metrics per fixed cohort (courier, broad category, order-volume band).

Every figure passes :mod:`app.analytics.network_privacy`.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import network_privacy as privacy
from app.analytics.network_models import NetworkBenchmark, NetworkBenchmarkCell, NetworkPreference
from app.analytics.rto import (
    COMPLETED_STATUSES,
    DISPATCHED_STATUSES,
    NOT_LIVE_PROVIDERS,
    RTO_STATUSES,
    ParcelOutcome,
    classify,
)
from app.consignments.models import MANUAL_PROVIDER, Consignment
from app.core.clock import utc_now
from app.db.session import system_session
from app.money.models import CodReceivable
from app.reconciliation.models import DISCREPANCY_STATUSES, ReconciliationItem
from app.tenants.models import BusinessCategory, Tenant, TenantUser

POLICY = "anonymous-month-v1"
CELL_POLICY = "anonymous-cohorts-v1"
MIN_SHOPS = privacy.MIN_SHOPS
MIN_SAMPLE = privacy.MIN_SAMPLE
MIN_OUTCOME = privacy.MIN_OUTCOME
MAX_SHOP_SAMPLE = privacy.MAX_SHOP_SAMPLE
MIN_SHOP_SAMPLE = privacy.MIN_SHOP_SAMPLE
MAX_SHARE_PERCENT = privacy.MAX_SHARE_PERCENT

MARKET = "BD"
#: Couriers with live, documented integrations; the only courier cohorts.
LIVE_COURIERS: tuple[str, ...] = ("pathao", "steadfast")
VOLUME_BANDS: tuple[str, ...] = ("UNDER_100", "100_TO_499", "500_PLUS")
#: A parcel that took longer than this to reach an outcome, or has not, is stuck.
STUCK_AFTER = timedelta(days=10)

#: metric -> (kind, dimensions, unit, rounding step for medians)
METRICS: dict[str, tuple[str, tuple[str, ...], str, int]] = {
    "RTO_RATE": ("rate", ("ALL", "COURIER", "CATEGORY", "VOLUME_BAND"), "PERCENT", 0),
    "DELIVERY_SUCCESS": ("rate", ("ALL", "COURIER", "CATEGORY", "VOLUME_BAND"), "PERCENT", 0),
    "DELIVERY_HOURS": ("median", ("ALL", "COURIER"), "HOURS", 12),
    "STUCK_RATE": ("rate", ("ALL", "COURIER"), "PERCENT", 0),
    "PAYOUT_DELAY_DAYS": ("median", ("ALL", "COURIER"), "DAYS", 1),
    "RECON_DISCREPANCY_RATE": ("rate", ("ALL", "COURIER"), "PERCENT", 0),
    "DELIVERY_CHARGE": ("median", ("COURIER",), "PAISA", 500),
}
DIMENSIONS: dict[str, tuple[str, ...]] = {
    "ALL": ("ALL",),
    "COURIER": LIVE_COURIERS,
    "CATEGORY": tuple(str(c) for c in BusinessCategory),
    "VOLUME_BAND": VOLUME_BANDS,
}


def anonymous_facts(counts: list[tuple[int, int]]) -> dict[str, Any] | None:
    """Counts are (completed, RTO), grouped by independent owner in SQL.

    Clip large contributors, suppress small/minority cohorts, publish only
    rounded rates. No caller filters, exact counts, or overlapping cohorts.
    """
    published = privacy.publish_rate(counts)
    if published is None:
        return None
    return {
        "rto_percent_rounded": published.value,
        "delivery_percent_rounded": 100 - published.value,
        "precision_percent": published.precision,
        "cohort": "ALL_OPTED_IN_SHOPS",
        "minimum_shops": MIN_SHOPS,
        "minimum_sample": MIN_SAMPLE,
    }


def closed_period(now: datetime) -> tuple[datetime, datetime, str] | None:
    """The last closed month, once a week of it has passed for late outcomes."""
    if now.day < 8:
        return None
    end = datetime(now.year, now.month, 1, tzinfo=UTC)
    previous = end - timedelta(days=1)
    start = datetime(previous.year, previous.month, 1, tzinfo=UTC)
    return start, end, start.strftime("%Y-%m")


def _single_owner_shops() -> sa.Select:
    return (
        sa.select(TenantUser.tenant_id)
        .where(TenantUser.role == "OWNER", TenantUser.is_active.is_(True))
        .group_by(TenantUser.tenant_id)
        .having(sa.func.count(TenantUser.id) == 1)
    )


async def build_network_benchmarks(ctx: dict[str, Any] | None = None) -> dict[str, str]:
    # One fixed, non-overlapping cohort per closed month; no caller controls
    # filtering, thresholds or publication, and failed cohorts publish no facts.
    period_bounds = closed_period(utc_now())
    if period_bounds is None:
        return {"status": "GATED", "blocker": "NETWORK_CLOSED_PERIOD_REQUIRED"}
    start, end, period = period_bounds
    async with system_session("network benchmarks: opted-in SQL counts only") as db:
        cells = await build_benchmark_cells(db, start, end, period)
        if await db.scalar(sa.select(NetworkBenchmark.id).where(NetworkBenchmark.period == period)):
            return {"status": "ALREADY_BUILT", "cells": cells}
        settled = sa.func.coalesce(
            Consignment.delivered_at, Consignment.returned_at, Consignment.last_status_at
        )
        counts = (
            await db.execute(
                sa.select(
                    sa.func.count(Consignment.id),
                    sa.func.sum(sa.case((Consignment.status.in_(RTO_STATUSES), 1), else_=0)),
                )
                .join(NetworkPreference, NetworkPreference.tenant_id == Consignment.tenant_id)
                .join(Tenant, Tenant.id == Consignment.tenant_id)
                .join(TenantUser, TenantUser.tenant_id == Consignment.tenant_id)
                .where(
                    NetworkPreference.opted_in.is_(True),
                    NetworkPreference.opted_in_at < start,
                    Tenant.status == "ACTIVE",
                    Tenant.deleted_at.is_(None),
                    TenantUser.role == "OWNER",
                    TenantUser.is_active.is_(True),
                    Consignment.tenant_id.in_(_single_owner_shops()),
                    Consignment.status.in_(COMPLETED_STATUSES),
                    settled >= start,
                    settled < end,
                    Consignment.provider != "redx",
                )
                .group_by(TenantUser.user_id)
            )
        ).all()
        facts = anonymous_facts([(int(n), int(r)) for n, r in counts])
        result = NetworkBenchmark(
            period=period,
            policy_version=POLICY,
            status="PUBLISHED" if facts else "GATED",
            facts=facts or {},
        )
        try:
            async with db.begin_nested():
                db.add(result)
                await db.flush()
        except IntegrityError:
            return {"status": "ALREADY_BUILT", "cells": cells}
        return {"status": result.status, "cells": cells}


# ----------------------------------------------------------- cohort cells ---


@dataclass(frozen=True)
class _Shop:
    owner: uuid.UUID
    category: str


def volume_band(completed: int) -> str:
    if completed >= 500:
        return "500_PLUS"
    if completed >= 100:
        return "100_TO_499"
    return "UNDER_100"


def _courier(provider: str | None) -> str | None:
    value = (provider or "").lower()
    if not value or value == MANUAL_PROVIDER or value in NOT_LIVE_PROVIDERS:
        return None
    return value


async def _eligible(db: AsyncSession, start: datetime) -> dict[uuid.UUID, _Shop]:
    rows = (
        await db.execute(
            sa.select(Tenant.id, TenantUser.user_id, Tenant.business_category)
            .join(NetworkPreference, NetworkPreference.tenant_id == Tenant.id)
            .join(TenantUser, TenantUser.tenant_id == Tenant.id)
            .where(
                NetworkPreference.opted_in.is_(True),
                NetworkPreference.opted_in_at < start,
                Tenant.status == "ACTIVE",
                Tenant.deleted_at.is_(None),
                TenantUser.role == "OWNER",
                TenantUser.is_active.is_(True),
                Tenant.id.in_(_single_owner_shops()),
            )
        )
    ).all()
    return {tenant: _Shop(owner, str(category)) for tenant, owner, category in rows}


def _cohorts(dimension: str, shop: _Shop, band: str, courier: str | None) -> list[str]:
    if dimension == "ALL":
        return ["ALL"]
    if dimension == "COURIER":
        return [courier] if courier else []
    if dimension == "CATEGORY":
        return [shop.category]
    return [band]


def _hours(later: datetime | None, earlier: datetime | None) -> float | None:
    if later is None or earlier is None:
        return None
    if later.tzinfo is None:
        later = later.replace(tzinfo=UTC)
    if earlier.tzinfo is None:
        earlier = earlier.replace(tzinfo=UTC)
    value = (later - earlier).total_seconds() / 3600
    return value if value >= 0 else None


class _Collector:
    """Per-cohort, per-owner contributions for one metric."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.rates: dict[tuple[str, str], dict[uuid.UUID, list[int]]] = defaultdict(
            lambda: defaultdict(lambda: [0, 0])
        )
        self.values: dict[tuple[str, str], dict[uuid.UUID, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )

    def rate(
        self, cells: Iterable[tuple[str, str]], owner: uuid.UUID, total: int, hits: int
    ) -> None:
        for cell in cells:
            pair = self.rates[cell][owner]
            pair[0] += total
            pair[1] += hits

    def value(self, cells: Iterable[tuple[str, str]], owner: uuid.UUID, value: float) -> None:
        for cell in cells:
            self.values[cell][owner].append(value)

    def publish(self, cell: tuple[str, str], unit: str, step: int) -> privacy.Published | None:
        if self.kind == "rate":
            return privacy.publish_rate([(t, h) for t, h in self.rates[cell].values()])
        return privacy.publish_median(list(self.values[cell].values()), step=step, unit=unit)


async def build_benchmark_cells(
    db: AsyncSession, start: datetime, end: datetime, period: str
) -> str:
    """Write every cohort cell of ``period`` once. Idempotent."""
    exists = await db.scalar(
        sa.select(NetworkBenchmarkCell.id).where(
            NetworkBenchmarkCell.period == period,
            NetworkBenchmarkCell.policy_version == CELL_POLICY,
        )
    )
    if exists is not None:
        return "ALREADY_BUILT"
    shops = await _eligible(db, start)
    tenants = list(shops)
    collectors = {metric: _Collector(spec[0]) for metric, spec in METRICS.items()}
    bands: dict[uuid.UUID, str] = {}

    def cells_for(metric: str, tenant: uuid.UUID, courier: str | None) -> list[tuple[str, str]]:
        shop = shops[tenant]
        found = []
        for dimension in METRICS[metric][1]:
            for cohort in _cohorts(dimension, shop, bands.get(tenant, "UNDER_100"), courier):
                found.append((dimension, cohort))
        return found

    settled = sa.func.coalesce(
        Consignment.delivered_at, Consignment.returned_at, Consignment.last_status_at
    )
    outcome_rows = (
        (
            await db.execute(
                sa.select(
                    Consignment.tenant_id,
                    Consignment.provider,
                    sa.func.count(Consignment.id),
                    sa.func.sum(sa.case((Consignment.status.in_(RTO_STATUSES), 1), else_=0)),
                )
                .where(
                    Consignment.tenant_id.in_(tenants),
                    Consignment.status.in_(COMPLETED_STATUSES),
                    settled >= start,
                    settled < end,
                )
                .group_by(Consignment.tenant_id, Consignment.provider)
            )
        ).all()
        if tenants
        else []
    )
    completed_by_shop: dict[uuid.UUID, int] = defaultdict(int)
    for tenant, provider, total, _ in outcome_rows:
        if _courier(provider):
            completed_by_shop[tenant] += int(total)
    bands.update({tenant: volume_band(completed_by_shop.get(tenant, 0)) for tenant in tenants})
    for tenant, provider, total, rto in outcome_rows:
        courier = _courier(provider)
        if courier is None:
            continue
        owner = shops[tenant].owner
        collectors["RTO_RATE"].rate(
            cells_for("RTO_RATE", tenant, courier), owner, int(total), int(rto or 0)
        )
        collectors["DELIVERY_SUCCESS"].rate(
            cells_for("DELIVERY_SUCCESS", tenant, courier),
            owner,
            int(total),
            int(total) - int(rto or 0),
        )

    if tenants:
        delivered = await db.execute(
            sa.select(
                Consignment.tenant_id,
                Consignment.provider,
                Consignment.booked_at,
                Consignment.delivered_at,
            ).where(
                Consignment.tenant_id.in_(tenants),
                Consignment.status.in_(("DELIVERED", "PARTIAL_DELIVERED")),
                Consignment.delivered_at >= start,
                Consignment.delivered_at < end,
                Consignment.booked_at.is_not(None),
            )
        )
        for tenant, provider, booked, done in delivered.all():
            courier, hours = _courier(provider), _hours(done, booked)
            if courier and hours is not None:
                collectors["DELIVERY_HOURS"].value(
                    cells_for("DELIVERY_HOURS", tenant, courier), shops[tenant].owner, hours
                )

        now = utc_now()
        booked_rows = await db.execute(
            sa.select(
                Consignment.tenant_id,
                Consignment.provider,
                Consignment.status,
                Consignment.booked_at,
                Consignment.delivered_at,
                Consignment.returned_at,
            ).where(
                Consignment.tenant_id.in_(tenants),
                Consignment.status.in_(DISPATCHED_STATUSES),
                Consignment.booked_at >= start,
                Consignment.booked_at < end,
            )
        )
        stuck_hours = STUCK_AFTER.total_seconds() / 3600
        for tenant, provider, status, booked, done, back in booked_rows.all():
            courier = _courier(provider)
            if courier is None:
                continue
            resolved = done or back
            if classify(status) is ParcelOutcome.IN_TRANSIT or resolved is None:
                age = _hours(now, booked)
                stuck = age is not None and age > stuck_hours
            else:
                took = _hours(resolved, booked)
                stuck = took is not None and took > stuck_hours
            collectors["STUCK_RATE"].rate(
                cells_for("STUCK_RATE", tenant, courier), shops[tenant].owner, 1, int(stuck)
            )

        payouts = await db.execute(
            sa.select(
                CodReceivable.tenant_id,
                CodReceivable.provider,
                CodReceivable.eligible_at,
                CodReceivable.settled_at,
            ).where(
                CodReceivable.tenant_id.in_(tenants),
                CodReceivable.status == "SETTLED",
                CodReceivable.settled_at >= start,
                CodReceivable.settled_at < end,
                CodReceivable.eligible_at.is_not(None),
            )
        )
        for tenant, provider, eligible_at, settled_at in payouts.all():
            courier, hours = _courier(provider), _hours(settled_at, eligible_at)
            if courier and hours is not None:
                collectors["PAYOUT_DELAY_DAYS"].value(
                    cells_for("PAYOUT_DELAY_DAYS", tenant, courier), shops[tenant].owner, hours / 24
                )

        items = await db.execute(
            sa.select(
                ReconciliationItem.tenant_id,
                ReconciliationItem.provider,
                ReconciliationItem.status,
                ReconciliationItem.actual_charge_paisa,
                ReconciliationItem.line_count,
            ).where(
                ReconciliationItem.tenant_id.in_(tenants),
                ReconciliationItem.created_at >= start,
                ReconciliationItem.created_at < end,
            )
        )
        discrepancy = {str(s) for s in DISCREPANCY_STATUSES}
        for tenant, provider, status, charge, lines in items.all():
            courier = _courier(provider)
            if courier is None:
                continue
            owner = shops[tenant].owner
            collectors["RECON_DISCREPANCY_RATE"].rate(
                cells_for("RECON_DISCREPANCY_RATE", tenant, courier),
                owner,
                1,
                int(str(status) in discrepancy),
            )
            if charge and charge > 0 and lines and lines > 0:
                collectors["DELIVERY_CHARGE"].value(
                    cells_for("DELIVERY_CHARGE", tenant, courier), owner, float(charge)
                )

    written = 0
    for metric, (_, dimensions, unit, step) in METRICS.items():
        for dimension in dimensions:
            for cohort in DIMENSIONS[dimension]:
                published = collectors[metric].publish((dimension, cohort), unit, step)
                db.add(
                    NetworkBenchmarkCell(
                        period=period,
                        policy_version=CELL_POLICY,
                        metric=metric,
                        dimension=dimension,
                        cohort=cohort,
                        status=privacy.PUBLISHED if published else privacy.DATA_NOT_SUFFICIENT,
                        value=published.value if published else None,
                        precision=published.precision if published else None,
                        unit=unit,
                        shops_band=published.shops_band if published else None,
                        sample_band=published.sample_band if published else None,
                    )
                )
                written += 1
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        return "ALREADY_BUILT"
    return "BUILT"


def cell_view(cell: NetworkBenchmarkCell) -> dict[str, Any]:
    return {
        "metric": cell.metric,
        "dimension": cell.dimension,
        "cohort": cell.cohort,
        "status": cell.status,
        "value": cell.value,
        "precision": cell.precision,
        "unit": cell.unit,
        "shops_band": cell.shops_band,
        "sample_band": cell.sample_band,
    }


def cohort_definition(period: str, dimension: str) -> dict[str, Any]:
    return {
        "market": MARKET,
        "period": period,
        "period_kind": "CALENDAR_MONTH_UTC",
        "dimension": dimension,
        "eligibility": "OPTED_IN_BEFORE_PERIOD_ACTIVE_SINGLE_OWNER",
        "couriers": list(LIVE_COURIERS),
        "minimum_shops": MIN_SHOPS,
        "minimum_sample": MIN_SAMPLE,
        "minimum_shop_sample": MIN_SHOP_SAMPLE,
        "max_share_percent": MAX_SHARE_PERCENT,
    }


async def latest_cells(db: AsyncSession, dimension: str | None = None) -> dict[str, Any]:
    """The most recent release's cells. Read only; nothing computed here."""
    period = await db.scalar(
        sa.select(sa.func.max(NetworkBenchmarkCell.period)).where(
            NetworkBenchmarkCell.policy_version == CELL_POLICY
        )
    )
    if period is None:
        return {"period": None, "computed_at": None, "cells": []}
    query = sa.select(NetworkBenchmarkCell).where(
        NetworkBenchmarkCell.period == period,
        NetworkBenchmarkCell.policy_version == CELL_POLICY,
    )
    if dimension is not None:
        query = query.where(NetworkBenchmarkCell.dimension == dimension)
    rows = (
        await db.scalars(
            query.order_by(
                NetworkBenchmarkCell.metric,
                NetworkBenchmarkCell.dimension,
                NetworkBenchmarkCell.cohort,
            )
        )
    ).all()
    computed = max((r.created_at for r in rows), default=None)
    return {
        "period": period,
        "computed_at": computed.isoformat() if computed else None,
        "cells": [cell_view(r) for r in rows],
    }
