"""Intake rule sets and per-shop intake settings (V4 WP1.2).

``intake_rule_sets`` is **global** (rule L5): detection rules, the app package
list and extension selectors are one versioned config that every device reads,
so a UI change at Meta/WhatsApp is fixed by publishing a version, not by an
app release. Operators manage it; exactly one version is active.

``shop_intake_settings`` is the shop's own layer on top: the server's
``covered_packages`` (apps an official connection already delivers, so devices
skip them) and the seller's per-app toggles. A shop without a row uses the
rule set's defaults; reading never creates one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import JSONColumn, TZDateTime

__all__ = ["IntakeRuleSet", "ShopIntakeSettings"]


class IntakeRuleSet(Base, PrimaryKeyMixin, TimestampMixin):
    """One published version of the device rules. Never edited after insert,
    apart from the activation columns."""

    __tablename__ = "intake_rule_sets"
    __table_args__ = (
        sa.UniqueConstraint("version", name="uq_intake_rule_sets_version"),
        sa.CheckConstraint("version >= 1", name="ck_intake_rule_sets_version"),
    )

    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False)
    active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    #: The operator label (admin tokens may have no user id); ``migration`` for v1.
    created_by: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    note: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    activated_by: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)


class ShopIntakeSettings(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "shop_intake_settings"
    __table_args__ = (sa.UniqueConstraint("tenant_id", name="uq_shop_intake_settings_tenant"),)

    #: Packages an official connection covers; set by the server only.
    covered_packages: Mapped[list[str]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: The seller's choices, ``{package: bool}``; a missing key means "rule default".
    package_toggles: Mapped[dict[str, bool]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
