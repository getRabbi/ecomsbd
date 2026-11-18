"""Courier statement profiles (V4 WP0.7).

A profile is only ever written from a real statement the operator supplied;
this test holds every shipped profile to its sample. With no samples yet,
no profile ships (``BLOCKED: STATEMENT_SAMPLE_REQUIRED``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.payouts.profiles import ProfileError, StatementProfile, load_profiles, match_profile
from app.payouts.statements import parse_statement

SAMPLES = Path(__file__).resolve().parent / "fixtures" / "statements"


@pytest.mark.parametrize("profile", load_profiles(), ids=lambda p: p.profile)
def test_every_profile_reads_its_real_sample(profile: StatementProfile) -> None:
    sample = SAMPLES / profile.sample
    assert sample.is_file(), f"{profile.profile}: sample {profile.sample} is missing"
    parsed = parse_statement(sample.read_bytes())
    assert parsed.headers == list(profile.headers)
    assert parsed.mapping == profile.mapping, "the profile must be what the reader uses"
    assert parsed.rows and not parsed.invalid_rows


def test_no_profile_without_a_sample() -> None:
    providers_with_samples = (
        {p.name for p in SAMPLES.iterdir() if p.is_dir()} if SAMPLES.exists() else set()
    )
    for profile in load_profiles():
        assert profile.provider in providers_with_samples, profile.profile


def _write(tmp_path: Path, body: str) -> Path:
    (tmp_path / "p.yaml").write_text(body, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            "provider: dhl\nprofile: x\nsample: a.csv\nheaders: [A]\nmapping: {amount: A}\n",
            "unknown provider",
        ),
        (
            "provider: redx\nprofile: x\nsample: a.csv\nheaders: [A]\nmapping: {fee: A}\n",
            "at least amount",
        ),
        (
            "provider: redx\nprofile: x\nsample: a.csv\nheaders: [A]\nmapping: {amount: B}\n",
            "not in headers",
        ),
        (
            "provider: redx\nprofile: x\nsample: a.csv\nheaders: [A]\nmapping: {amount: A, colour: A}\n",
            "unknown fields",
        ),
        ("provider: redx\nprofile: x\nheaders: [A]\nmapping: {amount: A}\n", "sample is required"),
    ],
)
def test_a_malformed_profile_is_refused_at_load(tmp_path: Path, body: str, message: str) -> None:
    load_profiles.cache_clear()
    try:
        with pytest.raises(ProfileError, match=message):
            load_profiles(_write(tmp_path, body))
    finally:
        load_profiles.cache_clear()


def test_a_matching_profile_supplies_the_mapping(tmp_path: Path) -> None:
    body = (
        "provider: redx\nprofile: redx-test\nsample: redx/x.csv\n"
        "headers: [Tracking, Amount Collected]\nmapping: {tracking_code: Tracking, amount: Amount Collected}\n"
    )
    load_profiles.cache_clear()
    try:
        profiles = load_profiles(_write(tmp_path, body))
        assert match_profile(["Tracking", "Amount Collected"], profiles) == profiles[0]
        assert match_profile(["Amount Collected", "Tracking"], profiles) is None, (
            "exact header row only"
        )
    finally:
        load_profiles.cache_clear()
