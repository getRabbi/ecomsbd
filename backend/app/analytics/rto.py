"""Return and RTO intelligence, from this shop's own parcels.

One definition, used by every figure that says "returned":

* **The unit is the parcel** (a consignment row), read at its *current*
  status. A courier that reports the same status five times still moves one
  row once, so duplicate events cannot inflate a count by construction.
* **RTO** is a parcel that went out and came back without being delivered:
  ``RETURNED``, or ``CANCELLED`` at the courier. Steadfast has no "returned"
  status at all — its return-to-origin outcome is ``cancelled`` (its pending
  form already maps to ``RETURNING``) — so counting ``RETURNED`` alone would
  report 0% RTO for every Steadfast shop. The two stay separately visible as
  ``returned`` and ``courier_cancelled``.
* **The denominator is completed parcels**: delivered + partially delivered +
  RTO. Parcels still moving are *open*, not returns; lost/damaged is the
  courier's failure and is shown on its own; a booking that never happened
  (``NOT_BOOKED``/``BOOKING``/``BOOKING_UNKNOWN``/``FAILED``) never left.
* **Cancelled** means an *order* cancelled before any parcel was dispatched.
  It is never RTO and never in the RTO denominator.

First-party only. Every query runs on the request's tenant-scoped session (the
ORM tenancy guard adds the shop filter to every tenant-owned entity), there is
no cross-shop view, and nothing here produces a score: every flag carries the
counts and the window it was computed from.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.consignments.models import Consignment, ConsignmentStatus
from app.core.clock import business_date, business_day_bounds, utc_now
from app.customers.models import Customer
from app.db.types import TZDateTime
from app.orders.models import Order, OrderItem, OrderStatus

__all__ = [
    "AREA_MIN_COVERAGE_BPS",
    "MIN_RATE_SAMPLE",
    "NOT_LIVE_PROVIDERS",
    "AreaReport",
    "CustomerHistory",
    "Observation",
    "OutcomeCounts",
    "ParcelOutcome",
    "RtoService",
    "classify",
    "rate_bps",
    "statuses_for",
]


class ParcelOutcome(StrEnum):
    """What a parcel's current status means for return intelligence."""

    DELIVERED = "DELIVERED"
    PARTIAL = "PARTIAL"
    RTO = "RTO"
    #: Lost or damaged by the courier. Neither delivered nor returned.
    LOST = "LOST"
    #: With the courier and unresolved — including a return still on its way.
    IN_TRANSIT = "IN_TRANSIT"
    #: Never handed to a courier.
    NOT_DISPATCHED = "NOT_DISPATCHED"


_S = ConsignmentStatus
_O = ParcelOutcome

#: Exhaustive on purpose: a status added to the enum without a decision here
#: fails the classification test rather than silently landing in a bucket.
_OUTCOME_OF: Final[Mapping[str, ParcelOutcome]] = MappingProxyType(
    {
        str(_S.NOT_BOOKED): _O.NOT_DISPATCHED,
        str(_S.BOOKING): _O.NOT_DISPATCHED,
        str(_S.BOOKING_UNKNOWN): _O.NOT_DISPATCHED,
        str(_S.FAILED): _O.NOT_DISPATCHED,
        str(_S.BOOKED): _O.IN_TRANSIT,
        str(_S.PICKED_UP): _O.IN_TRANSIT,
        str(_S.IN_TRANSIT): _O.IN_TRANSIT,
        str(_S.OUT_FOR_DELIVERY): _O.IN_TRANSIT,
        str(_S.RETURN_REQUESTED): _O.IN_TRANSIT,
        str(_S.RETURNING): _O.IN_TRANSIT,
        str(_S.DELIVERED): _O.DELIVERED,
        str(_S.PARTIAL_DELIVERED): _O.PARTIAL,
        str(_S.RETURNED): _O.RTO,
        str(_S.CANCELLED): _O.RTO,
        str(_S.LOST): _O.LOST,
        str(_S.DAMAGED): _O.LOST,
    }
)

COMPLETED_OUTCOMES: Final = frozenset({_O.DELIVERED, _O.PARTIAL, _O.RTO})

#: Completed parcels needed before a rate is compared with another one, or a
#: trend is called. Below it the row is shown, labelled limited data.
MIN_RATE_SAMPLE: Final = 10

#: Share of completed parcels that must carry a district before districts are
#: compared at all. District is free text; below this the table would describe
#: which orders happened to have one typed in, not where parcels come back from.
AREA_MIN_COVERAGE_BPS: Final = 8_000

#: A rate move smaller than this (basis points) between two windows is FLAT.
TREND_DEAD_BAND_BPS: Final = 200

#: Providers with no live, documented integration. Their parcels count in the
#: shop totals but no per-courier performance is claimed for them.
NOT_LIVE_PROVIDERS: Final = frozenset({"redx"})

#: Parcels read for one customer's history. Far above any real customer.
_CUSTOMER_PARCEL_CAP: Final = 500
#: Customers examined for repeat patterns in one request.
_PATTERN_CANDIDATES: Final = 200


def classify(status: str | ConsignmentStatus) -> ParcelOutcome:
    """The one mapping from a consignment status to an outcome."""
    return _OUTCOME_OF[str(status)]


def statuses_for(*outcomes: ParcelOutcome) -> list[str]:
    """Every consignment status that classifies into ``outcomes``, sorted."""
    wanted = set(outcomes)
    return sorted(status for status, outcome in _OUTCOME_OF.items() if outcome in wanted)


RTO_STATUSES: Final = tuple(statuses_for(_O.RTO))
COMPLETED_STATUSES: Final = tuple(statuses_for(*COMPLETED_OUTCOMES))
DISPATCHED_STATUSES: Final = tuple(
    statuses_for(_O.DELIVERED, _O.PARTIAL, _O.RTO, _O.LOST, _O.IN_TRANSIT)
)


def is_rto_status(status: str) -> bool:
    return _OUTCOME_OF.get(str(status)) is _O.RTO


def is_completed_status(status: str) -> bool:
    return _OUTCOME_OF.get(str(status)) in COMPLETED_OUTCOMES


def rate_bps(numerator: int, denominator: int) -> int | None:
    """``numerator / denominator`` in basis points; ``None`` with no denominator."""
    if denominator <= 0:
        return None
    return round(numerator * 10_000 / denominator)


def _settled_at() -> sa.ColumnElement[datetime]:
    """When a parcel reached its outcome.

    ``record_outcome`` stamps ``delivered_at`` for deliveries and
    ``returned_at`` for returns and courier cancellations; lost/damaged only
    move ``last_status_at``.
    """
    return sa.func.coalesce(
        Consignment.delivered_at,
        Consignment.returned_at,
        Consignment.last_status_at,
        Consignment.created_at,
        type_=TZDateTime(),
    )


def _day_start(day: date) -> datetime:
    return business_day_bounds(day)[0]


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class OutcomeCounts:
    """Parcel counts by outcome, with the RTO split kept visible."""

    delivered: int = 0
    partial: int = 0
    returned: int = 0
    courier_cancelled: int = 0
    lost: int = 0
    in_transit: int = 0

    def add(self, status: str, count: int = 1) -> None:
        outcome = classify(status)
        if outcome is _O.DELIVERED:
            self.delivered += count
        elif outcome is _O.PARTIAL:
            self.partial += count
        elif outcome is _O.RTO:
            if str(status) == str(_S.CANCELLED):
                self.courier_cancelled += count
            else:
                self.returned += count
        elif outcome is _O.LOST:
            self.lost += count
        elif outcome is _O.IN_TRANSIT:
            self.in_transit += count

    @property
    def rto(self) -> int:
        return self.returned + self.courier_cancelled

    @property
    def completed(self) -> int:
        return self.delivered + self.partial + self.rto

    @property
    def rto_rate_bps(self) -> int | None:
        return rate_bps(self.rto, self.completed)

    @property
    def success_rate_bps(self) -> int | None:
        return rate_bps(self.delivered + self.partial, self.completed)

    @property
    def sufficient(self) -> bool:
        return self.completed >= MIN_RATE_SAMPLE


def trend_of(recent: OutcomeCounts, previous: OutcomeCounts) -> str:
    """UP / DOWN / FLAT for the RTO rate, or INSUFFICIENT_DATA."""
    if not (recent.sufficient and previous.sufficient):
        return "INSUFFICIENT_DATA"
    delta = (recent.rto_rate_bps or 0) - (previous.rto_rate_bps or 0)
    if delta > TREND_DEAD_BAND_BPS:
        return "UP"
    if delta < -TREND_DEAD_BAND_BPS:
        return "DOWN"
    return "FLAT"


@dataclass(slots=True)
class WindowCounts:
    days: int
    since: date
    counts: OutcomeCounts


@dataclass(slots=True)
class RtoSummary:
    since: date
    until: date
    days: int
    counts: OutcomeCounts
    #: Parcels with the courier right now, whatever their age.
    open_now: int
    #: Orders cancelled in the window before any parcel was dispatched.
    cancelled_before_dispatch: int
    windows: list[WindowCounts]


@dataclass(slots=True)
class TrendPoint:
    week_start: date
    week_end: date
    counts: OutcomeCounts


@dataclass(slots=True)
class ProductRto:
    product_id: uuid.UUID | None
    product_name: str
    counts: OutcomeCounts = field(default_factory=OutcomeCounts)
    #: Line value (price x qty - discount) of this product inside RTO parcels.
    rto_value_paisa: int = 0


@dataclass(slots=True)
class CourierRto:
    provider: str
    counts: OutcomeCounts = field(default_factory=OutcomeCounts)
    recent: OutcomeCounts = field(default_factory=OutcomeCounts)
    previous: OutcomeCounts = field(default_factory=OutcomeCounts)
    in_transit_now: int = 0

    @property
    def trend(self) -> str:
        return trend_of(self.recent, self.previous)


@dataclass(slots=True)
class AreaRow:
    label: str
    counts: OutcomeCounts = field(default_factory=OutcomeCounts)


@dataclass(slots=True)
class AreaReport:
    status: str  # ACTIVE | DATA_NOT_RELIABLE
    level: str
    completed: int
    with_area: int
    rows: list[AreaRow]

    @property
    def coverage_bps(self) -> int | None:
        return rate_bps(self.with_area, self.completed)


class ObservationCode(StrEnum):
    """Factual patterns. Observations about parcels, never about people."""

    REPEAT_RTO = "REPEAT_RTO"
    RTO_AFTER_DELIVERIES = "RTO_AFTER_DELIVERIES"
    WORSENING = "WORSENING"
    IMPROVING = "IMPROVING"
    PRODUCT_REPEAT_RTO = "PRODUCT_REPEAT_RTO"


@dataclass(frozen=True, slots=True)
class Observation:
    """One pattern and the counts that make it true."""

    code: ObservationCode
    #: Parcels the statement is about (e.g. the last 3 completed).
    parcel_count: int
    rto_count: int
    delivered_count: int = 0
    #: Comparison group, for WORSENING / IMPROVING and RTO_AFTER_DELIVERIES.
    earlier_parcel_count: int = 0
    earlier_rto_count: int = 0
    product_name: str | None = None


@dataclass(frozen=True, slots=True)
class ParcelEvent:
    order_number: str
    outcome: ParcelOutcome
    status: str
    provider: str
    at: datetime | None


@dataclass(slots=True)
class CustomerHistory:
    """One customer's history with this shop.

    Shaped so :func:`app.customers.risk.assess` reads it directly: the V1 band
    keeps its rule, and is now fed from real parcel outcomes.
    """

    order_count: int = 0
    counts: OutcomeCounts = field(default_factory=OutcomeCounts)
    cancelled_count: int = 0
    first_order_at: datetime | None = None
    last_order_at: datetime | None = None
    recent: list[ParcelEvent] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)

    # --- the attributes risk.assess reads ---------------------------------

    @property
    def delivered_count(self) -> int:
        return self.counts.delivered + self.counts.partial

    @property
    def returned_count(self) -> int:
        return self.counts.rto

    @property
    def terminal_count(self) -> int:
        return self.delivered_count + self.returned_count + self.cancelled_count

    @property
    def success_rate_basis_points(self) -> int | None:
        return rate_bps(self.delivered_count, self.terminal_count)


@dataclass(slots=True)
class CustomerPattern:
    customer_id: uuid.UUID
    name: str | None
    phone_masked: str | None
    counts: OutcomeCounts
    observations: list[Observation]


@dataclass(slots=True)
class PatternReport:
    since: date
    until: date
    days: int
    customers: list[CustomerPattern]


# --------------------------------------------------------------------------- #
# Pattern rules (pure)
# --------------------------------------------------------------------------- #

#: Size of the "recent" group for WORSENING / IMPROVING, and the minimum size
#: of the earlier group it is compared with.
_RECENT_GROUP: Final = 3
#: Deliveries that must precede a return run for RTO_AFTER_DELIVERIES.
_PRIOR_DELIVERIES: Final = 2


def observe(timeline: Sequence[tuple[ParcelOutcome, str | None]]) -> list[Observation]:
    """Patterns in one customer's completed parcels, oldest first.

    ``timeline`` holds ``(outcome, product_name)`` per completed parcel. Every
    rule is a count comparison the screen can restate in words.
    """
    completed = [outcome for outcome, _ in timeline if outcome in COMPLETED_OUTCOMES]
    found: list[Observation] = []

    rto_total = sum(1 for outcome in completed if outcome is _O.RTO)
    delivered_total = len(completed) - rto_total
    if rto_total >= 2:
        found.append(
            Observation(
                code=ObservationCode.REPEAT_RTO,
                parcel_count=len(completed),
                rto_count=rto_total,
                delivered_count=delivered_total,
            )
        )

    # A run of returns at the end, after earlier deliveries.
    run = 0
    for outcome in reversed(completed):
        if outcome is not _O.RTO:
            break
        run += 1
    before = completed[: len(completed) - run]
    delivered_before = sum(1 for outcome in before if outcome is not _O.RTO)
    if run >= 1 and delivered_before >= _PRIOR_DELIVERIES:
        found.append(
            Observation(
                code=ObservationCode.RTO_AFTER_DELIVERIES,
                parcel_count=run,
                rto_count=run,
                earlier_parcel_count=len(before),
                earlier_rto_count=len(before) - delivered_before,
            )
        )

    if len(completed) >= 2 * _RECENT_GROUP:
        recent, earlier = completed[-_RECENT_GROUP:], completed[:-_RECENT_GROUP]
        recent_rto = sum(1 for outcome in recent if outcome is _O.RTO)
        earlier_rto = sum(1 for outcome in earlier if outcome is _O.RTO)
        # Compared as rates, in integers: recent/3 vs earlier/n.
        recent_bps = rate_bps(recent_rto, len(recent)) or 0
        earlier_bps = rate_bps(earlier_rto, len(earlier)) or 0
        code = None
        # A move of at least one parcel in three, i.e. one-third.
        if recent_bps - earlier_bps >= 3_333:
            code = ObservationCode.WORSENING
        elif earlier_bps - recent_bps >= 3_333:
            code = ObservationCode.IMPROVING
        if code is not None:
            found.append(
                Observation(
                    code=code,
                    parcel_count=len(recent),
                    rto_count=recent_rto,
                    earlier_parcel_count=len(earlier),
                    earlier_rto_count=earlier_rto,
                )
            )

    per_product: dict[str, int] = {}
    for outcome, product in timeline:
        if outcome is _O.RTO and product:
            per_product[product] = per_product.get(product, 0) + 1
    for product, count in sorted(per_product.items(), key=lambda item: (-item[1], item[0])):
        if count >= 2:
            found.append(
                Observation(
                    code=ObservationCode.PRODUCT_REPEAT_RTO,
                    parcel_count=count,
                    rto_count=count,
                    product_name=product,
                )
            )
    return found


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


class RtoService:
    """Read-only aggregates over the shop's parcels. Writes nothing."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    # ---------------------------------------------------------- windows --

    @staticmethod
    def window(days: int, *, today: date | None = None) -> tuple[date, date, datetime]:
        """``days`` business days ending today, and the UTC instant they start."""
        until = today or business_date(at=utc_now())
        since = until - timedelta(days=days - 1)
        return since, until, _day_start(since)

    # ---------------------------------------------------------- summary --

    async def summary(self, *, days: int = 30, today: date | None = None) -> RtoSummary:
        since, until, start = self.window(days, today=today)
        spans = sorted({7, 30, 90, days})
        starts = {span: self.window(span, today=today)[2] for span in spans}
        settled = _settled_at()

        columns = [sa.func.sum(sa.case((settled >= starts[span], 1), else_=0)) for span in spans]
        rows = await self._db.execute(
            sa.select(Consignment.status, *columns)
            .where(
                Consignment.status.in_(statuses_for(*COMPLETED_OUTCOMES, _O.LOST)),
                settled >= min(starts.values()),
            )
            .group_by(Consignment.status)
        )
        per_span = {span: OutcomeCounts() for span in spans}
        for status, *counts in rows:
            for span, count in zip(spans, counts, strict=True):
                per_span[span].add(status, int(count or 0))

        open_now = await self._db.execute(
            sa.select(sa.func.count(Consignment.id)).where(
                Consignment.status.in_(statuses_for(_O.IN_TRANSIT))
            )
        )

        return RtoSummary(
            since=since,
            until=until,
            days=days,
            counts=per_span[days],
            open_now=int(open_now.scalar_one() or 0),
            cancelled_before_dispatch=await self._cancelled_before_dispatch(start),
            windows=[
                WindowCounts(
                    days=span, since=self.window(span, today=today)[0], counts=per_span[span]
                )
                for span in (7, 30, 90)
            ],
        )

    async def _cancelled_before_dispatch(self, start: datetime) -> int:
        dispatched = sa.exists().where(
            Consignment.order_id == Order.id,
            Consignment.status.in_(DISPATCHED_STATUSES),
        )
        result = await self._db.execute(
            sa.select(sa.func.count(Order.id)).where(
                Order.status == str(OrderStatus.CANCELLED),
                sa.func.coalesce(Order.cancelled_at, Order.updated_at, type_=TZDateTime()) >= start,
                ~dispatched,
            )
        )
        return int(result.scalar_one() or 0)

    # ------------------------------------------------------------ trend --

    async def trend(self, *, weeks: int = 12, today: date | None = None) -> list[TrendPoint]:
        """Completed parcels per 7-day block, oldest first, empty weeks kept."""
        until = today or business_date(at=utc_now())
        blocks: list[tuple[date, date, datetime, datetime]] = []
        for index in range(weeks - 1, -1, -1):
            end_day = until - timedelta(days=7 * index)
            start_day = end_day - timedelta(days=6)
            blocks.append(
                (start_day, end_day, _day_start(start_day), business_day_bounds(end_day)[1])
            )

        settled = _settled_at()
        columns = [
            sa.func.sum(sa.case(((settled >= lower) & (settled < upper), 1), else_=0))
            for _, _, lower, upper in blocks
        ]
        rows = await self._db.execute(
            sa.select(Consignment.status, *columns)
            .where(
                Consignment.status.in_(COMPLETED_STATUSES),
                settled >= blocks[0][2],
                settled < blocks[-1][3],
            )
            .group_by(Consignment.status)
        )
        points = [
            TrendPoint(week_start=b[0], week_end=b[1], counts=OutcomeCounts()) for b in blocks
        ]
        for status, *counts in rows:
            for point, count in zip(points, counts, strict=True):
                point.counts.add(status, int(count or 0))
        return points

    # --------------------------------------------------------- products --

    async def products(self, *, days: int = 90, today: date | None = None) -> list[ProductRto]:
        """Every product with a completed parcel in the window.

        A parcel with three products counts once for each of them, never three
        times for one. Catalogue lines group by product; free-text lines by
        the name typed on the order.
        """
        _, _, start = self.window(days, today=today)
        settled = _settled_at()
        name_key = sa.case(
            (OrderItem.product_id.is_(None), OrderItem.product_name), else_=sa.null()
        )
        line_total = OrderItem.unit_price_paisa * OrderItem.quantity - OrderItem.discount_paisa
        rows = await self._db.execute(
            sa.select(
                OrderItem.product_id,
                name_key,
                sa.func.max(OrderItem.product_name),
                Consignment.status,
                sa.func.count(sa.distinct(Consignment.id)),
                sa.func.coalesce(sa.func.sum(sa.case((line_total > 0, line_total), else_=0)), 0),
            )
            .join(OrderItem, OrderItem.order_id == Consignment.order_id)
            .where(Consignment.status.in_(COMPLETED_STATUSES), settled >= start)
            .group_by(OrderItem.product_id, name_key, Consignment.status)
        )
        found: dict[tuple[object, object], ProductRto] = {}
        for product_id, free_name, name, status, count, value in rows:
            key = (product_id, free_name)
            entry = found.setdefault(key, ProductRto(product_id=product_id, product_name=name))
            # The newest-looking name wins deterministically across statuses.
            entry.product_name = max(entry.product_name, name)
            entry.counts.add(status, int(count))
            if is_rto_status(status):
                entry.rto_value_paisa += int(value or 0)
        return list(found.values())

    # --------------------------------------------------------- couriers --

    async def couriers(self, *, days: int = 90, today: date | None = None) -> list[CourierRto]:
        """Per provider, over the window, plus last 30 days against the 30 before."""
        _, _, start = self.window(days, today=today)
        until = today or business_date(at=utc_now())
        recent_start = _day_start(until - timedelta(days=29))
        previous_start = _day_start(until - timedelta(days=59))
        settled = _settled_at()

        rows = await self._db.execute(
            sa.select(
                Consignment.provider,
                Consignment.status,
                sa.func.sum(sa.case((settled >= start, 1), else_=0)),
                sa.func.sum(sa.case((settled >= recent_start, 1), else_=0)),
                sa.func.sum(
                    sa.case(((settled >= previous_start) & (settled < recent_start), 1), else_=0)
                ),
            )
            .where(
                Consignment.status.in_(statuses_for(*COMPLETED_OUTCOMES, _O.LOST)),
                Consignment.provider.notin_(sorted(NOT_LIVE_PROVIDERS)),
                settled >= min(start, previous_start),
            )
            .group_by(Consignment.provider, Consignment.status)
        )
        found: dict[str, CourierRto] = {}
        for provider, status, in_window, recent, previous in rows:
            entry = found.setdefault(provider, CourierRto(provider=provider))
            entry.counts.add(status, int(in_window or 0))
            entry.recent.add(status, int(recent or 0))
            entry.previous.add(status, int(previous or 0))

        open_rows = await self._db.execute(
            sa.select(Consignment.provider, sa.func.count(Consignment.id))
            .where(
                Consignment.status.in_(statuses_for(_O.IN_TRANSIT)),
                Consignment.provider.notin_(sorted(NOT_LIVE_PROVIDERS)),
            )
            .group_by(Consignment.provider)
        )
        for provider, count in open_rows:
            found.setdefault(provider, CourierRto(provider=provider)).in_transit_now = int(count)

        return sorted(
            (entry for entry in found.values() if entry.counts.completed or entry.in_transit_now),
            key=lambda entry: (-entry.counts.completed, entry.provider),
        )

    # ------------------------------------------------------------ areas --

    async def areas(self, *, days: int = 90, today: date | None = None) -> AreaReport:
        """RTO by the district typed on the order, only when enough have one.

        District is free text with no canonical list, so the only tidying is
        trimming and case: "Dhaka " and "dhaka" are one row, "Dhaka City" is
        another. Sub-district area is never compared — it is too free-form to
        group honestly.
        """
        _, _, start = self.window(days, today=today)
        settled = _settled_at()
        key = sa.func.lower(sa.func.trim(Order.delivery_district))
        rows = await self._db.execute(
            sa.select(
                key,
                sa.func.max(sa.func.trim(Order.delivery_district)),
                Consignment.status,
                sa.func.count(Consignment.id),
            )
            .join(Order, Order.id == Consignment.order_id)
            .where(Consignment.status.in_(COMPLETED_STATUSES), settled >= start)
            .group_by(key, Consignment.status)
        )
        completed = 0
        with_area = 0
        found: dict[str, AreaRow] = {}
        for district_key, label, status, count in rows:
            completed += int(count)
            if not district_key:
                continue
            with_area += int(count)
            entry = found.setdefault(district_key, AreaRow(label=label or district_key))
            entry.counts.add(status, int(count))

        coverage = rate_bps(with_area, completed) or 0
        reliable = completed >= 2 * MIN_RATE_SAMPLE and coverage >= AREA_MIN_COVERAGE_BPS
        return AreaReport(
            status="ACTIVE" if reliable else "DATA_NOT_RELIABLE",
            level="district",
            completed=completed,
            with_area=with_area,
            rows=list(found.values()) if reliable else [],
        )

    # --------------------------------------------------------- customers --

    async def customer_history(self, customer_id: uuid.UUID) -> CustomerHistory:
        """Everything this shop knows about one customer's parcels."""
        history = CustomerHistory()

        orders = (
            await self._db.execute(
                sa.select(
                    sa.func.count(Order.id),
                    sa.func.min(Order.created_at),
                    sa.func.max(Order.created_at),
                ).where(Order.customer_id == customer_id)
            )
        ).one()
        history.order_count = int(orders[0] or 0)
        history.first_order_at, history.last_order_at = orders[1], orders[2]
        cancelled = await self._db.execute(self._cancelled_query([customer_id]))
        history.cancelled_count = sum(int(count) for _, count in cancelled)

        settled = _settled_at()
        rows = await self._db.execute(
            sa.select(
                Consignment.id,
                Consignment.status,
                Consignment.provider,
                settled,
                Order.order_number,
            )
            .join(Order, Order.id == Consignment.order_id)
            .where(
                Order.customer_id == customer_id,
                Consignment.status.in_(DISPATCHED_STATUSES),
            )
            .order_by(settled.desc(), Consignment.id.desc())
            .limit(_CUSTOMER_PARCEL_CAP)
        )
        parcels = rows.all()
        for _, status, _, _, _ in parcels:
            history.counts.add(status)
        history.recent = [
            ParcelEvent(
                order_number=order_number,
                outcome=classify(status),
                status=status,
                provider=provider,
                at=at,
            )
            for _, status, provider, at, order_number in parcels[:10]
        ]

        products = await self._rto_products([row[0] for row in parcels if is_rto_status(row[1])])
        timeline = [
            (classify(status), products.get(consignment_id))
            for consignment_id, status, _, _, _ in reversed(parcels)
        ]
        history.observations = _observe_with_products(timeline)
        return history

    async def customer_counts(
        self, customer_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, CustomerHistory]:
        """Counts for a page of customers: two grouped queries, never one per row."""
        ids = list(dict.fromkeys(customer_ids))
        found = {customer_id: CustomerHistory() for customer_id in ids}
        if not ids:
            return found

        rows = await self._db.execute(
            sa.select(Order.customer_id, Consignment.status, sa.func.count(Consignment.id))
            .join(Order, Order.id == Consignment.order_id)
            .where(Order.customer_id.in_(ids), Consignment.status.in_(DISPATCHED_STATUSES))
            .group_by(Order.customer_id, Consignment.status)
        )
        for customer_id, status, count in rows:
            found[customer_id].counts.add(status, int(count))

        order_rows = await self._db.execute(
            sa.select(
                Order.customer_id,
                sa.func.count(Order.id),
                sa.func.min(Order.created_at),
                sa.func.max(Order.created_at),
            )
            .where(Order.customer_id.in_(ids))
            .group_by(Order.customer_id)
        )
        for customer_id, count, first, last in order_rows:
            entry = found[customer_id]
            entry.order_count = int(count)
            entry.first_order_at, entry.last_order_at = first, last

        cancelled = await self._db.execute(self._cancelled_query(ids))
        for customer_id, count in cancelled:
            found[customer_id].cancelled_count = int(count)
        return found

    def _cancelled_query(
        self, customer_ids: list[uuid.UUID]
    ) -> sa.Select[tuple[uuid.UUID | None, int]]:
        dispatched = sa.exists().where(
            Consignment.order_id == Order.id,
            Consignment.status.in_(DISPATCHED_STATUSES),
        )
        return (
            sa.select(Order.customer_id, sa.func.count(Order.id))
            .where(
                Order.customer_id.in_(customer_ids),
                Order.status == str(OrderStatus.CANCELLED),
                ~dispatched,
            )
            .group_by(Order.customer_id)
        )

    async def _rto_products(self, consignment_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
        """Product names inside each returned parcel, for product/customer repeats."""
        if not consignment_ids:
            return {}
        rows = await self._db.execute(
            sa.select(Consignment.id, OrderItem.product_name)
            .join(OrderItem, OrderItem.order_id == Consignment.order_id)
            .where(Consignment.id.in_(consignment_ids))
            .order_by(Consignment.id, OrderItem.position)
        )
        found: dict[uuid.UUID, list[str]] = {}
        for consignment_id, name in rows:
            names = found.setdefault(consignment_id, [])
            if name not in names:
                names.append(name)
        return found

    # --------------------------------------------------------- patterns --

    async def patterns(
        self, *, days: int = 90, limit: int = 20, today: date | None = None
    ) -> PatternReport:
        """Customers whose parcels show a repeat pattern, worst first.

        Candidates are customers with at least one RTO in the window; their
        full completed history (capped) is then read in one query and the pure
        rules in :func:`observe` decide what is true.
        """
        since, until, start = self.window(days, today=today)
        settled = _settled_at()
        rto_count = sa.func.count(Consignment.id)
        candidates = (
            await self._db.execute(
                sa.select(Order.customer_id, rto_count)
                .join(Order, Order.id == Consignment.order_id)
                .where(
                    Order.customer_id.is_not(None),
                    Consignment.status.in_(RTO_STATUSES),
                    settled >= start,
                )
                .group_by(Order.customer_id)
                .order_by(rto_count.desc(), Order.customer_id)
                .limit(_PATTERN_CANDIDATES)
            )
        ).all()
        ids = [row[0] for row in candidates]
        if not ids:
            return PatternReport(since=since, until=until, days=days, customers=[])

        rows = await self._db.execute(
            sa.select(Order.customer_id, Consignment.id, Consignment.status)
            .join(Order, Order.id == Consignment.order_id)
            .where(Order.customer_id.in_(ids), Consignment.status.in_(COMPLETED_STATUSES))
            .order_by(Order.customer_id, settled, Consignment.id)
        )
        timelines: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
        for customer_id, consignment_id, status in rows:
            timelines.setdefault(customer_id, []).append((consignment_id, status))

        rto_ids = [
            cid for line in timelines.values() for cid, status in line if is_rto_status(status)
        ]
        products = await self._rto_products(rto_ids)

        people = await self._db.execute(
            sa.select(Customer.id, Customer.name, Customer.phone_masked).where(Customer.id.in_(ids))
        )
        who = {row[0]: (row[1], row[2]) for row in people}

        report: list[CustomerPattern] = []
        for customer_id in ids:
            line = timelines.get(customer_id, [])
            timeline = [(classify(status), products.get(cid)) for cid, status in line]
            observations = _observe_with_products(timeline)
            if not observations:
                continue
            counts = OutcomeCounts()
            for _, status in line:
                counts.add(status)
            name, masked = who.get(customer_id, (None, None))
            report.append(
                CustomerPattern(
                    customer_id=customer_id,
                    name=name,
                    phone_masked=masked,
                    counts=counts,
                    observations=observations,
                )
            )
        report.sort(key=lambda row: (-row.counts.rto, -row.counts.completed, str(row.customer_id)))
        return PatternReport(since=since, until=until, days=days, customers=report[:limit])


def _observe_with_products(
    timeline: list[tuple[ParcelOutcome, list[str] | None]],
) -> list[Observation]:
    """Run :func:`observe` per parcel, then per product inside returned parcels.

    The outcome rules need one entry per parcel; the product rule needs every
    product inside a returned parcel, so it runs on a flattened copy and only
    its PRODUCT_REPEAT_RTO findings are kept.
    """
    found = observe([(outcome, None) for outcome, _ in timeline])
    per_product = [(outcome, name) for outcome, names in timeline if names for name in names]
    found.extend(
        obs for obs in observe(per_product) if obs.code is ObservationCode.PRODUCT_REPEAT_RTO
    )
    return found
