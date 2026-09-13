"""OTP delivery providers.

Only one provider ships in Phase A, and it deliberately does not send anything:
:class:`DevConsoleOtpProvider` records the code locally so a developer can sign
in without an SMS gateway contract.

Selecting a real gateway requires provider-specific facts this project does not
have yet — encoding rules, segment cost, sender-ID registration, delivery
receipts (master spec sections 22, 131). Guessing them would produce code that
looks finished and fails on the first real message, so :class:`SmsGatewayOtpProvider`
is an explicit ``UNVERIFIED`` placeholder that refuses to run.

**Three independent guards** keep the development provider out of production:

1.  ``Settings`` rejects ``OTP_PROVIDER=dev_console`` when ``APP_ENV=production``;
2.  ``Settings`` also rejects ``ALLOW_DEV_OTP=true`` there;
3.  the provider itself re-checks the environment at construction *and* at send
    time, so it cannot be instantiated by a code path that bypassed config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.auth.models import OtpDeliveryStatus
from app.core.config import AppEnv, OtpProvider, Settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger

__all__ = [
    "DevConsoleOtpProvider",
    "OtpDeliveryResult",
    "OtpSender",
    "SmsGatewayOtpProvider",
    "build_otp_provider",
]

log = get_logger(__name__)


class ProductionSafetyError(RuntimeError):
    """Raised when a development-only component is reached in production."""


@dataclass(frozen=True, slots=True)
class OtpDeliveryResult:
    """Outcome of an OTP send attempt."""

    status: OtpDeliveryStatus
    provider: str
    reference: str | None = None
    error: str | None = None
    #: Populated only by the development provider, and only when the settings
    #: explicitly allow echoing the code back. Never set in production.
    debug_code: str | None = None

    @property
    def delivered(self) -> bool:
        return self.status in (OtpDeliveryStatus.SENT, OtpDeliveryStatus.DEV_LOGGED)


class OtpSender(Protocol):
    """Contract every OTP delivery channel implements."""

    name: str

    async def send(self, *, phone_e164: str, code: str, masked_phone: str) -> OtpDeliveryResult: ...


class DevConsoleOtpProvider:
    """Development-only provider. Records the code; sends nothing.

    Anyone who can read the application log can sign in as any phone number, so
    this must never run anywhere real data lives.
    """

    name = "dev_console"

    def __init__(self, settings: Settings) -> None:
        self._assert_safe(settings)
        self._settings = settings

    @staticmethod
    def _assert_safe(settings: Settings) -> None:
        if settings.app_env is AppEnv.PRODUCTION:
            raise ProductionSafetyError(
                "DevConsoleOtpProvider must never be constructed in production"
            )
        if not settings.allow_dev_otp:
            raise ProductionSafetyError("DevConsoleOtpProvider requires ALLOW_DEV_OTP=true")

    async def send(self, *, phone_e164: str, code: str, masked_phone: str) -> OtpDeliveryResult:
        # Re-checked at send time: configuration can be reloaded at runtime, and
        # this is the last point before a code would be exposed.
        self._assert_safe(self._settings)

        log.warning(
            "DEVELOPMENT OTP issued (no SMS sent)",
            extra={
                "provider": self.name,
                "masked_phone": masked_phone,
                # Named so the redaction filter cannot mistake it for a secret
                # to scrub: exposing it is this provider's entire purpose, and
                # it is unreachable outside local and test environments.
                "development_only_code": code,
            },
        )
        return OtpDeliveryResult(
            status=OtpDeliveryStatus.DEV_LOGGED,
            provider=self.name,
            debug_code=code if self._settings.expose_otp_debug_code else None,
        )


class SmsGatewayOtpProvider:
    """UNVERIFIED. Placeholder for the real Bangladeshi SMS gateway.

    Implementing this requires, from the operator:

    *   the chosen gateway and its current API documentation;
    *   registered sender ID / masking approval;
    *   the gateway's own segment-count and cost rules (master spec section 131
        forbids hard-coding a segment size without running the gateway's
        calculation);
    *   delivery-report callback format.

    Until then it raises rather than pretending to deliver, because an OTP that
    silently fails looks exactly like an OTP that was never requested.
    """

    name = "sms_gateway"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, *, phone_e164: str, code: str, masked_phone: str) -> OtpDeliveryResult:
        raise AppError(
            "No SMS gateway is configured. OTP delivery is unavailable until a "
            "provider is selected and verified.",
            code=ErrorCode.OTP_DELIVERY_FAILED,
        )


def build_otp_provider(settings: Settings) -> OtpSender:
    """Select the configured provider, refusing unsafe combinations."""
    if settings.supabase_auth_active or not settings.phone_otp_login_enabled:
        return DisabledOtpProvider()
    if settings.otp_provider is OtpProvider.DEV_CONSOLE:
        if settings.app_env is AppEnv.PRODUCTION:  # pragma: no cover - config blocks this first
            raise ProductionSafetyError("OTP_PROVIDER=dev_console is not permitted in production")
        return DevConsoleOtpProvider(settings)
    return SmsGatewayOtpProvider(settings)


class DisabledOtpProvider:
    name = "disabled"

    async def send(self, *, phone_e164: str, code: str, masked_phone: str) -> OtpDeliveryResult:
        raise AppError("Phone login is disabled", code=ErrorCode.FEATURE_DISABLED)
