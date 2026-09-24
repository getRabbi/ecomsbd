"""Optional licensed facts, deliberately separate from the first-party risk score.

The provider-neutral layer lives in :mod:`app.risk_providers`. This module keeps
the capability summary the screens have read since V2.
"""

from app.risk_providers import registry
from app.risk_providers.registry import BLOCKER

__all__ = ["BLOCKER", "capability", "configured_provider"]


def configured_provider() -> object | None:
    """The first reviewed adapter, if any. Environment switches cannot add one."""
    adapters = registry.available()
    return adapters[0] if adapters else None


def capability() -> dict:
    available = configured_provider() is not None
    return {
        "status": "AVAILABLE" if available else "GATED",
        "available": available,
        "blocker": None if available else BLOCKER,
        "provider": None,
        "facts": [],
        "first_party_unchanged": True,
        "message_en": (
            "External facts come from a licensed provider your shop connects. Your shop's own Risk Check is unchanged."
            if available
            else "External facts require a licensed official provider. Your shop's own Risk Check remains available."
        ),
        "message_bn": (
            "বাইরের তথ্য আসে আপনার শপের যুক্ত করা লাইসেন্সপ্রাপ্ত প্রোভাইডার থেকে। আপনার শপের নিজস্ব রিস্ক চেক অপরিবর্তিত।"
            if available
            else "বাইরের তথ্যের জন্য লাইসেন্সপ্রাপ্ত অফিশিয়াল প্রোভাইডার প্রয়োজন। আপনার শপের নিজস্ব রিস্ক চেক চালু আছে।"
        ),
    }
