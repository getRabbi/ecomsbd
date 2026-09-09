"""Deterministic paste parser.

Master spec sections 7.1, 8 and 96.

The rule these tests enforce hardest is the one that costs money if broken:
**the parser leaves a field empty rather than guessing it.** A blank address the
seller fills in costs ten seconds; a confidently wrong address that gets booked
costs a parcel, a delivery charge and a return charge.
"""

from __future__ import annotations

import pytest

from app.orders.parser import DeterministicOrderParser, EnhancedOrderParser
from app.orders.parser.base import FieldConfidence, OrderTextParser


@pytest.fixture
def parser() -> DeterministicOrderParser:
    return DeterministicOrderParser()


class TestRealisticMessages:
    def test_the_canonical_messenger_paste(self, parser: DeterministicOrderParser) -> None:
        # The exact shape from master spec section 7.1: no labels, no order.
        result = parser.parse("Nusrat\n01712345678\nMirpur 10, Dhaka\nBlack Abaya XL\n1250 taka")

        assert result.selected_phone == "+8801712345678"
        assert result.confidence["phone"] == FieldConfidence.EXACT
        assert result.address is not None
        assert "Mirpur" in result.address
        assert result.cod_amount_paisa == 125_000
        assert result.items
        assert "Abaya" in result.items[0].name
        assert result.items[0].size == "XL"

    def test_bangla_numerals_throughout(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse("রফিক\n০১৮১৯২২৩৩৪৪\nউত্তরা, ঢাকা\nWatch X1\n১৮৫০ টাকা")

        assert result.selected_phone == "+8801819223344"
        assert result.cod_amount_paisa == 185_000

    def test_labelled_fields_are_trusted_exactly(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse(
            "Name: Mim Akter\n"
            "Phone: 01612340004\n"
            "Address: House 12, Road 3, Savar\n"
            "Product: Bag A2\n"
            "Amount: 990"
        )

        assert result.customer_name == "Mim Akter"
        assert result.confidence["name"] == FieldConfidence.EXACT
        assert result.selected_phone == "+8801612340004"
        assert result.address == "House 12, Road 3, Savar"
        assert result.confidence["address"] == FieldConfidence.EXACT
        assert result.cod_amount_paisa == 99_000
        assert result.confidence["amount"] == FieldConfidence.EXACT

    def test_bangla_labels(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse("নাম: নুসরাত\nমোবাইল: 01712345678\nঠিকানা: মিরপুর ১০\nদাম: 1250")
        assert result.customer_name == "নুসরাত"
        assert result.selected_phone == "+8801712345678"
        assert result.cod_amount_paisa == 125_000

    def test_quantity_forms(self, parser: DeterministicOrderParser) -> None:
        for text, expected in [
            ("Black Abaya 2 pcs", 2),
            ("Black Abaya x3", 3),
            ("Black Abaya\nqty: 4", 4),
        ]:
            result = parser.parse(f"01712345678\n{text}")
            assert result.items, text
            assert result.items[0].quantity == expected, text

    def test_colour_and_size_are_split_out(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse("01712345678\nRed Abaya XL")
        assert result.items
        assert result.items[0].size == "XL"
        assert result.items[0].color == "Red"


class TestPhoneRules:
    def test_several_numbers_are_never_chosen_for_the_seller(
        self, parser: DeterministicOrderParser
    ) -> None:
        # Master spec section 8: "never silently choose an ambiguous number."
        # Picking wrong here sends the parcel to the wrong person.
        result = parser.parse("Nusrat\n01712345678\nalt 01819223344\nMirpur 10\n1250")

        assert len(result.phones) == 2
        assert result.selected_phone is None
        assert result.needs_phone_selection is True
        assert any("choose" in w.lower() for w in result.warnings)

    def test_no_phone_is_reported_not_invented(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse("Nusrat\nMirpur 10\n1250")
        assert result.phones == []
        assert result.selected_phone is None
        assert result.confidence["phone"] == FieldConfidence.NONE

    def test_an_invalid_operator_prefix_is_not_a_phone(
        self, parser: DeterministicOrderParser
    ) -> None:
        # 010 is not allocated to mobile in Bangladesh.
        result = parser.parse("Nusrat\n01012345678\nMirpur 10")
        assert result.phones == []


class TestAmountRules:
    def test_a_phone_number_is_never_read_as_an_amount(
        self, parser: DeterministicOrderParser
    ) -> None:
        # The most obvious way to get this catastrophically wrong.
        result = parser.parse("01712345678\nBlack Abaya\n1250")
        assert result.cod_amount_paisa == 125_000

    def test_a_currency_marker_beats_a_bare_number(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse("01712345678\nBlack Abaya 2 pcs\nTotal 2500 tk")
        assert result.cod_amount_paisa == 250_000
        assert result.confidence["amount"] >= FieldConfidence.HIGH

    def test_several_bare_amounts_lower_the_confidence(
        self, parser: DeterministicOrderParser
    ) -> None:
        result = parser.parse("01712345678\nAbaya 1250\nBag 990")
        assert result.confidence["amount"] <= FieldConfidence.LOW
        assert any("amount" in w.lower() for w in result.warnings)

    def test_implausible_numbers_are_ignored(self, parser: DeterministicOrderParser) -> None:
        # A size or a house number is not a COD amount.
        result = parser.parse("01712345678\nAbaya size 40\nHouse 12")
        assert result.cod_amount_paisa is None


class TestRefusalToGuess:
    def test_unreadable_text_returns_empty_fields_and_a_warning(
        self, parser: DeterministicOrderParser
    ) -> None:
        result = parser.parse("hey are you there??")
        assert result.is_empty
        assert result.warnings
        assert result.customer_name is None or result.customer_name == "hey are you there??"

    def test_the_original_text_is_always_returned(self, parser: DeterministicOrderParser) -> None:
        # Section 96: "parsing failure returns editable original text, never
        # loses order data."
        original = "!!! nothing useful here !!!"
        assert parser.parse(original).source_text == original

    def test_empty_input_is_handled(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse("")
        assert result.is_empty
        assert result.source_text == ""

    def test_overlong_input_is_truncated_but_not_discarded(
        self, parser: DeterministicOrderParser
    ) -> None:
        original = "01712345678\n" + ("x" * 9000)
        result = parser.parse(original)
        assert result.source_text == original, "the seller's text is never lost"
        assert any("longer than" in w for w in result.warnings)

    def test_low_confidence_is_reported(self, parser: DeterministicOrderParser) -> None:
        # Drives the "try enhanced parse" affordance (section 96), never an
        # automatic action.
        assert parser.parse("just some words").is_low_confidence is True


class TestParserContract:
    def test_the_deterministic_parser_satisfies_the_protocol(
        self, parser: DeterministicOrderParser
    ) -> None:
        assert isinstance(parser, OrderTextParser)
        assert parser.name == "deterministic"

    def test_the_response_shape_matches_the_spec(self, parser: DeterministicOrderParser) -> None:
        payload = parser.parse("01712345678\nAbaya\n1250").to_json()
        for key in (
            "customer_name",
            "phones",
            "selected_phone",
            "address",
            "items",
            "cod_amount_paisa",
            "notes",
            "confidence",
        ):
            assert key in payload, key
        for field in ("phone", "address", "items", "amount"):
            assert field in payload["confidence"], field

    def test_the_enhanced_parser_refuses_rather_than_pretending(self) -> None:
        # It must not quietly fall through to the deterministic parser: that
        # would report an LLM parse that never happened, and would hide the
        # quota and prompt-injection work section 97 requires.
        with pytest.raises(NotImplementedError, match="not implemented"):
            EnhancedOrderParser().parse("anything")

    def test_the_parser_needs_no_network_or_model(self, parser: DeterministicOrderParser) -> None:
        # Section 97: "AI outage must not block business operations." This test
        # documents that order entry has no such dependency at all.
        import app.orders.parser.deterministic as module

        source = module.__file__
        assert source is not None
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        for forbidden in ("httpx", "requests", "openai", "anthropic", "aiohttp"):
            assert forbidden not in text, f"parser must not import {forbidden}"
