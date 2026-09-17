"""Pathao Courier integration.

Implemented from Pathao's own published integration source (the WooCommerce
plugin released by ``pathao-eng``), normalized in
``docs/providers/pathao/CONTRACT.md``. The capability split follows that source
exactly: Pathao creates parcels and reports their progress by webhook, and
publishes no status-lookup, quote, cancel, return, balance or payout endpoint.

Those absences are reported as :class:`~app.couriers.adapter.Unavailable` with a
reason, never worked around with a path taken from a community SDK.
"""

from app.couriers.pathao.adapter import PROVIDER, PathaoAdapter
from app.couriers.pathao.client import PathaoClient, PathaoConfig, PathaoCredentials

__all__ = [
    "PROVIDER",
    "PathaoAdapter",
    "PathaoClient",
    "PathaoConfig",
    "PathaoCredentials",
]
