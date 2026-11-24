"""The image and CI install with constraints.txt: every dependency must be pinned there.

A dependency missing from the file would float to whatever is newest on the day
of a build, which is how sentry-sdk 3.0 reached a production rebuild (2026-11-23).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

BACKEND = Path(__file__).resolve().parents[1]


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins() -> dict[str, Version]:
    pins: dict[str, Version] = {}
    for raw in (BACKEND / "constraints.txt").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, sep, version = line.partition("==")
        assert sep, f"constraints.txt line is not an exact pin: {raw!r}"
        assert _canonical(name) not in pins, f"pinned twice: {name}"
        pins[_canonical(name)] = Version(version)
    return pins


def _requirements() -> list[Requirement]:
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    raw = list(project["dependencies"])
    for extra in project.get("optional-dependencies", {}).values():
        raw.extend(extra)
    return [Requirement(item) for item in raw]


def test_every_dependency_is_pinned_within_its_range() -> None:
    pins = _pins()
    for requirement in _requirements():
        name = _canonical(requirement.name)
        assert name in pins, f"{requirement.name} is not pinned in constraints.txt"
        assert requirement.specifier.contains(pins[name], prereleases=True), (
            f"{requirement.name}=={pins[name]} is outside pyproject's {requirement.specifier}"
        )


def test_sentry_sdk_stays_below_3() -> None:
    assert _pins()["sentry-sdk"].major < 3
