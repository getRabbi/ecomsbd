"""Carrybee courier integration (V4 WP7.2).

Implemented against Carrybee's own developer reference
(https://merchant.carrybee.com/webhook/documentation, read 2026-11-23),
normalized in ``docs/providers/carrybee/CONTRACT.md``. Credentials check, single
create, status lookup, cancel, pickup stores and a shared-secret webhook; the
rest is reported unavailable. Behind the ``carrybee_enabled`` flag (default off).
"""

from __future__ import annotations

from app.couriers.carrybee.adapter import PROVIDER, SUPPORTED_CAPABILITIES, CarrybeeAdapter
from app.couriers.carrybee.client import CarrybeeClient, CarrybeeConfig, CarrybeeCredentials

__all__ = [
    "PROVIDER",
    "SUPPORTED_CAPABILITIES",
    "CarrybeeAdapter",
    "CarrybeeClient",
    "CarrybeeConfig",
    "CarrybeeCredentials",
]
