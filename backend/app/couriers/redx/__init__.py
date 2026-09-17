"""RedX courier integration — registered, and supporting nothing yet.

RedX has published no API documentation that ecomsbd can verify against, so the
adapter reports every capability as unavailable with a reason and no RedX call
is ever made. :mod:`app.couriers.redx.contract` records what was checked, when,
and why agreement between community packages is not promoted to a contract.

``REDX_API_DOCUMENTATION_REQUIRED`` is the blocker. The request to send RedX is
``docs/providers/redx/CONTRACT_REQUIRED.md``.
"""

from app.couriers.redx.adapter import PROVIDER, SUPPORTED_CAPABILITIES, RedxAdapter
from app.couriers.redx.contract import CONTRACT_BLOCKER, REQUIRED_CONTRACT_ITEMS

__all__ = [
    "CONTRACT_BLOCKER",
    "PROVIDER",
    "REQUIRED_CONTRACT_ITEMS",
    "SUPPORTED_CAPABILITIES",
    "RedxAdapter",
]
