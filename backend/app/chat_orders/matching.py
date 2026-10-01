"""Match what a customer asked for against the shop's real catalogue.

Customers write product names in Bangla, Banglish or English, and sellers name
products however they like. Both sides are folded to a phonetic skeleton
(Bangla is transliterated first, then vowels and aspiration are dropped), so
"পাঞ্জাবিটা" and "Panjabi" compare equal while "Panjabi" and "Pajama" do not.

The result is deliberately conservative. A product is ``MATCHED`` only when
exactly one catalogue product fits; any tie is ``AMBIGUOUS`` and lists the
candidates for the seller to choose from. Nothing is picked by a fuzzy name on
the seller's behalf, and a variant is chosen only when the customer's size or
colour leaves exactly one.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.orders.parser import ParsedItem
from app.orders.parser.deterministic import COLOR_EQUIVALENTS
from app.products.models import Product, ProductVariant

#: Catalogue rows read per match; a shop larger than this matches on the
#: most recently updated products.
MAX_CATALOGUE = 3000
MAX_CANDIDATES = 5

_CONSONANTS = {
    "ক": "k",
    "খ": "kh",
    "গ": "g",
    "ঘ": "gh",
    "ঙ": "ng",
    "চ": "ch",
    "ছ": "chh",
    "জ": "j",
    "ঝ": "jh",
    "ঞ": "n",
    "ট": "t",
    "ঠ": "th",
    "ড": "d",
    "ঢ": "dh",
    "ণ": "n",
    "ত": "t",
    "থ": "th",
    "দ": "d",
    "ধ": "dh",
    "ন": "n",
    "প": "p",
    "ফ": "f",
    "ব": "b",
    "ভ": "bh",
    "ম": "m",
    "য": "j",
    "র": "r",
    "ল": "l",
    "শ": "sh",
    "ষ": "sh",
    "স": "s",
    "হ": "h",
    "ড়": "r",
    "ঢ়": "rh",
    "য়": "y",
    "ৎ": "t",
}
_INDEPENDENT_VOWELS = {
    "অ": "o",
    "আ": "a",
    "ই": "i",
    "ঈ": "i",
    "উ": "u",
    "ঊ": "u",
    "ঋ": "ri",
    "এ": "e",
    "ঐ": "oi",
    "ও": "o",
    "ঔ": "ou",
}
_VOWEL_SIGNS = {
    "া": "a",
    "ি": "i",
    "ী": "i",
    "ু": "u",
    "ূ": "u",
    "ৃ": "ri",
    "ে": "e",
    "ৈ": "oi",
    "ো": "o",
    "ৌ": "ou",
}
_MARKS = {"ং": "ng", "ঃ": "h", "ঁ": ""}
_HASANTA = "্"
_NUKTA = "়"

_SIZES = {"xs", "s", "m", "l", "xl", "xxl", "xxxl", "2xl", "3xl", "free", "freesize"}
_STOP = {"and", "with", "for", "the", "of", "set", "new", "pcs", "pc", "piece"}
_SPLIT = re.compile(r"[\s/,\-_|()+&]+")
_BANGLA = re.compile(r"[ঀ-৿]")


def transliterate(word: str) -> str:
    """Bangla script to a rough Latin spelling, enough to compare names."""
    out: list[str] = []
    chars = [c for c in word if c != _NUKTA]
    for index, char in enumerate(chars):
        if char in _CONSONANTS:
            out.append(_CONSONANTS[char])
            following = chars[index + 1] if index + 1 < len(chars) else ""
            # A consonant carries an inherent vowel unless a sign or a hasanta
            # follows; at the end of a word it is usually silent.
            if (
                following
                and following not in _VOWEL_SIGNS
                and following != _HASANTA
                and following not in _MARKS
            ):
                out.append("o")
        elif char in _INDEPENDENT_VOWELS:
            out.append(_INDEPENDENT_VOWELS[char])
        elif char in _VOWEL_SIGNS:
            out.append(_VOWEL_SIGNS[char])
        elif char in _MARKS:
            out.append(_MARKS[char])
        elif char == _HASANTA:
            continue
        else:
            out.append(char)
    return "".join(out)


def skeleton(word: str) -> str:
    """A phonetic key: spelling variants of one name share it."""
    text = word.casefold()
    if _BANGLA.search(text):
        text = transliterate(text)
    text = re.sub(r"[^a-z0-9]", "", text)
    if not text:
        return ""
    text = text.replace("chh", "C").replace("ch", "C")
    text = re.sub(r"c(?=[eiy])", "s", text).replace("c", "k").replace("C", "c")
    for old, new in (
        ("sh", "s"),
        ("kh", "k"),
        ("gh", "g"),
        ("jh", "j"),
        ("th", "t"),
        ("dh", "d"),
        ("ph", "f"),
        ("bh", "b"),
        ("rh", "r"),
        ("ee", "i"),
        ("oo", "u"),
        ("q", "k"),
        ("z", "j"),
        ("v", "b"),
        ("w", "o"),
        ("y", "i"),
        ("x", "ks"),
    ):
        text = text.replace(old, new)
    head, rest = text[0], re.sub(r"[aeiou]", "", text[1:])
    collapsed = re.sub(r"(.)\1+", r"\1", head + rest)
    return collapsed


def _english_color(value: str | None) -> str | None:
    if not value:
        return None
    folded = value.casefold()
    return COLOR_EQUIVALENTS.get(folded, COLOR_EQUIVALENTS.get(value, folded))


def _is_attribute(word: str) -> bool:
    folded = word.casefold()
    return (
        folded in _SIZES
        or folded in COLOR_EQUIVALENTS
        or folded in set(COLOR_EQUIVALENTS.values())
        or folded in {"black", "white", "red", "blue", "green", "navy", "maroon", "grey"}
    )


def name_keys(text: str) -> set[str]:
    """The skeletons of a name's meaningful words (no colours, sizes, filler)."""
    keys: set[str] = set()
    for word in _SPLIT.split(text or ""):
        if not word or _is_attribute(word) or word.casefold() in _STOP:
            continue
        key = skeleton(word)
        if len(key) >= 2:
            keys.add(key)
    return keys


def _colors_in(text: str) -> set[str]:
    found: set[str] = set()
    for word in _SPLIT.split(text or ""):
        color = _english_color(word)
        if color and (word.casefold() in COLOR_EQUIVALENTS or color in COLOR_EQUIVALENTS.values()):
            found.add(color)
        elif word.casefold() in {"black", "white", "red", "blue", "green", "navy", "maroon"}:
            found.add(word.casefold())
    return found


@dataclass
class _Product:
    id: uuid.UUID
    name: str
    sku: str | None
    has_variants: bool
    price: int
    keys: set[str] = field(default_factory=set)
    colors: set[str] = field(default_factory=set)


async def _catalogue(db: AsyncSession) -> list[_Product]:
    rows = await db.execute(
        sa.select(
            Product.id,
            Product.name,
            Product.sku,
            Product.has_variants,
            Product.default_selling_price_paisa,
        )
        .where(Product.is_active.is_(True), Product.archived_at.is_(None))
        .order_by(Product.updated_at.desc())
        .limit(MAX_CATALOGUE)
    )
    return [
        _Product(
            id=row[0],
            name=row[1],
            sku=row[2],
            has_variants=bool(row[3]),
            price=int(row[4] or 0),
            keys=name_keys(row[1]),
            colors=_colors_in(row[1]),
        )
        for row in rows.all()
    ]


async def _sku_hit(db: AsyncSession, words: list[str]) -> tuple[uuid.UUID, uuid.UUID | None] | None:
    codes = sorted({w.upper() for w in words if len(w) >= 3 and any(c.isdigit() for c in w)})
    if not codes:
        return None
    variant = await db.execute(
        sa.select(ProductVariant.product_id, ProductVariant.id).where(
            sa.func.upper(ProductVariant.sku).in_(codes), ProductVariant.is_active.is_(True)
        )
    )
    found = variant.all()
    if len(found) == 1:
        return found[0][0], found[0][1]
    product = await db.execute(
        sa.select(Product.id).where(
            sa.func.upper(Product.sku).in_(codes), Product.is_active.is_(True)
        )
    )
    ids = product.scalars().all()
    if len(ids) == 1:
        return ids[0], None
    return None


def _variant_tokens(variant: ProductVariant) -> set[str]:
    values = [variant.name, *[str(v) for v in (variant.options or {}).values()]]
    tokens: set[str] = set()
    for value in values:
        for word in _SPLIT.split(value or ""):
            if not word:
                continue
            tokens.add(word.casefold())
            color = _english_color(word)
            if color:
                tokens.add(color)
    return tokens


async def _variants(
    db: AsyncSession, product: _Product, size: str | None, color: str | None
) -> dict[str, Any]:
    if not product.has_variants:
        return {"variant_status": "NONE"}
    rows = (
        await db.scalars(
            sa.select(ProductVariant)
            .where(ProductVariant.product_id == product.id, ProductVariant.is_active.is_(True))
            .order_by(ProductVariant.position, ProductVariant.name)
        )
    ).all()
    if not rows:
        return {"variant_status": "NONE"}
    wanted_size = size.casefold() if size else None
    wanted_color = _english_color(color)
    remaining = [
        row
        for row in rows
        if (wanted_size is None or wanted_size in _variant_tokens(row))
        and (wanted_color is None or wanted_color in _variant_tokens(row))
    ]
    if len(rows) == 1 and remaining:
        remaining = list(rows)
    if len(remaining) == 1:
        variant = remaining[0]
        return {
            "variant_status": "MATCHED",
            "variant_id": str(variant.id),
            "variant_name": variant.name,
            "unit_price_paisa": variant.price_paisa
            if variant.price_paisa is not None
            else (product.price or None),
        }
    choices = remaining or list(rows)
    return {
        "variant_status": "AMBIGUOUS",
        "variant_candidates": [
            {"variant_id": str(row.id), "name": row.name} for row in choices[:20]
        ],
    }


async def match_items(db: AsyncSession, items: list[ParsedItem]) -> list[dict[str, Any]]:
    """Each parsed item with its catalogue match. Reads only; never writes."""
    catalogue = await _catalogue(db) if items else []
    by_id = {product.id: product for product in catalogue}
    matched: list[dict[str, Any]] = []
    for item in items:
        line: dict[str, Any] = {
            "name": item.name,
            "quantity": item.quantity,
            "size": item.size,
            "color": item.color,
        }
        words = [w for w in _SPLIT.split(item.name) if w]
        hit = await _sku_hit(db, words)
        product: _Product | None = by_id.get(hit[0]) if hit else None
        match: dict[str, Any]
        if product is not None:
            match = {
                "status": "MATCHED",
                "product_id": str(product.id),
                "product_name": product.name,
            }
            if hit and hit[1] is not None:
                variant = await db.get(ProductVariant, hit[1])
                match |= {
                    "variant_status": "MATCHED",
                    "variant_id": str(hit[1]),
                    "variant_name": variant.name if variant else None,
                    "unit_price_paisa": variant.price_paisa
                    if variant and variant.price_paisa is not None
                    else (product.price or None),
                }
            else:
                match |= await _variants(db, product, item.size, item.color)
                match.setdefault("unit_price_paisa", product.price or None)
            line["match"] = match
            matched.append(line)
            continue

        wanted = name_keys(item.name)
        color = _english_color(item.color)
        scored: list[tuple[int, bool, bool, _Product]] = []
        for candidate in catalogue:
            overlap = len(wanted & candidate.keys)
            if not overlap:
                continue
            exact = bool(wanted) and wanted == candidate.keys
            scored.append((overlap, exact, bool(color and color in candidate.colors), candidate))
        if not wanted or not scored:
            line["match"] = {"status": "NOT_FOUND"}
            matched.append(line)
            continue
        scored.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
        best = scored[0]
        top = [row for row in scored if row[:3] == best[:3]]
        covers_item = best[0] == len(wanted)
        chosen: _Product | None = None
        if len(top) == 1 and covers_item and (len(scored) == 1 or best[1]):
            chosen = best[3]
        if chosen is None:
            line["match"] = {
                "status": "AMBIGUOUS",
                "candidates": [
                    {"product_id": str(row[3].id), "name": row[3].name}
                    for row in scored[:MAX_CANDIDATES]
                ],
            }
            matched.append(line)
            continue
        match = {"status": "MATCHED", "product_id": str(chosen.id), "product_name": chosen.name}
        match |= await _variants(db, chosen, item.size, item.color)
        match.setdefault("unit_price_paisa", chosen.price or None)
        line["match"] = match
        matched.append(line)
    return matched
