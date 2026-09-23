"""RedX courier integration.

Implemented against RedX's own developer documentation
(``https://redx.com.bd/developer-api/``), transcribed in
:mod:`app.couriers.redx.contract` and normalized in
``docs/providers/redx/CONTRACT.md``. The capability split follows that
documentation exactly: parcels are created against a delivery area the seller
picks from RedX's list, looked up, tracked and cancelled; areas, pickup stores
and a charge quote are read; status callbacks are verified by a per-shop token
in the callback URL. Bulk create, returns, balance and payouts are not in
RedX's API and are reported unavailable.
"""

from __future__ import annotations

from app.couriers.redx.adapter import PROVIDER, SUPPORTED_CAPABILITIES, RedxAdapter
from app.couriers.redx.client import RedxClient, RedxConfig, RedxCredentials

__all__ = [
    "PROVIDER",
    "SUPPORTED_CAPABILITIES",
    "RedxAdapter",
    "RedxClient",
    "RedxConfig",
    "RedxCredentials",
]
