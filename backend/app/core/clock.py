"""Time and business-day semantics.

Master spec section 69:

*   every timestamp is stored in UTC;
*   a seller's *business date* is an explicit ``DATE`` in the tenant timezone
    (Asia/Dhaka by default), never an inferred UTC date;
*   the Friday summary means Friday in the tenant timezone;
*   COD aging and subscription expiry are never computed from a device clock.

Dhaka is UTC+06:00 with no daylight saving, so a UTC timestamp late in the day
already belongs to the *next* Bangladeshi business date. Deriving the date by
truncating the UTC timestamp would silently misfile roughly a quarter of every
day's orders, which is why this module exists rather than a bare ``.date()``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DHAKA = ZoneInfo("Asia/Dhaka")

#: ``date.weekday()`` value for Friday, used by the weekly seller summary.
FRIDAY = 4

__all__ = [
    "DHAKA",
    "FRIDAY",
    "business_date",
    "business_day_bounds",
    "ensure_utc",
    "next_weekday_at",
    "tenant_now",
    "utc_now",
]


def utc_now() -> datetime:
    """Current instant, timezone-aware, in UTC. The only clock the domain uses."""
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Coerce a datetime to aware UTC.

    Naive values are assumed to already be UTC: every column in this schema is
    written through :func:`utc_now`, but SQLite hands datetimes back naive.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _zone(timezone_name: str | None) -> ZoneInfo:
    if timezone_name is None:
        return DHAKA
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        return DHAKA


def tenant_now(timezone_name: str | None = None, *, at: datetime | None = None) -> datetime:
    """Wall-clock time in the tenant's timezone."""
    moment = ensure_utc(at) if at is not None else utc_now()
    return moment.astimezone(_zone(timezone_name))


def business_date(timezone_name: str | None = None, *, at: datetime | None = None) -> date:
    """The seller's business date for an instant.

    This is the value stored in ``business_date`` columns on ledger entries and
    daily metrics.
    """
    return tenant_now(timezone_name, at=at).date()


def business_day_bounds(day: date, timezone_name: str | None = None) -> tuple[datetime, datetime]:
    """Half-open UTC interval ``[start, end)`` covering one tenant business day."""
    zone = _zone(timezone_name)
    start_local = datetime.combine(day, time.min, tzinfo=zone)
    end_local = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def next_weekday_at(
    weekday: int,
    at_hour: int,
    *,
    timezone_name: str | None = None,
    after: datetime | None = None,
) -> datetime:
    """Next occurrence of ``weekday`` at ``at_hour`` local time, returned in UTC.

    Used for the Friday 18:00 Asia/Dhaka seller summary (master spec section 42).
    """
    zone = _zone(timezone_name)
    reference = (ensure_utc(after) if after is not None else utc_now()).astimezone(zone)

    days_ahead = (weekday - reference.weekday()) % 7
    candidate = datetime.combine(
        reference.date() + timedelta(days=days_ahead),
        time(hour=at_hour),
        tzinfo=zone,
    )
    if candidate <= reference:
        candidate += timedelta(days=7)
    return candidate.astimezone(UTC)
