"""Pathao city / zone / area from a draft address (V4 WP0.7).

Pathao's structured location (city, zone, optional area) is what an automatic
booking needs when the free-text address alone is not enough. The lists come
from the merchant's own Pathao account (``list_cities`` / ``list_zones`` /
``list_areas``) and are cached per shop. Matching is deliberately strict: a
place name must appear in the address as whole words (or as one word with a
near-identical spelling), and anything short of exactly one best candidate is
``None``. The Autopilot gate (WP4) treats ``None`` as "a person decides".
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.common.cache import CacheBackend
from app.common.phone import normalize_digits
from app.orders.parser.districts import DISTRICTS, find_district

__all__ = ["CACHE_TTL_SECONDS", "PathaoLocation", "PathaoLocationResolver", "Place", "match_place"]

CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
#: A single-word place may differ by about one letter ("mohammadpur"/"mohammedpur").
_FUZZY_CUTOFF = 0.88


@dataclass(frozen=True, slots=True)
class Place:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class PathaoLocation:
    city: Place
    zone: Place
    #: ``None`` when no single area is named; city + zone is still a location.
    area: Place | None = None


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFC", normalize_digits(text)).casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def _contains_words(haystack: str, needle: str) -> bool:
    return bool(needle) and re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def match_place(
    address: str, places: Sequence[Place], *, aliases: dict[int, Sequence[str]] | None = None
) -> Place | None:
    """The one place the address names, or ``None`` (nothing, or a tie).

    Longer names win over names they contain ("Mirpur 10" over "Mirpur"), so
    an address naming the specific place is not ambiguous because of the
    general one. Fuzzy matching is only for one-word names and only when no
    exact match exists.
    """
    folded = _fold(address)
    if not folded:
        return None
    exact: dict[int, tuple[str, Place]] = {}
    for place in places:
        names = [_fold(n) for n in (place.name, *((aliases or {}).get(place.id, ())))]
        hits = [n for n in names if _contains_words(folded, n)]
        if hits:
            exact[place.id] = (max(hits, key=len), place)
    if exact:
        # Drop a match whose words sit inside another match ("mirpur" inside
        # "mirpur 10"); two unrelated matches remain a tie.
        kept = [
            place
            for name, place in exact.values()
            if not any(
                other != name and _contains_words(other, name) for other, _ in exact.values()
            )
        ]
        return kept[0] if len(kept) == 1 else None

    words = set(folded.split())
    fuzzy: list[Place] = []
    for place in places:
        name = _fold(place.name)
        if " " in name or len(name) < 5:
            continue
        if difflib.get_close_matches(name, words, n=1, cutoff=_FUZZY_CUTOFF):
            fuzzy.append(place)
    return fuzzy[0] if len(fuzzy) == 1 else None


def _rows(value: Any, id_field: str, name_field: str) -> list[Place]:
    places = []
    for row in value or []:
        place_id = getattr(row, id_field, None)
        name = getattr(row, name_field, None)
        if isinstance(place_id, int) and isinstance(name, str) and name.strip():
            places.append(Place(place_id, name.strip()))
    return places


class PathaoLocationResolver:
    """Cached Pathao lists for one shop, and the strict match over them."""

    def __init__(
        self,
        client: Any,
        cache: CacheBackend,
        *,
        tenant_id: uuid.UUID,
        sandbox: bool = False,
    ) -> None:
        self._client = client
        self._cache = cache
        self._prefix = f"pathao:loc:{tenant_id}:{'sandbox' if sandbox else 'live'}"

    async def _cached(self, key: str, load: Callable[[], Awaitable[list[Place]]]) -> list[Place]:
        full = f"{self._prefix}:{key}"
        raw = await self._cache.get(full)
        if raw is not None:
            try:
                return [Place(int(r["id"]), str(r["name"])) for r in json.loads(raw)]
            except (ValueError, KeyError, TypeError):
                pass
        places = await load()
        await self._cache.set(
            full,
            json.dumps([{"id": p.id, "name": p.name} for p in places]),
            ttl_seconds=CACHE_TTL_SECONDS,
        )
        return places

    async def cities(self, creds: Any) -> list[Place]:
        async def load() -> list[Place]:
            return _rows((await self._client.list_cities(creds)).value, "city_id", "city_name")

        return await self._cached("cities", load)

    async def zones(self, creds: Any, city_id: int) -> list[Place]:
        async def load() -> list[Place]:
            return _rows(
                (await self._client.list_zones(creds, city_id)).value, "zone_id", "zone_name"
            )

        return await self._cached(f"zones:{city_id}", load)

    async def areas(self, creds: Any, zone_id: int) -> list[Place]:
        async def load() -> list[Place]:
            return _rows(
                (await self._client.list_areas(creds, zone_id)).value, "area_id", "area_name"
            )

        return await self._cached(f"areas:{zone_id}", load)

    async def resolve(self, creds: Any, address: str | None) -> PathaoLocation | None:
        """City + zone (+ area when exactly one is named), or ``None``."""
        if not address or not address.strip():
            return None
        cities = await self.cities(creds)
        # The district gazetteer knows old and new spellings ("Chittagong" /
        # "Chattogram"); a Pathao city named either way matches either.
        district = find_district(address)
        aliases: dict[int, Sequence[str]] = {}
        for candidate in cities:
            for canonical, spellings in DISTRICTS.items():
                folded = {_fold(s) for s in (canonical, *spellings)}
                if _fold(candidate.name) in folded:
                    aliases[candidate.id] = sorted(folded)
        city_text = address if district is None else f"{address} {district}"
        city = match_place(city_text, cities, aliases=aliases)
        if city is None:
            return None
        zone = match_place(address, await self.zones(creds, city.id))
        if zone is None:
            return None
        area = match_place(address, await self.areas(creds, zone.id))
        return PathaoLocation(city=city, zone=zone, area=area)
