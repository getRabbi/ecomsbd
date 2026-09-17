"""Typed reads of Pathao responses.

Two kinds of parsing live here and they are kept apart on purpose:

*   **Named fields, verified.** ``access_token``, ``consignment_id``,
    ``delivery_fee``, ``store_id``/``store_name``, ``city_id``/``city_name``,
    ``zone_id``/``zone_name``, ``area_id``/``area_name`` are read by those exact
    names because Pathao's own published integration reads them by those exact
    names.

*   **Envelope shape, tolerant.** Pathao wraps list payloads inconsistently —
    its own JavaScript reaches through ``data``, ``data.data`` and
    ``data.data.data`` for different endpoints — and publishes no schema for
    the envelope. Rather than guess one depth and break on the others,
    :func:`extract_records` walks down to the first list of objects it finds.
    Every parsed object also keeps its ``raw`` dict, so a field this module does
    not name is never lost.

The raw body is preserved on every DTO. A provider field we do not understand
today is evidence tomorrow, and discarding it at parse time is how an
integration becomes undebuggable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Area",
    "City",
    "CreateOrderResponse",
    "PathaoStore",
    "TokenResponse",
    "Zone",
    "extract_records",
    "parse_area_list",
    "parse_city_list",
    "parse_create_order",
    "parse_stores",
    "parse_token",
    "parse_zone_list",
]


def _as_dict(body: Any) -> dict[str, Any]:
    return body if isinstance(body, dict) else {}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _decimal_like(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def extract_records(body: Any, *, max_depth: int = 4) -> list[dict[str, Any]]:
    """Find the list of objects inside a Pathao list response.

    Pathao nests list payloads at different depths per endpoint and documents
    none of them, so this descends through ``data`` keys until it reaches a
    list of dicts. Bounded by ``max_depth`` so a cyclic or pathological body
    cannot spin.

    Returns an empty list rather than raising: an endpoint that returns nothing
    useful is a capability that reports itself unavailable, not a crash.
    """
    seen = 0
    current: Any = body
    while seen < max_depth:
        if isinstance(current, list):
            return [item for item in current if isinstance(item, dict)]
        if not isinstance(current, dict):
            return []
        if "data" in current:
            current = current["data"]
            seen += 1
            continue
        # No `data` key: take the first list-of-dicts value at this level.
        for value in current.values():
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                return list(value)
        return []
    return []


# -------------------------------------------------------------------- auth --


@dataclass(frozen=True, slots=True)
class TokenResponse:
    """A Pathao access token.

    ``__repr__`` is overridden so a token cannot reach a log line or a
    traceback by being interpolated into a message.
    """

    access_token: str
    refresh_token: str | None
    #: Seconds, as Pathao reports it. Converted to an absolute expiry by the
    #: client, which is the only place that knows when the call was made.
    expires_in: int | None
    token_type: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return "TokenResponse(access_token='[redacted]', refresh_token='[redacted]', ...)"

    __str__ = __repr__


def parse_token(body: Any) -> TokenResponse | None:
    """Read a login response, or ``None`` if it carries no token."""
    data = _as_dict(body)
    # Pathao's plugin reads `access_token` at the top level; an envelope-wrapped
    # variant is accepted too rather than failing a login over nesting.
    token = _text(data.get("access_token"))
    if token is None:
        inner = _as_dict(data.get("data"))
        token = _text(inner.get("access_token"))
        if token is None:
            return None
        data = inner

    return TokenResponse(
        access_token=token,
        refresh_token=_text(data.get("refresh_token")),
        expires_in=_int(data.get("expires_in")),
        token_type=_text(data.get("token_type")),
        raw=_as_dict(body),
    )


# ------------------------------------------------------------------ create --


@dataclass(frozen=True, slots=True)
class CreateOrderResponse:
    """What a successful create returned.

    ``delivery_fee`` is Pathao's own number and is carried through as reported.
    It is *not* turned into a charge here: whether a quoted fee becomes a
    recorded cost is a money decision, and it belongs to the money engine.
    """

    consignment_id: str
    merchant_order_id: str | None
    delivery_fee: float | None
    order_status: str | None
    raw: dict[str, Any] = field(default_factory=dict)


def parse_create_order(body: Any) -> CreateOrderResponse | None:
    """Read a create response, or ``None`` if it carries no consignment id.

    ``None`` is meaningful: a 2xx with no consignment id is a response we
    cannot act on, and the caller turns it into an *ambiguous* booking rather
    than a failed one, because the parcel may well exist.
    """
    envelope = _as_dict(body)
    data = _as_dict(envelope.get("data")) or envelope

    consignment_id = _text(data.get("consignment_id"))
    if consignment_id is None:
        return None

    return CreateOrderResponse(
        consignment_id=consignment_id,
        merchant_order_id=_text(data.get("merchant_order_id")),
        delivery_fee=_decimal_like(data.get("delivery_fee")),
        order_status=_text(data.get("order_status")),
        raw=envelope,
    )


# ------------------------------------------------------- stores, locations --


@dataclass(frozen=True, slots=True)
class PathaoStore:
    """A pickup store on a merchant's Pathao account."""

    store_id: str
    store_name: str
    address: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def parse_stores(body: Any) -> list[PathaoStore]:
    stores: list[PathaoStore] = []
    for record in extract_records(body):
        store_id = _text(record.get("store_id")) or _text(record.get("id"))
        if store_id is None:
            continue
        stores.append(
            PathaoStore(
                store_id=store_id,
                store_name=(
                    _text(record.get("store_name")) or _text(record.get("name")) or store_id
                ),
                address=_text(record.get("store_address")) or _text(record.get("address")),
                raw=record,
            )
        )
    return stores


@dataclass(frozen=True, slots=True)
class City:
    city_id: int
    city_name: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Zone:
    zone_id: int
    zone_name: str
    city_id: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Area:
    area_id: int
    area_name: str
    zone_id: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def parse_city_list(body: Any) -> list[City]:
    cities: list[City] = []
    for record in extract_records(body):
        city_id = _int(record.get("city_id"))
        name = _text(record.get("city_name"))
        if city_id is None or name is None:
            continue
        cities.append(City(city_id=city_id, city_name=name, raw=record))
    return cities


def parse_zone_list(body: Any) -> list[Zone]:
    zones: list[Zone] = []
    for record in extract_records(body):
        zone_id = _int(record.get("zone_id"))
        name = _text(record.get("zone_name"))
        if zone_id is None or name is None:
            continue
        zones.append(
            Zone(
                zone_id=zone_id,
                zone_name=name,
                city_id=_int(record.get("city_id")),
                raw=record,
            )
        )
    return zones


def parse_area_list(body: Any) -> list[Area]:
    areas: list[Area] = []
    for record in extract_records(body):
        area_id = _int(record.get("area_id"))
        name = _text(record.get("area_name"))
        if area_id is None or name is None:
            continue
        areas.append(
            Area(
                area_id=area_id,
                area_name=name,
                zone_id=_int(record.get("zone_id")),
                raw=record,
            )
        )
    return areas
