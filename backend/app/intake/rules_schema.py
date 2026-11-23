"""The intake rules payload, schema version 1 (V4 WP1.2).

Devices (Kotlin on Android, TypeScript in the extension) run these rules on
their own text, so everything here must mean the same thing on every engine:

* regexes use only constructs Java, JavaScript and Python agree on — explicit
  character classes, ``(?:…)``, fixed-width look-around, ``\\uXXXX`` escapes.
  ``\\d``/``\\w``/``\\s``/``\\b`` are refused because their Unicode meaning differs
  between engines, and inline flags because JavaScript has no ``(?i)``;
* keywords are stored NFC-normalised and lower-cased, and matched as the
  ``matching`` block says (devices NFC-normalise and lower-case text first);
* a phone match is mandatory, and a phone alone never reaches the threshold —
  otherwise every "call me on 017…" would raise a heads-up.

Extension selectors are optional per host and carry ``verified_at``: a selector
nobody has checked against the live page is an unverified provider detail, so
it is not published (the extension then captures from the text selection).
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "DISTRICT_LIST_IDS",
    "EXTENSION_HOSTS",
    "MAX_PAYLOAD_BYTES",
    "IntakeRulesV1",
    "canonical_json",
]

#: Phase 5: the extension runs on these hosts only.
EXTENSION_HOSTS = ("business.facebook.com", "www.messenger.com", "web.whatsapp.com")
#: Lists a payload may reference instead of copying (resolved by the server).
DISTRICT_LIST_IDS = ("bd_districts_64",)
MAX_PAYLOAD_BYTES = 64 * 1024

_ALLOWED_ESCAPES = frozenset("-.+*?()[]{}|^$/\\tn")
_HEX4 = re.compile(r"[0-9A-Fa-f]{4}")
_LATIN_KEYWORD = re.compile(r"^[a-z0-9][a-z0-9 '\-]*$")
_BANGLA = re.compile(r"[\u0980-\u09FF]")


def canonical_json(value: object) -> str:
    """The one serialisation used for hashing and size checks."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _portable_regex(pattern: str) -> str:
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            escaped = pattern[index + 1 : index + 2]
            if escaped == "u" and _HEX4.fullmatch(pattern[index + 2 : index + 6]):
                index += 6
                continue
            if escaped not in _ALLOWED_ESCAPES:
                raise ValueError(f"escape \\{escaped} is not portable across devices")
            index += 2
            continue
        if (
            char == "("
            and pattern[index + 1 : index + 2] == "?"
            and not pattern.startswith(("(?:", "(?=", "(?!", "(?<=", "(?<!"), index)
        ):
            raise ValueError("inline flags and named groups are not portable")
        index += 1
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"does not compile: {exc.msg}") from None
    if compiled.search("") is not None:
        raise ValueError("must not match empty text")
    return pattern


PortableRegex = Annotated[str, Field(min_length=1, max_length=300), AfterValidator(_portable_regex)]


def _keywords(script: Literal["latin", "bangla"]) -> AfterValidator:
    def check(values: list[str]) -> list[str]:
        seen: dict[str, None] = {}
        for raw in values:
            word = " ".join(unicodedata.normalize("NFC", raw).casefold().split())
            if not 1 <= len(word) <= 40:
                raise ValueError("a keyword is 1-40 characters")
            if script == "latin" and not _LATIN_KEYWORD.fullmatch(word):
                raise ValueError(f"not a latin keyword: {word!r}")
            if script == "bangla" and not _BANGLA.search(word):
                raise ValueError(f"not a Bangla keyword: {word!r}")
            seen.setdefault(word, None)
        return list(seen)

    return AfterValidator(check)


LatinKeywords = Annotated[list[str], Field(min_length=1, max_length=200), _keywords("latin")]
BanglaKeywords = Annotated[list[str], Field(min_length=1, max_length=200), _keywords("bangla")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PackageRule(_Strict):
    package: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$", max_length=100)
    label: str = Field(min_length=1, max_length=60)
    default_enabled: bool


class PhoneRules(_Strict):
    #: Any match is a candidate span; devices then normalise it (below).
    patterns: list[PortableRegex] = Field(min_length=1, max_length=5)
    #: Applied after Bangla→ASCII digits, dropping non-digits and the country prefix.
    national_pattern: PortableRegex
    country_prefix: str = Field(pattern=r"^[0-9]{1,3}$")
    required: Literal[True] = True


class IntentKeywords(_Strict):
    bn: BanglaKeywords
    banglish: LatinKeywords
    en: LatinKeywords


class AddressRules(_Strict):
    bn: BanglaKeywords
    latin: LatinKeywords
    district_list: Literal["bd_districts_64"]


class Matching(_Strict):
    normalization: Literal["NFC"] = "NFC"
    case: Literal["insensitive"] = "insensitive"
    #: Latin keywords match whole words/phrases; Bangla ones as substrings,
    #: because Bangla attaches suffixes ("অর্ডারটা").
    latin: Literal["whole_word"] = "whole_word"
    bangla: Literal["substring"] = "substring"


class Weights(_Strict):
    phone: int = Field(ge=0, le=100)
    intent: int = Field(ge=0, le=100)
    address: int = Field(ge=0, le=100)
    district: int = Field(ge=0, le=100)


class MaxHits(_Strict):
    intent: int = Field(ge=1, le=10)
    address: int = Field(ge=1, le=10)


class Score(_Strict):
    """``phone + intent·min(hits, max) + address·min(hits, max) + district``."""

    weights: Weights
    max_hits: MaxHits
    threshold: int = Field(ge=1, le=1000)

    @model_validator(mode="after")
    def _reachable_and_not_phone_alone(self) -> Score:
        w, cap = self.weights, self.max_hits
        best = w.phone + w.intent * cap.intent + w.address * cap.address + w.district
        if self.threshold > best:
            raise ValueError("threshold is higher than any message can score")
        if self.threshold <= w.phone:
            raise ValueError("a phone number alone must not reach the threshold")
        return self


class Selectors(_Strict):
    conversation: str | None = Field(default=None, max_length=300)
    message: str | None = Field(default=None, max_length=300)
    message_text: str | None = Field(default=None, max_length=300)
    sender: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _plain_selectors(self) -> Selectors:
        for value in (self.conversation, self.message, self.message_text, self.sender):
            if value is not None and (not value.strip() or any(c in value for c in "{}<\n")):
                raise ValueError("a selector is a single CSS selector")
        return self


class ExtensionHost(_Strict):
    selectors: Selectors = Selectors()
    #: When someone last checked the selectors against the live page.
    verified_at: date | None = None

    @model_validator(mode="after")
    def _only_verified_selectors(self) -> ExtensionHost:
        if self.verified_at is None and any(self.selectors.model_dump().values()):
            raise ValueError("selectors need verified_at (unverified selectors are not shipped)")
        return self


class ExtensionRules(_Strict):
    hosts: dict[
        Literal["business.facebook.com", "www.messenger.com", "web.whatsapp.com"], ExtensionHost
    ]


class IntakeRulesV1(_Strict):
    schema_version: Literal[1] = 1
    packages: list[PackageRule] = Field(min_length=1, max_length=20)
    phone: PhoneRules
    intent: IntentKeywords
    address: AddressRules
    matching: Matching = Matching()
    score: Score
    #: Messages of one conversation within this window form one buffer.
    buffer_window_minutes: int = Field(ge=1, le=120)
    #: How often a device re-checks the rules (it sends If-None-Match).
    refresh_hours: int = Field(ge=1, le=168)
    extension: ExtensionRules

    @model_validator(mode="after")
    def _unique_packages_and_size(self) -> IntakeRulesV1:
        names = [rule.package for rule in self.packages]
        if len(set(names)) != len(names):
            raise ValueError("a package is listed twice")
        if len(canonical_json(self.model_dump(mode="json")).encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError("the rule set is larger than 64 KiB")
        return self
