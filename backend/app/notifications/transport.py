"""Push, SMS and email transports.

Master spec sections 22 and 94, and the Phase F brief's sections 33–35.

Section 94's rule shapes the whole module: **push is a second copy, never the
only one.** Everything a seller needs to know already lives in the notification
centre, which is a table they can open. A transport failing is therefore a
degraded experience, not lost information — and that is why nothing here raises
into a business path.

Email is the third transport and is shaped the same way, but it is not a second
copy of anything: section 8 of the production-configuration brief puts email
verification, password reset and security notices on it, and each of those is
the *only* copy. That is why ``console`` — which writes the message to a log —
is refused in staging and production by :class:`~app.core.config.Settings`
rather than merely discouraged.

Push uses FCM HTTP v1; business email uses Resend. Supabase owns auth emails.

*   ``FCM_CREDENTIALS_REQUIRED`` — supply a Firebase service account to enable
    :class:`FcmPushTransport` with short-lived Google OAuth credentials.
*   ``SMS_PROVIDER_REQUIRED`` — no Bangladeshi gateway has been chosen, and
    section 22 forbids assuming segment counting or per-segment cost that has
    not been verified against the chosen provider. So
    :class:`SmsGatewayTransport` does the same.
*   ``TRANSACTIONAL_EMAIL_PROVIDER_REQUIRED`` — configure a Resend account,
    sending domain and API key for :class:`ProviderApiEmailTransport`.

Both refuse rather than pretend. A transport that returned "sent" without
sending would make the delivery metrics in section 49 a comfortable fiction.

Segment counting *is* implemented, because it is a property of GSM-03.38 and
UCS-2 rather than of any provider — but the result is treated as an estimate
until a provider returns its own count, which the delivery record then keeps.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import httpx
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.redaction import mask_email, mask_phone

__all__ = [
    "EMAIL_BLOCKER",
    "ConsoleEmailTransport",
    "DeliveryOutcome",
    "DisabledEmailTransport",
    "DisabledPushTransport",
    "DisabledSmsTransport",
    "EmailMessage",
    "EmailTransport",
    "FcmPushTransport",
    "MockEmailTransport",
    "MockPushTransport",
    "MockSmsTransport",
    "ProviderApiEmailTransport",
    "PushMessage",
    "PushTransport",
    "SmsGatewayTransport",
    "SmsMessage",
    "SmsTransport",
    "TransportResult",
    "build_email_transport",
    "build_push_transport",
    "build_sms_transport",
    "count_sms_segments",
]

log = get_logger(__name__)


class DeliveryOutcome(StrEnum):
    """What a transport did with a message."""

    SENT = "SENT"
    #: The provider rejected it permanently — a dead push token, a number that
    #: does not exist. Retrying is pointless and the token should be dropped.
    REJECTED = "REJECTED"
    #: Temporary. Retry with backoff.
    FAILED = "FAILED"
    #: No transport is configured in this deployment.
    NOT_CONFIGURED = "NOT_CONFIGURED"
    #: The recipient opted out, or alert fatigue rules suppressed it.
    SUPPRESSED = "SUPPRESSED"
    #: Recognised as already sent. Sending again would be a duplicate.
    DUPLICATE = "DUPLICATE"


@dataclass(frozen=True, slots=True)
class TransportResult:
    outcome: DeliveryOutcome
    provider: str
    #: The provider's own message id, when it gives one.
    provider_reference: str | None = None
    detail: str | None = None
    #: Segments the provider actually charged for, when it reports them.
    #: ``None`` means our estimate stands and is labelled as an estimate.
    provider_segments: int | None = None

    @property
    def delivered(self) -> bool:
        return self.outcome is DeliveryOutcome.SENT

    @property
    def retryable(self) -> bool:
        return self.outcome is DeliveryOutcome.FAILED


@dataclass(frozen=True, slots=True)
class PushMessage:
    """One push. Carries a deep link, never a money figure in the title."""

    token: str
    title: str
    body: str
    #: Section 94: opening a push must land on the exact order, payout or case.
    deep_link: str | None = None
    data: dict[str, str] = field(default_factory=dict)
    #: Deduplication key. A transport that sees the same one twice must not
    #: send twice.
    idempotency_key: str | None = None

    def redacted(self) -> dict[str, Any]:
        """What may be logged: never the token, never the body verbatim."""
        return {
            "title": self.title,
            "deep_link": self.deep_link,
            "token_suffix": self.token[-6:] if self.token else None,
            "body_length": len(self.body),
        }


@dataclass(frozen=True, slots=True)
class SmsMessage:
    """One SMS."""

    phone_e164: str
    body: str
    #: Sender id, where the gateway supports one. Configuration, never guessed.
    sender_id: str | None = None
    idempotency_key: str | None = None

    def redacted(self) -> dict[str, Any]:
        return {
            "phone": mask_phone(self.phone_e164),
            "sender_id": self.sender_id,
            "segments": count_sms_segments(self.body),
            "body_length": len(self.body),
        }


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """One transactional email.

    There is no bulk or marketing path here on purpose. Everything this
    transport carries is a consequence of something the recipient just did —
    verify this address, reset this password, a new device signed in — which is
    what keeps it out of the consent and unsubscribe machinery that marketing
    mail needs.
    """

    to_address: str
    subject: str
    #: Plain text. Always populated, even when ``html_body`` is too: a client
    #: that cannot render HTML must still be able to read a reset link.
    text_body: str
    html_body: str | None = None
    reply_to: str | None = None
    #: Deduplication key. A transport that sees the same one twice must not
    #: send twice — a seller receiving two reset links cannot tell which is live.
    idempotency_key: str | None = None

    def redacted(self) -> dict[str, Any]:
        """What may be logged.

        The address is masked and the bodies never appear. A verification or
        reset body *is* the credential — logging one would put an account
        takeover in the log file.
        """
        return {
            "to": mask_email(self.to_address),
            "subject": self.subject,
            "body_length": len(self.text_body),
            "has_html": self.html_body is not None,
        }


# --------------------------------------------------------------------------- #
# Segment counting
# --------------------------------------------------------------------------- #

#: The GSM 03.38 basic set. A message using only these is 7-bit encoded.
_GSM_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
#: These occupy two septets in GSM encoding.
_GSM_EXTENDED = set("^{}\\[~]|€")


def count_sms_segments(body: str) -> int:
    """Estimate how many SMS segments a body needs.

    GSM-03.38 gives 160 characters in one segment and 153 in each part of a
    concatenated message; a message containing anything outside that alphabet —
    **which every Bangla message does** — falls back to UCS-2 at 70 and 67.

    This is why the product writes its templates in Banglish: one Bangla
    character makes the whole message UCS-2, so a 100-character Bangla alert
    costs two segments where the same sentence in Banglish costs one.

    An estimate, deliberately. The provider's own count wins when it reports
    one, because segment accounting differs between gateways and section 22
    forbids assuming one that has not been verified.
    """
    if not body:
        return 0

    gsm_length = 0
    is_gsm = True
    for character in body:
        if character in _GSM_BASIC:
            gsm_length += 1
        elif character in _GSM_EXTENDED:
            gsm_length += 2
        else:
            is_gsm = False
            break

    if is_gsm:
        if gsm_length <= 160:
            return 1
        return -(-gsm_length // 153)

    length = len(body)
    if length <= 70:
        return 1
    return -(-length // 67)


# --------------------------------------------------------------------------- #
# Contracts
# --------------------------------------------------------------------------- #


class PushTransport(Protocol):
    """Provider-neutral push."""

    name: str

    @property
    def is_configured(self) -> bool: ...

    async def send(self, message: PushMessage) -> TransportResult: ...


class SmsTransport(Protocol):
    """Provider-neutral SMS."""

    name: str

    @property
    def is_configured(self) -> bool: ...

    async def send(self, message: SmsMessage) -> TransportResult: ...


class EmailTransport(Protocol):
    """Provider-neutral transactional email."""

    name: str

    @property
    def is_configured(self) -> bool: ...

    async def send(self, message: EmailMessage) -> TransportResult: ...


# --------------------------------------------------------------------------- #
# Implementations
# --------------------------------------------------------------------------- #


class DisabledPushTransport:
    """Records the attempt and sends nothing. The production default today.

    Not a no-op that lies: it returns ``NOT_CONFIGURED``, so the delivery record
    says plainly that nothing was sent and the section 49 metrics stay honest.
    """

    name = "disabled"

    @property
    def is_configured(self) -> bool:
        return False

    async def send(self, message: PushMessage) -> TransportResult:
        log.info("push not sent: no transport configured", extra=message.redacted())
        return TransportResult(
            outcome=DeliveryOutcome.NOT_CONFIGURED,
            provider=self.name,
            detail="FCM_CREDENTIALS_REQUIRED",
        )


class DisabledSmsTransport:
    name = "disabled"

    @property
    def is_configured(self) -> bool:
        return False

    async def send(self, message: SmsMessage) -> TransportResult:
        log.info("sms not sent: no transport configured", extra=message.redacted())
        return TransportResult(
            outcome=DeliveryOutcome.NOT_CONFIGURED,
            provider=self.name,
            detail="SMS_PROVIDER_REQUIRED",
        )


class MockPushTransport:
    """Test double. Accepts everything and remembers it.

    Refused in staging and production by :class:`~app.core.config.Settings`,
    because a mock that reports success is exactly what makes a delivery
    dashboard lie.
    """

    name = "mock"

    def __init__(self) -> None:
        self.sent: list[PushMessage] = []
        self.seen_keys: set[str] = set()

    @property
    def is_configured(self) -> bool:
        return True

    async def send(self, message: PushMessage) -> TransportResult:
        if message.idempotency_key and message.idempotency_key in self.seen_keys:
            return TransportResult(DeliveryOutcome.DUPLICATE, self.name)
        if message.idempotency_key:
            self.seen_keys.add(message.idempotency_key)
        self.sent.append(message)
        return TransportResult(
            DeliveryOutcome.SENT, self.name, provider_reference=f"mock-{len(self.sent)}"
        )


class MockSmsTransport:
    name = "mock"

    def __init__(self) -> None:
        self.sent: list[SmsMessage] = []
        self.seen_keys: set[str] = set()

    @property
    def is_configured(self) -> bool:
        return True

    async def send(self, message: SmsMessage) -> TransportResult:
        if message.idempotency_key and message.idempotency_key in self.seen_keys:
            return TransportResult(DeliveryOutcome.DUPLICATE, self.name)
        if message.idempotency_key:
            self.seen_keys.add(message.idempotency_key)
        self.sent.append(message)
        return TransportResult(
            DeliveryOutcome.SENT,
            self.name,
            provider_reference=f"mock-sms-{len(self.sent)}",
            provider_segments=count_sms_segments(message.body),
        )


_PUSH_TOKEN_RE = re.compile(r"^[A-Za-z0-9_:.\-]{20,4096}$")


class FcmPushTransport:
    """FCM HTTP v1 with scoped, cached Google OAuth and sanitized failures."""

    name = "fcm"

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client
        self._credentials: Any = None
        self._refresh_lock = asyncio.Lock()
        if settings.fcm_credentials_json is not None:
            try:
                info = json.loads(settings.fcm_credentials_json.get_secret_value())
                if (
                    info.get("type") != "service_account"
                    or info.get("project_id") != settings.fcm_project_id
                    or info.get("token_uri") != "https://oauth2.googleapis.com/token"
                    or not re.fullmatch(r"[a-z][a-z0-9-]{4,62}", settings.fcm_project_id or "")
                ):
                    raise ValueError("Invalid FCM service account")
                self._credentials = service_account.Credentials.from_service_account_info(
                    info, scopes=["https://www.googleapis.com/auth/firebase.messaging"]
                )
            except (ValueError, TypeError, KeyError, AttributeError):
                raise ValueError(
                    "FCM_CREDENTIALS_JSON must be a valid matching service account"
                ) from None

    async def access_token(self) -> str:
        """Authenticate without sending a notification; also used by deployment checks."""
        async with self._refresh_lock:
            if self._credentials is None:
                raise ValueError("FCM credentials are not configured")
            if not self._credentials.valid:

                def refresh() -> None:
                    request = GoogleAuthRequest()
                    try:
                        self._credentials.refresh(lambda **kw: request(timeout=15, **kw))
                    finally:
                        request.session.close()

                await asyncio.to_thread(refresh)
            return str(self._credentials.token)

    @property
    def is_configured(self) -> bool:
        return self._credentials is not None

    async def send(self, message: PushMessage) -> TransportResult:
        problem = self._validate(message)
        if problem is not None:
            return TransportResult(DeliveryOutcome.REJECTED, self.name, detail=problem)
        if not self.is_configured:
            return TransportResult(
                DeliveryOutcome.NOT_CONFIGURED,
                self.name,
                detail="FCM_CREDENTIALS_REQUIRED: set FCM_PROJECT_ID and FCM_CREDENTIALS_JSON",
            )
        data = dict(message.data)
        if message.deep_link:
            data["deep_link"] = message.deep_link
        payload = {
            "message": {
                "token": message.token,
                "notification": {"title": message.title, "body": message.body},
                "data": data,
            }
        }
        try:
            token = await self.access_token()
            url = f"https://fcm.googleapis.com/v1/projects/{self._settings.fcm_project_id}/messages:send"
            headers = {"Authorization": f"Bearer {token}"}
            if self._client is not None:
                response = await self._client.post(url, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                reference = response.json().get("name")
                if isinstance(reference, str) and reference.startswith("projects/"):
                    return TransportResult(
                        DeliveryOutcome.SENT, self.name, provider_reference=reference
                    )
            # Only explicit token/payload rejections are permanent. Auth/configuration
            # and provider failures remain retryable; never persist raw response bodies.
            code = ""
            try:
                for detail in response.json().get("error", {}).get("details", []):
                    if detail.get("@type") == "type.googleapis.com/google.firebase.fcm.v1.FcmError":
                        code = detail.get("errorCode", "")
            except (ValueError, TypeError, AttributeError):
                pass
            permanent = code in {"UNREGISTERED", "INVALID_ARGUMENT", "SENDER_ID_MISMATCH"}
            return TransportResult(
                DeliveryOutcome.REJECTED if permanent else DeliveryOutcome.FAILED,
                self.name,
                detail=f"FCM request failed (HTTP {response.status_code})",
            )
        except (httpx.HTTPError, GoogleAuthError, ValueError, TypeError):
            return TransportResult(
                DeliveryOutcome.FAILED, self.name, detail="FCM request unavailable"
            )

    @staticmethod
    def _validate(message: PushMessage) -> str | None:
        if not message.token or not _PUSH_TOKEN_RE.match(message.token):
            return "the device push token is not a plausible FCM token"
        if not message.title.strip():
            return "a push needs a title"
        if len(message.title) > 100:
            return "the title is longer than a notification shade will show"
        if len(message.body) > 500:
            return "the body is too long for a push"
        if any(not isinstance(v, str) for v in message.data.values()):
            return "FCM data values must all be strings"
        return None


class SmsGatewayTransport:
    """A Bangladeshi SMS gateway.

    ``SMS_PROVIDER_REQUIRED``. No provider has been chosen, so no endpoint,
    sender-id rule or segment-pricing model is encoded here — section 22 is
    explicit that those must not be assumed. What *is* here is the payload
    validation and the segment estimate, both of which are properties of the
    message rather than of any gateway.
    """

    name = "sms_gateway"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def is_configured(self) -> bool:
        return self._settings.otp_provider_secret is not None

    async def send(self, message: SmsMessage) -> TransportResult:
        problem = self._validate(message)
        if problem is not None:
            return TransportResult(DeliveryOutcome.REJECTED, self.name, detail=problem)
        if not self.is_configured:
            return TransportResult(
                DeliveryOutcome.NOT_CONFIGURED,
                self.name,
                detail="SMS_PROVIDER_REQUIRED: no gateway has been selected",
            )
        return TransportResult(
            DeliveryOutcome.NOT_CONFIGURED,
            self.name,
            detail="No SMS client is wired in; the payload validated successfully.",
        )

    @staticmethod
    def _validate(message: SmsMessage) -> str | None:
        from app.common.phone import try_normalize_bd_phone

        if try_normalize_bd_phone(message.phone_e164) is None:
            return "not a valid Bangladeshi mobile number"
        if not message.body.strip():
            return "an SMS needs a body"
        segments = count_sms_segments(message.body)
        if segments > 4:
            # Four segments is roughly 600 Banglish characters. Beyond that the
            # cost stops being proportionate to any alert this product sends,
            # and it is far more likely to be a templating bug.
            return f"this message would cost {segments} segments; that is a bug, not an alert"
        return None


_EMAIL_RE = re.compile(r"^[^@\s,;]{1,64}@[A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,}$")

#: The blocker an unconfigured email path reports. One string, so the operator
#: sees the same token in a delivery record, in the config check and in
#: docs/RELEASE_READINESS.md.
EMAIL_BLOCKER = "TRANSACTIONAL_EMAIL_PROVIDER_REQUIRED"


def _validate_email(message: EmailMessage) -> str | None:
    """Payload checks that are true of email itself, not of any provider."""
    if not _EMAIL_RE.match(message.to_address):
        return "the recipient is not a valid email address"
    if not message.subject.strip():
        return "an email needs a subject"
    if "\n" in message.subject or "\r" in message.subject:
        # A newline in a header field is header injection: it lets whatever
        # built the subject append a Bcc of its own.
        return "the subject must not contain a line break"
    if not message.text_body.strip():
        return "an email needs a plain-text body"
    if message.reply_to is not None and not _EMAIL_RE.match(message.reply_to):
        return "the reply-to is not a valid email address"
    return None


class DisabledEmailTransport:
    """Records the attempt and sends nothing. The shipped default.

    Returns ``NOT_CONFIGURED`` rather than pretending, for the same reason as
    the push and SMS defaults: a caller must be able to tell "we did not send
    this" from "we sent this", and a password-reset flow that cannot tell the
    difference will tell a locked-out seller to check their inbox forever.
    """

    name = "disabled"

    @property
    def is_configured(self) -> bool:
        return False

    async def send(self, message: EmailMessage) -> TransportResult:
        log.info("email not sent: no transport configured", extra=message.redacted())
        return TransportResult(
            outcome=DeliveryOutcome.NOT_CONFIGURED,
            provider=self.name,
            detail=EMAIL_BLOCKER,
        )


class ConsoleEmailTransport:
    """Writes the message to the log. Local development only.

    Refused in staging and production by :class:`~app.core.config.Settings`.
    The body of a verification or reset email *is* a credential, so a transport
    that prints it is a transport that publishes account takeovers to whoever
    can read the log — which in a deployed environment is more people than can
    read the inbox.
    """

    name = "console"

    @property
    def is_configured(self) -> bool:
        return True

    async def send(self, message: EmailMessage) -> TransportResult:
        problem = _validate_email(message)
        if problem is not None:
            return TransportResult(DeliveryOutcome.REJECTED, self.name, detail=problem)
        log.warning(
            "EMAIL (console transport — not sent):\n  to: %s\n  subject: %s\n\n%s",
            message.to_address,
            message.subject,
            message.text_body,
        )
        return TransportResult(
            DeliveryOutcome.SENT, self.name, provider_reference="console", detail="not delivered"
        )


class MockEmailTransport:
    """Test double. Accepts everything and remembers it."""

    name = "mock"

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        self.seen_keys: set[str] = set()

    @property
    def is_configured(self) -> bool:
        return True

    async def send(self, message: EmailMessage) -> TransportResult:
        problem = _validate_email(message)
        if problem is not None:
            return TransportResult(DeliveryOutcome.REJECTED, self.name, detail=problem)
        if message.idempotency_key and message.idempotency_key in self.seen_keys:
            return TransportResult(DeliveryOutcome.DUPLICATE, self.name)
        if message.idempotency_key:
            self.seen_keys.add(message.idempotency_key)
        self.sent.append(message)
        return TransportResult(
            DeliveryOutcome.SENT, self.name, provider_reference=f"mock-email-{len(self.sent)}"
        )


class ProviderApiEmailTransport:
    """Resend's REST API, using the existing EMAIL_* configuration.

    Contract: https://resend.com/docs/api-reference/emails/send-email
    Checked 2026-09-12. No redirects, automatic retries or response-body logs:
    the request carries both an API key and a live account recovery token.
    """

    name = "provider_api"

    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._transport = transport

    @property
    def is_configured(self) -> bool:
        return bool(
            self._settings.email_api_base_url
            and self._settings.email_api_key is not None
            and self._settings.email_from_address
        )

    async def send(self, message: EmailMessage) -> TransportResult:
        problem = _validate_email(message)
        if problem is not None:
            return TransportResult(DeliveryOutcome.REJECTED, self.name, detail=problem)
        if not self.is_configured:
            return TransportResult(
                DeliveryOutcome.NOT_CONFIGURED,
                self.name,
                detail=(
                    f"{EMAIL_BLOCKER}: set EMAIL_API_BASE_URL, EMAIL_API_KEY and EMAIL_FROM_ADDRESS"
                ),
            )
        settings = self._settings
        if settings.email_api_key is None:
            return TransportResult(DeliveryOutcome.NOT_CONFIGURED, self.name, detail=EMAIL_BLOCKER)
        sender = settings.email_from_address or ""
        sender_name = settings.email_from_name or ""
        if not _EMAIL_RE.fullmatch(sender) or any(c in sender_name for c in '\r\n<>"'):
            return TransportResult(DeliveryOutcome.REJECTED, self.name, detail="invalid sender")
        payload: dict[str, Any] = {
            "from": f"{sender_name} <{sender}>" if sender_name else sender,
            "to": [message.to_address],
            "subject": message.subject,
            "text": message.text_body,
        }
        if message.html_body is not None:
            payload["html"] = message.html_body
        if message.reply_to is not None:
            payload["reply_to"] = message.reply_to
        headers = {"Authorization": f"Bearer {settings.email_api_key.get_secret_value()}"}
        if message.idempotency_key:
            headers["Idempotency-Key"] = message.idempotency_key
        try:
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=False, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{(settings.email_api_base_url or '').rstrip('/')}/emails",
                    headers=headers,
                    json=payload,
                )
            if not response.is_success:
                outcome = (
                    DeliveryOutcome.FAILED
                    if response.status_code in (408, 429) or response.status_code >= 500
                    else DeliveryOutcome.REJECTED
                )
                return TransportResult(outcome, self.name, detail=f"HTTP {response.status_code}")
            body = response.json()
            reference = body.get("id") if isinstance(body, dict) else None
            if not isinstance(reference, str) or not reference:
                return TransportResult(
                    DeliveryOutcome.FAILED, self.name, detail="provider returned no message id"
                )
            return TransportResult(DeliveryOutcome.SENT, self.name, provider_reference=reference)
        except (httpx.HTTPError, ValueError):
            return TransportResult(
                DeliveryOutcome.FAILED, self.name, detail="email provider request failed"
            )


def build_email_transport(settings: Settings) -> EmailTransport:
    match settings.email_transport:
        case "provider_api":
            return ProviderApiEmailTransport(settings)
        case "console":
            return ConsoleEmailTransport()
        case "mock":
            return MockEmailTransport()
        case _:
            return DisabledEmailTransport()


def build_push_transport(settings: Settings) -> PushTransport:
    match settings.push_transport:
        case "fcm":
            return FcmPushTransport(settings)
        case "mock":
            return MockPushTransport()
        case _:
            return DisabledPushTransport()


def build_sms_transport(settings: Settings) -> SmsTransport:
    match settings.sms_transport:
        case "sms_gateway":
            return SmsGatewayTransport(settings)
        case "mock":
            return MockSmsTransport()
        case _:
            return DisabledSmsTransport()
