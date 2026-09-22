"""Opt-in anonymous benchmarks. No phone, customer, or shop-level result leaves this module."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.analytics.network_models import NetworkBenchmark, NetworkPreference
from app.analytics.rto import COMPLETED_STATUSES, RTO_STATUSES
from app.consignments.models import Consignment
from app.core.clock import utc_now
from app.db.session import system_session
from app.tenants.models import Tenant, TenantUser

POLICY = "anonymous-month-v1"
MIN_SHOPS = 20
MIN_SAMPLE = 200
MIN_OUTCOME = 20
MAX_SHOP_SAMPLE = 100
MIN_SHOP_SAMPLE = 10
MAX_SHARE_PERCENT = 10


def anonymous_facts(counts: list[tuple[int, int]]) -> dict[str, Any] | None:
    """Counts are (completed, RTO), grouped by independent owner in SQL.

    Clip large contributors, suppress small/minority cohorts, publish only
    rounded rates. No caller filters, exact counts, or overlapping cohorts.
    """
    samples: list[tuple[int, int]] = []
    for completed, rto in counts:
        if completed < 0 or not 0 <= rto <= completed:
            return None
        if completed < MIN_SHOP_SAMPLE:
            continue
        bounded = min(MAX_SHOP_SAMPLE, completed)
        samples.append((bounded, rto * bounded // completed))
    total = sum(n for n, _ in samples)
    returns = sum(r for _, r in samples)
    if (
        len(samples) < MIN_SHOPS
        or total < MIN_SAMPLE
        or any(n * 100 > total * MAX_SHARE_PERCENT for n, _ in samples)
        or min(returns, total - returns) < MIN_OUTCOME
    ):
        return None
    rate = round((returns * 100 / total) / 5) * 5
    return {
        "rto_percent_rounded": rate,
        "delivery_percent_rounded": 100 - rate,
        "precision_percent": 5,
        "cohort": "ALL_OPTED_IN_SHOPS",
        "minimum_shops": MIN_SHOPS,
        "minimum_sample": MIN_SAMPLE,
    }


async def build_network_benchmarks(ctx: dict[str, Any] | None = None) -> dict[str, str]:
    # One fixed, non-overlapping cohort per closed month; no caller controls
    # filtering, thresholds or publication, and failed cohorts publish no facts.
    now = utc_now()
    if now.day < 8:
        return {"status": "GATED", "blocker": "NETWORK_CLOSED_PERIOD_REQUIRED"}
    end = datetime(now.year, now.month, 1, tzinfo=UTC)
    previous = end - timedelta(days=1)
    start = datetime(previous.year, previous.month, 1, tzinfo=UTC)
    period = start.strftime("%Y-%m")
    async with system_session("network benchmarks: opted-in SQL counts only") as db:
        if await db.scalar(sa.select(NetworkBenchmark.id).where(NetworkBenchmark.period == period)):
            return {"status": "ALREADY_BUILT"}
        settled = sa.func.coalesce(
            Consignment.delivered_at, Consignment.returned_at, Consignment.last_status_at
        )
        single_owner_shops = (
            sa.select(TenantUser.tenant_id)
            .where(TenantUser.role == "OWNER", TenantUser.is_active.is_(True))
            .group_by(TenantUser.tenant_id)
            .having(sa.func.count(TenantUser.id) == 1)
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
                    Consignment.tenant_id.in_(single_owner_shops),
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
            return {"status": "ALREADY_BUILT"}
        return {"status": result.status}
