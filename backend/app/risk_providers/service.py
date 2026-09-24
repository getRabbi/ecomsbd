"""External risk lookups: configuration, cache, freshness, fallback, health and audit.

The shop's own Risk Check never depends on anything here. When a provider is
missing, disabled, slow or down, the first-party answer is unchanged and the
external section says why it has nothing new.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.cache import RateLimiter
from app.common.outbox import OutboxTopic, enqueue
from app.core.clock import utc_now
from app.core.errors import NotFoundError, RateLimitedError, ValidationError
from app.core.logging import get_logger
from app.core.security import CredentialVault
from app.customers.models import Customer
from app.customers.service import CUSTOMER_PHONE_CONTEXT
from app.risk_providers import registry
from app.risk_providers.contract import (
    FailureKind,
    LookupRequest,
    ProviderError,
    ProviderResult,
    RiskProviderAdapter,
    normalize_facts,
)
from app.risk_providers.models import ExternalRiskLookup, RiskProviderConnection

log = get_logger(__name__)

#: Provider calls a shop may make in a day, cached answers excluded.
LOOKUP_LIMIT_PER_DAY = 200
#: Connection tests a shop may run in an hour.
TEST_LIMIT_PER_HOUR = 10
#: A refresh sooner than this after the last call returns the stored answer.
MIN_REFRESH_INTERVAL = timedelta(minutes=10)
MAX_ATTEMPTS = 2
BACKOFF_SECONDS = 0.5
#: Consecutive failures after which the provider is DOWN and calls pause.
DOWN_AFTER_FAILURES = 3
CIRCUIT_OPEN_FOR = timedelta(minutes=5)
DEFAULT_RETRY_AFTER_SECONDS = 60
MAX_RETRY_AFTER_SECONDS = 24 * 3600
#: Lookups older than this are deleted by the daily prune.
RETENTION_DAYS = 180

#: Provider failure → the code the seller sees (screens translate it).
SELLER_CODES: dict[FailureKind, str] = {
    FailureKind.TIMEOUT: "PROVIDER_TIMEOUT",
    FailureKind.UNAVAILABLE: "PROVIDER_UNAVAILABLE",
    FailureKind.RATE_LIMITED: "PROVIDER_RATE_LIMITED",
    FailureKind.AUTH_FAILED: "PROVIDER_AUTH_FAILED",
    FailureKind.REJECTED: "PROVIDER_REJECTED",
    FailureKind.BAD_RESPONSE: "PROVIDER_BAD_RESPONSE",
}


def vault_context(tenant_id: uuid.UUID, provider_id: str) -> str:
    return f"risk_provider:{tenant_id}:{provider_id}"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def catalog_entry(adapter: RiskProviderAdapter) -> dict[str, Any]:
    spec = adapter.spec
    return {
        "provider_id": spec.provider_id,
        "name": spec.display_name,
        "official_contract": spec.official_contract,
        "markets": sorted(spec.markets),
        "capabilities": sorted(str(c) for c in spec.capabilities),
        "credential_fields": [
            {"name": f.name, "label_en": f.label_en, "label_bn": f.label_bn, "secret": f.secret}
            for f in spec.credential_fields
        ],
        "config_fields": list(spec.config_fields),
        "cache_ttl_hours": {
            "default": spec.default_cache_ttl_hours,
            "min": spec.min_cache_ttl_hours,
            "max": spec.max_cache_ttl_hours,
        },
    }


def connection_view(row: RiskProviderConnection) -> dict[str, Any]:
    """Everything about a connection except its credentials."""
    return {
        "provider_id": row.provider_id,
        "enabled": row.enabled,
        "credentials_set": bool(row.credentials_enc),
        "credential_hint": row.credential_hint,
        "config": row.config or {},
        "cache_ttl_hours": row.cache_ttl_hours,
        "health": row.health,
        "last_tested_at": _iso(row.last_tested_at),
        "last_test_result": row.last_test_result,
        "last_success_at": _iso(row.last_success_at),
        "last_failure_at": _iso(row.last_failure_at),
        "last_error_code": row.last_error_code,
        "consecutive_failures": row.consecutive_failures,
        "rate_limited_until": _iso(row.rate_limited_until),
    }


def lookup_view(row: ExternalRiskLookup, now: datetime) -> dict[str, Any]:
    fresh = row.expires_at is not None and row.expires_at > now
    return {
        "status": row.status,
        "facts": row.facts or [],
        "provider_observed_at": _iso(row.provider_observed_at),
        "checked_at": _iso(row.checked_at),
        "expires_at": _iso(row.expires_at),
        "freshness": "FRESH" if fresh else "STALE",
        "sample_size": row.sample_size,
        "confidence": row.confidence,
    }


class ExternalRiskService:
    def __init__(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        vault: CredentialVault,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.vault = vault
        self.limiter = limiter or RateLimiter()

    # ------------------------------------------------------------ settings ---

    def _adapter(self, provider_id: str) -> RiskProviderAdapter:
        adapter = registry.get(provider_id)
        if adapter is None:
            raise NotFoundError(
                "This risk provider is not available",
                details={"blocker": registry.BLOCKER},
                message_bn="এই রিস্ক প্রোভাইডার এখন পাওয়া যাচ্ছে না",
            )
        return adapter

    async def connection(self, provider_id: str) -> RiskProviderConnection | None:
        return await self.db.scalar(
            sa.select(RiskProviderConnection).where(
                RiskProviderConnection.provider_id == provider_id
            )
        )

    async def catalog(self) -> dict[str, Any]:
        rows = {
            row.provider_id: row
            for row in (await self.db.scalars(sa.select(RiskProviderConnection))).all()
        }
        providers = []
        for adapter in registry.available():
            row = rows.get(adapter.spec.provider_id)
            providers.append(
                {**catalog_entry(adapter), "connection": connection_view(row) if row else None}
            )
        return {
            "status": "AVAILABLE" if providers else "GATED",
            "blocker": None if providers else registry.BLOCKER,
            "providers": providers,
        }

    async def configure(
        self,
        provider_id: str,
        *,
        credentials: dict[str, str] | None,
        config: dict[str, str] | None,
        cache_ttl_hours: int | None,
    ) -> RiskProviderConnection:
        adapter = self._adapter(provider_id)
        spec = adapter.spec
        row = await self.connection(provider_id)
        if row is None:
            row = RiskProviderConnection(
                provider_id=provider_id,
                enabled=False,
                config={},
                cache_ttl_hours=spec.default_cache_ttl_hours,
                health="UNKNOWN",
                consecutive_failures=0,
            )
            self.db.add(row)
        changed: list[str] = []
        if credentials is not None:
            names = [f.name for f in spec.credential_fields]
            if set(credentials) != set(names) or not all(
                isinstance(v, str) and 0 < len(v.strip()) <= 500 for v in credentials.values()
            ):
                raise ValidationError(
                    "Every credential field is required",
                    details={"fields": names},
                    message_bn="সব ক্রেডেনশিয়াল ঘর পূরণ করতে হবে",
                )
            clean = {k: v.strip() for k, v in credentials.items()}
            row.credentials_enc = self.vault.encrypt(
                json.dumps(clean, sort_keys=True),
                context=vault_context(self.tenant_id, provider_id),
            )
            row.credential_hint = clean[names[0]][-4:] if names else None
            # New credentials must pass a test before lookups use them.
            row.enabled = False
            row.last_test_result = None
            row.health = "UNKNOWN"
            row.consecutive_failures = 0
            changed.append("credentials")
        if config is not None:
            unknown = set(config) - set(spec.config_fields)
            if unknown or not all(isinstance(v, str) and len(v) <= 200 for v in config.values()):
                raise ValidationError(
                    "Unknown provider setting",
                    details={"allowed": list(spec.config_fields)},
                    message_bn="অজানা প্রোভাইডার সেটিং",
                )
            row.config = dict(config)
            changed.append("config")
        if cache_ttl_hours is not None:
            if not spec.min_cache_ttl_hours <= cache_ttl_hours <= spec.max_cache_ttl_hours:
                raise ValidationError(
                    "Cache time is outside what this provider allows",
                    details={"min": spec.min_cache_ttl_hours, "max": spec.max_cache_ttl_hours},
                    message_bn="ক্যাশের সময় এই প্রোভাইডারের সীমার বাইরে",
                )
            row.cache_ttl_hours = cache_ttl_hours
            changed.append("cache_ttl_hours")
        await self.db.flush()
        await record_audit(
            self.db,
            AuditAction.RISK_PROVIDER_CONFIGURED,
            entity_type="risk_provider_connection",
            entity_id=row.id,
            context={"provider_id": provider_id, "changed": changed},
        )
        return row

    async def set_enabled(self, provider_id: str, enabled: bool) -> RiskProviderConnection:
        self._adapter(provider_id)
        row = await self.connection(provider_id)
        if row is None or not row.credentials_enc:
            raise ValidationError(
                "Add the provider's credentials first",
                details={"blocker": "CREDENTIALS_REQUIRED"},
                message_bn="আগে প্রোভাইডারের ক্রেডেনশিয়াল দিন",
            )
        if enabled and row.last_test_result != "OK":
            raise ValidationError(
                "Run a successful connection test before turning it on",
                details={"blocker": "CONNECTION_TEST_REQUIRED"},
                message_bn="চালু করার আগে সফলভাবে কানেকশন টেস্ট করুন",
            )
        row.enabled = enabled
        await self.db.flush()
        await record_audit(
            self.db,
            AuditAction.RISK_PROVIDER_ENABLED if enabled else AuditAction.RISK_PROVIDER_DISABLED,
            entity_type="risk_provider_connection",
            entity_id=row.id,
            context={"provider_id": provider_id},
        )
        return row

    async def remove(self, provider_id: str) -> None:
        row = await self.connection(provider_id)
        if row is None:
            return
        row.enabled = False
        row.credentials_enc = None
        row.credential_hint = None
        row.last_test_result = None
        row.health = "UNKNOWN"
        await self.db.flush()
        await record_audit(
            self.db,
            AuditAction.RISK_PROVIDER_REMOVED,
            entity_type="risk_provider_connection",
            entity_id=row.id,
            context={"provider_id": provider_id},
        )

    def _credentials(self, row: RiskProviderConnection) -> dict[str, str]:
        assert row.credentials_enc  # noqa: S101 - callers check
        return json.loads(
            self.vault.decrypt(
                row.credentials_enc, context=vault_context(self.tenant_id, row.provider_id)
            )
        )

    async def test(self, provider_id: str) -> dict[str, Any]:
        adapter = self._adapter(provider_id)
        row = await self.connection(provider_id)
        if row is None or not row.credentials_enc:
            raise ValidationError(
                "Add the provider's credentials first",
                details={"blocker": "CREDENTIALS_REQUIRED"},
                message_bn="আগে প্রোভাইডারের ক্রেডেনশিয়াল দিন",
            )
        await self._limit("risk_provider_test", TEST_LIMIT_PER_HOUR, 3600)
        now = utc_now()
        code: str | None = None
        try:
            await asyncio.wait_for(
                adapter.test_connection(self._credentials(row), row.config or {}),
                timeout=adapter.spec.timeout_seconds,
            )
        except TimeoutError:
            code = SELLER_CODES[FailureKind.TIMEOUT]
            self._failed(row, FailureKind.TIMEOUT, None, now)
        except ProviderError as exc:
            code = SELLER_CODES[exc.kind]
            self._failed(row, exc.kind, exc.retry_after_seconds, now)
        except Exception as exc:
            log.warning("risk provider test crashed", extra={"error_type": type(exc).__name__})
            code = SELLER_CODES[FailureKind.BAD_RESPONSE]
            self._failed(row, FailureKind.BAD_RESPONSE, None, now)
        else:
            self._succeeded(row, now)
        row.last_tested_at = now
        row.last_test_result = "OK" if code is None else code
        if code is not None:
            row.enabled = False
        await self.db.flush()
        await record_audit(
            self.db,
            AuditAction.RISK_PROVIDER_TESTED,
            entity_type="risk_provider_connection",
            entity_id=row.id,
            context={"provider_id": provider_id, "result": row.last_test_result},
        )
        return {"ok": code is None, "error_code": code, "connection": connection_view(row)}

    # ------------------------------------------------------------- health ---

    @staticmethod
    def _succeeded(row: RiskProviderConnection, now: datetime) -> None:
        row.health = "HEALTHY"
        row.consecutive_failures = 0
        row.last_success_at = now
        row.last_error_code = None
        row.rate_limited_until = None

    @staticmethod
    def _failed(
        row: RiskProviderConnection, kind: FailureKind, retry_after: int | None, now: datetime
    ) -> None:
        row.consecutive_failures = (row.consecutive_failures or 0) + 1
        row.last_failure_at = now
        row.last_error_code = SELLER_CODES[kind]
        down = kind == FailureKind.AUTH_FAILED or row.consecutive_failures >= DOWN_AFTER_FAILURES
        row.health = "DOWN" if down else "DEGRADED"
        if kind == FailureKind.RATE_LIMITED:
            wait = min(MAX_RETRY_AFTER_SECONDS, max(1, retry_after or DEFAULT_RETRY_AFTER_SECONDS))
            row.rate_limited_until = now + timedelta(seconds=wait)

    async def _limit(self, scope: str, limit: int, window: int) -> None:
        result = await self.limiter.hit(
            scope, str(self.tenant_id), limit=limit, window_seconds=window
        )
        if not result.allowed:
            raise RateLimitedError(
                "Too many provider requests. Try again later.",
                retry_after_seconds=result.retry_after_seconds,
                details={"retry_after_seconds": result.retry_after_seconds},
                message_bn="প্রোভাইডারে অনেক বেশি অনুরোধ। পরে আবার চেষ্টা করুন।",
            )

    # ------------------------------------------------------------ lookups ---

    async def _enabled(self) -> list[tuple[RiskProviderConnection, RiskProviderAdapter]]:
        rows = (
            await self.db.scalars(
                sa.select(RiskProviderConnection)
                .where(RiskProviderConnection.enabled.is_(True))
                .order_by(RiskProviderConnection.provider_id)
            )
        ).all()
        pairs = []
        for row in rows:
            adapter = registry.get(row.provider_id)
            if adapter is not None and row.credentials_enc:
                pairs.append((row, adapter))
        return pairs

    async def _latest(
        self, customer_id: uuid.UUID, provider_id: str, *, answered: bool
    ) -> ExternalRiskLookup | None:
        query = sa.select(ExternalRiskLookup).where(
            ExternalRiskLookup.customer_id == customer_id,
            ExternalRiskLookup.provider_id == provider_id,
        )
        if answered:
            query = query.where(ExternalRiskLookup.status.in_(("FOUND", "NOT_FOUND")))
        return await self.db.scalar(query.order_by(ExternalRiskLookup.checked_at.desc()).limit(1))

    async def customer_view(self, customer_id: uuid.UUID) -> dict[str, Any]:
        """What is stored for a customer. Never calls a provider."""
        now = utc_now()
        adapters = registry.available()
        connections = (await self.db.scalars(sa.select(RiskProviderConnection))).all()
        sections = []
        for row in connections:
            adapter = registry.get(row.provider_id)
            if adapter is None:
                continue
            answer = await self._latest(customer_id, row.provider_id, answered=True)
            attempt = await self._latest(customer_id, row.provider_id, answered=False)
            failed_after = (
                attempt is not None
                and attempt.status == "FAILED"
                and (answer is None or attempt.checked_at > answer.checked_at)
            )
            sections.append(
                {
                    "provider_id": row.provider_id,
                    "name": adapter.spec.display_name,
                    "enabled": row.enabled,
                    "health": row.health,
                    "cache_ttl_hours": row.cache_ttl_hours,
                    "result": lookup_view(answer, now) if answer else None,
                    "last_error": (
                        {"code": attempt.error_code, "at": _iso(attempt.checked_at)}
                        if failed_after and attempt is not None
                        else None
                    ),
                }
            )
        if not adapters:
            status = "GATED"
        elif not any(s["enabled"] for s in sections):
            status = "NOT_CONFIGURED"
        else:
            status = "AVAILABLE"
        return {
            "customer_id": str(customer_id),
            "status": status,
            "blocker": registry.BLOCKER if status == "GATED" else None,
            "providers": sections,
            # Provider facts are never merged into the shop's own history.
            "first_party_unchanged": True,
            "source": "EXTERNAL_PROVIDER",
        }

    async def lookup(
        self,
        customer: Customer,
        *,
        force: bool = False,
        trigger: str = "MANUAL",
        actor_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Answer from cache where fresh; otherwise ask each enabled provider."""
        outcomes = []
        for row, adapter in await self._enabled():
            outcomes.append(
                await self._lookup_one(
                    row, adapter, customer, force=force, trigger=trigger, actor_id=actor_id
                )
            )
        view = await self.customer_view(customer.id)
        view["outcomes"] = outcomes
        return view

    async def _lookup_one(
        self,
        row: RiskProviderConnection,
        adapter: RiskProviderAdapter,
        customer: Customer,
        *,
        force: bool,
        trigger: str,
        actor_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        now = utc_now()
        provider_id = row.provider_id
        cached = await self._latest(customer.id, provider_id, answered=True)
        if cached is not None:
            fresh = cached.expires_at is not None and cached.expires_at > now
            recent = cached.checked_at > now - MIN_REFRESH_INTERVAL
            if (fresh and not force) or recent:
                return {"provider_id": provider_id, "outcome": "CACHED", "error_code": None}
        if row.rate_limited_until is not None and row.rate_limited_until > now:
            return {
                "provider_id": provider_id,
                "outcome": "SKIPPED",
                "error_code": SELLER_CODES[FailureKind.RATE_LIMITED],
            }
        if (
            row.health == "DOWN"
            and row.last_failure_at is not None
            and row.last_failure_at > now - CIRCUIT_OPEN_FOR
        ):
            return {
                "provider_id": provider_id,
                "outcome": "SKIPPED",
                "error_code": SELLER_CODES[FailureKind.UNAVAILABLE],
            }
        await self._limit("external_risk_lookup", LOOKUP_LIMIT_PER_DAY, 86_400)

        # Minimum PII: the number, and nothing else about the customer or shop.
        request = LookupRequest(
            phone_e164=self.vault.decrypt(customer.phone_enc, context=CUSTOMER_PHONE_CONTEXT)
        )
        started = time.monotonic()
        result: ProviderResult | None = None
        failure: ProviderError | None = None
        attempts = 0
        credentials = self._credentials(row)
        for attempt in range(MAX_ATTEMPTS):
            attempts = attempt + 1
            try:
                result = await asyncio.wait_for(
                    adapter.lookup(request, credentials, row.config or {}),
                    timeout=adapter.spec.timeout_seconds,
                )
                failure = None
                break
            except TimeoutError:
                failure = ProviderError(FailureKind.TIMEOUT)
            except ProviderError as exc:
                failure = exc
            except Exception as exc:
                log.warning(
                    "risk provider lookup crashed", extra={"error_type": type(exc).__name__}
                )
                failure = ProviderError(FailureKind.BAD_RESPONSE)
            if not failure.kind.retryable or attempt + 1 >= MAX_ATTEMPTS:
                break
            await asyncio.sleep(BACKOFF_SECONDS * (2**attempt))
        duration = int((time.monotonic() - started) * 1000)
        now = utc_now()

        lookup = ExternalRiskLookup(
            customer_id=customer.id,
            provider_id=provider_id,
            checked_at=now,
            trigger=trigger,
            requested_by=actor_id,
            attempts=attempts,
            duration_ms=duration,
            facts=[],
            dropped_facts=0,
        )
        if result is not None:
            facts, dropped = normalize_facts(result.facts)
            lookup.status = "FOUND" if result.found else "NOT_FOUND"
            lookup.facts = facts if result.found else []
            lookup.dropped_facts = dropped
            lookup.provider_observed_at = result.observed_at
            lookup.sample_size = (
                result.sample_size
                if type(result.sample_size) is int and result.sample_size >= 0
                else None
            )
            lookup.confidence = (result.confidence or None) and str(result.confidence)[:24]
            lookup.provider_reference = (result.reference or None) and str(result.reference)[:120]
            lookup.expires_at = now + timedelta(hours=row.cache_ttl_hours)
            self._succeeded(row, now)
        else:
            assert failure is not None  # noqa: S101 - one of the two is set
            lookup.status = "FAILED"
            lookup.error_code = SELLER_CODES[failure.kind]
            self._failed(row, failure.kind, failure.retry_after_seconds, now)
        self.db.add(lookup)
        await self.db.flush()
        await record_audit(
            self.db,
            AuditAction.EXTERNAL_RISK_LOOKUP,
            entity_type="customer",
            entity_id=customer.id,
            context={
                "provider_id": provider_id,
                "status": lookup.status,
                "error_code": lookup.error_code,
                "trigger": trigger,
            },
        )
        if lookup.status == "FAILED":
            await enqueue(
                self.db,
                OutboxTopic.EXTERNAL_RISK_UNAVAILABLE,
                {
                    "customer_id": str(customer.id),
                    "provider_id": provider_id,
                    "error_code": lookup.error_code,
                    "external_data_state": "STALE" if cached is not None else "NONE",
                },
                tenant_id=self.tenant_id,
            )
        else:
            await enqueue(
                self.db,
                OutboxTopic.EXTERNAL_RISK_LOOKUP_COMPLETED,
                {
                    "customer_id": str(customer.id),
                    "provider_id": provider_id,
                    "found": lookup.status == "FOUND",
                },
                tenant_id=self.tenant_id,
            )
        return {
            "provider_id": provider_id,
            "outcome": "FETCHED" if lookup.status != "FAILED" else "FAILED",
            "error_code": lookup.error_code,
        }


async def external_data_state(db: AsyncSession, customer_id: uuid.UUID) -> str:
    """FRESH, STALE or NONE across the shop's providers, for workflow conditions."""
    now = utc_now()
    expires = (
        await db.scalars(
            sa.select(ExternalRiskLookup.expires_at).where(
                ExternalRiskLookup.customer_id == customer_id,
                ExternalRiskLookup.status.in_(("FOUND", "NOT_FOUND")),
            )
        )
    ).all()
    if not expires:
        return "NONE"
    return "FRESH" if any(e is not None and e > now for e in expires) else "STALE"


async def prune_external_risk_lookups(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Delete lookups past retention. Daily; idempotent."""
    from app.db.session import system_session

    cutoff = utc_now() - timedelta(days=RETENTION_DAYS)
    async with system_session("prune external risk lookups past retention") as db:
        result = await db.execute(
            sa.delete(ExternalRiskLookup).where(ExternalRiskLookup.checked_at < cutoff)
        )
        return {"deleted": int(getattr(result, "rowcount", 0) or 0)}
