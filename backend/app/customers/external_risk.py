"""Optional licensed facts, deliberately separate from the first-party risk score."""

from dataclasses import dataclass
from typing import Protocol

BLOCKER = "EXTERNAL_RISK_PROVIDER_REQUIRED"


@dataclass(frozen=True)
class ExternalFact:
    name: str
    value: int
    observed_at: str
    source: str


class ExternalRiskProvider(Protocol):
    """A reviewed official adapter must implement this port before activation.

    No scraper, shared customer blacklist, or guessed API is registered. A live
    implementation also needs licensed purpose/retention and consent handling.
    """

    provider_id: str
    official_contract: str
    licensed: bool

    async def lookup(self, *, authorized_subject: str) -> list[ExternalFact]: ...


def configured_provider() -> ExternalRiskProvider | None:
    # No licensed contract is present in this release. Environment switches or
    # client-supplied provider names cannot bypass a missing implementation.
    return None


def capability() -> dict:
    return {
        "status": "GATED",
        "available": False,
        "blocker": BLOCKER,
        "provider": None,
        "facts": [],
        "first_party_unchanged": True,
        "message_en": "External facts require a licensed official provider. Your shop's own Risk Check remains available.",
        "message_bn": "বাইরের তথ্যের জন্য লাইসেন্সপ্রাপ্ত অফিশিয়াল প্রোভাইডার প্রয়োজন। আপনার শপের নিজস্ব রিস্ক চেক চালু আছে।",
    }
