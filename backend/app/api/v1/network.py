from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.analytics import network as network_rules
from app.analytics.courier_intelligence import courier_facts
from app.analytics.network import MIN_SAMPLE, MIN_SHOPS, POLICY
from app.analytics.network_models import NetworkBenchmark, NetworkPreference
from app.api.deps import DbSession, Principal, require_permission
from app.common.operation_lock import lock_shop
from app.core.clock import utc_now
from app.tenants.roles import Permission

router = APIRouter(prefix="/network-intelligence", tags=["anonymous benchmarks"])
Reader = Annotated[Principal, Depends(require_permission(Permission.ORDER_VIEW))]
Manager = Annotated[Principal, Depends(require_permission(Permission.SETTINGS_MANAGE))]
Dimension = Literal["ALL", "COURIER", "CATEGORY", "VOLUME_BAND"]


class PreferenceInput(BaseModel):
    opted_in: bool


@router.get("")
async def benchmark(db: DbSession, _: Reader) -> dict[str, Any]:
    preference = await db.scalar(sa.select(NetworkPreference))
    row = await db.scalar(
        sa.select(NetworkBenchmark)
        .where(NetworkBenchmark.policy_version == POLICY)
        .order_by(NetworkBenchmark.period.desc())
        .limit(1)
    )
    published = bool(row and row.status == "PUBLISHED")
    release = await network_rules.latest_cells(db, "ALL")
    return {
        "status": "COMPLETE" if published else "GATED",
        "blocker": None if published else "NETWORK_MINIMUM_SAMPLE_REQUIRED",
        "opted_in": bool(preference and preference.opted_in),
        "period": row.period if row else None,
        "facts": row.facts if row is not None and published else {},
        "minimum_shops": MIN_SHOPS,
        "minimum_sample": MIN_SAMPLE,
        "benchmarks": {
            **release,
            "cohort_definition": (
                network_rules.cohort_definition(release["period"], "ALL")
                if release["period"]
                else None
            ),
        },
        "message_en": "Anonymous monthly parcel outcomes from consenting shops. No customer or phone data is shared. At least 20 shops and 200 parcels; rates are rounded to 5%.",
        "message_bn": "সম্মত শপগুলোর মাসিক বেনামি পার্সেলের ফলাফল। কোনো গ্রাহক বা ফোনের তথ্য শেয়ার হয় না। কমপক্ষে ২০টি শপ ও ২০০টি পার্সেল; হার ৫% ধাপে দেখানো হয়।",
    }


@router.get("/benchmarks")
async def benchmark_drilldown(
    db: DbSession, _: Reader, dimension: Dimension = "ALL"
) -> dict[str, Any]:
    """One dimension of the latest release. Fixed cohorts; nothing is filtered by the caller."""
    release = await network_rules.latest_cells(db, dimension)
    return {
        **release,
        "dimension": dimension,
        "cohorts": list(network_rules.DIMENSIONS[dimension]),
        "cohort_definition": (
            network_rules.cohort_definition(release["period"], dimension)
            if release["period"]
            else None
        ),
    }


@router.get("/couriers")
async def couriers(db: DbSession, _: Reader) -> dict[str, Any]:
    """This shop's factual per-courier metrics, beside the anonymous courier cohorts."""
    own = await courier_facts(db)
    release = await network_rules.latest_cells(db, "COURIER")
    return {
        "own_shop": own,
        "network": {
            **release,
            "cohort_definition": (
                network_rules.cohort_definition(release["period"], "COURIER")
                if release["period"]
                else None
            ),
        },
    }


@router.patch("/preference")
async def preference(body: PreferenceInput, db: DbSession, _: Manager) -> dict[str, Any]:
    await lock_shop(db)
    row = await db.scalar(sa.select(NetworkPreference))
    if row is None:
        row = NetworkPreference(opted_in=False)
        db.add(row)
    if body.opted_in and not row.opted_in:
        row.opted_in_at = utc_now()
    elif not body.opted_in:
        row.opted_in_at = None
    row.opted_in = body.opted_in
    await db.flush()
    return {"opted_in": row.opted_in}
