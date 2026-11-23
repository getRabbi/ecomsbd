"""Model-assisted order parsing (V4 WP0.3).

``EnhancedOrderParser.parse`` always returns a usable :class:`ParsedOrder`:

1.  The deterministic parse runs first and is the floor.
2.  With the ``ai_parse_enabled`` flag off for the shop, that is the answer.
3.  The model is asked only when :func:`~app.orders.parser.merge.needs_model`
    says a non-phone field is a guess or missing (see ``merge`` for the rules).
4.  An answer for the same text is reused from the cache for 24 hours — no
    second call, no second quota unit.
5.  Otherwise one unit of ``ai_parse_monthly`` is spent, the provider is
    called, the call is recorded in ``ai_usage``, and a schema-valid answer is
    merged and cached.
6.  Not configured, input too long, quota exhausted, timeout, HTTP error,
    refusal, invalid answer: the deterministic parse plus the warning
    ``AI_PARSE_UNAVAILABLE``. Order entry never waits on the model.
7.  With the address still empty and a phone that belongs to an existing
    customer of this shop, the customer's last delivery address is prefilled
    and ``address`` is listed in ``uncertain_fields``.

Nothing here logs message text, phones or the model's answer.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from collections.abc import Awaitable, Callable

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_usage.models import AiUsageOutcome, AiUsagePurpose
from app.ai_usage.service import AiUsageService, estimate_cost_micro_usd
from app.common.cache import CacheBackend, get_cache
from app.common.feature_flags import FeatureFlagService, FlagKey
from app.common.phone import normalize_digits
from app.core.config import Settings
from app.core.errors import EntitlementRequiredError
from app.core.logging import get_logger
from app.entitlements.catalog import Entitlement
from app.entitlements.service import EntitlementService
from app.orders.parser.ai_client import (
    AiParseClient,
    AiParseUnavailableError,
    AnthropicParseClient,
)
from app.orders.parser.base import FieldConfidence, ParsedOrder
from app.orders.parser.deterministic import DeterministicOrderParser
from app.orders.parser.districts import find_district
from app.orders.parser.llm_schema import LlmParseOutput, validate_llm_output
from app.orders.parser.merge import merge, needs_model

__all__ = [
    "AI_PARSE_UNAVAILABLE",
    "CACHE_TTL_SECONDS",
    "EnhancedOrderParser",
    "LastAddressLookup",
    "build_ai_parse_client",
    "cache_key",
    "last_address_lookup",
]

log = get_logger(__name__)

#: Warning code returned when the model could not help; parsing still worked.
AI_PARSE_UNAVAILABLE = "AI_PARSE_UNAVAILABLE"
CACHE_TTL_SECONDS = 24 * 60 * 60
_CACHE_VERSION = "v1"

#: ``phone (E.164) -> last delivery address`` for the current shop, or ``None``.
LastAddressLookup = Callable[[str], Awaitable[str | None]]


def build_ai_parse_client(settings: Settings) -> AiParseClient | None:
    """The configured provider client, or ``None`` when it is not configured."""
    key = settings.ai_parse_api_key.get_secret_value() if settings.ai_parse_api_key else ""
    if settings.ai_parse_provider != "anthropic" or not key or not settings.ai_parse_model:
        return None
    return AnthropicParseClient(
        api_key=key,
        model=settings.ai_parse_model,
        timeout_seconds=settings.ai_parse_timeout_seconds,
        max_input_chars=settings.ai_parse_max_input_chars,
    )


def _normalised(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", normalize_digits(text)).split())


def cache_key(tenant_id: uuid.UUID, text: str) -> str:
    digest = hashlib.sha256(_normalised(text).encode("utf-8")).hexdigest()
    return f"ai_parse:{_CACHE_VERSION}:{tenant_id}:{digest}"


class EnhancedOrderParser:
    name = "enhanced"

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        client: AiParseClient | None = None,
        cache: CacheBackend | None = None,
        last_address: LastAddressLookup | None = None,
    ) -> None:
        self._db = session
        self._settings = settings
        self._client = client
        self._cache = cache
        self._last_address = last_address
        self._deterministic = DeterministicOrderParser()

    async def parse(self, text: str, *, tenant_id: uuid.UUID) -> ParsedOrder:
        parsed = self._deterministic.parse(text)
        if not await FeatureFlagService(self._db).is_enabled(
            FlagKey.AI_PARSE_ENABLED, tenant_id=tenant_id
        ):
            return parsed

        if needs_model(parsed) and text.strip():
            answer = await self._answer(text, tenant_id=tenant_id)
            if answer is None:
                parsed.warnings.append(AI_PARSE_UNAVAILABLE)
            else:
                parsed = merge(parsed, answer)

        await self._prefill_address(parsed)
        return parsed

    # ------------------------------------------------------------ model ---

    async def _answer(self, text: str, *, tenant_id: uuid.UUID) -> LlmParseOutput | None:
        client = self._client
        if client is None or len(text) > self._settings.ai_parse_max_input_chars:
            return None

        cache = self._cache or get_cache()
        key = cache_key(tenant_id, text)
        cached = await self._read_cache(cache, key)
        if cached is not None:
            return cached

        try:
            await EntitlementService(self._db, settings=self._settings).consume(
                tenant_id, Entitlement.AI_PARSE_MONTHLY
            )
        except EntitlementRequiredError:
            return None

        usage = AiUsageService(self._db)
        try:
            result = await client.extract(text)
        except AiParseUnavailableError as exc:
            log.warning(
                "ai_parse: model unavailable",
                extra={"code": exc.code, "provider": client.provider},
            )
            await usage.record(
                tenant_id=tenant_id,
                purpose=AiUsagePurpose.ORDER_PARSE,
                provider=client.provider,
                model=client.model,
                outcome=AiUsageOutcome.ERROR,
                error_code=exc.code,
            )
            return None

        answer = validate_llm_output(result.output)
        await usage.record(
            tenant_id=tenant_id,
            purpose=AiUsagePurpose.ORDER_PARSE,
            provider=client.provider,
            model=result.model,
            outcome=AiUsageOutcome.OK if answer is not None else AiUsageOutcome.INVALID,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
            cost_micro_usd=estimate_cost_micro_usd(
                result.input_tokens,
                result.output_tokens,
                input_per_mtok_usd=self._settings.ai_parse_price_input_per_mtok_usd,
                output_per_mtok_usd=self._settings.ai_parse_price_output_per_mtok_usd,
            ),
            error_code=None if answer is not None else "schema_violation",
        )
        if answer is None:
            log.warning("ai_parse: answer broke the schema", extra={"provider": client.provider})
            return None

        await cache.set(key, answer.model_dump_json(), ttl_seconds=CACHE_TTL_SECONDS)
        return answer

    @staticmethod
    async def _read_cache(cache: CacheBackend, key: str) -> LlmParseOutput | None:
        raw = await cache.get(key)
        if raw is None:
            return None
        try:
            return validate_llm_output(json.loads(raw))
        except ValueError:
            return None

    # ---------------------------------------------------------- address ---

    async def _prefill_address(self, parsed: ParsedOrder) -> None:
        if parsed.address or not parsed.phones or self._last_address is None:
            return
        phone = parsed.selected_phone or parsed.phones[0]
        address = await self._last_address(phone)
        if not address:
            return
        parsed.address = address
        parsed.district = find_district(address)
        parsed.confidence["address"] = FieldConfidence.MEDIUM
        if "address" not in parsed.uncertain_fields:
            parsed.uncertain_fields.append("address")
        parsed.warnings = [
            w
            for w in parsed.warnings
            if w
            not in (
                "No delivery address recognised",
                "Only a district found — add the full address",
            )
        ]
        parsed.warnings.append("Address copied from this customer's last order — check it")


def last_address_lookup(
    session: AsyncSession,
    find_customer_id: Callable[[str], Awaitable[uuid.UUID | None]],
) -> LastAddressLookup:
    """Reads the newest order's delivery address for the customer with a phone.

    ``orders`` is tenant-owned, so the query only ever sees the current shop.
    """
    from app.orders.models import Order

    async def lookup(phone: str) -> str | None:
        customer_id = await find_customer_id(phone)
        if customer_id is None:
            return None
        stmt = (
            sa.select(Order.delivery_address_raw)
            .where(
                Order.customer_id == customer_id,
                Order.deleted_at.is_(None),
                Order.delivery_address_raw.is_not(None),
                Order.delivery_address_raw != "",
            )
            .order_by(Order.created_at.desc())
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    return lookup
