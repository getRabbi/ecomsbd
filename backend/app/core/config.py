"""Application configuration.

Implements the environment contract from master spec section 68.

Design rules enforced here:

*   Local/test get working, obviously-insecure defaults so the stack boots with no
    setup at all. Every such default carries the ``INSECURE_DEV_`` prefix.
*   Production **fails fast at startup** if any critical secret is missing or is
    still one of those development placeholders.
*   The development OTP provider is guarded twice (provider choice *and* an
    explicit opt-in flag), and both guards are hard-failed in production, so it
    cannot be switched on by a single mistaken environment variable.
"""

from __future__ import annotations

import base64
from enum import StrEnum
from functools import lru_cache
from typing import Any, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Development placeholders. Any value carrying this prefix is rejected in production.
INSECURE_DEV_PREFIX = "INSECURE_DEV_"

_DEV_JWT_KEY = f"{INSECURE_DEV_PREFIX}jwt_signing_key_do_not_use_outside_local"
_DEV_OTP_HASH_SECRET = f"{INSECURE_DEV_PREFIX}otp_hash_secret_do_not_use_outside_local"
_DEV_PHONE_HMAC_KEY = f"{INSECURE_DEV_PREFIX}phone_search_hmac_do_not_use_outside_local"
# 32 zero bytes, base64. Deliberately worthless as a key.
_DEV_CREDENTIAL_KEY = base64.b64encode(b"\x00" * 32).decode()


class AppEnv(StrEnum):
    """Deployment environment. See master spec section 68."""

    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def is_production(self) -> bool:
        return self is AppEnv.PRODUCTION

    @property
    def is_deployed(self) -> bool:
        """Staging and production both hold real seller-adjacent data."""
        return self in (AppEnv.STAGING, AppEnv.PRODUCTION)


class OtpProvider(StrEnum):
    """OTP delivery channel.

    ``DEV_CONSOLE`` never sends an SMS; it records the challenge locally. Real SMS
    gateways are deliberately absent until a provider is selected and its
    encoding/cost rules are verified (master spec sections 131, 140).
    """

    DEV_CONSOLE = "dev_console"
    SMS_GATEWAY = "sms_gateway"


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- app ---
    app_env: AppEnv = AppEnv.LOCAL
    app_name: str = "ecomsbd"
    public_base_url: str = "http://localhost:8000"
    api_v1_prefix: str = "/v1"
    debug: bool = False

    # Business timezone. Stored per tenant later; this is the platform default.
    default_timezone: str = "Asia/Dhaka"

    # ----------------------------------------------------------- database ---
    database_url: str = "postgresql+asyncpg://ecomsbd:ecomsbd@localhost:5432/ecomsbd"
    database_echo: bool = False
    database_pool_size: int = 10
    database_max_overflow: int = 10
    database_pool_timeout_seconds: int = 30

    # -------------------------------------------------------------- redis ---
    # Optional in local/test: the rate limiter and cache fall back to an
    # in-process implementation so the app boots with no Redis running.
    redis_url: str | None = None

    # ----------------------------------------------------------- auth/jwt ---
    jwt_signing_key: SecretStr = SecretStr(_DEV_JWT_KEY)
    jwt_key_version: str = "v1"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "ecomsbd"
    access_token_ttl_seconds: int = 900  # 15 minutes; short by design
    refresh_token_ttl_days: int = 60

    # ------------------------------------------------------------ crypto ---
    # AES-GCM application-level vault for courier/billing credentials.
    credential_encryption_key: SecretStr = SecretStr(_DEV_CREDENTIAL_KEY)
    credential_key_version: str = "v1"
    # Separate secret. Master spec section 133: never a plain unsalted hash of a phone.
    phone_search_hmac_key: SecretStr = SecretStr(_DEV_PHONE_HMAC_KEY)

    # --------------------------------------------------------------- otp ---
    otp_provider: OtpProvider = OtpProvider.DEV_CONSOLE
    otp_provider_secret: SecretStr | None = None
    otp_hash_secret: SecretStr = SecretStr(_DEV_OTP_HASH_SECRET)
    otp_length: int = 6
    otp_ttl_seconds: int = 300  # master spec section 4: 5 minutes
    otp_max_attempts: int = 5  # master spec section 4
    otp_max_requests_per_phone_hour: int = 5
    otp_max_requests_per_ip_hour: int = 20
    otp_resend_cooldown_seconds: int = 60

    # Second, independent guard on the development OTP provider.
    allow_dev_otp: bool = True
    # Returns the OTP in the API response. Local convenience only.
    otp_expose_debug_code: bool = True

    # ----------------------------------------------------------- logging ---
    log_level: str = "INFO"
    log_json: bool = True

    # ------------------------------------------------------------ sentry ---
    sentry_dsn: SecretStr | None = None
    sentry_traces_sample_rate: float = 0.0
    sentry_environment: str | None = None

    # --------------------------------------------------------------- api ---
    cors_allow_origins: list[str] = Field(default_factory=list)
    request_body_limit_bytes: int = 2 * 1024 * 1024
    default_page_size: int = 30  # master spec section 106
    max_page_size: int = 100

    # Clients older than this are rejected with a stable upgrade error.
    minimum_supported_app_version: str | None = None

    # ------------------------------------------------------ object store ---
    # Cloudflare R2. Absent until credentials are provisioned.
    r2_endpoint_url: str | None = None
    r2_bucket: str | None = None
    r2_access_key_id: SecretStr | None = None
    r2_secret_access_key: SecretStr | None = None

    # --------------------------------------------------------------- fcm ---
    fcm_project_id: str | None = None
    fcm_credentials_json: SecretStr | None = None

    # ----------------------------------------------------------- workers ---
    worker_queue_name: str = "ecomsbd:jobs"
    worker_max_jobs: int = 10
    outbox_batch_size: int = 50
    outbox_max_attempts: int = 12

    # --------------------------------------------------------- validators ---

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return upper

    @field_validator("credential_encryption_key")
    @classmethod
    def _validate_credential_key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        try:
            decoded = base64.b64decode(raw, validate=True)
        except Exception as exc:
            raise ValueError("CREDENTIAL_ENCRYPTION_KEY must be base64-encoded 32 bytes") from exc
        if len(decoded) != 32:
            raise ValueError(
                f"CREDENTIAL_ENCRYPTION_KEY must decode to exactly 32 bytes, got {len(decoded)}"
            )
        return value

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> Self:
        """Fail fast rather than boot a deployed environment with dev secrets."""
        problems: list[str] = []

        if self.app_env.is_deployed:
            problems.extend(self._insecure_placeholder_problems())

            if self.redis_url is None:
                problems.append("REDIS_URL is required outside local/test")
            if self.debug:
                problems.append("DEBUG must be false in staging/production")

        if self.app_env.is_production:
            # The development OTP provider is blocked by two independent switches.
            if self.otp_provider is OtpProvider.DEV_CONSOLE:
                problems.append(
                    "OTP_PROVIDER=dev_console is forbidden in production "
                    "(it never delivers an SMS and would let anyone sign in)"
                )
            if self.allow_dev_otp:
                problems.append("ALLOW_DEV_OTP must be false in production")
            if self.otp_expose_debug_code:
                problems.append("OTP_EXPOSE_DEBUG_CODE must be false in production")
            if not self.public_base_url.startswith("https://"):
                problems.append("PUBLIC_BASE_URL must use https in production")
            if self.database_url.startswith("sqlite"):
                problems.append("SQLite is not a supported production database")

        if self.otp_provider is OtpProvider.SMS_GATEWAY and self.otp_provider_secret is None:
            problems.append("OTP_PROVIDER_SECRET is required when OTP_PROVIDER=sms_gateway")

        if problems:
            joined = "\n  - ".join(problems)
            raise ValueError(f"Invalid configuration for APP_ENV={self.app_env}:\n  - {joined}")
        return self

    def _insecure_placeholder_problems(self) -> list[str]:
        checks: dict[str, str] = {
            "JWT_SIGNING_KEY": self.jwt_signing_key.get_secret_value(),
            "OTP_HASH_SECRET": self.otp_hash_secret.get_secret_value(),
            "PHONE_SEARCH_HMAC_KEY": self.phone_search_hmac_key.get_secret_value(),
        }
        problems = [
            f"{name} still holds the development placeholder value"
            for name, value in checks.items()
            if value.startswith(INSECURE_DEV_PREFIX)
        ]
        if self.credential_encryption_key.get_secret_value() == _DEV_CREDENTIAL_KEY:
            problems.append(
                "CREDENTIAL_ENCRYPTION_KEY still holds the development placeholder value"
            )
        problems.extend(
            f"{name} must be at least 32 characters"
            for name, value in checks.items()
            if not value.startswith(INSECURE_DEV_PREFIX) and len(value) < 32
        )
        return problems

    # ------------------------------------------------------------ helpers ---

    @property
    def dev_otp_enabled(self) -> bool:
        """True only when *both* development OTP guards agree, outside production."""
        return (
            self.otp_provider is OtpProvider.DEV_CONSOLE
            and self.allow_dev_otp
            and not self.app_env.is_production
        )

    @property
    def expose_otp_debug_code(self) -> bool:
        """Whether the OTP may be echoed back in the API response."""
        return self.dev_otp_enabled and self.otp_expose_debug_code

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def sanitized(self) -> dict[str, Any]:
        """Config snapshot safe to log: secrets are replaced, never truncated."""
        data: dict[str, Any] = {}
        for name, value in self.model_dump().items():
            field = type(self).model_fields[name]
            if "SecretStr" in str(field.annotation):
                data[name] = "<set>" if value is not None else None
            else:
                data[name] = value
        return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()


def reset_settings_cache() -> None:
    """Clear the settings cache. Tests only."""
    get_settings.cache_clear()
