"""Provider statement profiles (V4 WP0.7).

A profile pins one real statement layout: its exact header row and the column
mapping for :func:`app.payouts.statements.parse_statement`. Profiles are YAML
files beside this module, each citing the real sample it was written from
(``tests/fixtures/statements/<provider>/...``). None ships until the operator
supplies a sample (``BLOCKED: STATEMENT_SAMPLE_REQUIRED``); until then the
generic column detection is used. See ``README.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.payouts.statements import STATEMENT_ALIASES

__all__ = [
    "PROFILE_DIR",
    "PROVIDERS",
    "ProfileError",
    "StatementProfile",
    "load_profiles",
    "match_profile",
]

PROFILE_DIR = Path(__file__).resolve().parent
PROVIDERS = frozenset({"steadfast", "pathao", "redx"})


class ProfileError(ValueError):
    """A profile file is malformed; raised at load, never while reading money."""


@dataclass(frozen=True, slots=True)
class StatementProfile:
    provider: str
    profile: str
    sample: str
    headers: tuple[str, ...]
    mapping: dict[str, str]


def _parse(path: Path, raw: Any) -> StatementProfile:
    if not isinstance(raw, dict):
        raise ProfileError(f"{path.name}: not a mapping")
    provider = raw.get("provider")
    if provider not in PROVIDERS:
        raise ProfileError(f"{path.name}: unknown provider {provider!r}")
    headers = raw.get("headers")
    mapping = raw.get("mapping")
    if (
        not isinstance(headers, list)
        or not headers
        or not all(isinstance(h, str) and h for h in headers)
    ):
        raise ProfileError(f"{path.name}: headers must be the exact header row")
    if not isinstance(mapping, dict) or "amount" not in mapping:
        raise ProfileError(f"{path.name}: mapping must map at least amount")
    unknown = set(mapping) - set(STATEMENT_ALIASES)
    if unknown:
        raise ProfileError(f"{path.name}: unknown fields {sorted(unknown)}")
    missing = {str(v) for v in mapping.values()} - set(headers)
    if missing:
        raise ProfileError(f"{path.name}: mapped columns not in headers {sorted(missing)}")
    for key in ("profile", "sample"):
        if not isinstance(raw.get(key), str) or not raw[key]:
            raise ProfileError(f"{path.name}: {key} is required")
    return StatementProfile(
        provider=str(provider),
        profile=str(raw["profile"]),
        sample=str(raw["sample"]),
        headers=tuple(headers),
        mapping={str(k): str(v) for k, v in mapping.items()},
    )


@lru_cache(maxsize=1)
def load_profiles(directory: Path = PROFILE_DIR) -> tuple[StatementProfile, ...]:
    profiles = tuple(
        _parse(path, yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.yaml"))
    )
    names = [p.profile for p in profiles]
    if len(names) != len(set(names)):
        raise ProfileError("duplicate profile name")
    return profiles


def match_profile(
    headers: list[str], profiles: tuple[StatementProfile, ...] | None = None
) -> StatementProfile | None:
    """The profile whose header row is exactly this file's, if any (one only)."""
    candidates = [
        p for p in (load_profiles() if profiles is None else profiles) if list(p.headers) == headers
    ]
    return candidates[0] if len(candidates) == 1 else None
