"""Deterministic order parser (Layer 1).

Master spec section 7.1 lists what this must attempt: Bangla digit
normalization, BD phone normalization, lines/colon parsing, likely-name
detection, longest-address candidate, amount/currency extraction, size/colour
patterns and product token matching.

The governing rule is section 7.1's "never auto-book" plus section 96's "never
lose order data". Concretely, that means this parser **would rather return an
empty field than a wrong one**. A blank address the seller fills in costs ten
seconds; a confidently wrong address that gets booked costs a parcel, a delivery
charge and a return charge.

Real input looks like this — no labels, no consistent order, mixed scripts::

    Nusrat
    01712345678
    Mirpur 10
    Black Abaya XL
    2 pcs
    1250

so the parser works line by line, classifying each line by what it contains
rather than by where it sits.
"""

from __future__ import annotations

import re

from app.common.money import paisa_from_taka
from app.common.phone import extract_phones, normalize_digits
from app.orders.parser.base import (
    MAX_PARSE_INPUT_LENGTH,
    FieldConfidence,
    ParsedItem,
    ParsedOrder,
)

__all__ = ["DeterministicOrderParser"]

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

#: Field labels sellers actually type, in Bangla and Banglish. A labelled line
#: is the strongest signal available, so these are checked first.
_LABELS: dict[str, tuple[str, ...]] = {
    "name": ("name", "নাম", "customer", "কাস্টমার", "ক্রেতা"),
    "phone": ("phone", "mobile", "number", "মোবাইল", "নাম্বার", "নম্বর", "ফোন", "contact"),
    "address": ("address", "ঠিকানা", "location", "এড্রেস", "ঠিকান"),
    "amount": ("amount", "total", "price", "cod", "taka", "tk", "দাম", "টাকা", "মোট", "মূল্য"),
    "product": ("product", "item", "order", "পণ্য", "প্রোডাক্ট", "আইটেম"),
    "note": ("note", "remark", "নোট", "মন্তব্য"),
    "quantity": ("qty", "quantity", "pcs", "piece", "পিস", "পরিমাণ"),
}

#: Words that indicate a line is describing where to deliver.
_ADDRESS_HINTS = (
    "road",
    "rd",
    "house",
    "flat",
    "block",
    "sector",
    "lane",
    "village",
    "po",
    "thana",
    "upazila",
    "district",
    "dhaka",
    "chittagong",
    "chattogram",
    "sylhet",
    "khulna",
    "rajshahi",
    "barisal",
    "rangpur",
    "mymensingh",
    "mirpur",
    "uttara",
    "gulshan",
    "banani",
    "dhanmondi",
    "savar",
    "gazipur",
    "narayanganj",
    "bashundhara",
    "রোড",
    "বাসা",
    "ফ্ল্যাট",
    "ব্লক",
    "সেক্টর",
    "গ্রাম",
    "থানা",
    "জেলা",
    "ঢাকা",
    "মিরপুর",
    "উত্তরা",
    "গুলশান",
    "বনানী",
    "ধানমন্ডি",
    "সাভার",
    "চট্টগ্রাম",
)

#: Clothing sizes, matched as whole tokens so "XL" in "XLARGE BAG" is not a size.
_SIZES = ("xs", "s", "m", "l", "xl", "xxl", "xxxl", "2xl", "3xl", "free size", "freesize")

_COLORS = (
    "black",
    "white",
    "red",
    "blue",
    "green",
    "yellow",
    "pink",
    "purple",
    "orange",
    "brown",
    "grey",
    "gray",
    "navy",
    "maroon",
    "beige",
    "cream",
    "gold",
    "silver",
    "কালো",
    "সাদা",
    "লাল",
    "নীল",
    "সবুজ",
    "হলুদ",
    "গোলাপি",
    "খয়েরি",
)

#: Currency markers. Their presence upgrades an amount from "a number" to
#: "an amount", which is the difference between MEDIUM and HIGH confidence.
_CURRENCY_TOKENS = ("৳", "tk", "taka", "bdt", "টাকা", "৳")

_QUANTITY_RE = re.compile(
    r"(?:^|\s)(?:(?P<qty1>\d{1,3})\s*(?:pcs?|piece|pieces|pc|টি|পিস)\b"
    r"|(?:qty|quantity|পরিমাণ)\s*[:=-]?\s*(?P<qty2>\d{1,3})\b"
    r"|x\s*(?P<qty3>\d{1,3})\b)",
    re.IGNORECASE,
)

_AMOUNT_RE = re.compile(
    r"(?<![\d.])(?:৳|tk\.?|taka|bdt)?\s*(\d{2,7}(?:[,\s]\d{3})*(?:\.\d{1,2})?)\s*"
    r"(?:৳|tk\.?|taka|bdt|টাকা)?(?![\d.])",
    re.IGNORECASE,
)

_SIZE_RE = re.compile(
    r"(?:^|\s|,)(?:size\s*[:=-]?\s*)?("
    + "|".join(re.escape(s) for s in _SIZES)
    + r")(?=$|\s|,|\.)",
    re.IGNORECASE,
)

_LABEL_SPLIT_RE = re.compile(r"^\s*([^:：]{1,24})\s*[:：]\s*(.*)$")

#: A line that is only digits and separators is a phone or an amount, never a
#: product name.
_NUMERIC_ONLY_RE = re.compile(r"^[\d\s\-+(),.৳]+$")


class DeterministicOrderParser:
    """Rule-based parser. No network, no model, no external dependency."""

    name = "deterministic"

    def parse(self, text: str) -> ParsedOrder:
        original = text or ""
        result = ParsedOrder(source_text=original)

        if not original.strip():
            result.warnings.append("Nothing to parse")
            return result

        if len(original) > MAX_PARSE_INPUT_LENGTH:
            # Truncate for processing but keep the seller's full text on the
            # result, so nothing they pasted is lost (section 96).
            result.warnings.append(
                f"Text longer than {MAX_PARSE_INPUT_LENGTH} characters; "
                "only the beginning was parsed"
            )
            working = original[:MAX_PARSE_INPUT_LENGTH]
        else:
            working = original

        normalized = normalize_digits(working)
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]

        labelled, unlabelled = self._split_labelled(lines)

        self._extract_phones(normalized, labelled, result)
        self._extract_amount(labelled, unlabelled, result)
        self._extract_address(labelled, unlabelled, result)
        self._extract_name(labelled, unlabelled, result)
        self._extract_items(labelled, unlabelled, result)
        self._extract_notes(labelled, result)

        if result.is_empty:
            result.warnings.append("Could not read this text. Please enter the order manually.")
        return result

    # ------------------------------------------------------------ labelling --

    def _split_labelled(self, lines: list[str]) -> tuple[dict[str, list[str]], list[str]]:
        """Separate ``Label: value`` lines from bare ones.

        A labelled line is unambiguous and is trusted; a bare line has to be
        classified by content, which is where the guessing risk lives.
        """
        labelled: dict[str, list[str]] = {}
        unlabelled: list[str] = []

        for line in lines:
            match = _LABEL_SPLIT_RE.match(line)
            if match is None:
                unlabelled.append(line)
                continue

            raw_label = match.group(1).strip().lower()
            value = match.group(2).strip()
            field = self._classify_label(raw_label)

            if field is None or not value:
                # An unrecognised label is still content: "Delivery: Mirpur 10"
                # should not be dropped because "Delivery" is not in the list.
                unlabelled.append(line)
                continue
            labelled.setdefault(field, []).append(value)

        return labelled, unlabelled

    @staticmethod
    def _classify_label(raw_label: str) -> str | None:
        for field, keywords in _LABELS.items():
            if any(keyword in raw_label for keyword in keywords):
                return field
        return None

    # --------------------------------------------------------------- phones --

    def _extract_phones(
        self, normalized: str, labelled: dict[str, list[str]], result: ParsedOrder
    ) -> None:
        # A labelled phone line wins; otherwise scan the whole text.
        source = " ".join(labelled.get("phone", [])) or normalized
        found = extract_phones(source)
        if not found and labelled.get("phone"):
            found = extract_phones(normalized)

        result.phones = [number.e164 for number in found]

        if len(found) == 1:
            result.selected_phone = found[0].e164
            result.confidence["phone"] = FieldConfidence.EXACT
        elif len(found) > 1:
            # Section 8: never silently choose. The seller picks.
            result.selected_phone = None
            result.confidence["phone"] = FieldConfidence.MEDIUM
            result.warnings.append(f"{len(found)} phone numbers found — choose the delivery number")
        else:
            result.confidence["phone"] = FieldConfidence.NONE
            result.warnings.append("No Bangladeshi mobile number found")

    # --------------------------------------------------------------- amount --

    def _extract_amount(
        self,
        labelled: dict[str, list[str]],
        unlabelled: list[str],
        result: ParsedOrder,
    ) -> None:
        if labelled.get("amount"):
            amount = self._first_amount(labelled["amount"][0])
            if amount is not None:
                result.cod_amount_paisa = amount
                result.confidence["amount"] = FieldConfidence.EXACT
                return

        phone_digits = {phone.lstrip("+")[-10:] for phone in result.phones}
        candidates: list[tuple[int, bool]] = []

        for line in unlabelled:
            if self._looks_like_phone_line(line, phone_digits):
                continue
            has_currency = any(token in line.lower() for token in _CURRENCY_TOKENS)
            for raw in _AMOUNT_RE.findall(line):
                value = self._to_paisa(raw)
                if value is None:
                    continue
                # Below ৳20 is far more likely a quantity or a size than a COD
                # amount; above ৳10,00,000 is not a realistic F-commerce order.
                if not (2_000 <= value <= 100_000_000):
                    continue
                candidates.append((value, has_currency))

        if not candidates:
            result.confidence["amount"] = FieldConfidence.NONE
            return

        with_currency = [value for value, marked in candidates if marked]
        if with_currency:
            result.cod_amount_paisa = max(with_currency)
            result.confidence["amount"] = FieldConfidence.HIGH
            return

        # No currency marker: the largest plausible number is the usual answer,
        # but it is a guess, so it is reported as one.
        result.cod_amount_paisa = max(value for value, _ in candidates)
        result.confidence["amount"] = (
            FieldConfidence.MEDIUM if len(candidates) == 1 else FieldConfidence.LOW
        )
        if len(candidates) > 1:
            result.warnings.append("Several amounts found — check the COD value")

    @staticmethod
    def _looks_like_phone_line(line: str, phone_digits: set[str]) -> bool:
        digits = re.sub(r"\D", "", line)
        return bool(digits) and any(digits.endswith(tail) for tail in phone_digits)

    def _first_amount(self, text: str) -> int | None:
        match = _AMOUNT_RE.search(text)
        return self._to_paisa(match.group(1)) if match else None

    @staticmethod
    def _to_paisa(raw: str) -> int | None:
        cleaned = raw.replace(",", "").replace(" ", "").strip()
        if not cleaned:
            return None
        try:
            return paisa_from_taka(cleaned)
        except (ValueError, TypeError):
            return None

    # -------------------------------------------------------------- address --

    def _extract_address(
        self,
        labelled: dict[str, list[str]],
        unlabelled: list[str],
        result: ParsedOrder,
    ) -> None:
        if labelled.get("address"):
            result.address = labelled["address"][0]
            result.confidence["address"] = FieldConfidence.EXACT
            return

        phone_digits = {phone.lstrip("+")[-10:] for phone in result.phones}
        scored: list[tuple[int, int, str]] = []

        for line in unlabelled:
            if _NUMERIC_ONLY_RE.match(line) or self._looks_like_phone_line(line, phone_digits):
                continue
            lowered = line.lower()
            hits = sum(1 for hint in _ADDRESS_HINTS if hint in lowered)
            if hits:
                scored.append((hits, len(line), line))

        if scored:
            # Most place-name hits first, then the longest — section 7.1's
            # "longest-address candidate", but only among lines that actually
            # look like addresses.
            scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
            result.address = scored[0][2]
            result.confidence["address"] = (
                FieldConfidence.HIGH if scored[0][0] >= 2 else FieldConfidence.MEDIUM
            )
            return

        result.confidence["address"] = FieldConfidence.NONE
        result.warnings.append("No delivery address recognised")

    # ----------------------------------------------------------------- name --

    def _extract_name(
        self,
        labelled: dict[str, list[str]],
        unlabelled: list[str],
        result: ParsedOrder,
    ) -> None:
        if labelled.get("name"):
            result.customer_name = labelled["name"][0]
            result.confidence["name"] = FieldConfidence.EXACT
            return

        phone_digits = {phone.lstrip("+")[-10:] for phone in result.phones}

        for line in unlabelled:
            if line == result.address:
                continue
            if _NUMERIC_ONLY_RE.match(line) or self._looks_like_phone_line(line, phone_digits):
                continue
            lowered = line.lower()
            if any(hint in lowered for hint in _ADDRESS_HINTS):
                continue
            words = line.split()
            # A name is short, has no digits and no currency. Anything longer is
            # more likely a product line or a note.
            if 1 <= len(words) <= 4 and not any(char.isdigit() for char in line):
                if any(token in lowered for token in _CURRENCY_TOKENS):
                    continue
                result.customer_name = line
                # Positional heuristic: the first short wordy line is usually the
                # name, but "usually" is exactly MEDIUM.
                result.confidence["name"] = FieldConfidence.MEDIUM
                return

        result.confidence["name"] = FieldConfidence.NONE

    # ---------------------------------------------------------------- items --

    def _extract_items(
        self,
        labelled: dict[str, list[str]],
        unlabelled: list[str],
        result: ParsedOrder,
    ) -> None:
        sources: list[tuple[str, bool]] = [(value, True) for value in labelled.get("product", [])]

        phone_digits = {phone.lstrip("+")[-10:] for phone in result.phones}
        for line in unlabelled:
            if line in (result.address, result.customer_name):
                continue
            if _NUMERIC_ONLY_RE.match(line) or self._looks_like_phone_line(line, phone_digits):
                continue
            lowered = line.lower()
            if any(hint in lowered for hint in _ADDRESS_HINTS):
                continue
            sources.append((line, False))

        shared_quantity: int | None = None
        if labelled.get("quantity"):
            match = re.search(r"\d{1,3}", labelled["quantity"][0])
            if match:
                shared_quantity = int(match.group())

        items: list[ParsedItem] = []
        for raw_line, was_labelled in sources:
            item = self._parse_item_line(raw_line)
            if item is None:
                continue
            if item.quantity == 1 and shared_quantity:
                item.quantity = shared_quantity
            items.append(item)
            if was_labelled:
                result.confidence["items"] = FieldConfidence.EXACT

        if not items:
            result.confidence["items"] = FieldConfidence.NONE
            result.warnings.append("No product line recognised")
            return

        result.items = items
        if result.confidence["items"] != FieldConfidence.EXACT:
            # Detected by elimination rather than by a label, so it is a guess
            # about which line was the product.
            result.confidence["items"] = (
                FieldConfidence.MEDIUM if len(items) == 1 else FieldConfidence.LOW
            )

    def _parse_item_line(self, line: str) -> ParsedItem | None:
        working = line.strip(" ,.-")
        if not working:
            return None

        quantity = 1
        quantity_match = _QUANTITY_RE.search(working)
        if quantity_match is not None:
            raw = (
                quantity_match.group("qty1")
                or quantity_match.group("qty2")
                or quantity_match.group("qty3")
            )
            if raw and raw.isdigit():
                parsed = int(raw)
                if 1 <= parsed <= 999:
                    quantity = parsed
            working = _QUANTITY_RE.sub(" ", working)

        size: str | None = None
        size_match = _SIZE_RE.search(working)
        if size_match is not None:
            size = size_match.group(1).upper()
            working = working[: size_match.start()] + " " + working[size_match.end() :]

        color: str | None = None
        for candidate in _COLORS:
            if re.search(rf"(?:^|\s){re.escape(candidate)}(?=$|\s)", working, re.IGNORECASE):
                color = candidate.title() if candidate.isascii() else candidate
                break

        # Strip a trailing price so the product name does not absorb it.
        working = _AMOUNT_RE.sub(" ", working)
        name = " ".join(working.split()).strip(" ,.-")

        if not name or len(name) < 2:
            return None
        # A line that became only a size or colour after stripping is not a
        # product name.
        if name.lower() in _SIZES or name.lower() in _COLORS:
            return None

        return ParsedItem(name=name, quantity=quantity, size=size, color=color)

    # ---------------------------------------------------------------- notes --

    def _extract_notes(self, labelled: dict[str, list[str]], result: ParsedOrder) -> None:
        if labelled.get("note"):
            result.notes = " ".join(labelled["note"])
