"""The demand forecast, as plain arithmetic (V3.6).

Deliberately simple and explainable. Every number a seller sees can be
recomputed by hand from the stock ledger:

* **Demand** is units booked out net of cancellations (``BOOKED_DECREMENT``
  minus ``CANCEL_RESTORE``), the same count the inventory insights use.
* Only **in-stock days** count. A day the item could not be sold says nothing
  about how much it would have sold, so averaging it in as zero would make
  every stock-out look like falling demand.
* **Rate** = half the recent average (last 14 in-stock days, when at least 7
  exist) plus half the long average (up to 56 days). Recent enough to follow a
  change, long enough not to chase one good day.
* **Safety stock** = 1.65 × daily variability × √lead time (about a 95%
  service level for normally distributed demand). Standard, and labelled.
* **Reorder point** = rate × lead time + safety. An item is **at risk** when
  what is on hand plus on order is at or below it.
* **Suggested quantity** = rate × (lead time + cover days) + safety − on hand
  − on order, never below zero. A suggestion for a person; nothing is ordered.

Too little history is an answer too: fewer than 14 in-stock days or 5 units
and the forecast is ``INSUFFICIENT`` with no numbers, not a guess.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

HISTORY_DAYS = 56
RECENT_DAYS = 14
MIN_RECENT_IN_STOCK_DAYS = 7
MIN_IN_STOCK_DAYS = 14
MIN_UNITS = 5
HIGH_IN_STOCK_DAYS = 28
HIGH_UNITS = 20
#: z for a ~95% cycle service level.
SERVICE_Z = 1.65
DEFAULT_COVER_DAYS = 14
DEFAULT_LEAD_TIME_DAYS = 7
#: How far ahead a stored snapshot predicts, and so when it can be scored.
HORIZON_DAYS = 28


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    INSUFFICIENT = "INSUFFICIENT"


class LeadTimeSource(StrEnum):
    #: Median of this supplier's own ordered → first-received days.
    OBSERVED = "OBSERVED"
    #: What the seller entered on the supplier.
    SUPPLIER = "SUPPLIER"
    #: Nothing known: a stated default, shown as an assumption.
    DEFAULT = "DEFAULT"


@dataclass(frozen=True, slots=True)
class History:
    """Oldest day first. ``in_stock[i]``: the item could be sold on day i."""

    daily_units: tuple[int, ...]
    in_stock: tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class Forecast:
    confidence: Confidence
    in_stock_days: int
    units: int
    #: Units per day; ``None`` when the history is insufficient.
    rate: float | None
    recent_rate: float | None
    long_rate: float | None
    daily_std: float | None
    lead_time_days: int
    cover_days: int
    on_hand: int
    incoming: int
    safety_stock: int | None
    reorder_point: int | None
    suggested_quantity: int | None
    days_of_cover: int | None
    stockout_on: date | None
    at_risk: bool

    @property
    def rate_milli(self) -> int | None:
        return None if self.rate is None else round(self.rate * 1000)

    def predicted_units(self, days: int = HORIZON_DAYS) -> int | None:
        return None if self.rate is None else round(self.rate * days)


def _mean(values: list[int]) -> float:
    return sum(values) / len(values)


def forecast(
    history: History,
    *,
    today: date,
    on_hand: int,
    incoming: int,
    lead_time_days: int,
    cover_days: int = DEFAULT_COVER_DAYS,
) -> Forecast:
    sold = [u for u, ok in zip(history.daily_units, history.in_stock, strict=True) if ok]
    n, units = len(sold), sum(sold)
    on_hand = max(0, on_hand)
    if n < MIN_IN_STOCK_DAYS or units < MIN_UNITS:
        return Forecast(
            confidence=Confidence.INSUFFICIENT,
            in_stock_days=n,
            units=units,
            rate=None,
            recent_rate=None,
            long_rate=None,
            daily_std=None,
            lead_time_days=lead_time_days,
            cover_days=cover_days,
            on_hand=on_hand,
            incoming=incoming,
            safety_stock=None,
            reorder_point=None,
            suggested_quantity=None,
            days_of_cover=None,
            stockout_on=None,
            at_risk=False,
        )

    long_rate = _mean(sold)
    recent = [
        u
        for u, ok in zip(
            history.daily_units[-RECENT_DAYS:], history.in_stock[-RECENT_DAYS:], strict=True
        )
        if ok
    ]
    recent_rate = _mean(recent) if len(recent) >= MIN_RECENT_IN_STOCK_DAYS else long_rate
    rate = 0.5 * recent_rate + 0.5 * long_rate
    std = math.sqrt(sum((u - long_rate) ** 2 for u in sold) / n)

    safety = math.ceil(SERVICE_Z * std * math.sqrt(lead_time_days))
    reorder_point = math.ceil(rate * lead_time_days) + safety
    target = math.ceil(rate * (lead_time_days + cover_days)) + safety
    suggested = max(0, target - on_hand - incoming)
    days_of_cover: int | None = None
    stockout_on: date | None = None
    if rate > 0:
        days_of_cover = math.floor(on_hand / rate)
        stockout_on = today + timedelta(days=days_of_cover)
    return Forecast(
        confidence=(
            Confidence.HIGH
            if n >= HIGH_IN_STOCK_DAYS and units >= HIGH_UNITS
            else Confidence.MEDIUM
        ),
        in_stock_days=n,
        units=units,
        rate=rate,
        recent_rate=recent_rate,
        long_rate=long_rate,
        daily_std=std,
        lead_time_days=lead_time_days,
        cover_days=cover_days,
        on_hand=on_hand,
        incoming=incoming,
        safety_stock=safety,
        reorder_point=reorder_point,
        suggested_quantity=suggested,
        days_of_cover=days_of_cover,
        stockout_on=stockout_on,
        at_risk=rate > 0 and on_hand + incoming <= reorder_point and suggested > 0,
    )


def median_days(samples: list[int]) -> int:
    ordered = sorted(samples)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return math.ceil((ordered[mid - 1] + ordered[mid]) / 2)


def wape(pairs: list[tuple[int, int]]) -> float | None:
    """Weighted absolute percentage error of (predicted, actual) pairs.

    Σ|predicted − actual| / Σ actual. Weighted by volume, so a slow item missing
    by two units does not swamp the score, and defined when some items sold
    nothing. ``None`` when nothing sold at all.
    """
    actual = sum(a for _, a in pairs)
    if actual <= 0:
        return None
    return sum(abs(p - a) for p, a in pairs) / actual
