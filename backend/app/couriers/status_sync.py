"""Keeping parcel status up to date by polling.

Brief sections 13, 14, 15, 16, 17; master spec sections 42, 62.13.

Steadfast's V1 documentation has no webhook section, so polling is not a
fallback here — it is **the** synchronisation path, and it is built to be one.
The webhook receiver exists and is tested (:mod:`app.couriers.webhooks`), but it
refuses everything until a real contract is supplied, and nothing in this module
depends on it ever arriving.

Three rules shape it:

*   **Adaptive, not uniform.** A parcel booked an hour ago changes state today;
    one booked three weeks ago will not change in the next ten minutes. Polling
    both every twenty minutes would be a tenth of the useful signal for ten
    times the requests. A settled parcel comes off the schedule entirely.

*   **A status is an observation, not an instruction.** Every answer is recorded
    as an immutable event first. Only then, and only for a status the
    documentation lists as settled, does anything touch money.

*   **Quantities are never inferred.** ``partial_delivered`` is a string with no
    numbers attached anywhere in the documentation. It flags the parcel for a
    person and stops. Assuming "half", "one" or "all but one" would put a wrong
    number into a seller's realized revenue and their stock at the same time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.provider_health import ProviderHealthService, ProviderKind
from app.consignments.models import Consignment, ConsignmentStatus
from app.consignments.service import ConsignmentService, DeliveryOutcome
from app.core.clock import utc_now
from app.core.config import Settings, get_settings
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.couriers.accounts import CourierAccountService
from app.couriers.adapter import Unavailable
from app.couriers.capabilities import Capability
from app.couriers.events import record_courier_event
from app.couriers.http import ProviderError
from app.couriers.metrics import CourierMetric, observe_latency, record_metric
from app.couriers.models import (
    CourierAccount,
    CourierEventKind,
    CourierEventSource,
)
from app.couriers.status_maps import CourierStatusMapping, map_courier_status
from app.couriers.steadfast.adapter import PROVIDER as STEADFAST
from app.couriers.steadfast.client import StatusLookupKind
from app.couriers.steadfast.errors import SteadfastError

__all__ = ["PollReport", "StatusSyncService", "next_poll_at"]

log = get_logger(__name__)

#: Statuses whose parcels are still worth asking about. A terminal ecomsbd
#: status means the parcel's story is over on our side; continuing to poll it
#: would spend requests to learn nothing.
_POLLABLE_STATUSES: frozenset[str] = frozenset(
    str(status)
    for status in ConsignmentStatus
    if not status.is_terminal and status is not ConsignmentStatus.NOT_BOOKED
)


def next_poll_at(
    settings: Settings,
    *,
    booked_at: datetime | None,
    now: datetime | None = None,
    failures: int = 0,
) -> datetime:
    """When a parcel should next be asked about.

    Three bands, widening with age, because the probability of a state change
    falls off sharply after the first day and again after the first week. A
    failure streak backs off on top of that — a provider that is refusing us is
    not a provider to ask more often.
    """
    moment = now or utc_now()
    age = moment - booked_at if booked_at is not None else timedelta(0)

    if age < timedelta(hours=settings.courier_poll_fresh_window_hours):
        minutes = settings.courier_poll_interval_fresh_minutes
    elif age < timedelta(days=settings.courier_poll_active_window_days):
        minutes = settings.courier_poll_interval_active_minutes
    else:
        minutes = settings.courier_poll_interval_stale_minutes

    if failures:
        minutes *= min(2**failures, 8)
    return moment + timedelta(minutes=minutes)


@dataclass(slots=True)
class PollReport:
    """What one polling run did."""

    provider: str
    checked: int = 0
    changed: int = 0
    settled: int = 0
    unknown_status: int = 0
    needs_quantities: int = 0
    errors: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "checked": self.checked,
            "changed": self.changed,
            "settled": self.settled,
            "unknown_status": self.unknown_status,
            "needs_quantities": self.needs_quantities,
            "errors": self.errors,
            "skipped": self.skipped,
        }


class StatusSyncService:
    """Polls the provider for parcel status and applies what it says."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        accounts: CourierAccountService,
        consignments: ConsignmentService,
        settings: Settings | None = None,
        health: ProviderHealthService | None = None,
    ) -> None:
        self._db = session
        self._accounts = accounts
        self._consignments = consignments
        self._settings = settings or get_settings()
        self._health = health or ProviderHealthService(session)

    # -------------------------------------------------------------- finding --

    async def due(
        self, *, provider: str = "steadfast", limit: int | None = None
    ) -> list[Consignment]:
        """Parcels due a status check, oldest due first.

        ``next_poll_at IS NULL`` is *not* included: a null means "not on the
        schedule", which is how a manual parcel and a settled one both stay off
        it. A parcel joins the schedule when it is booked or recovered.
        """
        now = utc_now()
        stmt = (
            sa.select(Consignment)
            .where(
                Consignment.provider == provider,
                Consignment.status.in_(_POLLABLE_STATUSES),
                Consignment.next_poll_at.is_not(None),
                Consignment.next_poll_at <= now,
            )
            .order_by(Consignment.next_poll_at.asc())
            .limit(limit or self._settings.courier_poll_batch_size)
        )
        dialect = self._db.bind.dialect.name if self._db.bind is not None else ""
        if dialect == "postgresql":
            # Two workers running the poll must split the queue, not double it.
            stmt = stmt.with_for_update(skip_locked=True)
        return list((await self._db.execute(stmt)).scalars().all())

    # ------------------------------------------------------------- polling --

    async def poll_due(
        self, *, provider: str = "steadfast", limit: int | None = None
    ) -> PollReport:
        """Check every parcel that is due, and apply what comes back."""
        report = PollReport(provider=provider)

        account = await self._accounts.usable_account(provider)
        if account is None:
            # No connected account: nothing to ask with. Not an error — a shop
            # on manual courier mode is the normal case.
            return report

        if not await self._health.allows(
            provider, capability=str(Capability.STATUS_LOOKUP), tenant_id=account.tenant_id
        ):
            report.notes.append("circuit breaker open")
            return report

        parcels = await self.due(provider=provider, limit=limit)
        if not parcels:
            return report

        for consignment in parcels:
            await self.poll_one(consignment, account=account, report=report)
        return report

    async def poll_one(
        self,
        consignment: Consignment,
        *,
        account: CourierAccount,
        report: PollReport | None = None,
    ) -> CourierStatusMapping | None:
        """Ask about one parcel and apply the answer.

        Returns the mapping that was applied, or ``None`` if the provider could
        not be reached — in which case the parcel keeps its current status and
        is simply asked again later.

        Steadfast keeps its own three-way lookup (consignment id, tracking
        code, invoice) through its typed client, exactly as V1 shipped it.
        Every other provider answers through the generic
        :meth:`~app.couriers.adapter.CourierAdapter.get_status`, and a provider
        that has no status lookup takes the parcel off the schedule rather than
        asking again for an answer that cannot come.
        """
        report = report or PollReport(provider=consignment.provider)
        adapter = self._accounts.adapter_for(consignment.provider)
        if adapter is None:
            report.skipped += 1
            return None

        reference, kind = _lookup_reference(consignment)
        if reference is None:
            # Nothing to look it up by. Only reachable for a parcel that was
            # never booked through a provider; take it off the schedule rather
            # than retrying a call that cannot be made.
            consignment.next_poll_at = None
            report.skipped += 1
            await self._db.flush()
            return None

        credentials = self._accounts.credentials_for(account)
        started = utc_now()
        detail: str | None = None
        try:
            if consignment.provider == STEADFAST:
                call = await adapter.client.get_status(credentials, reference, kind=kind)  # type: ignore[attr-defined]
                raw_status = str(call.value.delivery_status)
                correlation_id: str | None = call.correlation_id
            else:
                status = await adapter.get_status(credentials, reference)
                if isinstance(status, Unavailable):
                    consignment.next_poll_at = None
                    report.skipped += 1
                    await self._db.flush()
                    return None
                raw_status = status.raw_status
                # The provider's qualifier for the status, where it has one:
                # RedX's delivery type tells a whole delivery from a partial.
                raw_type = status.raw.get("delivery_type")
                detail = str(raw_type) if raw_type is not None else None
                correlation_id = None
        except (SteadfastError, ProviderError) as exc:
            await self._on_poll_failure(consignment, account, exc, started)
            report.errors += 1
            return None
        except Exception as exc:
            log.error(
                "status poll raised",
                extra={
                    "provider": consignment.provider,
                    "operation": "get_status",
                    "error": type(exc).__name__,
                },
            )
            report.errors += 1
            consignment.poll_failure_count += 1
            consignment.next_poll_at = next_poll_at(
                self._settings,
                booked_at=consignment.booked_at,
                failures=consignment.poll_failure_count,
            )
            await self._db.flush()
            return None

        latency_ms = int((utc_now() - started).total_seconds() * 1000)
        await self._health.record_success(
            ProviderKind.COURIER,
            consignment.provider,
            capability=str(Capability.STATUS_LOOKUP),
            latency_ms=latency_ms,
        )
        observe_latency(
            CourierMetric.LATENCY,
            milliseconds=latency_ms,
            provider=consignment.provider,
            capability=str(Capability.STATUS_LOOKUP),
        )

        return await self.apply_status(
            consignment,
            raw_status=raw_status,
            source=CourierEventSource.POLL,
            correlation_id=correlation_id,
            report=report,
            status_detail=detail,
        )

    # -------------------------------------------------------------- applying --

    async def apply_status(
        self,
        consignment: Consignment,
        *,
        raw_status: str,
        source: CourierEventSource,
        correlation_id: str | None = None,
        report: PollReport | None = None,
        status_detail: str | None = None,
    ) -> CourierStatusMapping:
        """Record an observation and move the parcel, if the status says to.

        Shared by polling and — once a webhook contract exists — by the webhook
        processor, so both paths apply exactly the same rules. The status is
        read against the parcel's own provider's table, together with
        ``status_detail`` where that provider qualifies its statuses.
        """
        report = report or PollReport(provider=consignment.provider)
        report.checked += 1

        now = utc_now()
        mapping = map_courier_status(consignment.provider, raw_status, detail=status_detail)

        await record_courier_event(
            self._db,
            provider=consignment.provider,
            kind=CourierEventKind.STATUS,
            source=source,
            consignment=consignment,
            raw_status=raw_status,
            correlation_id=correlation_id,
            status_detail=status_detail,
        )

        previous_raw = consignment.provider_raw_status
        consignment.provider_raw_status = raw_status
        consignment.provider_status_at = now
        consignment.last_polled_at = now
        consignment.poll_failure_count = 0

        if previous_raw != raw_status:
            report.changed += 1

        if mapping.canonical is None:
            # A status the documentation does not list — or one it lists that
            # makes no claim about where the parcel is. Stored verbatim, and
            # the parcel keeps the state it had. Never guessed at, never
            # allowed near money. Only the first kind counts as unknown.
            if not mapping.is_documented:
                report.unknown_status += 1
            consignment.next_poll_at = next_poll_at(
                self._settings, booked_at=consignment.booked_at, now=now
            )
            await self._db.flush()
            return mapping

        if mapping.needs_quantity_resolution:
            # `partial_delivered` and its approval-pending twin. The provider
            # says some units arrived and does not say how many, so a person
            # supplies the numbers through the existing partial-delivery
            # workflow. Nothing is settled until they do.
            consignment.needs_quantity_resolution = True
            report.needs_quantities += 1

        if mapping.is_final and not mapping.needs_quantity_resolution:
            settled = await self._settle(consignment, mapping, occurred_at=now)
            if settled:
                report.settled += 1
                consignment.next_poll_at = None
                await self._db.flush()
                record_metric(
                    CourierMetric.STATUS_SYNC_SUCCESS,
                    provider=consignment.provider,
                    state=str(mapping.canonical),
                )
                return mapping

        # Not settled: an in-flight state, an approval-pending state, or a
        # final one still waiting on quantities. Move the *visible* status so
        # the seller sees progress, and touch nothing financial.
        visible = _visible_status_for(mapping, consignment.consignment_status)
        if visible is not None and consignment.consignment_status is not visible:
            consignment.status = str(visible)
            consignment.last_status_at = now

        consignment.next_poll_at = next_poll_at(
            self._settings, booked_at=consignment.booked_at, now=now
        )
        await self._db.flush()
        record_metric(
            CourierMetric.STATUS_SYNC_SUCCESS,
            provider=consignment.provider,
            state=str(mapping.canonical),
        )
        return mapping

    async def _settle(
        self, consignment: Consignment, mapping: CourierStatusMapping, *, occurred_at: datetime
    ) -> bool:
        """Record a final outcome through the existing money path.

        Deliberately delegates to :meth:`ConsignmentService.record_outcome`
        rather than setting columns: that is where stock restoration, the COD
        receivable, the ledger entries and the profit snapshot all happen
        together, in one transaction. A status sync that wrote its own version
        of that would be a second, divergent implementation of the money rules.
        """
        if consignment.consignment_status.is_terminal:
            return False
        assert mapping.canonical is not None  # noqa: S101 - checked by the caller

        try:
            await self._consignments.record_outcome(
                consignment.id,
                DeliveryOutcome(status=mapping.canonical, occurred_at=occurred_at),
            )
        except ConflictError:
            # Someone recorded the outcome between our read and our write.
            # Theirs stands; the provider observation is already stored.
            return False
        return True

    async def _on_poll_failure(
        self,
        consignment: Consignment,
        account: CourierAccount,
        exc: SteadfastError | ProviderError,
        started: datetime,
    ) -> None:
        """A failed status check changes nothing about the parcel.

        The parcel keeps its status, backs off, and is asked again. A courier
        whose API is down has not delivered or lost anything.
        """
        latency_ms = int((utc_now() - started).total_seconds() * 1000)
        await self._health.record_failure(
            ProviderKind.COURIER,
            consignment.provider,
            capability=str(Capability.STATUS_LOOKUP),
            tenant_id=account.tenant_id,
            error_code=str(exc.kind),
            latency_ms=latency_ms,
            is_auth_failure=exc.is_auth_failure,
        )
        consignment.poll_failure_count += 1
        consignment.last_polled_at = utc_now()
        consignment.next_poll_at = next_poll_at(
            self._settings,
            booked_at=consignment.booked_at,
            failures=consignment.poll_failure_count,
        )
        await self._db.flush()
        log.info(
            "status poll failed",
            extra={
                "provider": consignment.provider,
                "operation": "get_status",
                "error_kind": str(exc.kind),
                "poll_failure_count": consignment.poll_failure_count,
            },
        )

    # ---------------------------------------------------------------- lag --

    async def sync_lag_seconds(self, *, provider: str = "steadfast") -> int:
        """How stale the oldest overdue parcel is, in seconds.

        The ``steadfast_status_sync_lag`` signal. Measured from the *due* time
        rather than the last observation, so a healthy fleet of parcels on a
        six-hour interval reads as zero lag rather than as six hours of it.
        """
        now = utc_now()
        oldest = await self._db.scalar(
            sa.select(sa.func.min(Consignment.next_poll_at)).where(
                Consignment.provider == provider,
                Consignment.status.in_(_POLLABLE_STATUSES),
                Consignment.next_poll_at.is_not(None),
                Consignment.next_poll_at <= now,
            )
        )
        if oldest is None:
            return 0
        from app.core.clock import ensure_utc

        return max(0, int((now - ensure_utc(oldest)).total_seconds()))

    async def parcels_awaiting_quantities(
        self, *, provider: str = "steadfast", limit: int = 100
    ) -> list[Consignment]:
        """Parcels the provider called partly delivered, waiting on a person."""
        result = await self._db.execute(
            sa.select(Consignment)
            .where(
                Consignment.provider == provider,
                Consignment.needs_quantity_resolution.is_(True),
                Consignment.status.notin_(
                    [str(status) for status in ConsignmentStatus if status.is_terminal]
                ),
            )
            .order_by(Consignment.provider_status_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


def _visible_status_for(
    mapping: CourierStatusMapping, current: ConsignmentStatus
) -> ConsignmentStatus | None:
    """The status to show the seller for an observation that settled nothing.

    ``None`` means leave the parcel where it is.

    The case this function exists for is ``partial_delivered``. Its canonical
    status is ``PARTIAL_DELIVERED``, which is *terminal* and carries money:
    reaching it means a realized revenue figure and a stock restoration exist,
    both computed from per-item quantities. The provider gave no quantities, so
    writing that status here would create a settled outcome out of a string.
    The parcel instead stays in flight, carries
    ``needs_quantity_resolution``, and shows the courier's own words until a
    person supplies the numbers through the existing partial-delivery workflow.

    A parcel that is already terminal is never moved: an outcome someone
    recorded by hand outranks a poll.
    """
    if current.is_terminal:
        return None
    if mapping.canonical is None:
        return None
    if mapping.needs_quantity_resolution and mapping.canonical.is_terminal:
        return None
    return mapping.canonical


def _lookup_reference(consignment: Consignment) -> tuple[str | None, StatusLookupKind]:
    """Which of the three documented lookups to use for this parcel.

    Preference order is by strength of identity. The provider's own id is
    unambiguous; the tracking code is next; the invoice is ours and is the only
    one available for a parcel recovered from ``BOOKING_UNKNOWN`` — where
    ``status_by_invoice`` proved the parcel exists but could not tell us its
    consignment id, because Steadfast documents no way to obtain one.
    """
    if consignment.provider_consignment_id:
        return consignment.provider_consignment_id, StatusLookupKind.CONSIGNMENT_ID
    if consignment.tracking_code:
        return consignment.tracking_code, StatusLookupKind.TRACKING_CODE
    if consignment.merchant_reference:
        return consignment.merchant_reference, StatusLookupKind.INVOICE
    return None, StatusLookupKind.CONSIGNMENT_ID
