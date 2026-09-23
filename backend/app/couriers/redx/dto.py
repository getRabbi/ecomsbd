"""Typed readings of RedX responses.

Each parser reads exactly the fields the documentation shows for its endpoint
and keeps the whole record in ``raw``. None of them raises: a body that is not
the documented shape comes back as ``None`` (or an empty list), and the client
decides what that means for the call it made — for a read, a protocol error;
for a create, the ambiguous case.

Money arrives in taka as JSON numbers (``"charge": 60``). It is converted to
paisa through :class:`~decimal.Decimal` from the number's text, never through a
float multiply, so ``60.5`` is 6050 paisa and not 6049.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

__all__ = [
    "RedxArea",
    "RedxCharge",
    "RedxParcel",
    "RedxPickupStore",
    "RedxTrackingEntry",
    "parse_areas",
    "parse_charge",
    "parse_create_parcel",
    "parse_parcel_info",
    "parse_pickup_store",
    "parse_pickup_stores",
    "parse_tracking",
    "parse_update",
    "taka_to_paisa",
]


@dataclass(frozen=True, slots=True)
class RedxArea:
    """One delivery area, as ``GET /areas`` lists it."""

    id: int
    name: str
    post_code: int | None = None
    division_name: str | None = None
    zone_id: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RedxPickupStore:
    """One pickup store on the merchant's RedX account."""

    id: str
    name: str
    address: str | None = None
    area_name: str | None = None
    area_id: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RedxParcel:
    """``GET /parcel/info/{tracking_id}``, the fields ecomsbd acts on."""

    tracking_id: str
    status: str | None
    delivery_type: str | None = None
    merchant_invoice_id: str | None = None
    #: RedX's delivery charge for the parcel, in paisa, as RedX reports it.
    charge_paisa: int | None = None
    cash_collection_paisa: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RedxTrackingEntry:
    """One line of ``GET /parcel/track/{tracking_id}``."""

    message_en: str | None
    message_bn: str | None
    time: datetime | None


@dataclass(frozen=True, slots=True)
class RedxCharge:
    """``GET /charge/charge_calculator``, in paisa."""

    delivery_charge_paisa: int
    cod_charge_paisa: int
    raw: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------ helpers --


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
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


def taka_to_paisa(value: Any) -> int | None:
    """A taka amount as RedX sends it, in paisa. ``None`` if it is not a number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    return int((amount * 100).to_integral_value())


def _datetime(value: Any) -> datetime | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _records(body: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        return []
    rows = body.get(key)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


# ------------------------------------------------------------------ parsers --


def parse_areas(body: Any) -> list[RedxArea]:
    """``{"areas": [{id, name, post_code, division_name, zone_id}, ...]}``."""
    areas: list[RedxArea] = []
    for row in _records(body, "areas"):
        area_id = _int(row.get("id"))
        name = _text(row.get("name"))
        if area_id is None or name is None:
            continue
        areas.append(
            RedxArea(
                id=area_id,
                name=name,
                post_code=_int(row.get("post_code")),
                division_name=_text(row.get("division_name")),
                zone_id=_int(row.get("zone_id")),
                raw=row,
            )
        )
    return areas


def _store(row: dict[str, Any]) -> RedxPickupStore | None:
    store_id = _text(row.get("id"))
    if store_id is None:
        return None
    return RedxPickupStore(
        id=store_id,
        name=_text(row.get("name")) or store_id,
        address=_text(row.get("address")),
        area_name=_text(row.get("area_name")),
        area_id=_int(row.get("area_id")),
        raw=row,
    )


def parse_pickup_stores(body: Any) -> list[RedxPickupStore] | None:
    """``{"pickup_stores": [...]}``.

    ``None`` — not an empty list — when the envelope itself is missing: a
    merchant with no stores is a real answer, a body without the documented key
    is not one.
    """
    if not isinstance(body, dict) or not isinstance(body.get("pickup_stores"), list):
        return None
    stores = [_store(row) for row in _records(body, "pickup_stores")]
    return [store for store in stores if store is not None]


def parse_pickup_store(body: Any) -> RedxPickupStore | None:
    """``{"pickup_store": {...}}``."""
    if not isinstance(body, dict) or not isinstance(body.get("pickup_store"), dict):
        return None
    return _store(body["pickup_store"])


def parse_create_parcel(body: Any) -> str | None:
    """The tracking id from ``{"tracking_id": "..."}``, or ``None``."""
    if not isinstance(body, dict):
        return None
    return _text(body.get("tracking_id"))


def parse_parcel_info(body: Any) -> RedxParcel | None:
    """``{"parcel": {tracking_id, status, delivery_type, charge, ...}}``."""
    if not isinstance(body, dict) or not isinstance(body.get("parcel"), dict):
        return None
    row: dict[str, Any] = body["parcel"]
    tracking_id = _text(row.get("tracking_id"))
    if tracking_id is None:
        return None
    return RedxParcel(
        tracking_id=tracking_id,
        status=_text(row.get("status")),
        delivery_type=_text(row.get("delivery_type")),
        merchant_invoice_id=_text(row.get("merchant_invoice_id")),
        charge_paisa=taka_to_paisa(row.get("charge")),
        cash_collection_paisa=taka_to_paisa(row.get("cash_collection_amount")),
        raw=row,
    )


def parse_tracking(body: Any) -> list[RedxTrackingEntry] | None:
    """``{"tracking": [{message_en, message_bn, time}, ...]}``."""
    if not isinstance(body, dict) or not isinstance(body.get("tracking"), list):
        return None
    return [
        RedxTrackingEntry(
            message_en=_text(row.get("message_en")),
            message_bn=_text(row.get("message_bn")),
            time=_datetime(row.get("time")),
        )
        for row in _records(body, "tracking")
    ]


def parse_charge(body: Any) -> RedxCharge | None:
    """``{"deliveryCharge": 60, "codCharge": 0}``."""
    if not isinstance(body, dict):
        return None
    delivery = taka_to_paisa(body.get("deliveryCharge"))
    cod = taka_to_paisa(body.get("codCharge"))
    if delivery is None or cod is None:
        return None
    return RedxCharge(delivery_charge_paisa=delivery, cod_charge_paisa=cod, raw=dict(body))


def parse_update(body: Any) -> tuple[bool | None, str | None]:
    """``{"success": true, "message": "Request Accepted"}``.

    ``success`` is ``None`` when the body does not carry a boolean, which the
    caller must not read as either answer.
    """
    if not isinstance(body, dict):
        return None, None
    success = body.get("success")
    return (success if isinstance(success, bool) else None), _text(body.get("message"))
