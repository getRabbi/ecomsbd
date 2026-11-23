"""BD Courier (``bdcourier.com``) behind the external risk port (V4 WP0.6).

Contract: ``docs/providers/bdcourier/CONTRACT.md`` (the provider's own developer page,
read 2026-11-23). One documented lookup, ``POST /courier-check`` with the number; the
answer's per-courier parcel counts become :class:`CourierHistory`. The provider's own
verdict (``risk_level``, ``risk_verdict``), its ratio and its ``reports`` are dropped:
the port carries counts, never a score.

The provider's terms shape the rest (CONTRACT.md §6): every shop uses its **own**
BD Courier key (there is no platform key), a lookup needs a new order
(``new_orders_only``), answers are kept 30 days at most, and nothing calls the
provider in the background.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.common.phone import try_normalize_bd_phone
from app.core.logging import get_logger
from app.risk_providers.contract import (
    Capability,
    CourierCount,
    CourierHistory,
    CredentialField,
    FailureKind,
    LookupRequest,
    ProviderError,
    ProviderFact,
    ProviderResult,
    ProviderSpec,
)

log = get_logger(__name__)

PROVIDER_ID = "bdcourier"
#: CONTRACT.md §1: "সকল API রিকোয়েস্টের জন্য".
BASE_URL = "https://api.bdcourier.com"
CHECK_PATH = "/courier-check"
PLANS_PATH = "/plans"
#: The courier key BD Courier uses for its own total (CONTRACT.md §2 sample).
SUMMARY_KEY = "summary"
_COUNT_FIELDS = ("total_parcel", "success_parcel", "cancelled_parcel")
_MAX_RETRY_AFTER = 24 * 3600

SPEC = ProviderSpec(
    provider_id=PROVIDER_ID,
    display_name="BD Courier",
    official_contract="docs/providers/bdcourier/CONTRACT.md",
    markets=frozenset({"BD"}),
    # No CONNECTION_TEST: the page has no key check (CONTRACT.md §3); the test
    # only proves the API answers.
    capabilities=frozenset({Capability.PHONE_DELIVERY_HISTORY}),
    credential_fields=(
        CredentialField("api_key", "Your BD Courier API key", "আপনার BD Courier API কী"),
    ),
    default_cache_ttl_hours=24,
    min_cache_ttl_hours=1,
    max_cache_ttl_hours=72,
    timeout_seconds=8.0,
    new_orders_only=True,
    retention_days=30,
    notice_en=(
        "Use your own BD Courier account and API key (BD Courier's terms require each "
        "shop's own key). A check is allowed only for a new order waiting for "
        "confirmation: ecomsbd checks a customer only when they have a draft or "
        "confirmed order from the last 3 days, and only when you tap Check. "
        "The connection test shows that BD Courier answers; your key is checked on the "
        "first lookup."
    ),
    notice_bn=(
        "নিজের BD Courier অ্যাকাউন্ট ও API কী ব্যবহার করুন (BD Courier-এর শর্তে প্রতিটি শপের "
        "নিজের কী লাগে)। শুধু কনফার্মেশনের অপেক্ষায় থাকা নতুন অর্ডারের জন্য চেক করা যায়: "
        "গত ৩ দিনের খসড়া বা কনফার্ম অর্ডার থাকলে এবং আপনি চেক চাপলে তবেই ecomsbd গ্রাহককে "
        "চেক করে। কানেকশন টেস্ট দেখায় BD Courier সাড়া দিচ্ছে; আপনার কী প্রথম চেকের সময় যাচাই হয়।"
    ),
)


def _count(value: Any) -> int | None:
    """A whole, non-negative count, or ``None``. ``True`` is not a count."""
    if type(value) is int and value >= 0:
        return value
    return None


def _row(courier: str, raw: Any) -> CourierCount | None:
    if not isinstance(raw, dict):
        return None
    total, success, cancelled = (_count(raw.get(name)) for name in _COUNT_FIELDS)
    if total is None or success is None or cancelled is None:
        return None
    return CourierCount(courier=courier, delivered=success, cancelled=cancelled, total=total)


def parse_courier_check(body: Any) -> ProviderResult:
    """The documented answer → counts. Raises :class:`ProviderError` when unreadable."""
    if not isinstance(body, dict):
        raise ProviderError(FailureKind.BAD_RESPONSE, detail="BDCOURIER_NOT_AN_OBJECT")
    status = body.get("status")
    if status == "error":
        raise ProviderError(FailureKind.REJECTED, detail="BDCOURIER_STATUS_ERROR")
    if status != "success":
        raise ProviderError(FailureKind.BAD_RESPONSE, detail="BDCOURIER_STATUS_UNKNOWN")
    data = body.get("data")
    if not isinstance(data, dict):
        raise ProviderError(FailureKind.BAD_RESPONSE, detail="BDCOURIER_NO_DATA")

    rows: list[CourierCount] = []
    for courier, raw in data.items():
        if courier == SUMMARY_KEY:
            continue
        row = _row(str(courier), raw)
        # A courier with no parcels says nothing; leave it out of the chip.
        if row is not None and row.total > 0:
            rows.append(row)
    aggregate = _row(SUMMARY_KEY, data.get(SUMMARY_KEY))
    total = aggregate.total if aggregate is not None else sum(r.total for r in rows)
    if total <= 0:
        return ProviderResult(found=False)

    facts: tuple[ProviderFact, ...] = ()
    if aggregate is not None:
        facts = (
            ProviderFact("TOTAL_PARCELS", aggregate.total),
            ProviderFact("DELIVERED_PARCELS", aggregate.delivered),
        )
    return ProviderResult(
        found=True,
        facts=facts,
        sample_size=total,
        courier_history=CourierHistory(provider_counts=tuple(rows), aggregate=aggregate),
    )


def _retry_after(response: httpx.Response) -> int | None:
    raw = (response.headers.get("retry-after") or "").strip()
    if raw.isdigit():
        return min(_MAX_RETRY_AFTER, max(1, int(raw)))
    return None


class BdCourierAdapter:
    """Asks BD Courier about one number with the shop's own key."""

    spec = SPEC

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        #: Tests install an ``httpx.MockTransport`` here; production uses the network.
        self._transport = transport

    def __repr__(self) -> str:  # never a key
        return f"BdCourierAdapter(base_url={self._base_url!r})"

    async def _request(
        self, method: str, path: str, *, headers: dict[str, str], json: dict | None = None
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self.spec.timeout_seconds
            ) as client:
                response = await client.request(
                    method, f"{self._base_url}{path}", headers=headers, json=json
                )
        except httpx.TimeoutException as exc:
            raise ProviderError(FailureKind.TIMEOUT, detail="BDCOURIER_TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(FailureKind.UNAVAILABLE, detail="BDCOURIER_UNREACHABLE") from exc
        status = response.status_code
        if status in (401, 403):
            raise ProviderError(FailureKind.AUTH_FAILED, detail=f"BDCOURIER_HTTP_{status}")
        if status == 429:
            raise ProviderError(
                FailureKind.RATE_LIMITED,
                detail="BDCOURIER_HTTP_429",
                retry_after_seconds=_retry_after(response),
            )
        if status >= 500:
            raise ProviderError(FailureKind.UNAVAILABLE, detail=f"BDCOURIER_HTTP_{status}")
        if status >= 400:
            raise ProviderError(FailureKind.REJECTED, detail=f"BDCOURIER_HTTP_{status}")
        if not 200 <= status < 300:
            raise ProviderError(FailureKind.BAD_RESPONSE, detail=f"BDCOURIER_HTTP_{status}")
        return response

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(FailureKind.BAD_RESPONSE, detail="BDCOURIER_NOT_JSON") from exc

    async def test_connection(self, credentials: dict[str, str], config: dict) -> None:
        """``GET /plans`` (no auth): BD Courier answers. The key is not checked here.

        The page documents no key check, and a test ``/courier-check`` would be a
        lookup without an order, which the provider's terms forbid.
        """
        if not (credentials.get("api_key") or "").strip():
            raise ProviderError(FailureKind.AUTH_FAILED, detail="BDCOURIER_KEY_MISSING")
        response = await self._request("GET", PLANS_PATH, headers={"Accept": "application/json"})
        body = self._json(response)
        if not isinstance(body, (dict, list)):
            raise ProviderError(FailureKind.BAD_RESPONSE, detail="BDCOURIER_PLANS_UNREADABLE")

    async def lookup(
        self, request: LookupRequest, credentials: dict[str, str], config: dict
    ) -> ProviderResult:
        phone = try_normalize_bd_phone(request.phone_e164)
        if request.market != "BD" or phone is None:
            # Refused here: nothing is sent for a number the page does not accept.
            raise ProviderError(FailureKind.REJECTED, detail="BDCOURIER_NUMBER")
        key = (credentials.get("api_key") or "").strip()
        if not key:
            raise ProviderError(FailureKind.AUTH_FAILED, detail="BDCOURIER_KEY_MISSING")
        response = await self._request(
            "POST",
            CHECK_PATH,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            # CONTRACT.md §2: "১১ ডিজিটের বাংলাদেশি ফোন নম্বর (যেমন: 01XXXXXXXXX)".
            json={"phone": phone.national},
        )
        return parse_courier_check(self._json(response))


#: The one instance the registry offers (behind ``bdcourier_enabled``).
ADAPTER = BdCourierAdapter()
