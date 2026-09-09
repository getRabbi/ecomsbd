"""Portable column types.

PostgreSQL is the production database (master spec section 29). These type
aliases keep the schema expressible on SQLite as well, which is what lets the
migration-sanity and tenant-isolation suites run in CI and on a developer laptop
without a database server. The PostgreSQL rendering is always the richer one:
native ``uuid``, ``jsonb`` and ``timestamptz``.

Two rules from the spec are encoded here:

*   money is ``BIGINT`` paisa, never a float (sections 17.5, 32);
*   percentages are basis points, never floats (section 32).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

__all__ = [
    "GUID",
    "BasisPoints",
    "JSONColumn",
    "Paisa",
    "TZDateTime",
]

#: UUID primary keys. Native ``uuid`` on PostgreSQL, ``CHAR(32)`` elsewhere.
GUID = sa.Uuid(as_uuid=True)

#: Structured metadata. ``jsonb`` on PostgreSQL for indexing and containment queries.
JSONColumn = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

#: Integer paisa. Master spec section 32: ``amount_paisa BIGINT``.
#:
#: 2^63-1 paisa is roughly 92 quadrillion BDT, so overflow is not a practical
#: concern, while every intermediate value stays exact.
Paisa = sa.BigInteger()

#: Percentages as basis points (100 bp = 1%). Master spec section 32.
BasisPoints = sa.Integer()


class TZDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC timestamps on every backend.

    PostgreSQL stores ``timestamptz`` and returns aware datetimes. SQLite has no
    timezone concept and hands back naive values, which silently compare wrong
    against aware ones. This decorator normalises both directions so COD aging
    and settlement windows cannot be off by the local UTC offset.
    """

    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Refusing to store a naive datetime; use app.core.clock.utc_now()")
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):  # some SQLite drivers hand back text
            value = datetime.fromisoformat(value)
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
