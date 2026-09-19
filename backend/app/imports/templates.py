"""Import column templates and row parsing.

Master spec section 98. The rule that governs every parser here is its closing
line: **"do not silently coerce invalid money/phone values."**

A row whose phone cannot be parsed is reported as invalid with the offending
value shown, never saved with a blank phone. A seller migrating a year of orders
out of a Google Sheet needs to know which twelve rows were wrong, not to
discover months later that twelve customers have no contact number.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.common.money import paisa_from_taka
from app.common.phone import normalize_digits, try_normalize_bd_phone
from app.imports.models import ImportTemplate

__all__ = [
    "TEMPLATES",
    "ParsedRow",
    "TemplateSpec",
    "autodetect_mapping",
    "parse_row",
    "row_fingerprint",
]


@dataclass(slots=True)
class ParsedRow:
    """One file line after parsing."""

    values: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def error(self, field_name: str, message: str) -> None:
        self.errors.append({"field": field_name, "message": message})

    def warn(self, field_name: str, message: str) -> None:
        self.warnings.append({"field": field_name, "message": message})


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    """Which columns a template understands."""

    template: ImportTemplate
    required: tuple[str, ...]
    optional: tuple[str, ...]
    #: Header spellings seen in real seller spreadsheets, lower-cased.
    aliases: dict[str, tuple[str, ...]]
    #: Fields whose combination identifies a logical record, for deduplication.
    fingerprint_fields: tuple[str, ...]


PRODUCTS_TEMPLATE = TemplateSpec(
    template=ImportTemplate.PRODUCTS,
    required=("name",),
    optional=("sku", "variant", "cost", "price", "stock", "low_stock", "description"),
    aliases={
        "name": ("name", "product", "product name", "title", "item", "পণ্য", "নাম"),
        "sku": ("sku", "code", "product code", "item code", "barcode"),
        "cost": ("cost", "cost price", "buy price", "purchase price", "ক্রয় মূল্য"),
        "price": (
            "price",
            "selling price",
            "sell price",
            "mrp",
            "rate",
            "বিক্রয় মূল্য",
            "দাম",
        ),
        "stock": ("stock", "quantity", "qty", "opening stock", "stock qty", "স্টক"),
        "variant": ("variant", "variant name", "variation", "option", "ভ্যারিয়েন্ট"),
        "low_stock": (
            "low stock",
            "low stock alert",
            "low stock threshold",
            "reorder level",
            "min stock",
            "লো স্টক",
        ),
        "description": ("description", "details", "note", "বিবরণ"),
    },
    # ``variant`` joins the identity so "T-Shirt / Black M" and "T-Shirt /
    # Black L" are two records. Empty parts are skipped, so the fingerprint of
    # a row without a variant is exactly what it was before V2.2.
    fingerprint_fields=("sku", "name", "variant"),
)

ORDERS_TEMPLATE = TemplateSpec(
    template=ImportTemplate.ORDERS,
    required=("phone", "product"),
    optional=("customer_name", "address", "district", "area", "amount", "quantity", "note"),
    aliases={
        "phone": ("phone", "mobile", "number", "contact", "মোবাইল", "নাম্বার", "ফোন"),
        "customer_name": ("name", "customer", "customer name", "নাম", "কাস্টমার"),
        "address": ("address", "delivery address", "ঠিকানা"),
        "district": ("district", "city", "জেলা"),
        "area": ("area", "thana", "zone", "এলাকা"),
        "product": ("product", "item", "product name", "order", "পণ্য"),
        "quantity": ("quantity", "qty", "pcs", "পরিমাণ"),
        "amount": ("amount", "cod", "cod amount", "total", "price", "টাকা", "মোট"),
        "note": ("note", "remark", "comment", "নোট"),
    },
    fingerprint_fields=("phone", "amount", "product"),
)

TEMPLATES: dict[ImportTemplate, TemplateSpec] = {
    ImportTemplate.PRODUCTS: PRODUCTS_TEMPLATE,
    ImportTemplate.ORDERS: ORDERS_TEMPLATE,
}


def autodetect_mapping(spec: TemplateSpec, headers: list[str]) -> dict[str, str]:
    """Guess which column feeds which field.

    A suggestion, not a decision: the seller sees and can correct it before the
    dry run. Exact matches are preferred over substring matches so a column
    called "Price" is not claimed by "cost price".
    """
    normalized = {header: header.strip().lower() for header in headers}
    mapping: dict[str, str] = {}
    taken: set[str] = set()

    for field_name in (*spec.required, *spec.optional):
        aliases = spec.aliases.get(field_name, (field_name,))
        for header, lowered in normalized.items():
            if header in taken:
                continue
            if lowered in aliases:
                mapping[field_name] = header
                taken.add(header)
                break

    for field_name in (*spec.required, *spec.optional):
        if field_name in mapping:
            continue
        aliases = spec.aliases.get(field_name, (field_name,))
        for header, lowered in normalized.items():
            if header in taken:
                continue
            if any(alias in lowered for alias in aliases):
                mapping[field_name] = header
                taken.add(header)
                break

    return mapping


def _read(raw: dict[str, Any], mapping: dict[str, str], field_name: str) -> str:
    column = mapping.get(field_name)
    if column is None:
        return ""
    return str(raw.get(column, "") or "").strip()


def _parse_money(text: str) -> int | None:
    """Taka text to paisa, or ``None`` if it is not a number.

    Returns ``None`` rather than 0 for unparseable input: silently importing a
    ৳0 order is exactly the coercion section 98 forbids.
    """
    cleaned = normalize_digits(text).replace(",", "").replace("৳", "").strip()
    cleaned = cleaned.replace("tk", "").replace("TK", "").replace("Tk", "").strip()
    if not cleaned:
        return None
    try:
        return paisa_from_taka(Decimal(cleaned))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _parse_int(text: str) -> int | None:
    cleaned = normalize_digits(text).strip()
    if not cleaned:
        return None
    try:
        return int(Decimal(cleaned))
    except (InvalidOperation, ValueError, TypeError):
        return None


def parse_row(spec: TemplateSpec, raw: dict[str, Any], mapping: dict[str, str]) -> ParsedRow:
    """Parse and validate one file line."""
    if spec.template is ImportTemplate.PRODUCTS:
        return _parse_product_row(raw, mapping)
    return _parse_order_row(raw, mapping)


def _parse_product_row(raw: dict[str, Any], mapping: dict[str, str]) -> ParsedRow:
    row = ParsedRow()

    name = _read(raw, mapping, "name")
    if not name:
        row.error("name", "A product name is required")
    else:
        row.values["name"] = name

    sku = _read(raw, mapping, "sku")
    row.values["sku"] = sku or None

    for source, target in (("cost", "cost_paisa"), ("price", "default_selling_price_paisa")):
        text = _read(raw, mapping, source)
        if not text:
            row.values[target] = 0
            continue
        amount = _parse_money(text)
        if amount is None:
            row.error(source, f"{text!r} is not a valid amount")
        elif amount < 0:
            row.error(source, "An amount cannot be negative")
        else:
            row.values[target] = amount

    stock_text = _read(raw, mapping, "stock")
    if stock_text:
        stock = _parse_int(stock_text)
        if stock is None:
            row.error("stock", f"{stock_text!r} is not a valid quantity")
        elif stock < 0:
            row.error("stock", "Opening stock cannot be negative")
        else:
            row.values["opening_stock"] = stock
            # A stated count, as opposed to a missing column. Only a stated
            # count may correct an existing SKU's stock.
            row.values["stock_given"] = True
    else:
        row.values["opening_stock"] = 0

    variant = _read(raw, mapping, "variant")
    row.values["variant"] = variant or None

    threshold_text = _read(raw, mapping, "low_stock")
    if threshold_text:
        threshold = _parse_int(threshold_text)
        if threshold is None or threshold < 0:
            row.error("low_stock", f"{threshold_text!r} is not a valid low-stock level")
        else:
            row.values["low_stock_threshold"] = threshold

    description = _read(raw, mapping, "description")
    row.values["description"] = description or None

    cost = row.values.get("cost_paisa", 0)
    price = row.values.get("default_selling_price_paisa", 0)
    if price and cost and price < cost:
        # Imported, but flagged: selling below cost is usually a swapped column.
        row.warn("price", "Selling price is below cost")

    return row


def _parse_order_row(raw: dict[str, Any], mapping: dict[str, str]) -> ParsedRow:
    row = ParsedRow()

    phone_text = _read(raw, mapping, "phone")
    if not phone_text:
        row.error("phone", "A phone number is required")
    else:
        number = try_normalize_bd_phone(phone_text)
        if number is None:
            # Never coerced into a blank or a partial number.
            row.error("phone", f"{phone_text!r} is not a valid Bangladeshi mobile number")
        else:
            row.values["phone"] = number.e164
            row.values["phone_masked"] = number.masked

    product = _read(raw, mapping, "product")
    if not product:
        row.error("product", "A product is required")
    else:
        row.values["product"] = product

    quantity_text = _read(raw, mapping, "quantity")
    if quantity_text:
        quantity = _parse_int(quantity_text)
        if quantity is None or quantity < 1:
            row.error("quantity", f"{quantity_text!r} is not a valid quantity")
        else:
            row.values["quantity"] = quantity
    else:
        row.values["quantity"] = 1

    amount_text = _read(raw, mapping, "amount")
    if amount_text:
        amount = _parse_money(amount_text)
        if amount is None:
            row.error("amount", f"{amount_text!r} is not a valid amount")
        elif amount < 0:
            row.error("amount", "An amount cannot be negative")
        else:
            row.values["cod_amount_paisa"] = amount
    else:
        # Allowed but flagged: an order with no COD amount is unusual and the
        # seller should confirm it rather than have a zero invented for them.
        row.values["cod_amount_paisa"] = 0
        row.warn("amount", "No COD amount in this row")

    for field_name in ("customer_name", "address", "district", "area", "note"):
        value = _read(raw, mapping, field_name)
        row.values[field_name] = value or None

    if not row.values.get("address"):
        row.warn("address", "No delivery address in this row")

    return row


def row_fingerprint(spec: TemplateSpec, parsed: ParsedRow) -> str | None:
    """Stable hash of the fields that identify a logical record.

    Lets the same order appearing twice — in one file or across a re-import — be
    recognised without comparing every column (section 98's "idempotent
    duplicate handling").
    """
    parts: list[str] = []
    for field_name in spec.fingerprint_fields:
        value = parsed.values.get(field_name)
        if value in (None, ""):
            continue
        parts.append(f"{field_name}={str(value).strip().lower()}")

    if not parts:
        return None
    return hashlib.sha256("|".join(parts).encode()).hexdigest()
