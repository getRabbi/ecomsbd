"""Phone normalization, including Bangla numerals.

Master spec sections 8 and 129.
"""

from __future__ import annotations

import pytest

from app.common.phone import (
    extract_phones,
    is_valid_bd_mobile,
    normalize_bd_phone,
    normalize_digits,
    try_normalize_bd_phone,
)
from app.core.errors import ErrorCode, ValidationError

CANONICAL = "+8801712345678"


class TestDigitNormalization:
    def test_bangla_digits(self) -> None:
        assert normalize_digits("০১৭১২৩৪৫৬৭৮") == "01712345678"

    def test_mixed_script(self) -> None:
        assert normalize_digits("০১৭12345678") == "01712345678"

    def test_eastern_arabic_digits(self) -> None:
        assert normalize_digits("٠١٧١٢٣٤٥٦٧٨") == "01712345678"

    def test_full_width_digits(self) -> None:
        assert normalize_digits("０１７１２３４５６７８") == "01712345678"

    def test_non_numeric_text_is_untouched(self) -> None:
        assert normalize_digits("Mirpur 10, Dhaka") == "Mirpur 10, Dhaka"


class TestNormalization:
    @pytest.mark.parametrize(
        "raw",
        [
            "01712345678",
            "+8801712345678",
            "8801712345678",
            "008801712345678",
            "017-1234-5678",
            "017 1234 5678",
            "(017) 1234 5678",
            "০১৭১২৩৪৫৬৭৮",
            "+৮৮০১৭১২৩৪৫৬৭৮",
            "  01712345678  ",
        ],
    )
    def test_accepted_shapes_all_normalize_the_same(self, raw: str) -> None:
        assert normalize_bd_phone(raw).e164 == CANONICAL

    @pytest.mark.parametrize("prefix", ["013", "014", "015", "016", "017", "018", "019"])
    def test_every_allocated_operator_prefix(self, prefix: str) -> None:
        assert is_valid_bd_mobile(f"{prefix}12345678")

    @pytest.mark.parametrize(
        "raw",
        [
            "01012345678",  # 010 is not allocated to mobile
            "01112345678",
            "01212345678",
            "0171234567",  # too short
            "017123456789",  # too long
            "+9171234567890",  # not Bangladesh
            "abcdefghijk",
            "",
            None,
        ],
    )
    def test_rejected_inputs(self, raw: str | None) -> None:
        assert try_normalize_bd_phone(raw) is None

    def test_invalid_raises_a_typed_error(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            normalize_bd_phone("01012345678")
        assert excinfo.value.code is ErrorCode.INVALID_PHONE_NUMBER

    def test_derived_fields(self) -> None:
        number = normalize_bd_phone("01712345678")
        assert number.national == "01712345678"
        assert number.operator_prefix == "017"
        assert number.last4 == "5678"
        # Master spec section 101 masking format.
        assert number.masked == "01712****78"


class TestExtraction:
    def test_finds_every_number_and_never_picks_one(self) -> None:
        # Section 8: with several candidates the caller must ask the seller.
        text = "Order for 01712345678, alt ০১৮১৯২২৩৩৪৪ please deliver"
        found = extract_phones(text)
        assert [p.e164 for p in found] == ["+8801712345678", "+8801819223344"]

    def test_deduplicates_the_same_number_in_different_shapes(self) -> None:
        text = "01712345678 and +8801712345678 and ০১৭১২৩৪৫৬৭৮"
        assert len(extract_phones(text)) == 1

    def test_realistic_messenger_paste(self) -> None:
        text = "Nusrat\n০১৭১২৩৪৫৬৭৮\nMirpur 10, Dhaka\nBlack Abaya XL\n1250 taka"
        assert [p.e164 for p in extract_phones(text)] == [CANONICAL]

    def test_no_numbers_returns_empty(self) -> None:
        assert extract_phones("Mirpur 10, Dhaka") == []
        assert extract_phones("") == []
