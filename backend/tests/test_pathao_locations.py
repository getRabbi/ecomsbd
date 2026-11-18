"""Pathao city / zone / area resolution from a draft address (V4 WP0.7)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from app.common.cache import InMemoryBackend
from app.couriers.pathao.locations import PathaoLocationResolver, Place, match_place


@dataclass
class _Call:
    value: Any


@dataclass
class _City:
    city_id: int
    city_name: str


@dataclass
class _Zone:
    zone_id: int
    zone_name: str


@dataclass
class _Area:
    area_id: int
    area_name: str


class FakePathao:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_cities(self, creds: Any) -> _Call:
        self.calls.append("cities")
        return _Call([_City(1, "Dhaka"), _City(2, "Chittagong"), _City(3, "Khulna")])

    async def list_zones(self, creds: Any, city_id: int) -> _Call:
        self.calls.append(f"zones:{city_id}")
        zones = {
            1: [
                _Zone(11, "Mirpur"),
                _Zone(12, "Mirpur 10"),
                _Zone(13, "Uttara"),
                _Zone(14, "Mohammadpur"),
            ],
            2: [_Zone(21, "Agrabad"), _Zone(22, "Halishahar")],
            3: [_Zone(31, "Sonadanga")],
        }
        return _Call(zones.get(city_id, []))

    async def list_areas(self, creds: Any, zone_id: int) -> _Call:
        self.calls.append(f"areas:{zone_id}")
        areas = {12: [_Area(121, "Section 6"), _Area(122, "Pallabi")], 13: [_Area(131, "Sector 7")]}
        return _Call(areas.get(zone_id, []))


def _resolver(
    fake: FakePathao, cache: InMemoryBackend | None = None, tenant: uuid.UUID | None = None
) -> PathaoLocationResolver:
    return PathaoLocationResolver(
        fake, cache or InMemoryBackend(), tenant_id=tenant or uuid.uuid4()
    )


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("House 4, Road 2, Mirpur 10, Dhaka", ("Dhaka", "Mirpur 10", None)),
        ("Pallabi, Mirpur 10, Dhaka", ("Dhaka", "Mirpur 10", "Pallabi")),
        ("Uttara sector 7, Dhaka", ("Dhaka", "Uttara", "Sector 7")),
        ("Mohammedpur, Dhaka", ("Dhaka", "Mohammadpur", None)),
        # Old and new spellings of the city both land on Pathao's name.
        ("আগ্রাবাদ, Agrabad, চট্টগ্রাম", ("Chittagong", "Agrabad", None)),
        ("Agrabad, Chattogram", ("Chittagong", "Agrabad", None)),
        ("khulna sonadanga", ("Khulna", "Sonadanga", None)),
    ],
)
async def test_known_addresses_resolve(address: str, expected: tuple[str, str, str | None]) -> None:
    location = await _resolver(FakePathao()).resolve(object(), address)
    assert location is not None
    assert (
        location.city.name,
        location.zone.name,
        location.area.name if location.area else None,
    ) == expected


@pytest.mark.parametrize(
    "address",
    [
        None,
        "",
        "Mirpur 10",  # no city named
        "Dhaka",  # no zone named
        "Mirpur, Uttara, Dhaka",  # two zones: ambiguous
        "Agrabad, Dhaka",  # zone not in that city
        "Dhaka, Khulna, Mirpur",  # two cities
    ],
)
async def test_anything_short_of_one_answer_is_none(address: str | None) -> None:
    assert await _resolver(FakePathao()).resolve(object(), address) is None


async def test_lists_are_cached_per_shop() -> None:
    cache = InMemoryBackend()
    fake = FakePathao()
    shop = uuid.uuid4()
    await _resolver(fake, cache, shop).resolve(object(), "Mirpur 10, Dhaka")
    await _resolver(fake, cache, shop).resolve(object(), "Section 6, Mirpur 10, Dhaka")
    assert fake.calls.count("cities") == 1 and fake.calls.count("zones:1") == 1
    await _resolver(fake, cache, uuid.uuid4()).resolve(object(), "Mirpur 10, Dhaka")
    assert fake.calls.count("cities") == 2, "another shop reads its own account's lists"


def test_match_place_prefers_the_specific_name_and_refuses_ties() -> None:
    places = [Place(1, "Mirpur"), Place(2, "Mirpur 10"), Place(3, "Banani")]
    assert match_place("Road 2, Mirpur 10", places) == Place(2, "Mirpur 10")
    assert match_place("Mirpur", places) == Place(1, "Mirpur")
    assert match_place("Mirpur 10 and Banani", places) is None
    assert match_place("Gulshan", places) is None
    # A one-word name tolerates one typo; a short or multi-word name never does.
    assert match_place("Banan", [Place(3, "Banani")]) == Place(3, "Banani")
    assert match_place("Wary", [Place(4, "Wari")]) is None
    assert match_place("Mirpur 1O", [Place(2, "Mirpur 10")]) is None
