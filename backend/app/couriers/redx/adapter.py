"""The RedX courier adapter.

Implements :class:`~app.couriers.adapter.CourierAdapter` and refuses every
capability, with a reason, because no RedX documentation has been supplied. See
:mod:`app.couriers.redx.contract` for what was checked and why community
agreement is not promoted to a contract.

This is a *documented refusal*, not a stub, and registering it is deliberate.
Before this existed, ``registry.get("redx")`` returned ``None``, and every
caller turned that into "redx is not a courier ecomsbd can connect to" — which
reads as *never will be*, and tells a seller nothing about what would change it.
An adapter that answers :class:`~app.couriers.adapter.Unavailable` with a
reason lets the settings screen, the booking sheet and the admin console all
say the same true thing: RedX needs its documentation supplied, and manual mode
covers it meanwhile.

The one method that could plausibly do something is
:meth:`RedxAdapter.validate_credentials`. It does not, and must not: with no
documented endpoint there is nothing safe to call, and returning anything other
than "we could not check" would either mark a seller's real credentials bad or
claim a connection that cannot book. Storing credentials for RedX is refused
higher up, in the account service, for the same reason.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.common.money import Money
from app.core.logging import get_logger
from app.couriers.adapter import (
    BookingOutcome,
    BookingRequest,
    BookingResult,
    ProviderCustomerStats,
    ProviderEvent,
    ProviderPayout,
    ProviderStatus,
    Quote,
    Store,
    Unavailable,
    ValidationResult,
)
from app.couriers.capabilities import Capability
from app.couriers.redx.contract import (
    CONTRACT_BLOCKER,
    PROVIDER,
    UNAVAILABLE_REASON_EN,
)

__all__ = ["PROVIDER", "SUPPORTED_CAPABILITIES", "RedxAdapter"]

log = get_logger(__name__)

#: Empty, and that is the point. A capability is only ever added here alongside
#: the documentation that describes it.
SUPPORTED_CAPABILITIES: frozenset[Capability] = frozenset()


class RedxAdapter:
    """RedX, behind the standard courier interface, supporting nothing yet."""

    provider = PROVIDER
    blocker = CONTRACT_BLOCKER

    # -------------------------------------------------------- capabilities --

    async def capabilities(self, creds: Any) -> set[Capability]:
        """Nothing. Does not call the provider — there is nothing to call."""
        return set()

    # ---------------------------------------------------------- credentials --

    async def validate_credentials(self, creds: Any) -> ValidationResult:
        """Inconclusive, always.

        ``valid=False, rejected=False`` is the honest answer and the safe one:
        ``rejected=True`` would tell a seller their real RedX token is wrong,
        which it may not be, and ``valid=True`` would claim a connection that
        cannot book a parcel.
        """
        log.info(
            "redx credential check skipped",
            extra={
                "provider": PROVIDER,
                "operation": "validate_credentials",
                "blocker": CONTRACT_BLOCKER,
            },
        )
        return ValidationResult(
            valid=False,
            rejected=False,
            message=UNAVAILABLE_REASON_EN,
            detected_capabilities=frozenset(),
        )

    # -------------------------------------------------------------- booking --

    async def create_consignment(
        self, creds: Any, req: BookingRequest, merchant_reference: str
    ) -> BookingResult:
        """A clean, unambiguous failure. Nothing was sent, so nothing exists.

        ``FAILED`` rather than ``UNKNOWN`` is correct and important here: the
        ambiguous outcome exists for requests that may have reached a provider,
        and this one provably did not leave the process. Reporting it as
        ambiguous would put a parcel into booking recovery that has nothing to
        recover.
        """
        return BookingResult(
            outcome=BookingOutcome.FAILED,
            error_code=CONTRACT_BLOCKER,
            error_message=UNAVAILABLE_REASON_EN,
        )

    async def create_bulk(
        self, creds: Any, reqs: list[BookingRequest]
    ) -> list[BookingResult] | Unavailable:
        return Unavailable(Capability.CREATE_BULK, UNAVAILABLE_REASON_EN)

    # --------------------------------------------- everything else, unasked --

    async def list_stores(self, creds: Any) -> list[Store] | Unavailable:
        return Unavailable(Capability.LIST_STORES, UNAVAILABLE_REASON_EN)

    async def quote(self, creds: Any, req: BookingRequest) -> Quote | Unavailable:
        return Unavailable(Capability.PRICE_QUOTE, UNAVAILABLE_REASON_EN)

    async def get_status(self, creds: Any, reference: str) -> ProviderStatus | Unavailable:
        return Unavailable(Capability.STATUS_LOOKUP, UNAVAILABLE_REASON_EN)

    async def cancel(self, creds: Any, reference: str) -> bool | Unavailable:
        return Unavailable(Capability.CANCEL, UNAVAILABLE_REASON_EN)

    async def request_return(self, creds: Any, reference: str, reason: str) -> bool | Unavailable:
        return Unavailable(Capability.RETURNS, UNAVAILABLE_REASON_EN)

    async def get_balance(self, creds: Any) -> Money | Unavailable:
        return Unavailable(Capability.BALANCE, UNAVAILABLE_REASON_EN)

    async def list_payouts(self, creds: Any, since: datetime) -> list[ProviderPayout] | Unavailable:
        return Unavailable(Capability.PAYOUTS, UNAVAILABLE_REASON_EN)

    async def customer_stats(
        self, creds: Any, phone_e164: str
    ) -> ProviderCustomerStats | Unavailable:
        return Unavailable(Capability.CUSTOMER_STATS, UNAVAILABLE_REASON_EN)

    # ------------------------------------------------------------- webhooks --

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Always ``False``. No signature scheme is documented.

        Returning ``True`` — or inventing an HMAC over a guessed header — would
        mean accepting unauthenticated requests that move money.
        """
        return False

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> list[ProviderEvent]:
        """Always empty. No payload shape is documented, so none is parsed."""
        return []
