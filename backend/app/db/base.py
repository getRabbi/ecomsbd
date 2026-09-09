"""Declarative base and shared model mixins.

Master spec section 31: every tenant-owned table carries ``tenant_id``,
``created_at``, ``updated_at``, and soft delete where appropriate.

:class:`TenantOwned` is the marker the tenancy guard keys off. Inheriting it is
the *only* thing a feature module has to do to get automatic tenant filtering
and cross-tenant write rejection — see :mod:`app.db.tenancy`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.clock import utc_now
from app.core.ids import new_id
from app.db.types import GUID, TZDateTime

__all__ = [
    "Base",
    "PrimaryKeyMixin",
    "SoftDeleteMixin",
    "TenantOwned",
    "TimestampMixin",
    "metadata",
]

#: Deterministic constraint names. Alembic autogenerate needs these to emit
#: stable, reversible migrations rather than database-assigned names.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base for every model."""

    metadata = metadata

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        identifier = getattr(self, "id", None)
        return f"<{type(self).__name__} id={identifier}>"


class PrimaryKeyMixin:
    """UUIDv7 primary key (master spec section 70)."""

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_id)


class TimestampMixin:
    """Creation and update timestamps, always UTC."""

    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class SoftDeleteMixin:
    """Soft deletion for business records.

    Financial and courier records are retained for audit reasons even when a
    seller removes them from their working view (master spec section 100).
    """

    deleted_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True, default=None)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class TenantOwned:
    """Marker mixin for tenant-scoped rows.

    Any model inheriting this is automatically:

    *   filtered to the ambient tenant on every ORM ``SELECT``;
    *   stamped with the ambient tenant on ``INSERT``;
    *   rejected on ``UPDATE``/``DELETE`` if it belongs to another tenant.

    Enforcement lives in :mod:`app.db.tenancy`, not in each repository, so
    isolation does not depend on every developer remembering to add a filter.
    """

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


def as_dict(instance: Any) -> dict[str, Any]:
    """Column values of a mapped instance. Used by audit and diagnostics."""
    mapper = sa.inspect(type(instance))
    return {column.key: getattr(instance, column.key) for column in mapper.columns}
