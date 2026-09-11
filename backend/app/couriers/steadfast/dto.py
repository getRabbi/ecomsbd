"""Typed request and response models for Steadfast V1.

Strict where the document is strict; tolerant where it is silent.

The tolerant half is the interesting one. Four documented endpoints — payments,
payment detail, return-request lookup and list, police stations — give a path
and a method and **no response schema at all**. The brief (section 55) says to
integrate them completely without inventing their fields, which means:

*   parse to a real object, not a blob;
*   type the keys whose meaning is fixed by the *purpose* of the endpoint, and
    only after looking for them in the payload rather than requiring them;
*   preserve every unrecognised key verbatim in :attr:`_Tolerant.extra`, so the
    first live response captures the true shape instead of discarding it;
*   record which fields were actually present, so
    ``docs/providers/steadfast/IMPLEMENTATION.md`` can be updated from evidence.

A field that is guessed is a field that will be wrong. A field that is *looked
for and absent* is a fact, and :attr:`_Tolerant.observed_fields` is how that
fact gets recorded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.common.money import paisa_from_taka
from app.core.clock import ensure_utc
from app.couriers.steadfast.contract import (
    BULK_ITEM_SUCCESS,
    SteadfastDeliveryStatus,
    is_documented_delivery_status,
)

__all__ = [
    "BulkCreateItemResult",
    "BulkCreateResult",
    "CreateOrderRequest",
    "CreateOrderResponse",
    "PaymentConsignmentRecord",
    "PaymentDetailResponse",
    "PaymentListResponse",
    "PaymentSummary",
    "PoliceStationRecord",
    "ReturnRequestRecord",
    "StatusResponse",
    "coerce_paisa",
    "parse_provider_datetime",
]


# ------------------------------------------------------------------ helpers --


def coerce_paisa(value: Any) -> int | None:
    """Read a documented amount into integer paisa.

    The document's own samples type ``cod_amount`` inconsistently — ``1060`` in
    the single-create response and ``"0.00"`` in the bulk result — so both are
    accepted. A ``float`` is routed through ``str`` rather than ``Decimal``
    directly: by the time money is a float it may already be wrong, and
    ``Decimal(0.1)`` is not ``Decimal("0.1")``.

    Returns ``None`` rather than ``0`` when the value is absent or unreadable.
    Zero is a real amount and must not be manufactured.
    """
    if value is None or value == "":
        return None
    try:
        if isinstance(value, float):
            return paisa_from_taka(str(value))
        if isinstance(value, (int, Decimal)):
            return paisa_from_taka(value)
        return paisa_from_taka(str(value).strip())
    except (ValueError, TypeError, InvalidOperation):
        return None


def parse_provider_datetime(value: Any) -> datetime | None:
    """Parse the ISO-8601 timestamps the document's samples show.

    ``2021-03-21T07:05:31.000000Z``. Python's ``fromisoformat`` handles the
    trailing ``Z`` from 3.11, but the six-digit fraction and a possible offset
    form are both tolerated. An unparseable value returns ``None`` rather than
    ``now()`` — a fabricated timestamp would silently become an event's
    position in a timeline.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return ensure_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def _first_present(payload: dict[str, Any], *names: str) -> Any:
    """First key that exists in ``payload``, in preference order.

    Used only on the path-only endpoints, where the *purpose* of a field is
    known but its name is not documented. Every alternative here is a name the
    field might plausibly carry; none is asserted to be correct, and
    ``observed_fields`` records which one the provider actually used so the
    documentation can be corrected from evidence rather than from a guess.
    """
    for name in names:
        if name in payload and payload[name] not in (None, ""):
            return payload[name]
    return None


@dataclass(frozen=True, slots=True)
class _Tolerant:
    """Base for the path-only endpoints' models."""

    #: Every key of the provider's object, verbatim, including ones we typed.
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def observed_fields(self) -> list[str]:
        """The keys the provider actually sent.

        Written into the raw-payload record and surfaced by the smoke tool, so
        the first live call turns ``UNVERIFIED`` into a documented fact.
        """
        return sorted(self.raw)


# ------------------------------------------------------------- create order --


@dataclass(frozen=True, slots=True)
class CreateOrderRequest:
    """The documented ``POST /create_order`` body.

    Field names and optionality are the document's. ``invoice`` is *ours* — a
    stable merchant reference generated and persisted before this object is
    built, never derived here.
    """

    invoice: str
    recipient_name: str
    #: Already transformed to the documented 11-digit national form. The
    #: canonical ``+8801…`` never reaches this object.
    recipient_phone: str
    recipient_address: str
    #: **Whole taka**, as a JSON number. The document types this ``numeric``
    #: and its example is ``1060``; a courier collects banknotes, and poisha
    #: coins are out of circulation, so a fractional COD is not collectable.
    #: The rounding is done once, in :func:`..adapter.provider_cod_taka`, and
    #: the residual is recorded on the booking attempt rather than discarded.
    cod_amount: int
    alternative_phone: str | None = None
    recipient_email: str | None = None
    note: str | None = None
    item_description: str | None = None
    total_lot: int | None = None
    delivery_type: int | None = None

    def as_payload(self) -> dict[str, Any]:
        """The JSON body. Optional fields absent rather than null.

        Sending explicit nulls for undocumented-as-nullable optional fields is
        a guess about how the provider's validator treats them; omitting them
        is what the document's own example does.
        """
        payload: dict[str, Any] = {
            "invoice": self.invoice,
            "recipient_name": self.recipient_name,
            "recipient_phone": self.recipient_phone,
            "recipient_address": self.recipient_address,
            "cod_amount": self.cod_amount,
        }
        optional: dict[str, Any] = {
            "alternative_phone": self.alternative_phone,
            "recipient_email": self.recipient_email,
            "note": self.note,
            "item_description": self.item_description,
            "total_lot": self.total_lot,
            "delivery_type": self.delivery_type,
        }
        payload.update({k: v for k, v in optional.items() if v is not None})
        return payload

    def redacted(self) -> dict[str, Any]:
        """The payload as it is safe to persist and log.

        The phone numbers are masked here rather than by the generic log
        redactor, because this dict is stored as evidence and the stored copy
        must already be safe — see the raw-payload model.
        """
        from app.core.redaction import mask_phone

        payload = self.as_payload()
        for key in ("recipient_phone", "alternative_phone"):
            if key in payload:
                payload[key] = mask_phone(str(payload[key]))
        if "recipient_email" in payload:
            payload["recipient_email"] = "[redacted]"
        return payload


@dataclass(frozen=True, slots=True)
class CreateOrderResponse:
    """The documented success body of ``POST /create_order``."""

    consignment_id: str
    invoice: str
    tracking_code: str | None
    recipient_name: str | None
    recipient_address: str | None
    cod_amount_paisa: int | None
    provider_status: str | None
    note: str | None
    created_at: datetime | None
    updated_at: datetime | None
    message: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, body: dict[str, Any]) -> CreateOrderResponse:
        """Read the documented envelope. Raises ``KeyError`` if it is absent.

        ``recipient_phone`` is present in the documented response and is
        deliberately **not** carried onto this object: it is the number we just
        sent, it adds nothing, and a second copy of it would flow into every
        downstream log and event.
        """
        consignment = body["consignment"]
        if not isinstance(consignment, dict):
            raise TypeError("consignment is not an object")
        return cls(
            consignment_id=str(consignment["consignment_id"]),
            invoice=str(consignment["invoice"]),
            tracking_code=_str_or_none(consignment.get("tracking_code")),
            recipient_name=_str_or_none(consignment.get("recipient_name")),
            recipient_address=_str_or_none(consignment.get("recipient_address")),
            cod_amount_paisa=coerce_paisa(consignment.get("cod_amount")),
            provider_status=_str_or_none(consignment.get("status")),
            note=_str_or_none(consignment.get("note")),
            created_at=parse_provider_datetime(consignment.get("created_at")),
            updated_at=parse_provider_datetime(consignment.get("updated_at")),
            message=_str_or_none(body.get("message")),
            raw=consignment,
        )


# --------------------------------------------------------------- bulk create --


@dataclass(frozen=True, slots=True)
class BulkCreateItemResult:
    """One entry of the documented bulk result array."""

    invoice: str | None
    status: str | None
    consignment_id: str | None
    tracking_code: str | None
    cod_amount_paisa: int | None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        """Success is ``status == "success"`` **and** an id came back.

        Both, because the document's error sample shows ``consignment_id:
        null`` and there is no stated guarantee that a ``success`` status
        always carries one. Treating a null id as a booked parcel would leave
        a consignment marked ``BOOKED`` with nothing to look it up by.
        """
        return self.status == BULK_ITEM_SUCCESS and bool(self.consignment_id)

    @classmethod
    def parse(cls, item: dict[str, Any]) -> BulkCreateItemResult:
        return cls(
            invoice=_str_or_none(item.get("invoice")),
            status=_str_or_none(item.get("status")),
            consignment_id=_str_or_none(item.get("consignment_id")),
            tracking_code=_str_or_none(item.get("tracking_code")),
            cod_amount_paisa=coerce_paisa(item.get("cod_amount")),
            raw=item,
        )


@dataclass(frozen=True, slots=True)
class BulkCreateResult:
    """The documented bulk response, in both of its documented shapes.

    The document shows a bare array on success and an object carrying ``data``
    when "there is any error in data". Both are accepted; neither is preferred.
    """

    items: list[BulkCreateItemResult]
    raw: Any = None

    @classmethod
    def parse(cls, body: Any) -> BulkCreateResult:
        if isinstance(body, dict):
            candidate = body.get("data", body.get("consignments"))
            rows = candidate if isinstance(candidate, list) else []
        elif isinstance(body, list):
            rows = body
        else:
            raise TypeError("bulk response is neither an array nor an object with data")
        return cls(
            items=[BulkCreateItemResult.parse(row) for row in rows if isinstance(row, dict)],
            raw=body,
        )

    def by_invoice(self) -> dict[str, BulkCreateItemResult]:
        """Results keyed by the invoice we sent.

        Position is never used to match a result back to a request. The
        document makes no ordering guarantee, and matching by position when the
        provider reorders or drops an entry attributes one order's consignment
        id to a different order — the worst outcome in this whole integration.
        """
        return {item.invoice: item for item in self.items if item.invoice}


# ------------------------------------------------------------------- status --


@dataclass(frozen=True, slots=True)
class StatusResponse:
    """``{"status": 200, "delivery_status": "in_review"}``."""

    delivery_status: str
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_documented(self) -> bool:
        return is_documented_delivery_status(self.delivery_status)

    @property
    def known(self) -> SteadfastDeliveryStatus | None:
        return SteadfastDeliveryStatus(self.delivery_status) if self.is_documented else None

    @classmethod
    def parse(cls, body: dict[str, Any]) -> StatusResponse:
        value = body.get("delivery_status")
        if value is None:
            raise KeyError("delivery_status")
        return cls(delivery_status=str(value), raw=body)


# ---------------------------------------------------------- return requests --


@dataclass(frozen=True, slots=True)
class ReturnRequestRecord(_Tolerant):
    """A return request as the provider reports it.

    ``POST /create_return_request`` documents these fields as a value table.
    ``GET /get_return_request/{id}`` and ``GET /get_return_requests`` are
    path-only, so the *same* tolerant parser reads all three: the create
    response proves the field names, and the lookups are assumed — and
    verified at runtime — to use them too. Where they do not, the keys land in
    ``raw`` and nothing is lost.
    """

    provider_return_id: str | None = None
    consignment_id: str | None = None
    status: str | None = None
    reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> ReturnRequestRecord:
        # The create response is documented flat; a lookup may wrap it. Unwrap
        # one documented-looking level, then read.
        body = payload
        for wrapper in ("data", "return_request"):
            inner = payload.get(wrapper)
            if isinstance(inner, dict):
                body = inner
                break
        return cls(
            provider_return_id=_str_or_none(body.get("id")),
            consignment_id=_str_or_none(body.get("consignment_id")),
            status=_str_or_none(body.get("status")),
            reason=_str_or_none(body.get("reason")),
            created_at=parse_provider_datetime(body.get("created_at")),
            updated_at=parse_provider_datetime(body.get("updated_at")),
            raw=body,
        )

    @classmethod
    def parse_list(cls, payload: Any) -> list[ReturnRequestRecord]:
        rows = _rows_of(payload)
        return [cls.parse(row) for row in rows]


# ----------------------------------------------------------------- payments --


@dataclass(frozen=True, slots=True)
class PaymentSummary(_Tolerant):
    """One entry of ``GET /payments``.

    **The document describes no field of this response.** Every typed attribute
    below is read by looking for a small set of plausible key names and
    recording which one was found; none is required, and an absent one stays
    ``None`` rather than becoming zero or "now". ``raw`` holds the provider's
    object verbatim, and :attr:`observed_fields` is what turns the first live
    response into documentation.
    """

    provider_payment_id: str | None = None
    amount_paisa: int | None = None
    status: str | None = None
    paid_at: datetime | None = None
    reference: str | None = None
    consignment_count: int | None = None

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> PaymentSummary:
        count = _first_present(payload, "consignment_count", "total_consignments", "count")
        return cls(
            provider_payment_id=_str_or_none(
                _first_present(payload, "id", "payment_id", "payment_no", "uuid")
            ),
            amount_paisa=coerce_paisa(
                _first_present(
                    payload, "amount", "total", "total_amount", "net_amount", "paid_amount"
                )
            ),
            status=_str_or_none(_first_present(payload, "status", "payment_status", "state")),
            paid_at=parse_provider_datetime(
                _first_present(payload, "paid_at", "payment_date", "created_at", "date")
            ),
            reference=_str_or_none(
                _first_present(
                    payload, "reference", "payment_reference", "trx_id", "transaction_id"
                )
            ),
            consignment_count=int(count)
            if isinstance(count, (int, str)) and str(count).isdigit()
            else None,
            raw=payload,
        )


@dataclass(frozen=True, slots=True)
class PaymentListResponse:
    """``GET /payments``, in whatever envelope it arrives in.

    No pagination parameter is documented. The parser looks for the shapes a
    Laravel application conventionally produces — a bare array, ``{"data": []}``,
    or a paginator with ``current_page``/``last_page``/``next_page_url`` — and
    reports what it found. It does not *send* a pagination parameter it cannot
    justify; see the payment sync job for how the window is walked instead.
    """

    payments: list[PaymentSummary]
    #: Set only when the response itself declared one.
    current_page: int | None = None
    last_page: int | None = None
    next_page_url: str | None = None
    raw: Any = None

    @property
    def has_declared_pagination(self) -> bool:
        return self.current_page is not None or self.next_page_url is not None

    @classmethod
    def parse(cls, body: Any) -> PaymentListResponse:
        rows = _rows_of(body)
        envelope = body if isinstance(body, dict) else {}
        inner = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        meta = envelope.get("meta") if isinstance(envelope.get("meta"), dict) else {}
        source = {**envelope, **(inner or {}), **(meta or {})}
        return cls(
            payments=[PaymentSummary.parse(row) for row in rows],
            current_page=_int_or_none(source.get("current_page")),
            last_page=_int_or_none(source.get("last_page")),
            next_page_url=_str_or_none(source.get("next_page_url")),
            raw=body,
        )


@dataclass(frozen=True, slots=True)
class PaymentConsignmentRecord(_Tolerant):
    """One parcel inside a payment detail.

    Same rule as :class:`PaymentSummary`: nothing here is documented. What the
    endpoint's *title* fixes is that consignments are present and that they
    identify parcels — so the identity fields are looked for first, because
    they are what reconciliation matches on, and an amount with no identity is
    explicitly not enough to match (brief section 24).
    """

    provider_consignment_id: str | None = None
    tracking_code: str | None = None
    invoice: str | None = None
    cod_amount_paisa: int | None = None
    #: What the merchant is credited for this parcel, if the payload says.
    payable_amount_paisa: int | None = None
    delivery_charge_paisa: int | None = None
    cod_fee_paisa: int | None = None
    return_charge_paisa: int | None = None
    discount_paisa: int | None = None
    adjustment_paisa: int | None = None
    status: str | None = None
    delivered_at: datetime | None = None

    @property
    def has_identity(self) -> bool:
        return bool(self.provider_consignment_id or self.tracking_code or self.invoice)

    @property
    def has_charge_breakdown(self) -> bool:
        """Whether the provider itemised its deductions for this parcel.

        When false, an aggregate difference is stored as one unclassified
        adjustment rather than being split into a fabricated breakdown
        (brief section 25).
        """
        return any(
            value is not None
            for value in (
                self.delivery_charge_paisa,
                self.cod_fee_paisa,
                self.return_charge_paisa,
            )
        )

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> PaymentConsignmentRecord:
        return cls(
            provider_consignment_id=_str_or_none(
                _first_present(payload, "consignment_id", "id", "cid")
            ),
            tracking_code=_str_or_none(_first_present(payload, "tracking_code", "tracking")),
            invoice=_str_or_none(
                _first_present(payload, "invoice", "invoice_id", "merchant_invoice")
            ),
            cod_amount_paisa=coerce_paisa(_first_present(payload, "cod_amount", "cod")),
            payable_amount_paisa=coerce_paisa(
                _first_present(payload, "payable_amount", "net_amount", "amount", "total")
            ),
            delivery_charge_paisa=coerce_paisa(
                _first_present(payload, "delivery_charge", "delivery_fee", "charge")
            ),
            cod_fee_paisa=coerce_paisa(_first_present(payload, "cod_fee", "cod_charge")),
            return_charge_paisa=coerce_paisa(
                _first_present(payload, "return_charge", "return_fee")
            ),
            discount_paisa=coerce_paisa(_first_present(payload, "discount")),
            adjustment_paisa=coerce_paisa(_first_present(payload, "adjustment", "adjustments")),
            status=_str_or_none(_first_present(payload, "status", "delivery_status")),
            delivered_at=parse_provider_datetime(
                _first_present(payload, "delivered_at", "delivery_date", "updated_at")
            ),
            raw=payload,
        )


@dataclass(frozen=True, slots=True)
class PaymentDetailResponse:
    """``GET /payments/{payment_id}`` — the payment plus its consignments."""

    payment: PaymentSummary
    consignments: list[PaymentConsignmentRecord]
    raw: Any = None

    @property
    def identified_consignments(self) -> list[PaymentConsignmentRecord]:
        return [row for row in self.consignments if row.has_identity]

    @classmethod
    def parse(cls, body: Any) -> PaymentDetailResponse:
        envelope: dict[str, Any] = body if isinstance(body, dict) else {}
        payment_body = envelope
        for wrapper in ("data", "payment"):
            inner = envelope.get(wrapper)
            if isinstance(inner, dict):
                payment_body = inner
                break

        rows: list[dict[str, Any]] = []
        for key in ("consignments", "consignment", "details", "items", "parcels"):
            candidate = payment_body.get(key) or envelope.get(key)
            if isinstance(candidate, list):
                rows = [row for row in candidate if isinstance(row, dict)]
                break

        # A consignment list must never be confused with the payment's own
        # fields, so the payment summary is parsed from the object with the
        # nested list removed.
        scalar_only = {k: v for k, v in payment_body.items() if not isinstance(v, (list, dict))}
        return cls(
            payment=PaymentSummary.parse(scalar_only or payment_body),
            consignments=[PaymentConsignmentRecord.parse(row) for row in rows],
            raw=body,
        )


# ----------------------------------------------------------- police stations --


@dataclass(frozen=True, slots=True)
class PoliceStationRecord(_Tolerant):
    """One entry of ``GET /police_stations``. Path-only; parsed tolerantly."""

    station_id: str | None = None
    name: str | None = None
    district: str | None = None

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> PoliceStationRecord:
        return cls(
            station_id=_str_or_none(_first_present(payload, "id", "police_station_id")),
            name=_str_or_none(_first_present(payload, "name", "police_station", "title")),
            district=_str_or_none(_first_present(payload, "district", "zilla", "city")),
            raw=payload,
        )

    @classmethod
    def parse_list(cls, payload: Any) -> list[PoliceStationRecord]:
        return [cls.parse(row) for row in _rows_of(payload)]


# ----------------------------------------------------------------- internals --


def _rows_of(body: Any) -> list[dict[str, Any]]:
    """Pull the list of objects out of whatever envelope arrived.

    Accepts a bare array, ``{"data": [...]}``, and Laravel's nested
    ``{"data": {"data": [...]}}`` paginator. Anything else yields no rows
    rather than an exception: a path-only endpoint returning an unexpected
    shape is information to record, not a crash.
    """
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if not isinstance(body, dict):
        return []
    for key in ("data", "payments", "return_requests", "police_stations", "results"):
        candidate = body.get(key)
        if isinstance(candidate, list):
            return [row for row in candidate if isinstance(row, dict)]
        if isinstance(candidate, dict):
            nested = candidate.get("data")
            if isinstance(nested, list):
                return [row for row in nested if isinstance(row, dict)]
    return []


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def json_loads_object(text: str) -> Any:
    """Parse a body, raising ``ValueError`` on anything that is not JSON."""
    return json.loads(text)
