"""Reading a courier statement.

**UNVERIFIED — no provider statement format has been confirmed.** Master spec
section 140 forbids inventing a provider's schema, so this is a *generic*
statement reader: it detects columns by name, suggests a mapping, and lets the
seller correct it before anything is applied. When a real Steadfast, Pathao or
RedX statement is available, a provider-specific profile can be added on top
without changing anything here.

The rule from section 98 carries over from the Phase B importer and matters
more here, because this is money: **do not silently coerce invalid values.** A
row whose amount cannot be read is reported with the text the file contained,
never imported as ৳0.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.common.money import paisa_from_taka
from app.common.phone import normalize_digits
from app.payouts.models import AdjustmentType

__all__ = [
    "ADJUSTMENT_RULES",
    "STATEMENT_ALIASES",
    "ParsedStatement",
    "StatementRow",
    "autodetect_columns",
    "classify_adjustment",
    "parse_statement",
    "sha256_of",
]


#: Column names seen in courier statements and seller spreadsheets, lower-cased.
#: English and Bangla, because a seller may well have re-typed the statement
#: into their own sheet before uploading it.
STATEMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "consignment_id": (
        "consignment id",
        "consignment",
        "parcel id",
        "parcel",
        "shipment id",
        "cn id",
        "কনসাইনমেন্ট",
    ),
    "tracking_code": (
        "tracking",
        "tracking code",
        "tracking id",
        "awb",
        "waybill",
        "ট্র্যাকিং",
    ),
    "merchant_reference": (
        "invoice",
        "invoice id",
        "invoice no",
        "merchant reference",
        "reference",
        "order id",
        "order no",
        "order number",
        "অর্ডার",
    ),
    "amount": (
        "amount",
        "collected amount",
        "cod amount",
        "paid amount",
        "net amount",
        "payable",
        "টাকা",
        "মোট",
    ),
    "delivered_on": (
        "delivered date",
        "delivery date",
        "delivered on",
        "date",
        "তারিখ",
    ),
    "phone": ("phone", "mobile", "customer phone", "number", "মোবাইল"),
    "fee": (
        "fee",
        "charge",
        "delivery charge",
        "courier charge",
        "cod charge",
        "cod fee",
        "deduction",
        "চার্জ",
    ),
    "fee_label": ("fee type", "charge type", "deduction type", "remarks", "note"),
}


#: How a provider's own words map to an adjustment type. Keyword matching,
#: deliberately conservative: anything unrecognised becomes
#: ``UNKNOWN_DEDUCTION`` rather than being guessed into a familiar bucket,
#: which is what section 84 requires.
ADJUSTMENT_RULES: tuple[tuple[str, AdjustmentType, str], ...] = (
    ("cod fee", AdjustmentType.COD_FEE, "label:cod-fee"),
    ("cod charge", AdjustmentType.COD_FEE, "label:cod-charge"),
    ("cash handling", AdjustmentType.COD_FEE, "label:cash-handling"),
    ("delivery charge", AdjustmentType.DELIVERY_FEE, "label:delivery-charge"),
    ("delivery fee", AdjustmentType.DELIVERY_FEE, "label:delivery-fee"),
    ("courier charge", AdjustmentType.DELIVERY_FEE, "label:courier-charge"),
    ("shipping", AdjustmentType.DELIVERY_FEE, "label:shipping"),
    ("return charge", AdjustmentType.RETURN_FEE, "label:return-charge"),
    ("return fee", AdjustmentType.RETURN_FEE, "label:return-fee"),
    ("rto", AdjustmentType.RETURN_FEE, "label:rto"),
    ("vat", AdjustmentType.TAX, "label:vat"),
    ("tax", AdjustmentType.TAX, "label:tax"),
    ("bonus", AdjustmentType.BONUS, "label:bonus"),
    ("incentive", AdjustmentType.BONUS, "label:incentive"),
    ("penalty", AdjustmentType.PENALTY, "label:penalty"),
    ("fine", AdjustmentType.PENALTY, "label:fine"),
)


@dataclass(slots=True)
class StatementRow:
    """One parsed line, with whatever the file actually contained."""

    row_number: int
    raw: dict[str, str]
    amount_paisa: int | None = None
    consignment_id: str | None = None
    tracking_code: str | None = None
    merchant_reference: str | None = None
    phone_last4: str | None = None
    delivered_on: date | None = None
    fee_paisa: int | None = None
    fee_label: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def has_reference(self) -> bool:
        """Whether the row names a parcel at all.

        A row with only an amount can never be auto-matched — section 82:
        *"amount-only match → never auto-match"* — but it is still imported,
        because the money genuinely arrived and hiding it would be worse.
        """
        return bool(self.consignment_id or self.tracking_code or self.merchant_reference)


@dataclass(slots=True)
class ParsedStatement:
    """Everything read out of one file."""

    headers: list[str]
    mapping: dict[str, str]
    rows: list[StatementRow]

    @property
    def total_paisa(self) -> int:
        return sum(row.amount_paisa or 0 for row in self.rows)

    @property
    def invalid_rows(self) -> list[StatementRow]:
        return [row for row in self.rows if not row.is_valid]


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def autodetect_columns(headers: list[str]) -> dict[str, str]:
    """Guess which column is which.

    A suggestion the seller confirms, never a decision.

    Two passes. The first takes exact header matches, and within that pass a
    header matching an earlier alias beats one matching a later alias — so with
    both "Amount" and "COD Amount" present, `amount` takes "Amount" and leaves
    the other free for whatever names it better. Position in the alias tuple is
    therefore meaningful: the first entry is the canonical name. The second pass
    falls back to substring matching for headers nothing claimed exactly.
    """
    normalized = {header: header.strip().lower() for header in headers}
    mapping: dict[str, str] = {}
    taken: set[str] = set()

    for field_name, aliases in STATEMENT_ALIASES.items():
        best: tuple[int, str] | None = None
        for header, lowered in normalized.items():
            if header in taken or lowered not in aliases:
                continue
            rank = aliases.index(lowered)
            if best is None or rank < best[0]:
                best = (rank, header)
        if best is not None:
            mapping[field_name] = best[1]
            taken.add(best[1])

    for field_name, aliases in STATEMENT_ALIASES.items():
        if field_name in mapping:
            continue
        for header, lowered in normalized.items():
            if header in taken:
                continue
            if any(alias in lowered for alias in aliases):
                mapping[field_name] = header
                taken.add(header)
                break

    return mapping


def classify_adjustment(label: str | None) -> tuple[AdjustmentType, str | None]:
    """Work out what a fee is, or admit that we cannot.

    Returns the type and the rule that decided it. An unrecognised label gives
    ``(UNKNOWN_DEDUCTION, None)`` — section 84's closing rule, because a
    deduction quietly filed as "delivery fee" is a deduction the seller will
    never question.
    """
    if not label:
        return AdjustmentType.UNKNOWN_DEDUCTION, None

    lowered = label.strip().lower()
    for keyword, adjustment_type, rule in ADJUSTMENT_RULES:
        if keyword in lowered:
            return adjustment_type, rule
    return AdjustmentType.UNKNOWN_DEDUCTION, None


def _read(raw: dict[str, str], mapping: dict[str, str], field_name: str) -> str:
    column = mapping.get(field_name)
    if column is None:
        return ""
    return str(raw.get(column, "") or "").strip()


def _parse_money(text: str) -> int | None:
    """Taka text to paisa, or ``None`` when it is not a number.

    Never 0 on failure. An unreadable amount imported as ৳0 is a settlement
    that silently never happened.
    """
    cleaned = normalize_digits(text)
    for token in ("৳", ",", "tk", "TK", "Tk", "BDT", "bdt"):
        cleaned = cleaned.replace(token, "")
    cleaned = cleaned.strip()
    # A parenthesised or leading-minus figure is a deduction in most
    # statements; the sign is carried by the field, not by the amount.
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()").lstrip("-").strip()
    if not cleaned:
        return None
    try:
        value = paisa_from_taka(Decimal(cleaned))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return -value if negative else value


_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%d %b %Y",
    "%d %B %Y",
)


def _parse_date(text: str) -> date | None:
    cleaned = normalize_digits(text).strip()
    if not cleaned:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned[:20], fmt).date()
        except ValueError:
            continue
    # Ambiguous or unreadable. Left unset rather than guessed: the delivery
    # date is one of the scoring inputs, and a wrong one moves a match.
    return None


def _decode(content: bytes) -> str:
    """Text out of whatever encoding the seller's spreadsheet produced."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def parse_statement(content: bytes, *, mapping: dict[str, str] | None = None) -> ParsedStatement:
    """Read a CSV statement into rows.

    Nothing is applied and nothing is created. The result is evidence for the
    seller to look at before a single paisa moves.
    """
    text = _decode(content)
    if not text.strip():
        return ParsedStatement(headers=[], mapping={}, rows=[])

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [header.strip() for header in (reader.fieldnames or [])]
    resolved = mapping or autodetect_columns(headers)

    rows: list[StatementRow] = []
    for index, raw_row in enumerate(reader, start=1):
        raw = {
            (key or "").strip(): (value or "").strip()
            for key, value in raw_row.items()
            if key is not None
        }
        if not any(raw.values()):
            continue

        row = StatementRow(row_number=index, raw=raw)

        amount_text = _read(raw, resolved, "amount")
        if not amount_text:
            row.errors.append("No amount in this row")
        else:
            amount = _parse_money(amount_text)
            if amount is None:
                row.errors.append(f"{amount_text!r} is not a valid amount")
            elif amount < 0:
                # A negative line is a deduction, not a payment. Treated as a
                # fee so it lands in the adjustments rather than reducing a
                # parcel's principal invisibly.
                row.fee_paisa = -amount
                row.amount_paisa = 0
            else:
                row.amount_paisa = amount

        row.consignment_id = _read(raw, resolved, "consignment_id") or None
        row.tracking_code = _read(raw, resolved, "tracking_code") or None
        row.merchant_reference = _read(raw, resolved, "merchant_reference") or None
        row.delivered_on = _parse_date(_read(raw, resolved, "delivered_on"))

        phone = normalize_digits(_read(raw, resolved, "phone"))
        digits = "".join(char for char in phone if char.isdigit())
        row.phone_last4 = digits[-4:] if len(digits) >= 4 else None

        fee_text = _read(raw, resolved, "fee")
        if fee_text:
            fee = _parse_money(fee_text)
            if fee is None:
                row.errors.append(f"{fee_text!r} is not a valid fee")
            else:
                row.fee_paisa = (row.fee_paisa or 0) + abs(fee)
        row.fee_label = _read(raw, resolved, "fee_label") or None

        rows.append(row)

    return ParsedStatement(headers=headers, mapping=resolved, rows=rows)
