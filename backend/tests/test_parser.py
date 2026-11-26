"""Deterministic paste parser.

Master spec sections 7.1, 8 and 96.

The rule these tests enforce hardest is the one that costs money if broken:
**the parser leaves a field empty rather than guessing it.** A blank address the
seller fills in costs ten seconds; a confidently wrong address that gets booked
costs a parcel, a delivery charge and a return charge.
"""

from __future__ import annotations

import pytest

from app.orders.parser import DeterministicOrderParser
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

    async def test_the_enhanced_parser_never_pretends_a_model_ran(
        self, system_db, settings
    ) -> None:  # type: ignore[no-untyped-def]
        # V4 WP0.3 replaced the "not implemented" stub. The rule it guarded
        # stands: with no provider configured the result must not report an
        # LLM parse that never happened. It is labelled deterministic and
        # carries AI_PARSE_UNAVAILABLE, and order entry still gets the fields.
        from app.common.cache import InMemoryBackend
        from app.common.feature_flags import FeatureFlag, FlagKey, FlagScope
        from app.orders.parser.enhanced import AI_PARSE_UNAVAILABLE, EnhancedOrderParser
        from app.tenants.models import Tenant

        tenant = Tenant(name="Boundary Shop", business_category="CLOTHING")
        system_db.add(tenant)
        await system_db.flush()
        system_db.add(
            FeatureFlag(
                key=str(FlagKey.AI_PARSE_ENABLED),
                scope=FlagScope.TENANT,
                tenant_id=tenant.id,
                enabled=True,
                rollout_percentage=100,
            )
        )
        await system_db.flush()

        result = await EnhancedOrderParser(
            system_db, settings, client=None, cache=InMemoryBackend()
        ).parse("anything 01712345678", tenant_id=tenant.id)
        assert result.parser == "deterministic"
        assert AI_PARSE_UNAVAILABLE in result.warnings
        assert result.phones == ["+8801712345678"]

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


class TestV4LabelAndDistrictRules:
    """V4 WP0.3: label shapes and district lines seen in real messages."""

    def test_dash_labels_are_labels(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse("name- karim\naddress- kushtia sadar, kushtia\nproduct: watch 1 ta")
        assert result.customer_name == "karim"
        assert result.confidence["name"] == FieldConfidence.EXACT
        assert result.address == "kushtia sadar, kushtia"
        assert result.district == "Kushtia"
        assert [item.name for item in result.items] == ["watch"]

    def test_a_hyphenated_product_is_not_a_label(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse("01700000001\nt-shirt XL 2ta")
        assert result.customer_name is None
        assert result.items[0].name == "t-shirt"

    def test_number_label_without_separator(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse("Rina\nnumber 01700000002 / 01700000003\nMirpur 2, Dhaka\nabaya 1ta")
        assert result.phones == ["+8801700000002", "+8801700000003"]
        assert result.needs_phone_selection
        assert result.customer_name == "Rina"

    def test_a_lone_district_is_an_uncertain_address_not_a_name(
        self, parser: DeterministicOrderParser
    ) -> None:
        result = parser.parse("01700000004\nTangail\n2ta tshirt L")
        assert result.customer_name is None
        assert result.address == "Tangail"
        assert result.district == "Tangail"
        # Only a district: offered as a guess, below the confirm threshold.
        assert result.confidence["address"] < 0.5

    def test_bangla_district_with_nukta_variants(self, parser: DeterministicOrderParser) -> None:
        precomposed = "০১৭০০০০০০০৫\nবগুড়া\nহিজাব ১টা"
        decomposed = "০১৭০০০০০০০৫\nবগুড়া\nহিজাব ১টা"
        assert parser.parse(precomposed).district == "Bogura"
        assert parser.parse(decomposed).district == "Bogura"

    def test_one_line_message_is_split_into_parts(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse(
            "Rahim 01700000006 Mirpur 10 Dhaka. Black panjabi XL 2 pcs. total 2450tk"
        )
        assert result.customer_name == "Rahim"
        assert result.address == "Mirpur 10 Dhaka"
        assert result.items[0].name == "Black panjabi"
        assert result.items[0].quantity == 2
        assert result.cod_amount_paisa == 245_000

    def test_md_abbreviation_stays_with_the_name(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse("Md. Rahim Uddin\n01700000007\nSavar, Dhaka\ncap 1ta")
        assert result.customer_name == "Md. Rahim Uddin"

    def test_inline_bangla_name(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse("আপু আমার নাম সুমি, ০১৭০০০০০০০৮, ঠিকানা: আগ্রাবাদ, চট্টগ্রাম।")
        assert result.customer_name == "সুমি"
        assert result.address == "আগ্রাবাদ, চট্টগ্রাম"

    def test_phone_intro_words_are_not_a_name(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse("My number is 01700000009\nUttara sector 4, Dhaka\nshirt L 1ta")
        assert result.customer_name is None

    def test_short_hints_only_match_whole_words(self, parser: DeterministicOrderParser) -> None:
        # "po" inside "Polash" and "rd" inside "order" are not address words.
        result = parser.parse("Polash\n01700000010\nmouse 1ta")
        assert result.customer_name == "Polash"
        assert result.address is None

    def test_a_greeting_with_a_filler_word_is_not_a_name(
        self, parser: DeterministicOrderParser
    ) -> None:
        assert parser.parse("hi apu\nprice koto?").customer_name is None

    def test_thousands_separator_with_one_leading_digit(
        self, parser: DeterministicOrderParser
    ) -> None:
        result = parser.parse("01700000011\nসাভার, আশুলিয়া\nপাঞ্জাবি ২টা M\nমোট: 2,600 টাকা")
        assert result.cod_amount_paisa == 260_000

    def test_line_price_is_the_unit_price_only_for_one_piece(
        self, parser: DeterministicOrderParser
    ) -> None:
        one = parser.parse("01700000012\nproduct: watch 1 ta 1200")
        assert one.items[0].unit_price_paisa == 120_000
        two = parser.parse("01700000012\nproduct: watch 2 ta 2400")
        assert two.items[0].unit_price_paisa is None

    def test_a_size_number_is_not_a_unit_price(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse("01700000013\nproduct: shoe size 40")
        assert result.items[0].unit_price_paisa is None


#: One-line chat messages, fields separated by commas, dari, slashes, pipes,
#: semicolons or a newline (run 16). (text, name, phone, address part, item, qty)
_ONE_LINE_CASES = [
    # English
    (
        "Rahim, 01700000101, Mirpur 10, 2 ta t-shirt",
        "Rahim",
        "+8801700000101",
        "Mirpur 10",
        "t-shirt",
        2,
    ),
    (
        "Karim Uddin, 01800000102, House 12, Road 5, Dhanmondi, Dhaka, 1 pc Black Panjabi XL",
        "Karim Uddin",
        "+8801800000102",
        "House 12, Road 5, Dhanmondi, Dhaka",
        "Panjabi",
        1,
    ),
    (
        "Nusrat, 01900000103, Uttara Sector 7, 3 pcs hijab",
        "Nusrat",
        "+8801900000103",
        "Uttara Sector 7",
        "hijab",
        3,
    ),
    (
        "Tania, 01700000104, Chittagong Agrabad, 2 pcs three piece",
        "Tania",
        "+8801700000104",
        "Agrabad",
        "three piece",
        2,
    ),
    (
        "Rahim,01700000105,Mirpur 10,2 ta t-shirt",
        "Rahim",
        "+8801700000105",
        "Mirpur 10",
        "t-shirt",
        2,
    ),
    (
        "2 ta t-shirt, Rahim, 01700000106, Mirpur 10",
        "Rahim",
        "+8801700000106",
        "Mirpur 10",
        "t-shirt",
        2,
    ),
    (
        "Name Rahim, 01700000107, Mirpur 10, 2 ta tshirt",
        "Rahim",
        "+8801700000107",
        "Mirpur 10",
        "tshirt",
        2,
    ),
    # Banglish
    (
        "Sumi Akter, 01600000108, Savar, Dhaka, 2 ta kurti",
        "Sumi Akter",
        "+8801600000108",
        "Savar, Dhaka",
        "kurti",
        2,
    ),
    (
        "Mitu, 01700000109, Khulna Sonadanga, duita jersey",
        "Mitu",
        "+8801700000109",
        "Khulna Sonadanga",
        "jersey",
        2,
    ),
    (
        "Rafiq, 01700000110, Mirpur 2, Dhaka, 2 ta t shirt L size",
        "Rafiq",
        "+8801700000110",
        "Mirpur 2, Dhaka",
        "t shirt",
        2,
    ),
    (
        "Hasan, 01700000111, Rajshahi Shaheb Bazar, 4 ta cap",
        "Hasan",
        "+8801700000111",
        "Shaheb Bazar",
        "cap",
        4,
    ),
    (
        "Arif Hossain, 01700000112, Bashundhara R/A Block C, 1 ta wallet",
        "Arif Hossain",
        "+8801700000112",
        "Bashundhara R/A Block C",
        "wallet",
        1,
    ),
    # Bangla, with Bangla digits
    ("রহিম, ০১৭০০০০০১১৩, মিরপুর ১০, ২টা টি-শার্ট", "রহিম", "+8801700000113", "মিরপুর 10", "টি-শার্ট", 2),
    (
        "করিম, 01800000114, উত্তরা সেক্টর ৭, ঢাকা, ১টি পাঞ্জাবি",
        "করিম",
        "+8801800000114",
        "উত্তরা সেক্টর 7, ঢাকা",
        "পাঞ্জাবি",
        1,
    ),
    ("রাকিব, ০১৭০০০০০১১৫, সাভার, ঢাকা, ২ টা জার্সি", "রাকিব", "+8801700000115", "সাভার, ঢাকা", "জার্সি", 2),
    (
        "নাসরিন, 01700000116, গ্রাম: চরপাড়া, থানা: সদর, জেলা: ময়মনসিংহ, দুইটা শাড়ি",
        "নাসরিন",
        "+8801700000116",
        "গ্রাম: চরপাড়া, থানা: সদর, জেলা: ময়মনসিংহ",
        "শাড়ি",
        2,
    ),
    # Other separators: dari, slash, pipe, semicolon, newline
    (
        "সুমি আক্তার। ০১৯০০০০০১১৭। ধানমন্ডি ২৭, ঢাকা। ৩ পিস হিজাব",
        "সুমি আক্তার",
        "+8801900000117",
        "ধানমন্ডি 27",
        "হিজাব",
        3,
    ),
    (
        "Jamal / 01500000118 / Gulshan 1 / 1 ta watch",
        "Jamal",
        "+8801500000118",
        "Gulshan 1",
        "watch",
        1,
    ),
    (
        "Mim | 01700000119 | Narayanganj Fatullah | 1 ta bag",
        "Mim",
        "+8801700000119",
        "Narayanganj Fatullah",
        "bag",
        1,
    ),
    (
        "Polash; 01700000120; Gazipur Tongi; 2 ta shoe",
        "Polash",
        "+8801700000120",
        "Gazipur Tongi",
        "shoe",
        2,
    ),
    (
        "Rahim, 01700000121\nMirpur 10, 2 ta t-shirt",
        "Rahim",
        "+8801700000121",
        "Mirpur 10",
        "t-shirt",
        2,
    ),
    # Mixed script and separators
    (
        "Shakil, 01700000122, Banani, 2 ta polo shirt, 1200 tk",
        "Shakil",
        "+8801700000122",
        "Banani",
        "polo shirt",
        2,
    ),
    ("রুমা / 01700000123, Mirpur 1, দুইটা থ্রি পিস", "রুমা", "+8801700000123", "Mirpur 1", "থ্রি পিস", 2),
]


class TestOneLineFieldLists:
    """ "Rahim, 017..., Mirpur 10, 2 ta t-shirt": every field, not one long address."""

    @pytest.mark.parametrize(
        ("text", "name", "phone", "address", "item", "quantity"),
        _ONE_LINE_CASES,
        ids=[case[0][:40] for case in _ONE_LINE_CASES],
    )
    def test_each_field_is_read(
        self,
        parser: DeterministicOrderParser,
        text: str,
        name: str,
        phone: str,
        address: str,
        item: str,
        quantity: int,
    ) -> None:
        result = parser.parse(text)

        assert result.customer_name == name
        assert result.selected_phone == phone
        assert result.address is not None
        assert address in result.address
        assert item not in result.address
        assert len(result.items) == 1, result.items
        assert item.lower() in result.items[0].name.lower()
        assert result.items[0].quantity == quantity

    def test_the_amount_piece_is_the_cod(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse("Shakil, 01700000122, Banani, 2 ta polo shirt, 1200 tk")
        assert result.cod_amount_paisa == 120_000

    def test_a_size_piece_stays_with_its_product(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse("Rafiq, 01700000110, Mirpur 2, Dhaka, 2 ta t shirt L size")
        assert result.items[0].size == "L"

    def test_a_comma_address_line_is_still_one_address(
        self, parser: DeterministicOrderParser
    ) -> None:
        result = parser.parse(
            "Rahim\n01700000124\nHouse 12, Road 5, Kalabagan, Dhaka\nBlack Abaya XL"
        )
        assert result.address == "House 12, Road 5, Kalabagan, Dhaka"
        assert result.items[0].name == "Black Abaya"

    def test_a_comma_product_line_is_still_one_product(
        self, parser: DeterministicOrderParser
    ) -> None:
        result = parser.parse("Rahim\n01700000125\nMirpur 10\nBlack Abaya, XL")
        assert len(result.items) == 1
        assert result.items[0].size == "XL"

    def test_thousands_separators_are_not_field_separators(
        self, parser: DeterministicOrderParser
    ) -> None:
        result = parser.parse("Rahim, 01700000126, Mirpur 10, 1 ta watch, 1,250 tk")
        assert result.cod_amount_paisa == 125_000

    def test_an_area_before_the_road_stays_in_the_address(
        self, parser: DeterministicOrderParser
    ) -> None:
        result = parser.parse("Rahim, 01700000127, Shyamoli, Ring Road, Dhaka, 1 ta bag")
        assert result.address == "Shyamoli, Ring Road, Dhaka"
        assert [item.name for item in result.items] == ["bag"]

    def test_labelled_fields_on_one_line(self, parser: DeterministicOrderParser) -> None:
        result = parser.parse(
            "Name: Rahim, Phone: 01700000128, Address: House 3, Road 2, Mirpur 10, Product: 2 ta shirt"
        )
        assert result.customer_name == "Rahim"
        assert result.selected_phone == "+8801700000128"
        assert result.address == "House 3, Road 2, Mirpur 10"
        assert result.items[0].name == "shirt"
        assert result.items[0].quantity == 2
