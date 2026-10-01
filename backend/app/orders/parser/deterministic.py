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

__all__ = ["COLOR_EQUIVALENTS", "DeterministicOrderParser"]

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
    # Banglish, as customers type colours in chat.
    "kalo",
    "sada",
    "lal",
    "nil",
    "sobuj",
    "holud",
    "golapi",
)

#: The English name of each colour word, so a Bangla or Banglish colour can be
#: compared with a catalogue that names its products in English.
COLOR_EQUIVALENTS: dict[str, str] = {
    "কালো": "black",
    "kalo": "black",
    "সাদা": "white",
    "sada": "white",
    "লাল": "red",
    "lal": "red",
    "নীল": "blue",
    "nil": "blue",
    "সবুজ": "green",
    "sobuj": "green",
    "হলুদ": "yellow",
    "holud": "yellow",
    "গোলাপি": "pink",
    "golapi": "pink",
    "খয়েরি": "brown",
    "gray": "grey",
}

#: Counted quantities as customers write them in chat ("XL দুইটা").
_QUANTITY_WORDS: dict[str, int] = {
    "একটা": 1,
    "একটি": 1,
    "একখানা": 1,
    "ekta": 1,
    "akta": 1,
    "দুইটা": 2,
    "দুইটি": 2,
    "দুটো": 2,
    "দুটি": 2,
    "দুইটো": 2,
    "duita": 2,
    "duto": 2,
    "duiti": 2,
    "তিনটা": 3,
    "তিনটি": 3,
    "tinta": 3,
    "tinti": 3,
    "চারটা": 4,
    "চারটি": 4,
    "charta": 4,
    "পাঁচটা": 5,
    "পাঁচটি": 5,
    "pachta": 5,
}

#: Words that say what the customer wants rather than what the product is
#: ("পাঞ্জাবিটা লাগবে", "order korte chai"). Dropped from product names, and a
#: line made only of them carries no order detail.
_FILLER_WORDS = frozenset(
    {
        "লাগবে",
        "লাগবো",
        "চাই",
        "দিবেন",
        "দিন",
        "দেন",
        "নিবো",
        "নিব",
        "নেব",
        "নিতে",
        "অর্ডার",
        "করতে",
        "করবো",
        "করব",
        "করুন",
        "প্লিজ",
        "আমার",
        "আমি",
        "আমাকে",
        "এই",
        "এটা",
        "ওই",
        "ওটা",
        "ভাই",
        "আপু",
        "ভাইয়া",
        "একটু",
        "আছে",
        "টা",
        "টি",
        "lagbe",
        "chai",
        "diben",
        "den",
        "nibo",
        "nebo",
        "order",
        "korte",
        "korbo",
        "please",
        "plz",
        "pls",
        "ami",
        "amar",
        "amake",
        "ei",
        "eta",
        "oi",
        "ota",
        "bhai",
        "vai",
        "apu",
        "ache",
        "ase",
        "need",
        "want",
        "buy",
        "kinbo",
        "kinte",
        "ta",
        "ti",
    }
)

#: Words that mark a line as the customer asking for something, never a name.
_INTENT_WORDS = frozenset(
    {
        "লাগবে",
        "চাই",
        "দিবেন",
        "নিবো",
        "নেব",
        "অর্ডার",
        "lagbe",
        "chai",
        "diben",
        "nibo",
        "nebo",
        "order",
        "need",
        "want",
    }
)

#: Whole lines that are conversation, not order detail.
_NOISE_LINES = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "hlw",
        "helo",
        "salam",
        "assalamualaikum",
        "assalamu alaikum",
        "আসসালামু আলাইকুম",
        "আসসালামুয়ালাইকুম",
        "সালাম",
        "ok",
        "okay",
        "oke",
        "ঠিক আছে",
        "thanks",
        "thank you",
        "ধন্যবাদ",
        "ji",
        "জি",
        "হ্যাঁ",
        "yes",
        "no",
        "না",
    }
)

_EDGE_PUNCTUATION = ",.!?।:;()\"'"

#: The Bangla definite suffix on a noun ("পাঞ্জাবিটা" is "the panjabi").
_DEFINITE_SUFFIXES = ("টা", "টি", "খানা")

#: Currency markers. Their presence upgrades an amount from "a number" to
#: "an amount", which is the difference between MEDIUM and HIGH confidence.
_CURRENCY_TOKENS = ("৳", "tk", "taka", "bdt", "টাকা", "৳")

# A Bangla vowel sign is not a regex word character, so "২টি" never ends on a
# ``\b``: the counters end on a lookahead instead.
_QUANTITY_RE = re.compile(
    r"(?:^|\s)(?:(?P<qty1>\d{1,3})\s*(?:pcs?|pieces?|pc|টি|টা|খানা|পিস|ta|ti)(?=$|[\s,.!?।])"
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

#: Numbers that describe a measurement or a place, never an amount of money.
_MEASUREMENT_RE = re.compile(
    r"(?i)(?<![A-Za-z])(?:size|house|road|flat|block|sector|floor|apt|apartment|"
    r"সাইজ|বাসা|রোড|ব্লক|সেক্টর)\s*[:=#-]?\s*\d+\w*"
)

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
        lines = [
            line.strip()
            for line in normalized.splitlines()
            if line.strip() and not self._is_noise(line)
        ]

        labelled, unlabelled = self._split_labelled(lines)

        self._extract_phones(normalized, labelled, result)
        self._extract_amount(labelled, unlabelled, result)
        self._extract_address(labelled, unlabelled, result)
        # Name and items are decided together: the same bare line could be
        # either, and resolving them independently lets one steal the other's.
        self._extract_name_and_items(labelled, unlabelled, result)
        self._extract_notes(labelled, result)

        if result.is_empty:
            result.warnings.append("Could not read this text. Please enter the order manually.")
        return result

    # ----------------------------------------------------------------- noise --

    def _is_noise(self, line: str) -> bool:
        """A greeting, a thank-you or a question with no order detail in it.

        Chat transcripts are full of these. Kept, they would be read as the
        customer's name or as a product called "hello".
        """
        folded = " ".join(line.casefold().strip(_EDGE_PUNCTUATION + " ").split())
        if folded in _NOISE_LINES:
            return True
        if any(char.isdigit() for char in line):
            return False
        if self._looks_like_product(line) or any(hint in folded for hint in _ADDRESS_HINTS):
            return False
        if "?" in line:
            return True
        tokens = [token.strip(_EDGE_PUNCTUATION) for token in folded.split()]
        return bool(tokens) and all(not token or token in _FILLER_WORDS for token in tokens)

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
            lowered_line = line.lower()
            # An address line is full of numbers that are not money: house
            # numbers, road numbers, sector numbers.
            if any(hint in lowered_line for hint in _ADDRESS_HINTS):
                continue
            # "size 40" is a measurement, not forty taka.
            line = _MEASUREMENT_RE.sub(" ", line)
            has_currency = any(token in lowered_line for token in _CURRENCY_TOKENS)
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

    # ------------------------------------------------------- name / items --

    def _looks_like_product(self, line: str) -> bool:
        """Whether a line carries an unmistakable product signal.

        A size, a colour or a quantity is something a person's name never has,
        so these lines are products and are never offered as name candidates.
        """
        if _SIZE_RE.search(line) or _QUANTITY_RE.search(line):
            return True
        lowered = line.lower()
        if any(token.strip(_EDGE_PUNCTUATION) in _QUANTITY_WORDS for token in lowered.split()):
            return True
        return any(re.search(rf"(?:^|\s){re.escape(color)}(?=$|\s)", lowered) for color in _COLORS)

    def _extract_name_and_items(
        self,
        labelled: dict[str, list[str]],
        unlabelled: list[str],
        result: ParsedOrder,
    ) -> None:
        """Decide which bare lines are the customer's name and which are items.

        Resolved in one place because the two compete for the same lines. A
        message like ``Red Abaya XL`` on its own is a product, not a person, and
        letting name detection run first would swallow it and leave the order
        with no items at all.

        The order of preference:

        1. a labelled ``Name:`` or ``Product:`` line is taken at its word;
        2. any line with a size, colour or quantity is a product;
        3. a ``qty:`` label anywhere means the remaining line is the product it
           counts;
        4. otherwise the first remaining candidate is the name and the rest are
           products, because these messages almost always open with the name.
        """
        phone_digits = {phone.lstrip("+")[-10:] for phone in result.phones}

        candidates: list[str] = []
        product_lines: list[str] = []
        #: Every product or candidate line in the order it was written, so a
        #: follow-up such as "XL দুইটা" attaches to the product before it.
        ordered: list[str] = []

        for line in unlabelled:
            if line == result.address:
                continue
            if _NUMERIC_ONLY_RE.match(line) or self._looks_like_phone_line(line, phone_digits):
                continue
            lowered = line.lower()
            if any(hint in lowered for hint in _ADDRESS_HINTS):
                continue
            ordered.append(line)
            if self._looks_like_product(line):
                product_lines.append(line)
            else:
                candidates.append(line)

        # --- name ---
        if labelled.get("name"):
            result.customer_name = labelled["name"][0]
            result.confidence["name"] = FieldConfidence.EXACT
        elif candidates and not (
            # A quantity label implies the remaining line is what it counts.
            labelled.get("quantity")
            and not labelled.get("product")
            and len(candidates) == 1
            and not product_lines
        ):
            first = candidates[0]
            words = first.split()
            asks = any(word.casefold().strip(_EDGE_PUNCTUATION) in _INTENT_WORDS for word in words)
            if (
                1 <= len(words) <= 4
                and not any(char.isdigit() for char in first)
                and not asks
                and "?" not in first
            ):
                result.customer_name = first
                # Positional heuristic: usually right, which is exactly MEDIUM.
                result.confidence["name"] = FieldConfidence.MEDIUM
                ordered.remove(first)
            else:
                result.confidence["name"] = FieldConfidence.NONE
        else:
            result.confidence["name"] = FieldConfidence.NONE

        # --- items ---
        sources: list[tuple[str, bool]] = [(value, True) for value in labelled.get("product", [])]
        sources.extend((line, False) for line in ordered)

        shared_quantity: int | None = None
        if labelled.get("quantity"):
            match = re.search(r"\d{1,3}", labelled["quantity"][0])
            if match:
                shared_quantity = int(match.group())

        items: list[ParsedItem] = []
        saw_labelled = False
        #: Size or quantity said before the product it belongs to.
        pending: ParsedItem | None = None
        for raw_line, was_labelled in sources:
            item = self._parse_item_line(raw_line)
            if item is None:
                continue
            if not item.name:
                # "XL দুইটা" on its own line describes the product before it.
                if items:
                    self._merge_attributes(items[-1], item)
                elif pending is None:
                    pending = item
                else:
                    self._merge_attributes(pending, item)
                continue
            if pending is not None:
                self._merge_attributes(item, pending)
                pending = None
            if item.quantity == 1 and shared_quantity:
                item.quantity = shared_quantity
            items.append(item)
            saw_labelled = saw_labelled or was_labelled

        if not items:
            result.confidence["items"] = FieldConfidence.NONE
            result.warnings.append("No product line recognised")
            return

        result.items = items
        if saw_labelled:
            result.confidence["items"] = FieldConfidence.EXACT
        else:
            # Identified by elimination rather than by a label, so it remains a
            # judgement about which line was the product.
            result.confidence["items"] = (
                FieldConfidence.MEDIUM if len(items) == 1 else FieldConfidence.LOW
            )

    @staticmethod
    def _merge_attributes(item: ParsedItem, extra: ParsedItem) -> None:
        """Fold an attributes-only line into the product it describes."""
        item.size = item.size or extra.size
        item.color = item.color or extra.color
        if extra.quantity != 1 and item.quantity == 1:
            item.quantity = extra.quantity

    def _parse_item_line(self, line: str) -> ParsedItem | None:
        """Pull quantity, size and colour out of a product line.

        Each recognised attribute is removed from the working string as it is
        consumed, so the product name is what remains rather than the whole line
        with the price still glued to the end.

        A line that carries only attributes ("XL দুইটা") comes back with an empty
        name; the caller attaches it to the product it describes.
        """
        working = line.strip(" ,.-")
        if not working:
            return None

        quantity = 1
        counted = False
        words: list[str] = []
        for word in working.split():
            value = _QUANTITY_WORDS.get(word.casefold().strip(_EDGE_PUNCTUATION))
            if value is not None and not counted:
                quantity, counted = value, True
            elif value is None:
                words.append(word)
        working = " ".join(words)

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
                    quantity, counted = parsed, True
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

        # Strip a trailing price so it does not become part of the name.
        working = _AMOUNT_RE.sub(" ", working)
        name = " ".join(self._product_words(working)).strip(" ,.-?!।")

        # A line that reduced to nothing but a size or a colour is not a
        # product of its own, but it still says something about one.
        if not name or len(name) < 2 or name.lower() in _SIZES or name.lower() in _COLORS:
            if size or counted or (color and name.lower() in _COLORS):
                return ParsedItem(name="", quantity=quantity, size=size, color=color)
            return None

        return ParsedItem(name=name, quantity=quantity, size=size, color=color)

    @staticmethod
    def _product_words(text: str) -> list[str]:
        """The words of a product line without what the customer asked with it."""
        kept: list[str] = []
        for word in text.split():
            folded = word.casefold().strip(_EDGE_PUNCTUATION)
            if not folded or folded in _FILLER_WORDS:
                continue
            for suffix in _DEFINITE_SUFFIXES:
                if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                    word = word[: -len(suffix)]
                    break
            kept.append(word.strip(_EDGE_PUNCTUATION))
        return [word for word in kept if word]

    # ---------------------------------------------------------------- notes --

    def _extract_notes(self, labelled: dict[str, list[str]], result: ParsedOrder) -> None:
        if labelled.get("note"):
            result.notes = " ".join(labelled["note"])
