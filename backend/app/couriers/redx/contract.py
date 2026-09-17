"""The RedX contract — deliberately empty, and the record of why.

This module contains **no endpoint, no field name, no status string and no
header**. That is not an oversight or a stub awaiting a spare afternoon; it is
the only honest state until RedX documentation is supplied, and this file exists
so that fact is reviewable rather than implicit in an absence.

What was checked, on 2026-09-18:

*   ``openapi.redx.com.bd`` is live and answers JSON, so the host is real. It
    serves ``404 {"message": "Please check your specified endpoint and request
    method"}`` for ``/``, ``/docs``, ``/api-docs``, ``/swagger.json``,
    ``/openapi.json``, ``/redoc`` and ``/.well-known/openapi.json``. No
    machine-readable spec is published.
*   ``redx.com.bd/api-documentation`` answers ``301`` to ``/404/``. There is no
    public documentation page.
*   The RedX Shopify app is published by ShopUp — RedX's parent — but an app
    store listing carries no contract.
*   Everything else discoverable is third party: Laravel packages, a
    community WooCommerce plugin, a nopCommerce plugin, blog posts.

Several of those third-party integrations agree with each other on a base URL,
an ``API-ACCESS-TOKEN`` header and a handful of paths. **Agreement between
community implementations is not documentation.** Two reasons this repository
refuses to promote it:

1.  It is the exact category :mod:`app.couriers.steadfast.contract` and
    :mod:`app.couriers.pathao.contract` both name and exclude. Pathao was
    implemented from Pathao's *own* published source, and the difference was
    not academic — the community contract for Pathao's own login endpoint is
    wrong. Applying a weaker standard to RedX would void the guarantee the
    capability manifest exists to make.
2.  The failure is asymmetric and expensive. A wrong field name on a read
    fails harmlessly. A wrong field name on a create either fails, or ships a
    real parcel to the wrong place and bills the seller for it. RedX pairs a
    delivery-area name with an area id, which is precisely the shape that
    misroutes silently rather than erroring.

So RedX is *registered and known*, reports every capability as unavailable with
a reason a seller can act on, and cannot have credentials stored against it.
Manual courier mode covers RedX completely in the meantime, exactly as it does
for any provider outage.

``docs/providers/redx/CONTRACT_REQUIRED.md`` is the request to send RedX. When
the answer arrives, this module becomes a transcription of it and the adapter
fills in behind the interface it already implements.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "CONTRACT_BLOCKER",
    "CONTRACT_REQUEST_DOC",
    "DOCUMENTATION_CHECKED_ON",
    "PROVIDER",
    "REQUIRED_CONTRACT_ITEMS",
    "UNAVAILABLE_REASON_BN",
    "UNAVAILABLE_REASON_EN",
]

PROVIDER: Final = "redx"

#: The operator-facing identifier. Appears verbatim in the provider manifest,
#: in docs/RELEASE_READINESS.md and in the admin console, so all three read the
#: same string.
CONTRACT_BLOCKER: Final = "REDX_API_DOCUMENTATION_REQUIRED"

#: When the search above was performed. Re-check before assuming it still holds.
DOCUMENTATION_CHECKED_ON: Final = "2026-09-18"

CONTRACT_REQUEST_DOC: Final = "docs/providers/redx/CONTRACT_REQUIRED.md"

#: Exactly what has to be supplied before any RedX call may be written. Each
#: entry is a question the documentation must answer, not a feature request.
#: The admin console renders this list, so an operator chasing RedX knows what
#: to ask for in one message rather than three rounds of email.
REQUIRED_CONTRACT_ITEMS: Final[tuple[str, ...]] = (
    "Base URL(s), and whether a sandbox host exists.",
    "Authentication: header or parameter name, credential format, and whether "
    "the credential expires or is refreshed.",
    "Create parcel: path, method, every request field with which are required, "
    "and the success response shape including the parcel/tracking identifier.",
    "Whether a merchant-supplied reference is accepted on create, and what "
    "happens when the same one is sent twice — the idempotency answer.",
    "Status lookup: path, method, and the complete list of status strings with "
    "what each one means for the parcel and for money.",
    "Delivery-area handling: whether a free-text address is accepted, or an "
    "area id is required, and where the area list comes from.",
    "Pickup stores: whether one must be selected, and how to list them.",
    "Cancel and return: whether either is supported, and the exact contract.",
    "Delivery charge and COD: which figures the API reports, and whether a "
    "create response carries a fee.",
    "Webhook: whether callbacks exist, the signature scheme, the payload "
    "shape, and the event list.",
    "Payments/settlement: whether payouts can be read, and the response shape.",
    "Rate limits, pagination, and the error response body.",
)

#: Shown to a seller. Says what to do instead, not which section is missing.
UNAVAILABLE_REASON_EN: Final = (
    "RedX has not published its API documentation, and ecomsbd will not call a "
    "courier using guessed field names — a wrong guess can ship a real parcel "
    "to the wrong place. Record RedX parcels by hand and upload the RedX "
    "statement; everything else works exactly as it does for a connected "
    "courier."
)

UNAVAILABLE_REASON_BN: Final = (
    "RedX তাদের API ডকুমেন্টেশন প্রকাশ করেনি, আর অনুমান করা ফিল্ড দিয়ে ecomsbd "
    "কোনো কুরিয়ারে অনুরোধ পাঠায় না — একটি ভুল অনুমানে আসল পার্সেল ভুল জায়গায় "
    "চলে যেতে পারে। RedX পার্সেল নিজে লিখে রাখুন আর RedX স্টেটমেন্ট আপলোড করুন; "
    "বাকি সব যুক্ত কুরিয়ারের মতোই কাজ করবে।"
)
