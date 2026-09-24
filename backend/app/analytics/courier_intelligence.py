"""Factual per-courier comparison for one shop (V3.7).

Numbers from the shop's own courier truth: parcel outcomes, courier
timestamps, COD receivables and reconciliation items. There is no overall
courier score and no recommendation; each metric stands on its own with the
sample it came from. Couriers without a live, documented integration show
their counts but no rates (see :data:`app.analytics.rto.NOT_LIVE_PROVIDERS`).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.rto import (
    COMPLETED_STATUSES,
    NOT_LIVE_PROVIDERS,
    RTO_STATUSES,
    ParcelOutcome,
    rate_bps,
    statuses_for,
)
from app.consignments.models import MANUAL_PROVIDER, Consignment
from app.core.clock import utc_now
from app.money.models import CodReceivable, ReceivableStatus
from app.reconciliation.models import DISCREPANCY_STATUSES, ReconciliationItem

WINDOW_DAYS = 90
#: An in-transit parcel with no courier update for this long counts as stuck.
STUCK_AFTER = timedelta(days=7)
#: Delivered COD still unpaid after this long counts as overdue.
OVERDUE_AFTER = timedelta(days=7)
#: Observations needed before a median is shown.
MIN_MEDIAN_SAMPLE = 5
_ROW_CAP = 20_000

_COLLECTIBLE = [
    str(s)
    for s in (
        ReceivableStatus.ELIGIBLE,
        ReceivableStatus.PAYOUT_IDENTIFIED,
        ReceivableStatus.PARTIALLY_SETTLED,
        ReceivableStatus.MISMATCHED,
        ReceivableStatus.DISPUTED,
    )
]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _median(values: list[float], digits: int = 1) -> float | None:
    return round(median(values), digits) if len(values) >= MIN_MEDIAN_SAMPLE else None


async def courier_facts(db: AsyncSession, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    since = now - timedelta(days=WINDOW_DAYS)
    settled = sa.func.coalesce(
        Consignment.delivered_at, Consignment.returned_at, Consignment.last_status_at
    )
    rows: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "completed": 0,
            "delivered": 0,
            "rto": 0,
            "in_transit": 0,
            "stuck": 0,
            "hours": [],
            "payout_days": [],
            "outstanding_paisa": 0,
            "overdue_paisa": 0,
            "recon_items": 0,
            "recon_mismatches": 0,
            "charges": [],
        }
    )

    for provider, total, rto in (
        await db.execute(
            sa.select(
                Consignment.provider,
                sa.func.count(Consignment.id),
                sa.func.sum(sa.case((Consignment.status.in_(RTO_STATUSES), 1), else_=0)),
            )
            .where(Consignment.status.in_(COMPLETED_STATUSES), settled >= since)
            .group_by(Consignment.provider)
        )
    ).all():
        row = rows[provider.lower()]
        row["completed"] += int(total)
        row["rto"] += int(rto or 0)
        row["delivered"] += int(total) - int(rto or 0)

    stale_before = now - STUCK_AFTER
    for provider, last, booked in (
        await db.execute(
            sa.select(Consignment.provider, Consignment.last_status_at, Consignment.booked_at)
            .where(Consignment.status.in_(statuses_for(ParcelOutcome.IN_TRANSIT)))
            .limit(_ROW_CAP)
        )
    ).all():
        row = rows[provider.lower()]
        row["in_transit"] += 1
        seen = last or booked
        if seen is not None and _aware(seen) < stale_before:
            row["stuck"] += 1

    for provider, booked, done in (
        await db.execute(
            sa.select(Consignment.provider, Consignment.booked_at, Consignment.delivered_at)
            .where(
                Consignment.status.in_(("DELIVERED", "PARTIAL_DELIVERED")),
                Consignment.delivered_at >= since,
                Consignment.booked_at.is_not(None),
            )
            .limit(_ROW_CAP)
        )
    ).all():
        hours = (_aware(done) - _aware(booked)).total_seconds() / 3600
        if hours >= 0:
            rows[provider.lower()]["hours"].append(hours)

    for provider, eligible_at, settled_at in (
        await db.execute(
            sa.select(CodReceivable.provider, CodReceivable.eligible_at, CodReceivable.settled_at)
            .where(
                CodReceivable.status == str(ReceivableStatus.SETTLED),
                CodReceivable.settled_at >= since,
                CodReceivable.eligible_at.is_not(None),
            )
            .limit(_ROW_CAP)
        )
    ).all():
        days = (_aware(settled_at) - _aware(eligible_at)).total_seconds() / 86_400
        if days >= 0:
            rows[provider.lower()]["payout_days"].append(days)

    overdue_before = now - OVERDUE_AFTER
    for receivable in (
        await db.scalars(
            sa.select(CodReceivable).where(CodReceivable.status.in_(_COLLECTIBLE)).limit(_ROW_CAP)
        )
    ).all():
        owed = receivable.outstanding_paisa
        row = rows[receivable.provider.lower()]
        row["outstanding_paisa"] += owed
        if receivable.eligible_at is not None and _aware(receivable.eligible_at) < overdue_before:
            row["overdue_paisa"] += owed

    discrepancy = {str(s) for s in DISCREPANCY_STATUSES}
    for provider, status, charge, lines in (
        await db.execute(
            sa.select(
                ReconciliationItem.provider,
                ReconciliationItem.status,
                ReconciliationItem.actual_charge_paisa,
                ReconciliationItem.line_count,
            )
            .where(ReconciliationItem.created_at >= since)
            .limit(_ROW_CAP)
        )
    ).all():
        row = rows[provider.lower()]
        row["recon_items"] += 1
        row["recon_mismatches"] += int(str(status) in discrepancy)
        if charge and charge > 0 and lines and lines > 0:
            row["charges"].append(float(charge))

    couriers = []
    for provider in sorted(rows):
        if provider == MANUAL_PROVIDER:
            continue
        row = rows[provider]
        live = provider not in NOT_LIVE_PROVIDERS
        charge = _median(row["charges"], 0)
        couriers.append(
            {
                "courier": provider,
                "live_integration": live,
                "completed_parcels": row["completed"],
                "delivered_parcels": row["delivered"],
                "rto_parcels": row["rto"],
                "delivery_rate_bps": rate_bps(row["delivered"], row["completed"]) if live else None,
                "rto_rate_bps": rate_bps(row["rto"], row["completed"]) if live else None,
                "median_delivery_hours": _median(row["hours"]) if live else None,
                "delivery_time_sample": len(row["hours"]),
                "in_transit_parcels": row["in_transit"],
                "stuck_parcels": row["stuck"],
                "median_payout_delay_days": _median(row["payout_days"]) if live else None,
                "payout_sample": len(row["payout_days"]),
                "outstanding_cod_paisa": row["outstanding_paisa"],
                "overdue_cod_paisa": row["overdue_paisa"],
                "reconciliation_items": row["recon_items"],
                "reconciliation_mismatches": row["recon_mismatches"],
                "reconciliation_mismatch_rate_bps": (
                    rate_bps(row["recon_mismatches"], row["recon_items"]) if live else None
                ),
                "median_delivery_charge_paisa": int(charge)
                if charge is not None and live
                else None,
                "charge_sample": len(row["charges"]),
            }
        )
    return {
        "window_days": WINDOW_DAYS,
        "as_of": now.isoformat(),
        "stuck_after_days": STUCK_AFTER.days,
        "overdue_after_days": OVERDUE_AFTER.days,
        "min_median_sample": MIN_MEDIAN_SAMPLE,
        "couriers": couriers,
    }
