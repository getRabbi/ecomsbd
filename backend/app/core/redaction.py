"""Log and diagnostic redaction.

Master spec section 33 lists what must never reach a log: OTP codes, courier
credentials, bKash/Play secrets, customer phone numbers in plain text, and
access/refresh tokens. Section 101 additionally requires masked phone display
(``01712****78``) in UI and admin surfaces.

Redaction is applied centrally in the logging formatter rather than at each call
site, so a new feature module cannot leak a secret by forgetting to scrub it.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["REDACTED", "is_sensitive_key", "mask_phone", "redact_text", "redact_value"]

REDACTED = "[redacted]"

#: Substrings that mark a mapping key as sensitive. Matched case-insensitively.
_SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "auth_header",
    "credential",
    "private_key",
    "signing_key",
    "encryption_key",
    "hmac",
    "otp",
    "code_hash",
    "session_key",
    "refresh",
    "access_key",
    "cookie",
    "x-api-key",
    "purchase_token",
    "client_secret",
)

#: Keys holding a phone number: masked rather than removed, so support can still
#: correlate a case without the log becoming a phone-number dump.
_PHONE_KEY_PARTS: tuple[str, ...] = ("phone", "msisdn", "mobile", "recipient_number")

_BD_PHONE_RE = re.compile(r"(?:\+?880|0)1[3-9]\d{8}")
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-=/+]{8,}")
_LONG_SECRET_RE = re.compile(r"\b(?:sk|pk|key|tok)_[A-Za-z0-9]{12,}\b")


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _is_phone_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _PHONE_KEY_PARTS)


def mask_phone(value: str | None) -> str | None:
    """``+8801712345678`` -> ``01712****78`` (master spec section 101).

    Keeps enough digits for a seller to recognise their own customer while
    refusing to render a complete, dialable number into a log or admin screen.
    """
    if not value:
        return value
    digits = re.sub(r"\D", "", value)
    if len(digits) < 7:
        return "*" * len(digits)
    if digits.startswith("880"):
        digits = "0" + digits[3:]
    return f"{digits[:5]}****{digits[-2:]}"


def redact_text(value: str) -> str:
    """Scrub secrets and phone numbers that appear inside free text."""
    scrubbed = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", value)
    scrubbed = _LONG_SECRET_RE.sub(REDACTED, scrubbed)
    return _BD_PHONE_RE.sub(lambda m: mask_phone(m.group(0)) or REDACTED, scrubbed)


def redact_value(value: Any, *, key: str | None = None, _depth: int = 0) -> Any:
    """Recursively redact a value destined for a log line or support bundle."""
    if _depth > 6:
        return "[truncated]"

    if key is not None and is_sensitive_key(key):
        return REDACTED

    if isinstance(value, dict):
        return {str(k): redact_value(v, key=str(k), _depth=_depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact_value(v, key=key, _depth=_depth + 1) for v in value]
    if isinstance(value, str):
        if key is not None and _is_phone_key(key):
            return mask_phone(value)
        return redact_text(value)
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return value
