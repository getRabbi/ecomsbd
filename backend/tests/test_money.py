"""Money rules.

Master spec sections 17, 32 and 123. These are golden tests in the sense of
section 54: if one of them changes, a real seller's totals changed with it, and
that needs a deliberate migration rather than a code edit.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.common.money import (
    Money,
    apply_basis_points,
    format_bdt,
    format_bdt_compact,
    group_bd,
    paisa_from_taka,
    sum_money,
    taka_from_paisa,
)


class TestPaisaConversion:
    def test_taka_to_paisa_is_exact(self) -> None:
        assert paisa_from_taka(Decimal("1250")) == 125_000
        assert paisa_from_taka("1250.50") == 125_050
        assert paisa_from_taka(0) == 0

    def test_float_is_rejected(self) -> None:
        # A float that reaches this point may already be wrong; refusing it is
        # the only way to keep "never use float for money" enforceable.
        with pytest.raises(TypeError):
            paisa_from_taka(1250.5)  # type: ignore[arg-type]

    def test_rounding_is_half_up_not_bankers(self) -> None:
        # Python's default rounding would give 0 here, disagreeing with the
        # provider's printed statement on exactly the .5 cases.
        assert paisa_from_taka(Decimal("0.005")) == 1
        assert paisa_from_taka(Decimal("0.015")) == 2
        assert paisa_from_taka(Decimal("-0.005")) == -1

    def test_round_trip(self) -> None:
        assert taka_from_paisa(125_050) == Decimal("1250.50")


class TestBasisPoints:
    def test_one_percent(self) -> None:
        assert apply_basis_points(125_000, 100) == 1_250

    def test_fractional_result_rounds_half_up(self) -> None:
        # 1.5% of ৳12.55 = 18.825 paisa -> 19
        assert apply_basis_points(1_255, 150) == 19

    def test_money_helper_matches(self) -> None:
        assert Money(125_000).of_basis_points(150) == Money(1_875)

    def test_basis_points_of_zero_total_is_zero(self) -> None:
        assert Money(500).basis_points_of(Money(0)) == 0


class TestAllocation:
    def test_split_loses_no_paisa(self) -> None:
        parts = Money(1_000).allocate([1, 1, 1])
        assert [p.paisa for p in parts] == [334, 333, 333]
        assert sum_money(parts) == Money(1_000)

    def test_weighted_split(self) -> None:
        parts = Money(10_000).allocate([3, 1, 1])
        assert [p.paisa for p in parts] == [6_000, 2_000, 2_000]

    def test_negative_total_mirrors_positive(self) -> None:
        positive = [p.paisa for p in Money(1_000).allocate([5, 3, 2])]
        negative = [p.paisa for p in Money(-1_000).allocate([5, 3, 2])]
        assert negative == [-p for p in positive]
        assert sum(negative) == -1_000

    def test_zero_weights_spread_evenly_rather_than_dropping_money(self) -> None:
        parts = Money(100).allocate([0, 0])
        assert sum_money(parts) == Money(100)

    def test_allocation_is_deterministic(self) -> None:
        first = [p.paisa for p in Money(1_003).allocate([1, 1, 1])]
        second = [p.paisa for p in Money(1_003).allocate([1, 1, 1])]
        assert first == second

    @pytest.mark.parametrize(
        ("total", "weights"),
        [
            (1, [1, 1, 1]),
            (7, [2, 3, 5]),
            (999_999, [17, 5, 3, 1]),
            (-1, [1, 1]),
        ],
    )
    def test_allocation_always_sums_back(self, total: int, weights: list[int]) -> None:
        parts = Money(total).allocate(weights)
        assert sum(p.paisa for p in parts) == total


class TestArithmetic:
    def test_add_and_subtract(self) -> None:
        assert Money(1_000) + Money(250) == Money(1_250)
        assert Money(1_000) - Money(1_250) == Money(-250)

    def test_multiplication_requires_int(self) -> None:
        assert Money(1_000) * 3 == Money(3_000)
        with pytest.raises(TypeError):
            Money(1_000) * 1.5  # type: ignore[operator]

    def test_float_paisa_is_rejected(self) -> None:
        with pytest.raises(TypeError):
            Money(12.5)  # type: ignore[arg-type]

    def test_unsupported_currency_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsupported currency"):
            Money(100, "USD")

    def test_comparison(self) -> None:
        assert Money(100) < Money(200)
        assert Money(200) >= Money(200)


class TestFormatting:
    def test_bangladeshi_grouping(self) -> None:
        # Lakh grouping, as used throughout the master spec money screens.
        assert group_bd("284500") == "2,84,500"
        assert group_bd("87450") == "87,450"
        assert group_bd("12345678") == "1,23,45,678"

    def test_amounts_match_the_ui_prototype(self) -> None:
        assert format_bdt(125_000) == "৳1,250"
        assert format_bdt(-8_000) == "-৳80"
        assert format_bdt(140_500, signed=True) == "+৳1,405"

    def test_paisa_shown_only_when_present(self) -> None:
        assert format_bdt(125_050) == "৳1,250.50"
        assert format_bdt(125_000, show_paisa=True) == "৳1,250.00"

    def test_compact_truncates_rather_than_rounding_up(self) -> None:
        # ৳2,84,500 must not display as ৳2.85L: an abbreviation may never
        # claim more money than the exact figure behind it.
        assert format_bdt_compact(28_450_000) == "৳2.84L"
        assert format_bdt_compact(8_745_000) == "৳87,450"
        assert format_bdt_compact(-845_000) == "-৳8,450"
