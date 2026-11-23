"""Reading and changing a shop's delivery rules (V4 WP0.4)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.couriers.credentials import CREDENTIAL_SPECS
from app.delivery_rules.models import DEFAULTS, ShopDeliveryRules
from app.orders.zones import DeliveryZone

__all__ = ["DeliveryRules", "DeliveryRulesService"]

_HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_ZONE_FIELD = {
    DeliveryZone.INSIDE_DHAKA: "inside_dhaka_charge_paisa",
    DeliveryZone.DHAKA_SUBURB: "dhaka_suburb_charge_paisa",
    DeliveryZone.OUTSIDE_DHAKA: "outside_dhaka_charge_paisa",
}


@dataclass(frozen=True, slots=True)
class DeliveryRules:
    """The effective rules: the shop's row, or the defaults when it has none."""

    values: dict[str, Any]
    #: ``False`` while the shop has never saved rules (defaults in effect).
    configured: bool

    def zone_charge(self, zone: DeliveryZone | None) -> int | None:
        """The shop's charge for a zone; ``None`` when the zone is unknown or unset."""
        if zone is None or not self.configured:
            return None
        charge = int(self.values[_ZONE_FIELD[zone]])
        return charge if charge > 0 else None

    def __getitem__(self, key: str) -> Any:
        return self.values[key]


def _invalid(message: str, field: str) -> AppError:
    return AppError(message, code=ErrorCode.VALIDATION_ERROR, details={"field": field})


class DeliveryRulesService:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def _row(self, tenant_id: uuid.UUID) -> ShopDeliveryRules | None:
        stmt = sa.select(ShopDeliveryRules).where(ShopDeliveryRules.tenant_id == tenant_id)
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def get(self, tenant_id: uuid.UUID) -> DeliveryRules:
        row = await self._row(tenant_id)
        if row is None:
            return DeliveryRules(values=dict(DEFAULTS), configured=False)
        return DeliveryRules(values={key: getattr(row, key) for key in DEFAULTS}, configured=True)

    async def update(self, tenant_id: uuid.UUID, changes: dict[str, Any]) -> DeliveryRules:
        unknown = set(changes) - set(DEFAULTS)
        if unknown:
            raise _invalid("Unknown delivery rule", sorted(unknown)[0])
        current = await self.get(tenant_id)
        merged = {**current.values, **changes}
        self._validate(merged)

        row = await self._row(tenant_id)
        if row is None:
            row = ShopDeliveryRules(tenant_id=tenant_id, **merged)
            self._db.add(row)
        else:
            for key, value in changes.items():
                setattr(row, key, value)
        await self._db.flush()
        return DeliveryRules(values={key: getattr(row, key) for key in DEFAULTS}, configured=True)

    @staticmethod
    def _validate(values: dict[str, Any]) -> None:
        courier = values.get("default_courier")
        if courier is not None and courier not in CREDENTIAL_SPECS:
            raise _invalid("Unknown courier", "default_courier")
        for key in ("quiet_start", "quiet_end"):
            if not isinstance(values[key], str) or not _HHMM.match(values[key]):
                raise _invalid("Use 24-hour HH:MM", key)
        if values["call_everyone"] and values["call_nobody"]:
            raise _invalid("Call everyone and call nobody cannot both be on", "call_nobody")
