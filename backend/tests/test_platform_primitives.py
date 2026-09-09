"""Outbox, idempotency, crypto, feature flags, redaction and time.

These are the primitives every later phase's money handling is built on, so
they are tested directly rather than only through the features that use them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, AuditLog, record_audit
from app.common.cache import InMemoryBackend, RateLimiter
from app.common.feature_flags import FeatureFlag, FeatureFlagService, FlagKey
from app.common.idempotency import (
    IdempotencyKey,
    begin_idempotent,
    complete_idempotent,
    request_hash,
)
from app.common.outbox import (
    OutboxEvent,
    OutboxStatus,
    OutboxTopic,
    claim_batch,
    enqueue,
    mark_done,
    mark_failed,
)
from app.core.clock import DHAKA, FRIDAY, business_date, business_day_bounds, next_weekday_at
from app.core.config import Settings
from app.core.context import RequestContext, set_context
from app.core.errors import IdempotencyConflictError
from app.core.redaction import mask_phone, redact_value
from app.core.security import CredentialVault, SecretHasher, TokenService, generate_numeric_code
from app.tenants.models import Tenant


@pytest.fixture
def local_settings() -> Settings:
    return Settings(app_env="local")


# --------------------------------------------------------------------------- #
# Outbox
# --------------------------------------------------------------------------- #


class TestOutbox:
    async def test_event_is_written_in_the_callers_transaction(
        self, system_db: AsyncSession
    ) -> None:
        # The whole guarantee: the business row and its side effect share one
        # transaction, so they cannot disagree.
        tenant = Tenant(name="Outbox Shop")
        system_db.add(tenant)
        await system_db.flush()
        await enqueue(
            system_db,
            OutboxTopic.TENANT_CREATED,
            {"tenant_id": str(tenant.id)},
            tenant_id=tenant.id,
        )
        await system_db.rollback()

        remaining = (
            await system_db.execute(
                sa.select(sa.func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.tenant_id == tenant.id)
            )
        ).scalar_one()
        assert remaining == 0, "rolling back the business row must drop the event too"

    async def test_claim_marks_events_processing(self, system_db: AsyncSession) -> None:
        await enqueue(system_db, OutboxTopic.USER_SIGNED_IN, {"n": 1})
        await system_db.commit()

        claimed = await claim_batch(system_db, worker_id="w1", batch_size=10)
        assert claimed
        assert all(e.status == OutboxStatus.PROCESSING for e in claimed)
        assert all(e.locked_by == "w1" for e in claimed)
        await system_db.rollback()

    async def test_dedupe_key_prevents_duplicates(self, system_db: AsyncSession) -> None:
        key = f"dedupe-{uuid.uuid4().hex}"
        await enqueue(system_db, OutboxTopic.USER_SIGNED_IN, {}, dedupe_key=key)
        await system_db.commit()

        await enqueue(system_db, OutboxTopic.USER_SIGNED_IN, {}, dedupe_key=key)
        with pytest.raises(Exception):  # noqa: B017 - dialect-specific integrity error
            await system_db.commit()
        await system_db.rollback()

    async def test_failure_schedules_a_backoff_retry(self, system_db: AsyncSession) -> None:
        await enqueue(system_db, OutboxTopic.USER_SIGNED_IN, {"n": 2})
        await system_db.commit()
        event = (await claim_batch(system_db, worker_id="w1", batch_size=1))[0]

        await mark_failed(system_db, event, "boom", max_attempts=5)
        assert event.status == OutboxStatus.PENDING
        assert event.attempts == 1
        assert event.available_at > datetime.now(UTC)
        assert event.last_error == "boom"
        await system_db.rollback()

    async def test_exhausted_retries_park_the_event_rather_than_dropping_it(
        self, system_db: AsyncSession
    ) -> None:
        await enqueue(system_db, OutboxTopic.USER_SIGNED_IN, {"n": 3})
        await system_db.commit()
        event = (await claim_batch(system_db, worker_id="w1", batch_size=1))[0]
        event.attempts = 4

        await mark_failed(system_db, event, "still broken", max_attempts=5)
        assert event.status == OutboxStatus.DEAD
        await system_db.rollback()

    async def test_completion_records_the_time(self, system_db: AsyncSession) -> None:
        await enqueue(system_db, OutboxTopic.USER_SIGNED_IN, {"n": 4})
        await system_db.commit()
        event = (await claim_batch(system_db, worker_id="w1", batch_size=1))[0]

        await mark_done(system_db, event)
        assert event.status == OutboxStatus.DONE
        assert event.processed_at is not None
        assert event.locked_by is None
        await system_db.rollback()

    async def test_backoff_grows(self, system_db: AsyncSession) -> None:
        event = OutboxEvent(topic="x", payload={}, attempts=0)
        first = event.next_available_at()
        event.attempts = 5
        assert event.next_available_at() > first


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #


class TestIdempotency:
    @staticmethod
    def _use_tenant(tenant_id: uuid.UUID) -> None:
        set_context(RequestContext(trace_id="test", tenant_id=tenant_id))

    async def test_first_call_executes_and_replay_returns_the_stored_response(
        self, db: AsyncSession, system_db: AsyncSession
    ) -> None:
        tenant = Tenant(name="Idem Shop")
        system_db.add(tenant)
        await system_db.commit()
        self._use_tenant(tenant.id)

        key = f"key-{uuid.uuid4().hex}"
        payload = {"order_id": "abc", "amount_paisa": 125_000}

        first = await begin_idempotent(db, key=key, endpoint="/v1/orders/book", payload=payload)
        assert first.should_execute is True
        await complete_idempotent(db, first.record, status_code=201, body={"ok": True})

        second = await begin_idempotent(db, key=key, endpoint="/v1/orders/book", payload=payload)
        assert second.should_execute is False
        assert second.record.response_body == {"ok": True}
        await db.rollback()

    async def test_same_key_with_a_different_body_is_a_conflict(
        self, db: AsyncSession, system_db: AsyncSession
    ) -> None:
        # Executing it would double-book; replaying the first response would
        # report something that did not happen. Neither is acceptable.
        tenant = Tenant(name="Idem Shop 2")
        system_db.add(tenant)
        await system_db.commit()
        self._use_tenant(tenant.id)

        key = f"key-{uuid.uuid4().hex}"
        await begin_idempotent(db, key=key, endpoint="/v1/orders/book", payload={"a": 1})
        with pytest.raises(IdempotencyConflictError):
            await begin_idempotent(db, key=key, endpoint="/v1/orders/book", payload={"a": 2})
        await db.rollback()

    def test_hash_ignores_key_order_and_whitespace(self) -> None:
        assert request_hash({"a": 1, "b": 2}) == request_hash({"b": 2, "a": 1})
        assert request_hash({"a": 1}) != request_hash({"a": 2})

    async def test_records_are_tenant_scoped(self) -> None:
        from app.db.tenancy import tenant_owned_table_names

        assert IdempotencyKey.__tablename__ in tenant_owned_table_names()


# --------------------------------------------------------------------------- #
# Crypto
# --------------------------------------------------------------------------- #


class TestCredentialVault:
    def test_round_trip(self, local_settings: Settings) -> None:
        vault = CredentialVault(local_settings)
        envelope = vault.encrypt("api-secret", context="courier_account:1")
        assert "api-secret" not in envelope
        assert vault.decrypt(envelope, context="courier_account:1") == "api-secret"

    def test_ciphertext_is_bound_to_its_row(self, local_settings: Settings) -> None:
        # A ciphertext copied into another row must fail rather than decrypt.
        vault = CredentialVault(local_settings)
        envelope = vault.encrypt("api-secret", context="courier_account:1")
        with pytest.raises(Exception):  # noqa: B017 - cryptography InvalidTag
            vault.decrypt(envelope, context="courier_account:2")

    def test_envelope_records_its_key_version(self, local_settings: Settings) -> None:
        vault = CredentialVault(local_settings)
        assert vault.encrypt("x", context="c").startswith(f"{vault.key_version}.")

    def test_encryption_is_randomised(self, local_settings: Settings) -> None:
        vault = CredentialVault(local_settings)
        assert vault.encrypt("same", context="c") != vault.encrypt("same", context="c")


class TestSecretHasher:
    def test_phone_hash_is_deterministic_and_keyed(self, local_settings: Settings) -> None:
        hasher = SecretHasher(local_settings)
        digest = hasher.phone_search_hash("+8801712345678")
        assert digest == hasher.phone_search_hash("+8801712345678")
        assert digest != hasher.phone_search_hash("+8801712345679")

        import hashlib

        # Master spec section 133: not a plain unsalted hash of the number.
        assert digest != hashlib.sha256(b"+8801712345678").hexdigest()

    def test_otp_hash_is_bound_to_its_challenge(self, local_settings: Settings) -> None:
        hasher = SecretHasher(local_settings)
        challenge_a, challenge_b = uuid.uuid4(), uuid.uuid4()
        digest = hasher.otp_hash("123456", challenge_id=challenge_a)

        assert hasher.verify_otp("123456", challenge_id=challenge_a, expected_hash=digest)
        assert not hasher.verify_otp("123456", challenge_id=challenge_b, expected_hash=digest)
        assert not hasher.verify_otp("654321", challenge_id=challenge_a, expected_hash=digest)

    def test_ip_hash_never_returns_the_address(self, local_settings: Settings) -> None:
        hasher = SecretHasher(local_settings)
        digest = hasher.ip_hash("203.0.113.7")
        assert digest is not None
        assert "203.0.113.7" not in digest
        assert hasher.ip_hash(None) is None


class TestTokenService:
    def test_round_trip(self, local_settings: Settings) -> None:
        service = TokenService(local_settings)
        user_id, session_id, tenant_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        token = service.issue_access_token(
            user_id=user_id, session_id=session_id, tenant_id=tenant_id, role="OWNER"
        )
        claims = service.decode_access_token(token)
        assert claims.user_id == user_id
        assert claims.session_id == session_id
        assert claims.tenant_id == tenant_id
        assert claims.role == "OWNER"

    def test_tampered_token_is_rejected(self, local_settings: Settings) -> None:
        from app.core.errors import AppError

        service = TokenService(local_settings)
        token = service.issue_access_token(
            user_id=uuid.uuid4(), session_id=uuid.uuid4(), tenant_id=None, role=None
        )
        with pytest.raises(AppError):
            service.decode_access_token(token[:-4] + "aaaa")

    def test_access_token_is_short_lived(self, local_settings: Settings) -> None:
        assert TokenService(local_settings).access_token_ttl_seconds == 900


class TestOtpCodeGeneration:
    def test_length_and_charset(self) -> None:
        for _ in range(50):
            code = generate_numeric_code(6)
            assert len(code) == 6
            assert code.isdigit()

    def test_leading_zeros_are_preserved(self) -> None:
        # A zero-padded code must stay six characters or verification breaks.
        assert all(len(generate_numeric_code(6)) == 6 for _ in range(200))

    def test_unreasonable_lengths_are_refused(self) -> None:
        with pytest.raises(ValueError):
            generate_numeric_code(2)


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #


class TestRedaction:
    def test_phone_masking_matches_the_spec_format(self) -> None:
        assert mask_phone("+8801712345678") == "01712****78"
        assert mask_phone("01712345678") == "01712****78"
        assert mask_phone(None) is None

    def test_masking_is_idempotent(self) -> None:
        assert mask_phone(mask_phone("01712345678")) == "01712****78"

    def test_sensitive_keys_are_removed(self) -> None:
        redacted = redact_value(
            {
                "api_key": "sk_live_abcdef123456",
                "password": "hunter2",
                "refresh_token": "abc",
                "order_id": "CP-20260909-0042",
            }
        )
        assert redacted["api_key"] == "[redacted]"
        assert redacted["password"] == "[redacted]"
        assert redacted["refresh_token"] == "[redacted]"
        assert redacted["order_id"] == "CP-20260909-0042"

    def test_phone_numbers_in_free_text_are_masked(self) -> None:
        assert "01712345678" not in redact_value("Customer 01712345678 called")

    def test_bearer_tokens_in_text_are_scrubbed(self) -> None:
        assert "eyJhbGciOi" not in redact_value("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9")

    def test_nesting_is_handled(self) -> None:
        redacted = redact_value({"outer": {"inner": {"otp_code": "123456"}}})
        assert redacted["outer"]["inner"]["otp_code"] == "[redacted]"


# --------------------------------------------------------------------------- #
# Feature flags
# --------------------------------------------------------------------------- #


class TestFeatureFlags:
    async def test_unknown_flags_default_to_off_for_providers(
        self, system_db: AsyncSession
    ) -> None:
        # An unverified integration must not become reachable just because its
        # flag row is missing.
        flags = FeatureFlagService(system_db)
        assert await flags.is_enabled(FlagKey.STEADFAST_ENABLED) is False
        assert await flags.is_enabled(FlagKey.PATHAO_ENABLED) is False

    async def test_global_enable(self, system_db: AsyncSession) -> None:
        system_db.add(FeatureFlag(key=FlagKey.AI_PARSE_ENABLED, enabled=True))
        await system_db.flush()
        assert await FeatureFlagService(system_db).is_enabled(FlagKey.AI_PARSE_ENABLED) is True
        await system_db.rollback()

    async def test_tenant_override_beats_global(self, system_db: AsyncSession) -> None:
        tenant = Tenant(name="Flag Shop")
        system_db.add(tenant)
        await system_db.flush()
        system_db.add(FeatureFlag(key=FlagKey.REDX_ENABLED, enabled=False))
        system_db.add(
            FeatureFlag(key=FlagKey.REDX_ENABLED, tenant_id=tenant.id, enabled=True, scope="TENANT")
        )
        await system_db.flush()

        flags = FeatureFlagService(system_db)
        assert await flags.is_enabled(FlagKey.REDX_ENABLED, tenant_id=tenant.id) is True
        assert await flags.is_enabled(FlagKey.REDX_ENABLED) is False
        await system_db.rollback()

    async def test_percentage_rollout_is_stable_for_a_tenant(self, system_db: AsyncSession) -> None:
        # A seller must not see a feature flicker between requests.
        system_db.add(
            FeatureFlag(
                key=FlagKey.COURIER_RECOMMENDATION_ENABLED, enabled=False, rollout_percentage=50
            )
        )
        await system_db.flush()
        tenant_id = uuid.uuid4()

        flags = FeatureFlagService(system_db)
        first = await flags.is_enabled(FlagKey.COURIER_RECOMMENDATION_ENABLED, tenant_id=tenant_id)
        flags.invalidate()
        second = await flags.is_enabled(FlagKey.COURIER_RECOMMENDATION_ENABLED, tenant_id=tenant_id)
        assert first == second
        await system_db.rollback()


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #


class TestRateLimiter:
    async def test_allows_up_to_the_limit_then_refuses(self) -> None:
        limiter = RateLimiter(InMemoryBackend())
        results = [await limiter.hit("test", "id", limit=3, window_seconds=60) for _ in range(4)]
        assert [r.allowed for r in results] == [True, True, True, False]
        assert results[-1].retry_after_seconds > 0

    async def test_identities_are_independent(self) -> None:
        limiter = RateLimiter(InMemoryBackend())
        await limiter.hit("test", "a", limit=1, window_seconds=60)
        assert (await limiter.hit("test", "b", limit=1, window_seconds=60)).allowed

    async def test_peek_does_not_consume(self) -> None:
        limiter = RateLimiter(InMemoryBackend())
        for _ in range(5):
            assert (await limiter.peek("test", "id", limit=1, window_seconds=60)).allowed
        assert (await limiter.hit("test", "id", limit=1, window_seconds=60)).allowed

    async def test_lock_is_exclusive(self) -> None:
        limiter = RateLimiter(InMemoryBackend())
        async with limiter.lock("booking:1") as first:
            assert first is True
            async with limiter.lock("booking:1") as second:
                assert second is False
        async with limiter.lock("booking:1") as third:
            assert third is True


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #


class TestBusinessTime:
    def test_late_utc_belongs_to_the_next_dhaka_business_date(self) -> None:
        # 19:30 UTC is 01:30 the next day in Dhaka. Truncating the UTC
        # timestamp would misfile roughly a quarter of every day's orders.
        assert (
            business_date(at=datetime(2026, 9, 9, 19, 30, tzinfo=UTC))
            == datetime(2026, 9, 10).date()
        )

    def test_early_utc_stays_on_the_same_date(self) -> None:
        assert (
            business_date(at=datetime(2026, 9, 9, 5, 0, tzinfo=UTC)) == datetime(2026, 9, 9).date()
        )

    def test_business_day_bounds_span_exactly_24_hours(self) -> None:
        start, end = business_day_bounds(datetime(2026, 9, 9).date())
        assert end - start == timedelta(days=1)
        assert start.astimezone(DHAKA).hour == 0

    def test_friday_summary_lands_on_friday_evening_in_dhaka(self) -> None:
        moment = next_weekday_at(FRIDAY, 18, after=datetime(2026, 9, 9, 12, 0, tzinfo=UTC))
        local = moment.astimezone(DHAKA)
        assert local.weekday() == FRIDAY
        assert local.hour == 18

    def test_an_unknown_timezone_falls_back_to_dhaka(self) -> None:
        # A bad tenant timezone must not break money math.
        assert business_date("Not/AZone", at=datetime(2026, 9, 9, 19, 30, tzinfo=UTC)) == (
            datetime(2026, 9, 10).date()
        )


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


class TestAudit:
    async def test_entry_records_actor_and_redacts_context(self, system_db: AsyncSession) -> None:
        set_context(RequestContext(trace_id="audit-trace"))
        entry = await record_audit(
            system_db,
            AuditAction.OTP_REQUESTED,
            entity_type="otp_challenge",
            entity_id=uuid.uuid4(),
            context={"api_key": "sk_live_secret_value", "provider": "dev_console"},
        )
        await system_db.flush()

        assert entry.action == AuditAction.OTP_REQUESTED
        assert entry.trace_id == "audit-trace"
        assert entry.context["api_key"] == "[redacted]"
        assert entry.context["provider"] == "dev_console"
        await system_db.rollback()

    async def test_audit_rows_survive_the_action_they_describe(
        self, client, unique_phone: str, system_db: AsyncSession
    ) -> None:
        from tests.test_auth_flow import sign_in

        await sign_in(client, unique_phone)
        actions = set((await system_db.execute(sa.select(AuditLog.action))).scalars().all())
        assert AuditAction.OTP_REQUESTED in actions
        assert AuditAction.OTP_VERIFIED in actions
