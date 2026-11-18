"""``shop_delivery_rules``: one row per shop.

Zone charges feed the COD calculator; the thresholds, quiet hours, daily cap
and call switches are read by the confirmation policy (WP2.2) and Autopilot
(WP4). A shop without a row uses :data:`DEFAULTS`; reading never creates one.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import Paisa

__all__ = ["DEFAULTS", "ShopDeliveryRules"]

#: Spec WP0.4 defaults. Zone charges default to 0 ("not set") so nothing is
#: added to a COD until the seller enters their own charges.
DEFAULTS: dict[str, Any] = {
    "inside_dhaka_charge_paisa": 0,
    "dhaka_suburb_charge_paisa": 0,
    "outside_dhaka_charge_paisa": 0,
    "default_courier": None,
    "max_auto_cod_paisa": 500_000,
    "risk_low_bps": 5000,
    "trusted_network_bps": 8500,
    "trusted_repeat_min": 2,
    "quiet_start": "21:00",
    "quiet_end": "09:00",
    "autopilot_daily_cap": 30,
    "call_everyone": False,
    "call_nobody": False,
}


class ShopDeliveryRules(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "shop_delivery_rules"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", name="uq_shop_delivery_rules_tenant"),
        sa.CheckConstraint(
            "inside_dhaka_charge_paisa >= 0 AND dhaka_suburb_charge_paisa >= 0"
            " AND outside_dhaka_charge_paisa >= 0",
            name="ck_shop_delivery_rules_charges",
        ),
        sa.CheckConstraint("max_auto_cod_paisa >= 0", name="ck_shop_delivery_rules_max_cod"),
        sa.CheckConstraint(
            "risk_low_bps BETWEEN 0 AND 10000 AND trusted_network_bps BETWEEN 0 AND 10000",
            name="ck_shop_delivery_rules_bps",
        ),
        sa.CheckConstraint(
            "trusted_repeat_min >= 0 AND autopilot_daily_cap >= 0",
            name="ck_shop_delivery_rules_counts",
        ),
        sa.CheckConstraint(
            "NOT (call_everyone AND call_nobody)", name="ck_shop_delivery_rules_call_switches"
        ),
    )

    inside_dhaka_charge_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    dhaka_suburb_charge_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    outside_dhaka_charge_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    #: A courier provider key (``steadfast``, ``pathao``, ``redx``) or ``None``.
    default_courier: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    max_auto_cod_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=500_000)
    risk_low_bps: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=5000)
    trusted_network_bps: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=8500)
    trusted_repeat_min: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=2)
    #: Asia/Dhaka wall-clock ``HH:MM``; the window may wrap midnight.
    quiet_start: Mapped[str] = mapped_column(sa.String(5), nullable=False, default="21:00")
    quiet_end: Mapped[str] = mapped_column(sa.String(5), nullable=False, default="09:00")
    autopilot_daily_cap: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=30)
    call_everyone: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    call_nobody: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
