"""Bangladeshi phone normalization.

Master spec section 8. Canonical form is ``+8801XXXXXXXXX``. Accepted inputs
include ``01712345678``, ``+8801712345678``, ``8801712345678``, Bangla digits,
spaces and hyphens.

Section 8 also states the rule that matters most: **if multiple phone numbers
are found, require explicit selection — never silently choose an ambiguous
number.** Picking the wrong number sends a parcel to the wrong person and turns
into a COD loss, so :func:`extract_phones` returns every candidate and refuses
to guess.

Bangla numeral normalization (section 129) runs before every search and parse,
because sellers paste Messenger text that mixes ``০১৭`` and ``017`` freely.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.core.errors import ErrorCode, ValidationError

__all__ = [
    "BD_COUNTRY_CODE",
    "PhoneNumber",
    "extract_phones",
    "is_valid_bd_mobile",
    "normalize_bd_phone",
    "normalize_digits",
    "try_normalize_bd_phone",
]

BD_COUNTRY_CODE = "880"

#: Bangla-Assamese digits U+09E6..U+09EF, in value order.
_BANGLA_DIGITS = "০১২৩৪৫৬৭৮৯"
#: Eastern Arabic digits, occasionally pasted from Arabic-locale keyboards.
_ARABIC_INDIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"

_DIGIT_TRANSLATION = str.maketrans(
    {
        **{char: str(index) for index, char in enumerate(_BANGLA_DIGITS)},
        **{char: str(index) for index, char in enumerate(_ARABIC_INDIC_DIGITS)},
    }
)

#: Bangladeshi mobile numbers are 11 digits national: 01 + operator + 8 digits.
#: Operator prefixes in use: 013 Grameenphone, 014 Banglalink, 015 Teletalk,
#: 016 Airtel, 017 Grameenphone, 018 Robi, 019 Banglalink. 010/011/012 are not
#: allocated to mobile, so they are rejected rather than silently accepted.
_NATIONAL_RE = re.compile(r"^01[3-9]\d{8}$")

#: Scans free text for anything phone-shaped, including separator noise.
_CANDIDATE_RE = re.compile(r"(?:\+?88)?[\s\-().]*0?1[\s\-().]*[3-9](?:[\s\-().]*\d){8}")

_NON_DIGIT_RE = re.compile(r"\D")


def normalize_digits(text: str) -> str:
    """Convert Bangla and Eastern-Arabic digits to ASCII, and normalise width.

    Applied before phone parsing, amount parsing and search (section 129).
    """
    if not text:
        return text
    # NFKC folds full-width digits and compatibility forms to their ASCII form.
    return unicodedata.normalize("NFKC", text).translate(_DIGIT_TRANSLATION)


@dataclass(frozen=True, slots=True)
class PhoneNumber:
    """A validated Bangladeshi mobile number."""

    e164: str
    national: str
    operator_prefix: str

    @property
    def last4(self) -> str:
        """Stored alongside the encrypted number for support lookups (section 133)."""
        return self.national[-4:]

    @property
    def masked(self) -> str:
        """``01712****78`` — the only form allowed in logs and admin views."""
        return f"{self.national[:5]}****{self.national[-2:]}"

    def __str__(self) -> str:
        return self.e164


def _to_national(raw: str) -> str | None:
    """Reduce any accepted input shape to 11-digit national form."""
    digits = _NON_DIGIT_RE.sub("", normalize_digits(raw))
    if not digits:
        return None

    if digits.startswith("00" + BD_COUNTRY_CODE):
        digits = digits[len("00" + BD_COUNTRY_CODE) :]
    elif digits.startswith(BD_COUNTRY_CODE):
        digits = digits[len(BD_COUNTRY_CODE) :]

    # After stripping the country code the number may be missing its trunk zero.
    if len(digits) == 10 and digits.startswith("1"):
        digits = "0" + digits

    return digits


def try_normalize_bd_phone(raw: str | None) -> PhoneNumber | None:
    """Normalize to canonical form, or ``None`` if the input is not valid."""
    if not raw:
        return None
    national = _to_national(raw)
    if national is None or not _NATIONAL_RE.match(national):
        return None
    return PhoneNumber(
        e164=f"+{BD_COUNTRY_CODE}{national[1:]}",
        national=national,
        operator_prefix=national[:3],
    )


def normalize_bd_phone(raw: str | None) -> PhoneNumber:
    """Normalize to canonical form, raising a typed error on failure."""
    number = try_normalize_bd_phone(raw)
    if number is None:
        raise ValidationError(
            "Not a valid Bangladeshi mobile number",
            code=ErrorCode.INVALID_PHONE_NUMBER,
        )
    return number


def is_valid_bd_mobile(raw: str | None) -> bool:
    return try_normalize_bd_phone(raw) is not None


def extract_phones(text: str) -> list[PhoneNumber]:
    """Find every distinct valid phone number in free text, in order of appearance.

    Returns all candidates rather than the first: master spec section 8 forbids
    silently choosing among several numbers, so the caller must ask the seller
    when this returns more than one.
    """
    if not text:
        return []

    normalized = normalize_digits(text)
    seen: set[str] = set()
    found: list[PhoneNumber] = []

    for match in _CANDIDATE_RE.finditer(normalized):
        number = try_normalize_bd_phone(match.group(0))
        if number is not None and number.e164 not in seen:
            seen.add(number.e164)
            found.append(number)
    return found
