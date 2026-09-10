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

    # ----------------------------------------------------------- billing ---
    # Plan pricing and entitlement values are product-validation data, not
    # technical constants (master spec section 26), so both are overridable
    # without a release. JSON objects keyed by plan code, e.g.
    #   PLAN_PRICE_OVERRIDES='{"starter": 24900}'
    #   PLAN_ENTITLEMENT_OVERRIDES='{"pro": {"sms_segments_monthly": 2000}}'
    plan_price_overrides: dict[str, Any] = Field(default_factory=dict)
    plan_entitlement_overrides: dict[str, Any] = Field(default_factory=dict)

    #: Which build this deployment serves. Decides whether an out-of-app
    #: payment CTA may be shown at all (master spec section 27.1).
    distribution_channel: str = "DIRECT"

    #: Dunning window after a failed renewal. Generic on purpose: neither Play's
    #: nor bKash's real retry schedule has been verified against merchant
    #: documentation, and hard-coding a guess would make the seller-facing
    #: "we will try again" copy a fabrication (master spec section 91).
    billing_grace_period_days: int = 3
    billing_max_payment_retries: int = 3
    billing_retry_interval_hours: int = 24
    #: 0 disables the trial. No trial is offered until pricing is validated.
    billing_trial_days: int = 0

    # Google Play Billing. PLAY_BILLING_EXTERNAL_CONFIGURATION_REQUIRED: the
    # provider stays disabled until all three are present *and* the feature
    # flag is on, because a half-configured verifier that returns "looks fine"
    # is worse than one that refuses.
    play_package_name: str | None = None
    play_service_account_json: SecretStr | None = None
    play_rtdn_shared_secret: SecretStr | None = None
    #: Play product id -> plan code. Empty until products exist in the console.
    play_product_plan_map: dict[str, str] = Field(default_factory=dict)

    # bKash web/direct. BKASH_MERCHANT_SETUP_REQUIRED.
    bkash_base_url: str | None = None
    bkash_app_key: SecretStr | None = None
    bkash_app_secret: SecretStr | None = None
    bkash_username: SecretStr | None = None
    bkash_password: SecretStr | None = None
    bkash_webhook_secret: SecretStr | None = None

    # ------------------------------------------------------------- admin ---
    # Platform admin is a separate identity from any seller account. Empty by
    # default: no console access exists until an operator provisions it.
    admin_api_tokens: list[str] = Field(default_factory=list)
    #: How long a support "reveal PII" grant lasts before it must be re-asked.
    admin_reveal_ttl_seconds: int = 300

    # ------------------------------------------------------------ exports ---
    export_max_sync_rows: int = 2_000
    export_download_ttl_seconds: int = 900
    export_max_active_per_tenant: int = 3

    # ------------------------------------------------------- notifications ---
    #: Transport selection. ``disabled`` records the attempt and sends nothing;
    #: ``mock`` is the CI/test double. No real provider exists yet
    #: (FCM_CREDENTIALS_REQUIRED, SMS_PROVIDER_REQUIRED).
    push_transport: str = "disabled"
    sms_transport: str = "disabled"
    notification_max_attempts: int = 5

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

    @field_validator("distribution_channel")
    @classmethod
    def _validate_distribution_channel(cls, value: str) -> str:
        allowed = {"PLAY", "WEB", "DIRECT", "INTERNAL_TEST"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"DISTRIBUTION_CHANNEL must be one of {sorted(allowed)}")
        return upper

    @field_validator("push_transport", "sms_transport")
    @classmethod
    def _validate_transport(cls, value: str) -> str:
        allowed = {"disabled", "mock", "fcm", "sms_gateway"}
        lowered = value.lower()
        if lowered not in allowed:
            raise ValueError(f"transport must be one of {sorted(allowed)}")
        return lowered

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

        # A transport that names a real provider must actually be configured.
        # "mock" outside local/test would silently mark every notification
        # delivered without sending one.
        if self.push_transport == "fcm" and self.fcm_credentials_json is None:
            problems.append("FCM_CREDENTIALS_JSON is required when PUSH_TRANSPORT=fcm")
        if self.sms_transport == "sms_gateway" and self.otp_provider_secret is None:
            problems.append("a gateway secret is required when SMS_TRANSPORT=sms_gateway")
        if self.app_env.is_deployed and "mock" in (self.push_transport, self.sms_transport):
            problems.append("mock notification transports are forbidden outside local/test")

        # Admin tokens are compared in constant time but are still bearer
        # credentials: a short one is guessable, and a placeholder one is a
        # published password.
        weak_admin_tokens = [
            token
            for token in self.admin_api_tokens
            if len(token) < 32 or token.startswith(INSECURE_DEV_PREFIX)
        ]
        if weak_admin_tokens and self.app_env.is_deployed:
            problems.append(
                f"{len(weak_admin_tokens)} ADMIN_API_TOKENS entries are shorter than 32 "
                "characters or hold a development placeholder"
            )

        if self.billing_grace_period_days < 0:
            problems.append("BILLING_GRACE_PERIOD_DAYS cannot be negative")

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
    def play_billing_configured(self) -> bool:
        """Whether Play verification *could* run.

        Deliberately strict: package name, service account and a product
        mapping must all be present. A verifier missing any one of them cannot
        distinguish a genuine purchase from a fabricated token, so the provider
        reports itself unconfigured rather than approximating.
        """
        return bool(
            self.play_package_name
            and self.play_service_account_json is not None
            and self.play_product_plan_map
        )

    @property
    def bkash_billing_configured(self) -> bool:
        return bool(
            self.bkash_base_url
            and self.bkash_app_key is not None
            and self.bkash_app_secret is not None
            and self.bkash_username is not None
            and self.bkash_password is not None
        )

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
