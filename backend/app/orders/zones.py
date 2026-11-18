"""Delivery zone from address text (V4 WP0.4).

``resolve_zone("House 4, Road 2, Mirpur 10, Dhaka")`` is ``INSIDE_DHAKA``.
The answer is ``None`` whenever the text does not settle it: a bare "Dhaka",
"Gazipur" or "Narayanganj" (each holds both city and suburbs), an area the data
file does not list, or nothing recognisable at all. A wrong zone puts a wrong
delivery charge into a COD, so the resolver never guesses.

Order of evidence:

1.  the last district named, when it is not one of the split districts,
    gives ``OUTSIDE_DHAKA`` ("Mirpur, Kushtia" is Kushtia, not Dhaka);
2.  a listed suburb area gives ``DHAKA_SUBURB``;
3.  a listed Dhaka city area gives ``INSIDE_DHAKA`` unless the district named
    is Gazipur or Narayanganj (a contradiction, so unknown).
"""

from __future__ import annotations

import re
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.orders.parser.districts import find_district, fold_place_text, place_pattern

__all__ = ["DeliveryZone", "resolve_zone"]

_DATA_FILE = Path(__file__).resolve().parent / "data" / "zones.yaml"


class DeliveryZone(StrEnum):
    INSIDE_DHAKA = "INSIDE_DHAKA"
    DHAKA_SUBURB = "DHAKA_SUBURB"
    OUTSIDE_DHAKA = "OUTSIDE_DHAKA"


class _Data:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.split_districts = frozenset(str(name) for name in raw["split_districts"])
        self.inside = self._patterns(raw["inside_dhaka"])
        self.suburb = self._patterns(raw["dhaka_suburb"])

    @staticmethod
    def _patterns(groups: list[list[str]]) -> tuple[re.Pattern[str], ...]:
        spellings = sorted({str(s) for group in groups for s in group}, key=len, reverse=True)
        return tuple(place_pattern(spelling) for spelling in spellings)


@lru_cache(maxsize=1)
def _data() -> _Data:
    return _Data(yaml.safe_load(_DATA_FILE.read_text(encoding="utf-8")))


def _any(patterns: tuple[re.Pattern[str], ...], folded: str) -> bool:
    return any(pattern.search(folded) for pattern in patterns)


def resolve_zone(*texts: str | None) -> DeliveryZone | None:
    """The zone the address (and optional district/area hints) settle, or ``None``."""
    text = " , ".join(t for t in texts if t)
    if not text.strip():
        return None
    data = _data()
    district = find_district(text)
    if district is not None and district not in data.split_districts:
        return DeliveryZone.OUTSIDE_DHAKA
    folded = fold_place_text(text)
    if _any(data.suburb, folded):
        return DeliveryZone.DHAKA_SUBURB
    if _any(data.inside, folded) and district in (None, "Dhaka"):
        return DeliveryZone.INSIDE_DHAKA
    return None
