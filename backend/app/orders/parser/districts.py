"""The 64 districts of Bangladesh, for recognising a delivery area.

A message often carries nothing but a district ("Tangail") where the address
should be. Without this list the parser read such a line as the customer's
name. A district alone is still a poor address, so callers treat a match as an
uncertain address, never an exact one.

Spellings: the current official romanisation plus the older ones customers
still type (Chittagong, Comilla, Jessore, Bogra, Barisal). Bangla names are
compared after NFC normalisation because "ড়"/"য়" arrive both precomposed and
as base letter + nukta.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["DISTRICTS", "find_district", "is_district_only"]

#: Canonical English name -> every spelling that means it.
DISTRICTS: dict[str, tuple[str, ...]] = {
    # Dhaka division
    "Dhaka": ("dhaka", "ঢাকা"),
    "Faridpur": ("faridpur", "ফরিদপুর"),
    "Gazipur": ("gazipur", "গাজীপুর", "গাজিপুর"),
    "Gopalganj": ("gopalganj", "গোপালগঞ্জ"),
    "Kishoreganj": ("kishoreganj", "kishorganj", "কিশোরগঞ্জ"),
    "Madaripur": ("madaripur", "মাদারীপুর"),
    "Manikganj": ("manikganj", "মানিকগঞ্জ"),
    "Munshiganj": ("munshiganj", "মুন্সীগঞ্জ", "মুন্সিগঞ্জ"),
    "Narayanganj": ("narayanganj", "নারায়ণগঞ্জ", "নারায়নগঞ্জ"),
    "Narsingdi": ("narsingdi", "norsingdi", "নরসিংদী"),
    "Rajbari": ("rajbari", "রাজবাড়ী"),
    "Shariatpur": ("shariatpur", "শরীয়তপুর"),
    "Tangail": ("tangail", "টাঙ্গাইল"),
    # Chattogram division
    "Bandarban": ("bandarban", "বান্দরবান"),
    "Brahmanbaria": ("brahmanbaria", "ব্রাহ্মণবাড়িয়া"),
    "Chandpur": ("chandpur", "চাঁদপুর"),
    "Chattogram": ("chattogram", "chittagong", "ctg", "চট্টগ্রাম"),
    "Cox's Bazar": ("cox's bazar", "coxs bazar", "cox bazar", "coxsbazar", "কক্সবাজার"),
    "Cumilla": ("cumilla", "comilla", "কুমিল্লা"),
    "Feni": ("feni", "ফেনী"),
    "Khagrachhari": ("khagrachhari", "khagrachari", "খাগড়াছড়ি"),
    "Lakshmipur": ("lakshmipur", "laxmipur", "লক্ষ্মীপুর"),
    "Noakhali": ("noakhali", "নোয়াখালী"),
    "Rangamati": ("rangamati", "রাঙ্গামাটি", "রাঙামাটি"),
    # Khulna division
    "Bagerhat": ("bagerhat", "বাগেরহাট"),
    "Chuadanga": ("chuadanga", "চুয়াডাঙ্গা"),
    "Jashore": ("jashore", "jessore", "যশোর"),
    "Jhenaidah": ("jhenaidah", "jhenidah", "ঝিনাইদহ"),
    "Khulna": ("khulna", "খুলনা"),
    "Kushtia": ("kushtia", "কুষ্টিয়া"),
    "Magura": ("magura", "মাগুরা"),
    "Meherpur": ("meherpur", "মেহেরপুর"),
    "Narail": ("narail", "নড়াইল"),
    "Satkhira": ("satkhira", "সাতক্ষীরা"),
    # Rajshahi division
    "Bogura": ("bogura", "bogra", "বগুড়া"),
    "Joypurhat": ("joypurhat", "জয়পুরহাট"),
    "Naogaon": ("naogaon", "নওগাঁ"),
    "Natore": ("natore", "নাটোর"),
    "Chapainawabganj": ("chapainawabganj", "chapai nawabganj", "চাঁপাইনবাবগঞ্জ"),
    "Pabna": ("pabna", "পাবনা"),
    "Rajshahi": ("rajshahi", "রাজশাহী"),
    "Sirajganj": ("sirajganj", "সিরাজগঞ্জ"),
    # Rangpur division
    "Dinajpur": ("dinajpur", "দিনাজপুর"),
    "Gaibandha": ("gaibandha", "গাইবান্ধা"),
    "Kurigram": ("kurigram", "কুড়িগ্রাম"),
    "Lalmonirhat": ("lalmonirhat", "লালমনিরহাট"),
    "Nilphamari": ("nilphamari", "নীলফামারী"),
    "Panchagarh": ("panchagarh", "পঞ্চগড়"),
    "Rangpur": ("rangpur", "রংপুর"),
    "Thakurgaon": ("thakurgaon", "ঠাকুরগাঁও"),
    # Mymensingh division
    "Jamalpur": ("jamalpur", "জামালপুর"),
    "Mymensingh": ("mymensingh", "ময়মনসিংহ"),
    "Netrokona": ("netrokona", "netrakona", "নেত্রকোনা"),
    "Sherpur": ("sherpur", "শেরপুর"),
    # Sylhet division
    "Habiganj": ("habiganj", "হবিগঞ্জ"),
    "Moulvibazar": ("moulvibazar", "maulvibazar", "মৌলভীবাজার"),
    "Sunamganj": ("sunamganj", "সুনামগঞ্জ"),
    "Sylhet": ("sylhet", "সিলেট"),
    # Barishal division
    "Barguna": ("barguna", "বরগুনা"),
    "Barishal": ("barishal", "barisal", "বরিশাল"),
    "Bhola": ("bhola", "ভোলা"),
    "Jhalokati": ("jhalokati", "jhalakathi", "ঝালকাঠি"),
    "Patuakhali": ("patuakhali", "পটুয়াখালী"),
    "Pirojpur": ("pirojpur", "পিরোজপুর"),
}

#: Words that may sit next to a district on a district-only line.
_DISTRICT_FILLER = frozenset({"sadar", "সদর", "district", "zila", "zilla", "জেলা", "city", "শহর"})

#: Bangla case endings a district name commonly carries ("ঢাকায়", "ঢাকার").
_BANGLA_SUFFIX = "(?:য়|য়|র|ের|তে|এ|ে)?"


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _pattern(spelling: str) -> str:
    escaped = re.escape(_nfc(spelling))
    if spelling.isascii():
        return rf"(?<![a-z]){escaped}(?![a-z])"
    return rf"(?<![ঀ-৿]){escaped}{_BANGLA_SUFFIX}(?![ঀ-৿])"


_LOOKUP: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (canonical, re.compile(_pattern(spelling)))
    for canonical, spellings in DISTRICTS.items()
    # Longest spelling first, so "chapai nawabganj" wins over a shorter overlap.
    for spelling in sorted(spellings, key=len, reverse=True)
)


def find_district(text: str | None) -> str | None:
    """The canonical name of the last district mentioned, or ``None``.

    The last one, because addresses run from house to district: in
    "Agrabad, Chattogram" the district is at the end.
    """
    if not text:
        return None
    folded = _nfc(text).casefold()
    best: tuple[int, str] | None = None
    for canonical, pattern in _LOOKUP:
        for match in pattern.finditer(folded):
            if best is None or match.start() > best[0]:
                best = (match.start(), canonical)
    return best[1] if best else None


def is_district_only(line: str) -> bool:
    """Whether a line names a district and nothing else ("Tangail", "Kushtia sadar")."""
    folded = _nfc(line).casefold()
    if find_district(folded) is None:
        return False
    remainder = folded
    for _, pattern in _LOOKUP:
        remainder = pattern.sub(" ", remainder)
    words = [word.strip(",.।-:;()") for word in remainder.split()]
    return all(not word or word in _DISTRICT_FILLER for word in words)
