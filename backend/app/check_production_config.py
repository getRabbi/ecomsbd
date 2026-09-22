"""Production configuration check.

Run before a deploy, and again after filling in ``.env.production.local``::

    python -m app.check_production_config
    python -m app.check_production_config --env-file ../.env.production.local

It answers one question per line: *is this part of the system configured, and
if not, does that stop the deploy?* Three rules shape it.

**It never prints a secret.** Not truncated, not first-four-characters. Every
line reports presence, shape or a count — never a value — because a
configuration check is exactly the command an operator runs with someone
watching their screen, and its output is exactly what gets pasted into a chat.

**A disabled integration is not a failure.** Master spec section 140's rule
holds here too: credentials are required for what is switched on, and demanding
a bKash merchant key from a deployment that has bKash off would train an
operator to fill in values they do not need.

**Exit status is about boot safety, not completeness.** Non-zero means this
configuration is unsafe or would not start — a missing crypto key, a wildcard
CORS origin in production, nobody able to sign in. Missing Sentry, missing R2,
missing push credentials all exit zero: the system runs without them, honestly
degraded, and says so on its own line.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.core.config import IMPLEMENTED_AUTH_METHODS, AppEnv, Settings

__all__ = ["CheckResult", "Status", "main", "run_checks"]

#: Width of the dotted leader, so every status starts in the same column.
_LABEL_WIDTH = 24


class Status(StrEnum):
    """What one area of the configuration is.

    ``BLOCKING`` is the only value that changes the exit code. The difference
    between it and ``MISSING`` is whether the system can run without the thing:
    a missing session key cannot be worked around, a missing Sentry DSN can.
    """

    OK = "OK"
    #: Configured as far as this repository can take it, with a known gap
    #: behind it — an integration whose client is not written yet.
    PARTIAL = "PARTIAL"
    #: Deliberately switched off. Requires nothing.
    DISABLED = "DISABLED"
    #: Not configured; the system runs without it in a degraded but honest way.
    MISSING = "MISSING"
    #: Not configured, and nothing here needs it to be.
    OPTIONAL = "OPTIONAL/MISSING"
    #: Unsafe or unbootable. Exits non-zero.
    BLOCKING = "BLOCKING"


@dataclass(frozen=True, slots=True)
class CheckResult:
    area: str
    status: Status
    #: One line of explanation. Never contains a configured value.
    detail: str = ""

    @property
    def blocking(self) -> bool:
        return self.status is Status.BLOCKING

    def render(self) -> str:
        leader = "." * max(4, _LABEL_WIDTH - len(self.area))
        line = f"{self.area} {leader} {self.status.value}"
        return f"{line}\n{' ' * (_LABEL_WIDTH + 2)}{self.detail}" if self.detail else line


def _check_database(settings: Settings) -> CheckResult:
    if not settings.database_url:
        return CheckResult("DATABASE", Status.BLOCKING, "DATABASE_URL is not set")
    if settings.is_sqlite:
        status = Status.BLOCKING if settings.app_env.is_deployed else Status.OK
        return CheckResult("DATABASE", status, "SQLite - local and test only")
    driver = settings.database_url.split("://", 1)[0]
    if "asyncpg" not in driver:
        return CheckResult(
            "DATABASE",
            Status.BLOCKING,
            f"driver is '{driver}'; the application needs postgresql+asyncpg",
        )
    return CheckResult(
        "DATABASE",
        Status.OK,
        f"postgresql+asyncpg, pool {settings.database_pool_size}+{settings.database_max_overflow}",
    )


def _check_redis(settings: Settings) -> CheckResult:
    if not settings.redis_url:
        if settings.app_env.is_deployed:
            return CheckResult(
                "REDIS", Status.BLOCKING, "required outside local/test; the worker cannot run"
            )
        return CheckResult("REDIS", Status.MISSING, "in-process cache fallback (local only)")
    scheme = settings.redis_url.split("://", 1)[0]
    detail = f"{scheme}://, queue '{settings.worker_queue_name}'"
    if settings.app_env.is_production and scheme == "redis":
        detail += " - plaintext; use rediss:// if the hop is not private"
    return CheckResult("REDIS", Status.OK, detail)


def _check_crypto(settings: Settings) -> CheckResult:
    """Presence and independence of the three backend key materials.

    Reuse is checked as well as presence. Three distinct secrets that happen to
    hold the same string are one secret, and a leak of it would compromise
    admin tokens, the courier vault and phone lookup together.
    """
    values = {
        "JWT_SIGNING_KEY": settings.jwt_signing_key.get_secret_value(),
        "PHONE_SEARCH_HMAC_KEY": settings.phone_search_hmac_key.get_secret_value(),
        "CREDENTIAL_ENCRYPTION_KEY": settings.credential_encryption_key.get_secret_value(),
    }
    if len(set(values.values())) != len(values):
        return CheckResult(
            "CRYPTO",
            Status.BLOCKING,
            "two or more key materials hold the same value; they must be independent",
        )
    return CheckResult(
        "CRYPTO",
        Status.OK,
        f"{len(values)} independent keys, versions "
        f"vault={settings.credential_key_version}; phone lookup key configured",
    )


def _check_auth(settings: Settings) -> CheckResult:
    if settings.supabase_auth_active:
        return CheckResult(
            "SIGN-IN",
            Status.OK,
            "Supabase Auth only; Android email/password + Google; Apple deferred",
        )
    enabled = sorted(settings.enabled_auth_methods)
    available = sorted(settings.available_auth_methods)
    if not enabled:
        return CheckResult("SIGN-IN", Status.BLOCKING, "every sign-in method is disabled")
    if not available:
        return CheckResult(
            "SIGN-IN",
            Status.BLOCKING,
            f"enabled {enabled} but none is implemented in this build "
            f"(implemented: {sorted(IMPLEMENTED_AUTH_METHODS)}) - "
            "SELLER_AUTH_IMPLEMENTATION_REQUIRED",
        )
    unusable = sorted(set(enabled) - set(available))
    if unusable:
        return CheckResult(
            "SIGN-IN",
            Status.PARTIAL,
            f"usable: {available}; enabled but not implemented: {unusable}",
        )
    return CheckResult("SIGN-IN", Status.OK, f"usable: {available}")


def _check_email(settings: Settings) -> CheckResult:
    if settings.supabase_auth_active:
        return CheckResult(
            "AUTH EMAIL", Status.PARTIAL, "Configure and verify Supabase SMTP in Dashboard"
        )
    transport = settings.email_transport
    if transport == "disabled":
        status = (
            Status.BLOCKING
            if settings.email_password_auth_enabled and settings.app_env.is_deployed
            else Status.MISSING
        )
        return CheckResult("EMAIL", status, "no transport - TRANSACTIONAL_EMAIL_PROVIDER_REQUIRED")
    if transport in ("console", "mock"):
        status = Status.BLOCKING if settings.app_env.is_deployed else Status.OK
        return CheckResult("EMAIL", status, f"{transport} transport - sends nothing")
    if not settings.email_transport_can_deliver:
        return CheckResult(
            "EMAIL",
            Status.BLOCKING if settings.email_password_auth_enabled else Status.MISSING,
            "EMAIL_TRANSPORT=provider_api but base URL, key or from-address is missing",
        )
    return CheckResult(
        "EMAIL",
        Status.OK,
        "Resend API transport configured; verify delivery with a real inbox",
    )


def _check_google_auth(settings: Settings) -> CheckResult:
    if settings.supabase_auth_active:
        return CheckResult(
            "GOOGLE AUTH", Status.PARTIAL, "Supabase provider; verify Dashboard configuration"
        )
    if not settings.google_auth_enabled:
        return CheckResult("GOOGLE AUTH", Status.DISABLED, "GOOGLE_AUTH_ENABLED=false")
    if not settings.google_client_ids:
        return CheckResult("GOOGLE AUTH", Status.BLOCKING, "enabled with no client id configured")
    return CheckResult(
        "GOOGLE AUTH",
        Status.OK,
        f"{len(settings.google_client_ids)} audience(s); identity tokens are "
        "verified server-side against Google's JWKS",
    )


def _check_apple_auth(settings: Settings) -> CheckResult:
    if settings.supabase_auth_active:
        return CheckResult(
            "APPLE AUTH", Status.DISABLED, "Deferred until iOS; not required for Android launch"
        )
    if not settings.apple_auth_enabled:
        return CheckResult("APPLE AUTH", Status.DISABLED, "APPLE_AUTH_ENABLED=false")
    if not settings.apple_auth_configured:
        return CheckResult("APPLE AUTH", Status.BLOCKING, "enabled with no client id configured")
    return CheckResult(
        "APPLE AUTH",
        Status.OK,
        "identity tokens are verified server-side against Apple's JWKS",
    )


def _check_phone_otp(settings: Settings) -> CheckResult:
    if not settings.phone_otp_login_enabled:
        return CheckResult("PHONE OTP", Status.DISABLED, "deferred - PHONE_OTP_LOGIN_ENABLED=false")
    if settings.dev_otp_enabled:
        return CheckResult(
            "PHONE OTP", Status.MISSING, "development provider - codes go to the log, not an SMS"
        )
    if settings.otp_provider_secret is None:
        return CheckResult(
            "PHONE OTP", Status.BLOCKING, "no gateway secret - SMS_PROVIDER_REQUIRED"
        )
    return CheckResult("PHONE OTP", Status.OK, f"provider {settings.otp_provider}")


def _check_steadfast(settings: Settings) -> CheckResult:
    """Steadfast needs no platform credential, by design.

    Merchant keys live encrypted per courier account in the database, one per
    seller. There is deliberately nothing here to fill in: a single global key
    in the environment would mean every shop's parcels booked on one account.
    """
    if settings.steadfast_webhook_enabled:
        return CheckResult(
            "STEADFAST",
            Status.BLOCKING,
            "webhook enabled with no verified signature contract "
            "(STEADFAST_WEBHOOK_CONTRACT_REQUIRED)",
        )
    return CheckResult(
        "STEADFAST",
        Status.OK,
        "READY FOR MERCHANT ACCOUNT - per-seller credentials, encrypted in the DB; "
        "rollout is the 'steadfast_enabled' runtime flag",
    )


def _check_r2(settings: Settings) -> CheckResult:
    if not settings.r2_configured:
        return CheckResult("R2", Status.MISSING, "payout files and exports stay in database rows")
    return CheckResult(
        "R2", Status.OK, "private R2 client configured; verify connectivity separately"
    )


def _check_fcm(settings: Settings) -> CheckResult:
    if settings.push_transport != "fcm":
        return CheckResult(
            "FCM", Status.DISABLED, f"PUSH_TRANSPORT={settings.push_transport} - nothing is sent"
        )
    if settings.fcm_credentials_json is None or not settings.fcm_project_id:
        return CheckResult("FCM", Status.BLOCKING, "PUSH_TRANSPORT=fcm with credentials missing")
    from app.notifications.transport import FcmPushTransport

    try:
        FcmPushTransport(settings)
    except ValueError:
        return CheckResult("FCM", Status.BLOCKING, "invalid or mismatched FCM service account")
    return CheckResult("FCM", Status.OK, "FCM HTTP v1 initialized; verify OAuth separately")


def _check_sentry(settings: Settings) -> CheckResult:
    if settings.sentry_dsn is None:
        return CheckResult("SENTRY", Status.OPTIONAL, "crashes will be invisible")
    environment = settings.sentry_environment or str(settings.app_env)
    release = settings.sentry_release or "derived from the package version"
    return CheckResult("SENTRY", Status.OK, f"environment={environment}, release={release}")


def _check_play_billing(settings: Settings) -> CheckResult:
    if not settings.play_package_name and settings.play_service_account_json is None:
        return CheckResult(
            "PLAY BILLING",
            Status.DISABLED,
            "unconfigured; the 'play_billing_enabled' runtime flag gates it too",
        )
    if not settings.play_billing_configured:
        return CheckResult(
            "PLAY BILLING",
            Status.MISSING,
            "partly configured - needs package name, service account and product map",
        )
    if settings.play_package_name and settings.play_package_name.startswith("com.example"):
        return CheckResult("PLAY BILLING", Status.BLOCKING, "package name is still a placeholder")
    return CheckResult(
        "PLAY BILLING",
        Status.PARTIAL,
        f"{len(settings.play_product_plan_map)} product(s) mapped; no Play API client is wired in",
    )


def _check_bkash(settings: Settings) -> CheckResult:
    if not settings.bkash_billing_configured:
        return CheckResult("BKASH", Status.DISABLED, "BKASH_MERCHANT_SETUP_REQUIRED")
    return CheckResult("BKASH", Status.PARTIAL, "credentials present; no bKash client is wired in")


def _check_admin(settings: Settings) -> CheckResult:
    count = len(settings.admin_api_tokens)
    if count == 0:
        return CheckResult(
            "ADMIN", Status.OK, "no bootstrap token; provision platform_admins rows instead"
        )
    weak = [token for token in settings.admin_api_tokens if len(token) < 32]
    if weak and settings.app_env.is_deployed:
        return CheckResult("ADMIN", Status.BLOCKING, f"{len(weak)} bootstrap token(s) too short")
    return CheckResult(
        "ADMIN",
        Status.OK if not settings.app_env.is_production else Status.PARTIAL,
        f"{count} bootstrap token(s) - shared, unrevokable without a redeploy",
    )


def _check_urls(settings: Settings) -> CheckResult:
    """URLs, CORS and the host allow-list.

    Most of this is already enforced by ``Settings`` — a wildcard origin in a
    deployed environment stops the boot, so it cannot reach here. What is left
    for this check is the one case that is a judgement rather than a rule: an
    empty ``TRUSTED_HOSTS``, which is correct behind a proxy that checks the
    Host header and wrong when the app is exposed directly. It cannot be told
    which is true, so it reports rather than refuses.
    """
    if not settings.public_base_url:
        return CheckResult("URLS / CORS", Status.BLOCKING, "PUBLIC_BASE_URL is not set")
    if "*" in settings.cors_allow_origins:
        return CheckResult("URLS / CORS", Status.BLOCKING, "CORS allows '*'")
    if settings.app_env.is_deployed and not settings.trusted_hosts:
        return CheckResult(
            "URLS / CORS",
            Status.MISSING,
            "TRUSTED_HOSTS is empty - only safe behind a proxy that checks the Host header",
        )
    return CheckResult(
        "URLS / CORS",
        Status.OK,
        f"{len(settings.cors_allow_origins)} CORS origin(s), "
        f"{len(settings.trusted_hosts)} trusted host(s)",
    )


#: Every check, in the order an operator works through them.
_CHECKS = (
    _check_database,
    _check_redis,
    _check_crypto,
    _check_auth,
    _check_email,
    _check_google_auth,
    _check_apple_auth,
    _check_phone_otp,
    _check_steadfast,
    _check_r2,
    _check_fcm,
    _check_sentry,
    _check_play_billing,
    _check_bkash,
    _check_admin,
    _check_urls,
)


def run_checks(settings: Settings) -> list[CheckResult]:
    """Every check against an already-valid :class:`Settings`."""
    return [check(settings) for check in _CHECKS]


def _load_settings(env_file: Path | None, app_env: str | None) -> Settings:
    overrides: dict[str, Any] = {}
    if env_file is not None:
        if not env_file.is_file():
            raise SystemExit(f"no such env file: {env_file}")
        overrides["_env_file"] = env_file
    if app_env is not None:
        overrides["app_env"] = AppEnv(app_env)
    return Settings(**overrides)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.check_production_config",
        description="Report whether this deployment's configuration is complete and safe.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="read this file instead of ./.env (real environment variables still win)",
    )
    parser.add_argument(
        "--env",
        dest="app_env",
        choices=[str(value) for value in AppEnv],
        default=None,
        help="check as if APP_ENV were this, without exporting it",
    )
    args = parser.parse_args(argv)

    try:
        settings = _load_settings(args.env_file, args.app_env)
    except PydanticValidationError as exc:
        # The configuration is not merely incomplete: it would not start.
        #
        # Only the `msg` of each error is printed. `str(exc)` would also render
        # an `input_value=` repr of what was rejected, and for a field like
        # DATABASE_URL that repr is a connection string with a password in it.
        # Pydantic happens to truncate long ones, which is not a guarantee —
        # and the hand-written messages in app/core/config.py are the part
        # worth reading anyway.
        print("CONFIGURATION INVALID - this process would refuse to start.\n")
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "configuration"
            print(f"  {location}: {error['msg']}")
        return 1

    results = run_checks(settings)
    blocking = [result for result in results if result.blocking]

    print(f"ecomsbd configuration check - APP_ENV={settings.app_env}")
    if args.env_file is not None:
        print(f"source: {args.env_file}")
    print()
    for result in results:
        print(result.render())
    print()

    if blocking:
        print(f"{len(blocking)} blocking problem(s): {', '.join(r.area for r in blocking)}")
        print("This configuration is not safe to deploy.")
        return 1

    incomplete = [r for r in results if r.status in (Status.MISSING, Status.PARTIAL)]
    if incomplete:
        print(f"No blocking problems. {len(incomplete)} area(s) incomplete or degraded:")
        for result in incomplete:
            print(f"  - {result.area}: {result.detail}")
    else:
        print("No blocking problems.")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
