"""Steadfast Courier V1 integration.

Implemented strictly against the operator-supplied V1 documentation, a redacted
copy of which lives in ``docs/providers/steadfast/``. The normalized reading of
that document is ``docs/providers/steadfast/CONTRACT.md``, and
:mod:`app.couriers.steadfast.contract` is that reading as code.

Layering, outermost first::

    adapter.py     CourierAdapter implementation; the only thing services see
    client.py      typed calls, retry policy, body interpretation
    transport.py   HTTP, timeouts, pooling, reached-provider determination
    contract.py    endpoints, fields, statuses — transcribed, never inferred
    dto.py         request/response models, strict and tolerant halves
    mapping.py     provider status -> ecomsbd consignment state
    errors.py      provider failure taxonomy

No module below ``adapter.py`` imports anything from the ecomsbd domain except
value types, and no module above it knows a Steadfast field name.
"""

from app.couriers.steadfast.adapter import SteadfastAdapter, to_provider_phone
from app.couriers.steadfast.client import (
    StatusLookupKind,
    SteadfastClient,
    SteadfastConfig,
    SteadfastCredentials,
)
from app.couriers.steadfast.contract import (
    DEFAULT_BASE_URL,
    DOCUMENTATION_VERSION,
    Endpoint,
    ReturnRequestStatus,
    SteadfastDeliveryStatus,
)
from app.couriers.steadfast.errors import SteadfastError, SteadfastErrorKind
from app.couriers.steadfast.mapping import map_delivery_status
from app.couriers.steadfast.transport import (
    FakeSteadfastTransport,
    HttpxSteadfastTransport,
    TransportTimeouts,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "DOCUMENTATION_VERSION",
    "Endpoint",
    "FakeSteadfastTransport",
    "HttpxSteadfastTransport",
    "ReturnRequestStatus",
    "StatusLookupKind",
    "SteadfastAdapter",
    "SteadfastClient",
    "SteadfastConfig",
    "SteadfastCredentials",
    "SteadfastDeliveryStatus",
    "SteadfastError",
    "SteadfastErrorKind",
    "TransportTimeouts",
    "map_delivery_status",
    "to_provider_phone",
]
