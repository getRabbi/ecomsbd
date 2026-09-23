"""Recording what a provider said.

Brief sections 13 and 37; master spec section 62.13.

One function, and the reason it exists rather than each caller writing a row is
deduplication. A parcel polled every twenty minutes for two weeks produces a
thousand identical "still pending" answers. Writing a thousand rows would make
the table useless to read and expensive to keep; writing none would lose the
fact that we *were* checking.

So an observation is keyed on its content — kind, reference, raw status — and a
repeat bumps ``last_seen_at`` and ``observation_count`` instead of inserting.
The row's meaning never changes, which is what "immutable event" has to
guarantee, and a genuine status *change* is always a new row because the status
is part of the key.

``normalized_status`` is resolved and stored at write time rather than derived
on read. A later correction to the mapping table must not silently rewrite what
a parcel's history says happened.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.consignments.models import Consignment
from app.core.clock import utc_now
from app.core.logging import get_logger
from app.couriers.metrics import CourierMetric, record_metric
from app.couriers.models import (
    CourierEvent,
    CourierEventKind,
    CourierEventSource,
)
from app.couriers.status_maps import map_courier_status

__all__ = ["recent_events_for", "record_courier_event"]

log = get_logger(__name__)


async def record_courier_event(
    session: AsyncSession,
    *,
    provider: str,
    kind: CourierEventKind,
    source: CourierEventSource,
    consignment: Consignment | None = None,
    provider_consignment_id: str | None = None,
    tracking_code: str | None = None,
    merchant_reference: str | None = None,
    raw_status: str | None = None,
    raw_payload_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
    courier_account_id: uuid.UUID | None = None,
    status_detail: str | None = None,
) -> CourierEvent:
    """Append an observation, or recognise one we already hold.

    Returns the row either way, so a caller can tell a genuine change from a
    repeat by checking ``observation_count``.

    ``status_detail`` is the provider's qualifier for the status, where it has
    one (RedX's delivery type), and is read by that provider's status table.
    """
    reference = (
        provider_consignment_id
        or (consignment.provider_consignment_id if consignment else None)
        or tracking_code
        or (consignment.tracking_code if consignment else None)
        or merchant_reference
        or (consignment.merchant_reference if consignment else None)
        or ""
    )

    mapping = (
        map_courier_status(provider, raw_status, detail=status_detail)
        if raw_status is not None
        else None
    )
    dedupe_key = CourierEvent.build_dedupe_key(
        kind=kind,
        reference=reference,
        raw_status=raw_status,
        extra=str(consignment.id) if consignment else "",
    )

    existing = (
        await session.execute(
            sa.select(CourierEvent).where(
                CourierEvent.provider == provider,
                CourierEvent.dedupe_key == dedupe_key,
            )
        )
    ).scalar_one_or_none()

    now = utc_now()
    if existing is not None:
        # Same answer as last time. The event is unchanged; only the fact that
        # we asked again is new.
        existing.last_seen_at = now
        existing.observation_count += 1
        await session.flush()
        return existing

    event = CourierEvent(
        provider=provider,
        courier_account_id=(
            courier_account_id
            or (consignment.courier_account_id if consignment is not None else None)
        ),
        kind=str(kind),
        source=str(source),
        consignment_id=consignment.id if consignment is not None else None,
        provider_consignment_id=(
            provider_consignment_id
            or (consignment.provider_consignment_id if consignment else None)
        ),
        tracking_code=tracking_code or (consignment.tracking_code if consignment else None),
        merchant_reference=(
            merchant_reference or (consignment.merchant_reference if consignment else None)
        ),
        raw_status=raw_status,
        normalized_status=(
            str(mapping.canonical) if mapping is not None and mapping.canonical else None
        ),
        status_undocumented=bool(mapping is not None and not mapping.is_documented),
        raw_payload_id=raw_payload_id,
        dedupe_key=dedupe_key,
        correlation_id=correlation_id,
        observed_at=now,
        last_seen_at=now,
        observation_count=1,
    )
    session.add(event)
    await session.flush()

    if event.status_undocumented:
        # A status the supplied documentation does not list. Loud on purpose:
        # a provider adding a state must be noticed within a day, not after a
        # quarter of quietly wrong reports.
        record_metric(
            CourierMetric.STATUS_UNKNOWN_VALUE,
            provider=provider,
            # The status string itself is not a label: an unbounded provider
            # value would blow up cardinality. It is on the row and in the log.
            kind=str(kind),
        )
        log.warning(
            "courier reported an undocumented status",
            extra={
                "provider": provider,
                "operation": "record_event",
                "raw_status": raw_status,
                "courier_event_id": str(event.id),
            },
        )
    return event


async def recent_events_for(
    session: AsyncSession, consignment_id: uuid.UUID, *, limit: int = 50
) -> list[CourierEvent]:
    """A parcel's provider history, newest first.

    What the seller-facing timeline renders. Observations, not events the
    provider timestamped — Steadfast's status response carries no timestamp, so
    the UI labels these as when ecomsbd saw them rather than implying the
    courier said so at that moment.
    """
    result = await session.execute(
        sa.select(CourierEvent)
        .where(CourierEvent.consignment_id == consignment_id)
        .order_by(CourierEvent.observed_at.desc(), CourierEvent.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
