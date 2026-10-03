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
import json
import uuid
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Any, Self

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Development placeholders. Any value carrying this prefix is rejected in production.
INSECURE_DEV_PREFIX = "INSECURE_DEV_"

_DEV_JWT_KEY = f"{INSECURE_DEV_PREFIX}jwt_signing_key_do_not_use_outside_local"
_DEV_OTP_HASH_SECRET = f"{INSECURE_DEV_PREFIX}otp_hash_secret_do_not_use_outside_local"
_DEV_PHONE_HMAC_KEY = f"{INSECURE_DEV_PREFIX}phone_search_hmac_do_not_use_outside_local"
# 32 zero bytes, base64. Deliberately worthless as a key.
_DEV_CREDENTIAL_KEY = base64.b64encode(b"\x00" * 32).decode()

#: Where a blank value for each required secret lands. See
#: :meth:`Settings._blank_secret_falls_back_to_the_placeholder`.
_DEV_SECRET_DEFAULTS = {
    "jwt_signing_key": _DEV_JWT_KEY,
    "otp_hash_secret": _DEV_OTP_HASH_SECRET,
    "phone_search_hmac_key": _DEV_PHONE_HMAC_KEY,
    "credential_encryption_key": _DEV_CREDENTIAL_KEY,
}


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


#: Sign-in methods that have a working server-side implementation in this build.
#:
#: All four are implemented: email/password with Argon2id and verified email,
#: Google and Apple with server-side identity-token verification, and phone OTP
#: (deferred in production but still working code). This set is what the "can
#: anyone actually sign in?" boot check counts, so a method is added here only
#: once its endpoint, its verifier and its tests exist.
#:
#: Being implemented is not the same as being *configured*: Google and Apple
#: still need client ids, which is a separate refusal below.
IMPLEMENTED_AUTH_METHODS = frozenset({"phone_otp", "email_password", "google", "apple"})

#: Hosts that mean "this machine" and so must never appear in a production URL.
#: ``10.0.2.2`` is the Android emulator's alias for its host and reaches a
#: developer's laptop, nothing else. S104 is about *binding* to all interfaces;
#: these are strings being matched against, not an address anything listens on.
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "10.0.2.2")  # noqa: S104


def _is_loopback(url: str) -> bool:
    """Whether a URL or origin points at the machine running it.

    Matched on the authority rather than anywhere in the string, so a perfectly
    good ``https://localhost-shop.example.com`` is not mistaken for one.
    """
    authority = url.split("://", 1)[-1].split("/", 1)[0].rsplit("@", 1)[-1]
    if authority.startswith("["):  # bracketed IPv6 literal, optionally with a port
        host = authority[1:].split("]", 1)[0]
    else:
        host = authority.rsplit(":", 1)[0]
    return host.lower() in _LOOPBACK_HOSTS


def _parse_env_list(value: Any) -> Any:
    """Read a list setting from an environment string.

    pydantic-settings decodes a collection field as JSON, which makes
    ``CORS_ALLOW_ORIGINS=`` — a line an operator leaves blank in a template they
    are filling in — a parse error rather than an empty list. That error names
    the field but not the cause, and the operator's reasonable next move is to
    delete the line.

    So: blank is empty, JSON is JSON, and anything else is a comma-separated
    list, which is what a person writes when nobody has told them otherwise.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    return [item.strip() for item in text.split(",") if item.strip()]


def _parse_env_mapping(value: Any) -> Any:
    """Read a mapping setting. Blank is empty; everything else must be JSON."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return {}
    return json.loads(text)


_PEM_HEADER = "-----BEGIN PRIVATE KEY-----"
_PEM_FOOTER = "-----END PRIVATE KEY-----"


def _apple_key_pem(raw: str) -> str | None:
    """The .p8 key as PEM, whether it arrived armoured or as its bare body.

    A dashboard or secret store often keeps only the base64 between the
    markers, with its line breaks turned into spaces or dropped. That is the
    whole key, so it is re-armoured here rather than refused: refusing it
    stopped every API and worker process at boot, for a feature (Apple token
    revocation) that is best effort. A value that is not base64 DER, such as a
    file path, is still refused.
    """
    if "BEGIN PRIVATE KEY" in raw:
        return raw
    body = "".join(raw.split())
    try:
        der = base64.b64decode(body, validate=True)
    except ValueError:
        return None
    # Every PKCS#8 key is an ASN.1 SEQUENCE, and no real one is this short.
    if len(der) < 32 or der[0] != 0x30:
        return None
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]
    return "\n".join([_PEM_HEADER, *lines, _PEM_FOOTER]) + "\n"


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # A refused value is often a secret, and a boot failure is printed to
        # the service log, so errors name the setting and never echo its value.
        hide_input_in_errors=True,
    )

    # ---------------------------------------------------------------- app ---
    app_env: AppEnv = AppEnv.LOCAL
    app_name: str = "ecomsbd"
    public_base_url: str = "http://localhost:8000"
    api_v1_prefix: str = "/v1"
    debug: bool = False

    #: Where a human-facing page lives, when one exists. Today it is the base a
    #: transactional email builds its links from; there is no web client.
    public_web_url: str | None = None
    #: Reply-to on outbound mail, and the address shown when the product tells a
    #: seller to contact support.
    support_email: str | None = None

    # Business timezone. Stored per tenant later; this is the platform default.
    default_timezone: str = "Asia/Dhaka"

    # ----------------------------------------------------------- database ---
    database_url: str = "postgresql+asyncpg://ecomsbd:ecomsbd@localhost:5432/ecomsbd"
    database_echo: bool = False
    # Per-process pool. Production reaches Postgres through Supabase's session
    # pooler, where every open client connection pins one of a small, fixed
    # number of server slots (15) shared by the API, the worker and any
    # container a rollout briefly overlaps. Idle pooled connections keep their
    # slot, and an overflow connection asks for a new one exactly when a burst
    # arrives, so the pool is bounded with no overflow: a request that finds it
    # full waits here (up to the timeout) instead of being refused by the
    # pooler with EMAXCONNSESSION. 10+10 per process was up to 40 against 15.
    database_pool_size: int = 5
    database_max_overflow: int = 0
    database_pool_timeout_seconds: int = 30

    # -------------------------------------------------------------- redis ---
    # Optional in local/test: the rate limiter and cache fall back to an
    # in-process implementation so the app boots with no Redis running.
    redis_url: str | None = None

    # Supabase is the only deployed seller authentication authority.
    supabase_url: str | None = None
    supabase_anon_key: SecretStr | None = None
    supabase_service_role_key: SecretStr | None = None

    @property
    def supabase_auth_active(self) -> bool:
        return self.app_env.is_deployed or bool(self.supabase_url)

    # Legacy seller settings remain for local fixtures. JWT_SIGNING_KEY also
    # protects existing platform-admin token hashes in production.
    # ----------------------------------------------------------- auth/jwt ---
    jwt_signing_key: SecretStr = SecretStr(_DEV_JWT_KEY)
    jwt_key_version: str = "v1"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "ecomsbd"
    access_token_ttl_seconds: int = 900  # 15 minutes; short by design
    refresh_token_ttl_days: int = 60

    # ------------------------------------------------- sign-in methods ---
    # BOOT configuration, not runtime feature flags. These decide which
    # credentials must be present for the process to be safe to start, so they
    # cannot live in the database table that :mod:`app.common.feature_flags`
    # serves — that table is read per request, after boot, by a connection this
    # configuration is what establishes.
    #
    # Only ``phone_otp`` is implemented (see :data:`IMPLEMENTED_AUTH_METHODS`).
    # The other three exist so their credentials have a validated home before
    # the sign-in work lands; turning one on configures nothing into existence.

    #: Deferred. The production decision is email/password + Google + Apple, and
    #: no SMS gateway has been selected, so OTP sign-in is off in production and
    #: its endpoints refuse with ``FEATURE_DISABLED``.
    phone_otp_login_enabled: bool = False
    email_password_auth_enabled: bool = False
    google_auth_enabled: bool = False
    apple_auth_enabled: bool = False

    # Google Sign-In. The backend must verify the identity token itself:
    # issuer, signature, expiry, and an ``aud`` that is one of these client ids.
    # A client's claim to have signed in is never evidence. Each id is public —
    # it ships inside the app — so none of these is a secret.
    google_client_id_android: str | None = None
    google_client_id_ios: str | None = None
    google_client_id_web: str | None = None

    # Sign in with Apple. The private key is a ``.p8`` from the Apple Developer
    # portal, supplied as PEM *content* rather than a path: a file on a
    # production host is one `docker cp` from being somewhere else, and the
    # repository already carries the ``FCM_CREDENTIALS_JSON`` precedent.
    apple_team_id: str | None = None
    #: The Services ID (web/redirect flow) or the app's bundle id (native
    #: flow). This is the ``aud`` an Apple identity token must carry.
    apple_client_id: str | None = None
    apple_key_id: str | None = None
    apple_private_key: SecretStr | None = None

    # Email/password policy.
    #: How long a verification link stays valid. A day is long enough for mail
    #: that was delayed and short enough that an old inbox is not a key.
    email_verification_ttl_seconds: int = 86_400
    #: Password reset. Much shorter, because a live reset link *is* the account.
    password_reset_ttl_seconds: int = 3_600
    #: Whether an unverified address blocks sign-in. False by default: a seller
    #: who cannot receive mail must still be able to reach the shop they just
    #: created, and the verified flag already gates what it needs to gate
    #: (account linking). Turn it on once email delivery is proven.
    email_verification_required_for_login: bool = False
    #: Login throttling, per hour. Per-address stops one account being ground
    #: down; per-IP stops one attacker grinding down many.
    login_max_attempts_per_email_hour: int = 10
    login_max_attempts_per_ip_hour: int = 30

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
    allow_dev_otp: bool = False
    # Returns the OTP in the API response. Local convenience only.
    otp_expose_debug_code: bool = False

    # ----------------------------------------------------------- logging ---
    log_level: str = "INFO"
    log_json: bool = True

    # ------------------------------------------------------------ sentry ---
    sentry_dsn: SecretStr | None = None
    sentry_traces_sample_rate: float = 0.0
    sentry_environment: str | None = None
    #: Which build an event came from. Without it every regression looks like it
    #: has always been there. Conventionally ``ecomsbd-backend@<version>`` or the
    #: deployed commit sha.
    sentry_release: str | None = None

    # --------------------------------------------------------------- api ---
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    #: ``Host`` headers this deployment answers to. Empty disables the check,
    #: which is right behind a proxy that already enforces one and wrong when
    #: the app is reachable directly. ``*`` is refused in production.
    trusted_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
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
    # Backend-only launch policy. Stored subscriptions are never changed.
    free_launch_mode: bool = False
    billing_enabled: bool = False
    # First-party risk lookup also protects against phone-number probing.
    # Keep a plan-independent ceiling during free launch (the existing top tier).
    risk_checks_daily_safety_limit: int = Field(default=500, ge=1)

    @property
    def purchases_enabled(self) -> bool:
        return self.billing_enabled and not self.free_launch_mode

    # Plan pricing and entitlement values are product-validation data, not
    # technical constants (master spec section 26), so both are overridable
    # without a release. JSON objects keyed by plan code, e.g.
    #   PLAN_PRICE_OVERRIDES='{"starter": 24900}'
    #   PLAN_ENTITLEMENT_OVERRIDES='{"pro": {"sms_segments_monthly": 2000}}'
    plan_price_overrides: Annotated[dict[str, Any], NoDecode] = Field(default_factory=dict)
    plan_entitlement_overrides: Annotated[dict[str, Any], NoDecode] = Field(default_factory=dict)

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
    play_product_plan_map: Annotated[dict[str, str], NoDecode] = Field(default_factory=dict)

    # bKash web/direct. BKASH_MERCHANT_SETUP_REQUIRED.
    bkash_base_url: str | None = None
    bkash_app_key: SecretStr | None = None
    bkash_app_secret: SecretStr | None = None
    bkash_username: SecretStr | None = None
    bkash_password: SecretStr | None = None
    bkash_webhook_secret: SecretStr | None = None

    # ------------------------------------------------------- integrations ---
    # Shopify public app from the Shopify Dev Dashboard. Until both are set the
    # Integrations Hub reports SHOPIFY_APP_SETUP_REQUIRED and starts no OAuth.
    shopify_client_id: str | None = None
    shopify_client_secret: SecretStr | None = None
    #: Pinned GraphQL Admin API version. Shopify releases quarterly; bump it
    #: deliberately after reading that version's changelog.
    shopify_api_version: str = "2026-07"
    #: Orders and the customer fields on them. read_all_orders (older than 60
    #: days) is a protected scope Shopify must approve, so it is not requested.
    shopify_scopes: str = "read_orders"
    # Meta app for Messenger Page connections. META_APP_SETUP_REQUIRED until all
    # three are set; pages_messaging also needs Meta App Review.
    meta_app_id: str | None = None
    meta_app_secret: SecretStr | None = None
    meta_webhook_verify_token: SecretStr | None = None
    meta_graph_api_version: str = "v26.0"
    meta_whatsapp_embedded_signup_config_id: str | None = None
    # Operator attestation after Meta grants the required public access.
    meta_whatsapp_public_signup_enabled: bool = False
    meta_whatsapp_test_user_ids: list[uuid.UUID] = Field(default_factory=list)
    # Local/test environments only. Production requires independent admin auth.
    meta_whatsapp_manual_enabled: bool = False

    # ------------------------------------------------------------- admin ---
    # Platform admin is a separate identity from any seller account. Empty by
    # default: no console access exists until an operator provisions it.
    admin_api_tokens: Annotated[list[str], NoDecode] = Field(default_factory=list)
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

    # -------------------------------------------------------------- email ---
    #: TRANSACTIONAL_EMAIL_PROVIDER_REQUIRED. Provider-neutral, like push and
    #: SMS. ``console`` writes the message to the log and sends nothing — it is
    #: the development transport, and it is refused in staging and production
    #: so it cannot become the thing that "delivers" a password reset.
    #: ``provider_api`` uses Resend's documented send-email contract.
    email_transport: str = "disabled"
    #: Envelope sender. Must be on a domain whose SPF/DKIM/DMARC you control,
    #: or verification mail lands in spam and password resets stop arriving.
    email_from_address: str | None = None
    email_from_name: str = "ecomsbd"
    #: Resend API base URL (https://api.resend.com), without /emails.
    email_api_base_url: str | None = None
    email_api_key: SecretStr | None = None
    #: Resend webhook signing secret (``whsec_...``) for delivery, bounce and
    #: complaint receipts on customer messages. Without it email statuses stop
    #: at SENT and are labelled as not reported.
    email_webhook_secret: SecretStr | None = None

    # ---------------------------------------------------------- couriers ---
    # Transport budgets. `connect` is short and separate on purpose: its expiry
    # is the only failure that *proves* a request never left, which is what
    # lets an ambiguous booking be a clean failure instead of a parcel nobody
    # can account for. A generous connect timeout would manufacture
    # BOOKING_UNKNOWNs that never needed to exist.
    courier_connect_timeout_seconds: float = 5.0
    courier_read_timeout_seconds: float = 20.0
    courier_write_timeout_seconds: float = 10.0
    courier_pool_timeout_seconds: float = 5.0
    courier_max_connections: int = 10
    #: Reads only. A create is never retried whatever this says.
    courier_read_retries: int = 2

    # Steadfast. The base URL is the one its V1 documentation prints; it is
    # overridable so a future sandbox does not need a release, not because we
    # know of one (the document names none).
    steadfast_base_url: str = "https://portal.packzy.com/api/v1"
    #: ecomsbd's own batch size. The provider permits 500; a failed 500-item
    #: request leaves 500 parcels in an unknown state, and permission to send
    #: that many is not a reason to.
    steadfast_bulk_chunk_size: int = 50
    #: STEADFAST_WEBHOOK_CONTRACT_REQUIRED. The supplied V1 documentation has no
    #: webhook section, so there is no signature scheme to verify against.
    #: Turning this on without one would mean accepting unauthenticated requests
    #: that move money, so it is refused in deployed environments below.
    steadfast_webhook_enabled: bool = False

    # Pathao. Both hosts are the ones Pathao's own published integration
    # selects between; sandbox is chosen per courier account, not per
    # deployment, so a seller can rehearse against it while the shop is live.
    pathao_base_url: str = "https://api-hermes.pathao.com"
    pathao_sandbox_base_url: str = "https://courier-api-sandbox.pathao.com"
    #: ecomsbd's own batch size. Pathao publishes no ceiling for /orders/bulk,
    #: and no published ceiling is not a licence to send an unbounded batch.
    pathao_bulk_chunk_size: int = 50
    #: Pathao publishes a real webhook contract, so unlike Steadfast this can be
    #: switched on. It still does nothing on its own: each shop's callback is
    #: verified against that shop's own stored webhook secret, and a shop
    #: without one is treated as not configured.
    pathao_webhook_enabled: bool = True

    # RedX. Both hosts are the ones RedX's developer page lists; each has its
    # own merchant token, and sandbox is chosen per courier account.
    redx_base_url: str = "https://openapi.redx.com.bd/v1.0.0-beta"
    redx_sandbox_base_url: str = "https://sandbox.redx.com.bd/v1.0.0-beta"

    # Adaptive status polling, in minutes. A fresh parcel changes state within
    # hours; a two-week-old one will not change in the next ten minutes.
    courier_poll_interval_fresh_minutes: int = 20
    courier_poll_interval_active_minutes: int = 60
    courier_poll_interval_stale_minutes: int = 360
    #: How long a parcel stays "fresh" after booking.
    courier_poll_fresh_window_hours: int = 24
    #: After this, a parcel is polled on the stale interval.
    courier_poll_active_window_days: int = 7
    #: Parcels per shop per poll run. Bounds the load a single big shop can put
    #: on the provider and on this VPS.
    courier_poll_batch_size: int = 100

    # BOOKING_UNKNOWN recovery. Backoff between attempts to resolve one
    # ambiguous booking by asking the provider about its invoice.
    courier_recovery_initial_delay_seconds: int = 60
    courier_recovery_max_delay_seconds: int = 3600
    #: After this many inconclusive attempts the parcel goes to a person
    #: rather than being asked about forever.
    courier_recovery_max_attempts: int = 8

    # Payment sync. The overlap re-reads a little of what was already seen;
    # duplicates are free (the provider payment id is unique) and a gap is
    # money that never reaches the seller's screen.
    courier_payment_sync_interval_minutes: int = 60
    courier_payment_sync_overlap_minutes: int = 120
    courier_payment_sync_max_pages: int = 20

    # ----------------------------------------------------------- workers ---
    worker_queue_name: str = "ecomsbd:jobs"
    worker_max_jobs: int = 10
    #: The worker's share of the pooler budget (see ``database_pool_size``).
    #: Jobs beyond it wait for a connection rather than opening another.
    worker_database_pool_size: int = 4
    outbox_batch_size: int = 50
    outbox_max_attempts: int = 12

    # --------------------------------------------------------- validators ---

    @field_validator(
        "supabase_url",
        "supabase_anon_key",
        "supabase_service_role_key",
        "redis_url",
        "public_web_url",
        "support_email",
        "otp_provider_secret",
        "google_client_id_android",
        "google_client_id_ios",
        "google_client_id_web",
        "apple_team_id",
        "apple_client_id",
        "apple_key_id",
        "apple_private_key",
        "email_from_address",
        "email_api_base_url",
        "email_api_key",
        "sentry_dsn",
        "sentry_environment",
        "sentry_release",
        "minimum_supported_app_version",
        "r2_endpoint_url",
        "r2_bucket",
        "r2_access_key_id",
        "r2_secret_access_key",
        "fcm_project_id",
        "fcm_credentials_json",
        "play_package_name",
        "play_service_account_json",
        "play_rtdn_shared_secret",
        "bkash_base_url",
        "bkash_app_key",
        "bkash_app_secret",
        "bkash_username",
        "bkash_password",
        "bkash_webhook_secret",
        "shopify_client_id",
        "shopify_client_secret",
        "meta_app_id",
        "meta_app_secret",
        "meta_webhook_verify_token",
        "meta_whatsapp_embedded_signup_config_id",
        mode="before",
    )
    @classmethod
    def _blank_means_unset(cls, value: object) -> object:
        """Treat an empty environment value as absent.

        ``.env.production.local`` ships with every optional line present and
        blank, so the operator fills them in place. Without this, ``REDIS_URL=``
        would be the empty *string* — which is not ``None``, so a presence check
        passes and the failure moves from startup to the first request.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "jwt_signing_key",
        "otp_hash_secret",
        "phone_search_hmac_key",
        "credential_encryption_key",
        mode="before",
    )
    @classmethod
    def _blank_secret_falls_back_to_the_placeholder(cls, value: Any, info: ValidationInfo) -> Any:
        """A blank required secret becomes the development placeholder.

        These four have no ``None`` to fall back to, and an empty string would
        be an empty signing key — which is a working key that signs nothing.
        Routing blank to the tagged ``INSECURE_DEV_`` value instead means local
        keeps its zero-setup boot, and a deployed environment gets the existing
        refusal, which names the variable, rather than a length complaint about
        a value the operator never set.
        """
        if isinstance(value, str) and not value.strip():
            return _DEV_SECRET_DEFAULTS[str(info.field_name)]
        return value

    @field_validator("cors_allow_origins", "trusted_hosts", "admin_api_tokens", mode="before")
    @classmethod
    def _decode_list(cls, value: Any) -> Any:
        return _parse_env_list(value)

    @field_validator(
        "plan_price_overrides",
        "plan_entitlement_overrides",
        "play_product_plan_map",
        mode="before",
    )
    @classmethod
    def _decode_mapping(cls, value: Any) -> Any:
        return _parse_env_mapping(value)

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

    @field_validator("email_transport")
    @classmethod
    def _validate_email_transport(cls, value: str) -> str:
        allowed = {"disabled", "console", "mock", "provider_api"}
        lowered = value.lower()
        if lowered not in allowed:
            raise ValueError(f"EMAIL_TRANSPORT must be one of {sorted(allowed)}")
        return lowered

    @field_validator("apple_private_key")
    @classmethod
    def _validate_apple_key(cls, value: SecretStr | None) -> SecretStr | None:
        """Catch the two ways this is supplied wrong before Apple rejects it.

        A path instead of the contents, or a key whose newlines were eaten by a
        shell, both fail at the first sign-in rather than at boot — which is the
        worst time to find out. The key's bare base64 body, without the PEM
        markers, is the whole key and is accepted (see :func:`_apple_key_pem`).
        """
        if value is None:
            return None
        raw = value.get_secret_value().replace("\\n", "\n").strip()
        if not raw:
            return None
        pem = _apple_key_pem(raw)
        if pem is None:
            raise ValueError(
                "APPLE_PRIVATE_KEY must hold the PEM *contents* of the .p8 file "
                "(the '-----BEGIN PRIVATE KEY-----' block, or the base64 between "
                "its markers), not a path to it"
            )
        return SecretStr(pem)

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

            if not self.redis_url:
                problems.append("REDIS_URL is required outside local/test")
            if self.debug:
                problems.append("DEBUG must be false in staging/production")
            if not self.database_url:
                problems.append("DATABASE_URL is not set")
            elif not self.database_url.startswith("postgresql"):
                problems.append(
                    "DATABASE_URL must be a PostgreSQL URL "
                    "(postgresql+asyncpg://...) outside local/test"
                )

        if self.app_env.is_production:
            # The development OTP provider is blocked by two independent
            # switches. Both are conditional on OTP sign-in being on at all:
            # with PHONE_OTP_LOGIN_ENABLED=false nothing ever calls a provider,
            # and demanding an SMS gateway for a disabled feature is exactly the
            # "credentials for an integration nobody turned on" this refuses to
            # do elsewhere.
            if self.phone_otp_login_enabled:
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
            if _is_loopback(self.public_base_url):
                problems.append(
                    "PUBLIC_BASE_URL still points at localhost. Production must not "
                    "fall back to a development URL"
                )
            if self.public_web_url and not self.public_web_url.startswith("https://"):
                problems.append("PUBLIC_WEB_URL must use https in production")
            if self.database_url.startswith("sqlite"):
                problems.append("SQLite is not a supported production database")
            if "*" in self.trusted_hosts:
                problems.append("TRUSTED_HOSTS must not be '*' in production")

        # Only when OTP sign-in is actually on. A deployment with
        # PHONE_OTP_LOGIN_ENABLED=false may still name a gateway it intends to
        # use later, and demanding that gateway's key for a feature nobody can
        # reach is how operators learn to fill in values they do not need.
        if (
            self.phone_otp_login_enabled
            and self.otp_provider is OtpProvider.SMS_GATEWAY
            and self.otp_provider_secret is None
        ):
            problems.append("OTP_PROVIDER_SECRET is required when OTP_PROVIDER=sms_gateway")

        problems.extend(self._auth_method_problems())
        problems.extend(self._email_problems())
        problems.extend(self._integration_credential_problems())

        if self.app_env.is_deployed:
            problems.extend(self._public_surface_problems())

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

        # A webhook receiver with no verified signature contract cannot tell a
        # provider's callback from anyone else's POST. Accepting one would mean
        # letting an unauthenticated request change a parcel's status and,
        # through it, a seller's money. The receiver exists and is tested; it
        # stays disabled until a real contract is supplied, and this refuses to
        # boot a deployed environment that turned it on without one.
        if self.steadfast_webhook_enabled and self.app_env.is_deployed:
            problems.append(
                "STEADFAST_WEBHOOK_ENABLED is true but no verified Steadfast webhook "
                "contract exists (STEADFAST_WEBHOOK_CONTRACT_REQUIRED). There is no "
                "signature scheme to check, so every delivery would be accepted "
                "unauthenticated."
            )

        if self.courier_connect_timeout_seconds <= 0:
            problems.append("COURIER_CONNECT_TIMEOUT_SECONDS must be positive")
        if not 1 <= self.steadfast_bulk_chunk_size <= 500:
            problems.append(
                "STEADFAST_BULK_CHUNK_SIZE must be between 1 and the provider's "
                "documented maximum of 500"
            )
        if not 1 <= self.pathao_bulk_chunk_size <= 200:
            problems.append(
                "PATHAO_BULK_CHUNK_SIZE must be between 1 and 200. Pathao publishes no "
                "maximum, so this ceiling is ecomsbd's own and is deliberately small: a "
                "failed batch leaves every parcel in it in an unknown state."
            )

        if problems:
            joined = "\n  - ".join(problems)
            raise ValueError(f"Invalid configuration for APP_ENV={self.app_env}:\n  - {joined}")
        return self

    def _insecure_placeholder_problems(self) -> list[str]:
        checks: dict[str, str] = {
            # Existing platform_admins.token_hash values depend on this key.
            # TokenService refuses seller issuance/verification in production.
            "JWT_SIGNING_KEY": self.jwt_signing_key.get_secret_value(),
            "PHONE_SEARCH_HMAC_KEY": self.phone_search_hmac_key.get_secret_value(),
        }
        unset = "is not set, or still holds the development placeholder value"
        problems = [
            f"{name} {unset}"
            for name, value in checks.items()
            if value.startswith(INSECURE_DEV_PREFIX)
        ]
        if self.credential_encryption_key.get_secret_value() == _DEV_CREDENTIAL_KEY:
            problems.append(f"CREDENTIAL_ENCRYPTION_KEY {unset}")
        problems.extend(
            f"{name} must be at least 32 characters"
            for name, value in checks.items()
            if not value.startswith(INSECURE_DEV_PREFIX) and len(value) < 32
        )
        return problems

    def _auth_method_problems(self) -> list[str]:
        """Sign-in must be both configured and reachable.

        Enabled providers need their configuration, and a deployed environment
        must enable at least one implemented method.
        """
        problems: list[str] = []

        if self.supabase_auth_active:
            from urllib.parse import urlsplit

            url = urlsplit(self.supabase_url or "")
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path not in ("", "/")
                or _is_loopback(self.supabase_url or "")
            ):
                problems.append("SUPABASE_URL must be the HTTPS project origin")
            if not self.supabase_anon_key or not self.supabase_anon_key.get_secret_value():
                problems.append("SUPABASE_ANON_KEY is required")
            if self.phone_otp_login_enabled:
                problems.append("PHONE_OTP_LOGIN_ENABLED must be false")
            return problems

        if self.google_auth_enabled and not self.google_client_ids:
            problems.append(
                "GOOGLE_AUTH_ENABLED is true but no Google client id is set. At "
                "least one of GOOGLE_CLIENT_ID_ANDROID / _IOS / _WEB is required: "
                "they are the audiences an identity token is checked against, and "
                "a verifier with an empty audience set accepts tokens minted for "
                "any other application"
            )

        if self.apple_auth_enabled:
            missing = [
                name for name, value in (("APPLE_CLIENT_ID", self.apple_client_id),) if not value
            ]
            if missing:
                problems.append(
                    f"APPLE_AUTH_ENABLED is true but {', '.join(missing)} "
                    f"{'is' if len(missing) == 1 else 'are'} missing"
                )

        if self.email_password_auth_enabled and self.app_env.is_deployed:
            if not self.email_transport_can_deliver:
                problems.append(
                    "EMAIL_PASSWORD_AUTH_ENABLED is true but EMAIL_TRANSPORT="
                    f"{self.email_transport} cannot deliver mail. Verification and "
                    "password reset would be unreachable, which locks sellers out "
                    "of their own accounts (TRANSACTIONAL_EMAIL_PROVIDER_REQUIRED)"
                )
            if not self.email_from_address:
                problems.append(
                    "EMAIL_FROM_ADDRESS is required when EMAIL_PASSWORD_AUTH_ENABLED is true"
                )

        if self.app_env.is_deployed and not self.available_auth_methods:
            enabled = sorted(self.enabled_auth_methods)
            detail = (
                f"the enabled method(s) {enabled} have no implementation in this build"
                if enabled
                else "every sign-in method is disabled"
            )
            problems.append(
                f"no seller can sign in: {detail}. Implemented methods are "
                f"{sorted(IMPLEMENTED_AUTH_METHODS)} "
                "Refusing to start rather than serving an app nobody can log in to"
            )

        return problems

    def _email_problems(self) -> list[str]:
        problems: list[str] = []
        if self.app_env.is_deployed and self.email_transport in ("console", "mock"):
            problems.append(
                f"EMAIL_TRANSPORT={self.email_transport} is forbidden outside local/test. "
                "It writes the message to a log instead of sending it, so a password "
                "reset would be 'delivered' to nobody - and printed where it can be read"
            )
        # Deployed only. Locally an incomplete provider_api simply reports
        # NOT_CONFIGURED at send time, which is what that transport is for;
        # refusing to boot over it would break the zero-setup local start.
        if self.email_transport == "provider_api" and self.app_env.is_deployed:
            missing = [
                name
                for name, value in (
                    ("EMAIL_API_BASE_URL", self.email_api_base_url),
                    ("EMAIL_API_KEY", self.email_api_key),
                    ("EMAIL_FROM_ADDRESS", self.email_from_address),
                )
                if not value
            ]
            if missing:
                problems.append(f"EMAIL_TRANSPORT=provider_api requires {', '.join(missing)}")
            if self.email_api_base_url and not self.email_api_base_url.startswith("https://"):
                problems.append("EMAIL_API_BASE_URL must use https outside local/test")
        if self.email_from_address and "@" not in self.email_from_address:
            problems.append("EMAIL_FROM_ADDRESS is not an email address")
        if self.support_email and "@" not in self.support_email:
            problems.append("SUPPORT_EMAIL is not an email address")
        return problems

    def _integration_credential_problems(self) -> list[str]:
        """Partly-configured integrations, which are worse than absent ones.

        An absent integration reports itself absent. A half-configured one
        fails at the first real call, on the first seller who needed it.
        """
        problems: list[str] = []

        r2_fields = {
            "R2_ENDPOINT_URL": self.r2_endpoint_url,
            "R2_BUCKET": self.r2_bucket,
            "R2_ACCESS_KEY_ID": self.r2_access_key_id,
            "R2_SECRET_ACCESS_KEY": self.r2_secret_access_key,
        }
        # A provisioned private bucket may wait for application credentials
        # without blocking core API/worker deployment. Partial credentials fail.
        if (self.r2_access_key_id or self.r2_secret_access_key) and not all(r2_fields.values()):
            missing = [name for name, value in r2_fields.items() if not value]
            problems.append(f"R2 is partly configured; missing {', '.join(missing)}")

        bkash_fields = {
            "BKASH_BASE_URL": self.bkash_base_url,
            "BKASH_APP_KEY": self.bkash_app_key,
            "BKASH_APP_SECRET": self.bkash_app_secret,
            "BKASH_USERNAME": self.bkash_username,
            "BKASH_PASSWORD": self.bkash_password,
        }
        if any(bkash_fields.values()) and not all(bkash_fields.values()):
            missing = [name for name, value in bkash_fields.items() if not value]
            problems.append(f"bKash is partly configured; missing {', '.join(missing)}")

        if self.fcm_credentials_json is not None and not self.fcm_project_id:
            problems.append("FCM_PROJECT_ID is required alongside FCM_CREDENTIALS_JSON")

        if bool(self.shopify_client_id) != bool(self.shopify_client_secret):
            problems.append("Shopify needs both SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET")
        meta_fields = {
            "META_APP_ID": self.meta_app_id,
            "META_APP_SECRET": self.meta_app_secret,
            "META_WEBHOOK_VERIFY_TOKEN": self.meta_webhook_verify_token,
        }
        if any(meta_fields.values()) and not all(meta_fields.values()):
            missing = [name for name, value in meta_fields.items() if not value]
            problems.append(f"Meta is partly configured; missing {', '.join(missing)}")

        return problems

    def _public_surface_problems(self) -> list[str]:
        """CORS and host rules for an environment reachable from the internet."""
        problems: list[str] = []

        if "*" in self.cors_allow_origins:
            problems.append(
                "CORS_ALLOW_ORIGINS must not contain '*' in a deployed environment. "
                "The API is served with allow_credentials=True, so a wildcard origin "
                "would let any site make authenticated requests on a seller's behalf"
            )
        local_origins = [origin for origin in self.cors_allow_origins if _is_loopback(origin)]
        if local_origins:
            problems.append(
                f"CORS_ALLOW_ORIGINS contains development origins {local_origins}; "
                "those belong in local configuration only"
            )
        insecure = [
            origin
            for origin in self.cors_allow_origins
            if origin.startswith("http://") and not _is_loopback(origin)
        ]
        if insecure and self.app_env.is_production:
            problems.append(f"CORS_ALLOW_ORIGINS must use https in production: {insecure}")

        return problems

    # ------------------------------------------------------------ helpers ---

    @property
    def dev_otp_enabled(self) -> bool:
        """True only when *both* development OTP guards agree, outside production."""
        return (
            self.phone_otp_login_enabled
            and self.otp_provider is OtpProvider.DEV_CONSOLE
            and self.allow_dev_otp
            and not self.app_env.is_production
        )

    @property
    def google_client_ids(self) -> tuple[str, ...]:
        """Every audience a Google identity token may legitimately carry."""
        return tuple(
            value
            for value in (
                self.google_client_id_android,
                self.google_client_id_ios,
                self.google_client_id_web,
            )
            if value
        )

    @property
    def google_auth_configured(self) -> bool:
        return self.google_auth_enabled and bool(self.google_client_ids)

    @property
    def apple_auth_configured(self) -> bool:
        # Identity-token verification uses Apple's public JWKS. Team/key ids and
        # a private key are only needed for a separate authorization-code exchange.
        return self.apple_auth_enabled and bool(self.apple_client_id)

    @property
    def email_transport_can_deliver(self) -> bool:
        """Whether the selected transport actually puts mail on the wire.

        ``console`` and ``mock`` are excluded on purpose: both report success
        without sending, which is the failure mode this property exists to stop
        anything from depending on.
        """
        return self.email_transport == "provider_api" and bool(
            self.email_api_base_url and self.email_api_key and self.email_from_address
        )

    @property
    def enabled_auth_methods(self) -> frozenset[str]:
        """Sign-in methods this deployment has switched on."""
        if self.supabase_auth_active:
            return frozenset({"email_password", "google", "apple"})
        selected = {
            "phone_otp": self.phone_otp_login_enabled,
            "email_password": self.email_password_auth_enabled,
            "google": self.google_auth_enabled,
            "apple": self.apple_auth_enabled,
        }
        return frozenset(name for name, on in selected.items() if on)

    @property
    def available_auth_methods(self) -> frozenset[str]:
        """Enabled methods a seller can actually use in this build."""
        return self.enabled_auth_methods & IMPLEMENTED_AUTH_METHODS

    @property
    def r2_configured(self) -> bool:
        return all(
            (
                self.r2_endpoint_url,
                self.r2_bucket,
                self.r2_access_key_id,
                self.r2_secret_access_key,
            )
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
