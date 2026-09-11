"""Steadfast error taxonomy.

The supplied documentation describes **no error response body at all** — no
error codes, no message field, no validation shape. So this module classifies on
the only evidence that actually exists: the HTTP status line, the transport-level
failure, and whether the request could have reached the provider.

That last distinction is the one that matters. Master spec sections 11 and 36,
and the brief's sections 8 and 30, all turn on the same question:

    **Could this request have created a parcel?**

A connect-timeout answers "no" — nothing was sent. A read-timeout answers
"maybe" — the request went out and the answer was lost. Those two must not share
a code, because one is safe to retry and the other ships a second parcel and
bills the seller for both.

:class:`SteadfastError` therefore carries :attr:`SteadfastError.reached_provider`
as a tri-state, and :func:`classify_http` never sets it optimistically.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.core.errors import AppError, ErrorCode

__all__ = [
    "SteadfastAmbiguousError",
    "SteadfastAuthError",
    "SteadfastError",
    "SteadfastErrorKind",
    "SteadfastProtocolError",
    "SteadfastRateLimitedError",
    "SteadfastUnavailableError",
    "SteadfastValidationError",
    "classify_http",
    "to_app_error",
]


class SteadfastErrorKind(StrEnum):
    """How a Steadfast call failed, in ecomsbd's vocabulary.

    Maps onto the existing :class:`~app.core.errors.ErrorCode` taxonomy through
    :data:`_ERROR_CODE_FOR_KIND`; the provider's own words never reach a seller.
    """

    #: Credentials rejected. Deterministic — retrying changes nothing.
    AUTH = "INVALID_COURIER_CREDENTIALS"
    #: The provider positively rejected the payload. Safe failure.
    VALIDATION = "VALIDATION_ERROR"
    #: The provider rejected the address specifically.
    ADDRESS_REJECTED = "ADDRESS_REJECTED"
    #: Throttled. The document states no rate limit, so this is only ever
    #: inferred from a literal HTTP 429.
    RATE_LIMITED = "RATE_LIMITED"
    #: Reachable but erroring, or not reachable at all. Safe for reads.
    UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    #: We got bytes back and they were not the documented shape: HTML, a
    #: truncated body, JSON of the wrong type, a body status we cannot read.
    PROTOCOL = "PROVIDER_PROTOCOL_ERROR"
    #: The write may or may not have been applied. Never retried.
    AMBIGUOUS = "BOOKING_AMBIGUOUS"
    #: Classified as nothing else. Treated as unsafe for writes.
    UNKNOWN = "UNKNOWN_PROVIDER_ERROR"

    @property
    def is_deterministic(self) -> bool:
        """Whether re-sending the identical request would fail identically.

        Only these may move a courier account to ``NEEDS_RECONNECT`` or fail a
        booking safely. A transient error must never invalidate credentials
        (brief section 5).
        """
        return self in (
            SteadfastErrorKind.AUTH,
            SteadfastErrorKind.VALIDATION,
            SteadfastErrorKind.ADDRESS_REJECTED,
        )

    @property
    def is_safe_for_write_retry(self) -> bool:
        """Whether a *create* may be re-sent after this.

        False for everything that could have reached the provider. The only
        errors a create may be retried after are the ones that prove the
        request never arrived, and those surface as
        :attr:`SteadfastError.reached_provider` being ``False`` rather than as
        a kind.
        """
        return False


#: Kind -> the stable client-facing code. These strings are part of the API
#: contract the mobile app branches on.
_ERROR_CODE_FOR_KIND: dict[SteadfastErrorKind, ErrorCode] = {
    SteadfastErrorKind.AUTH: ErrorCode.INVALID_COURIER_CREDENTIALS,
    SteadfastErrorKind.VALIDATION: ErrorCode.VALIDATION_ERROR,
    SteadfastErrorKind.ADDRESS_REJECTED: ErrorCode.ADDRESS_REJECTED,
    SteadfastErrorKind.RATE_LIMITED: ErrorCode.RATE_LIMITED,
    SteadfastErrorKind.UNAVAILABLE: ErrorCode.COURIER_PROVIDER_UNAVAILABLE,
    SteadfastErrorKind.PROTOCOL: ErrorCode.PROVIDER_PROTOCOL_ERROR,
    SteadfastErrorKind.AMBIGUOUS: ErrorCode.BOOKING_AMBIGUOUS,
    SteadfastErrorKind.UNKNOWN: ErrorCode.UNKNOWN_PROVIDER_ERROR,
}


class SteadfastError(Exception):
    """A failed Steadfast call.

    Carries no credential and no raw provider body. ``technical_context`` is
    the redacted detail support needs; it is stored, not shown to a seller.
    """

    kind: SteadfastErrorKind = SteadfastErrorKind.UNKNOWN

    def __init__(
        self,
        message: str,
        *,
        kind: SteadfastErrorKind | None = None,
        http_status: int | None = None,
        reached_provider: bool | None = None,
        correlation_id: str | None = None,
        technical_context: dict[str, Any] | None = None,
    ) -> None:
        self.kind = kind or type(self).kind
        self.http_status = http_status
        #: ``True`` the request certainly arrived, ``False`` it certainly did
        #: not, ``None`` we cannot tell. ``None`` and ``True`` both forbid a
        #: create retry.
        self.reached_provider = reached_provider
        self.correlation_id = correlation_id
        self.technical_context = technical_context or {}
        super().__init__(message)

    @property
    def error_code(self) -> ErrorCode:
        return _ERROR_CODE_FOR_KIND[self.kind]

    @property
    def is_auth_failure(self) -> bool:
        return self.kind is SteadfastErrorKind.AUTH

    @property
    def create_may_have_succeeded(self) -> bool:
        """Whether a create that raised this could have produced a parcel.

        The default is the unsafe answer. Only an explicit ``False`` — set when
        the transport proves the bytes never left, or when the provider
        positively rejected the payload — permits treating the create as a
        clean failure.
        """
        if self.reached_provider is False:
            # The transport proved the bytes never left.
            return False
        # A deterministic kind — a 401, a 422 — is the provider answering *and*
        # refusing, which only a request that arrived can produce, so nothing
        # was created. Everything else (a lost answer, a 500, an unreadable
        # body) keeps the unsafe answer, which is the default on purpose.
        return not self.kind.is_deterministic

    def as_log_context(self) -> dict[str, Any]:
        """Redaction-safe fields for a log line or a stored event."""
        return {
            "provider": "steadfast",
            "error_kind": str(self.kind),
            "http_status": self.http_status,
            "reached_provider": self.reached_provider,
            "correlation_id": self.correlation_id,
            **self.technical_context,
        }


class SteadfastAuthError(SteadfastError):
    kind = SteadfastErrorKind.AUTH


class SteadfastValidationError(SteadfastError):
    kind = SteadfastErrorKind.VALIDATION


class SteadfastRateLimitedError(SteadfastError):
    kind = SteadfastErrorKind.RATE_LIMITED


class SteadfastUnavailableError(SteadfastError):
    kind = SteadfastErrorKind.UNAVAILABLE


class SteadfastProtocolError(SteadfastError):
    """The response was not the documented shape.

    An HTML error page from a load balancer, a truncated body, a JSON array
    where an object was documented. Distinct from ``UNAVAILABLE`` because it
    usually means the request reached *something* — so a create that ends here
    is ambiguous, not failed.
    """

    kind = SteadfastErrorKind.PROTOCOL


class SteadfastAmbiguousError(SteadfastError):
    """The outcome is genuinely unknown. Never retried automatically."""

    kind = SteadfastErrorKind.AMBIGUOUS

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("reached_provider", None)
        super().__init__(message, **kwargs)


def classify_http(status_code: int) -> SteadfastErrorKind:
    """Classify an HTTP status the provider actually returned.

    The provider answered, so ``reached_provider`` is ``True`` at every call
    site that uses this — which is why a 5xx here becomes ``UNAVAILABLE`` for a
    read and is escalated to ambiguous by the *caller* for a write, rather than
    being decided here without knowing the operation.

    The document describes no error bodies, so this is inference from the status
    line alone and nothing finer is claimed.
    """
    if status_code in (401, 403):
        return SteadfastErrorKind.AUTH
    if status_code == 404:
        # No documented "not found" body exists. For a status lookup this is
        # handled by the caller as "no answer", never as "the parcel does not
        # exist" — see the booking-recovery rules.
        return SteadfastErrorKind.VALIDATION
    if status_code == 422:
        return SteadfastErrorKind.VALIDATION
    if status_code == 429:
        return SteadfastErrorKind.RATE_LIMITED
    if 400 <= status_code < 500:
        return SteadfastErrorKind.VALIDATION
    if 500 <= status_code < 600:
        return SteadfastErrorKind.UNAVAILABLE
    return SteadfastErrorKind.UNKNOWN


def to_app_error(error: SteadfastError) -> AppError:
    """Convert to the client-facing error.

    The provider's technical detail does not cross this boundary: the seller
    gets a stable code and Bangla copy from the existing catalogue, and the
    detail is kept for support (brief section 31).
    """
    details: dict[str, Any] = {"provider": "steadfast"}
    if error.kind is SteadfastErrorKind.AMBIGUOUS:
        details["do_not_retry"] = True
    return AppError(
        str(error),
        code=error.error_code,
        details=details,
        # Nothing that might have reached the provider is ever marked
        # retryable, whatever the underlying cause (master spec section 62.7).
        retryable=False if error.create_may_have_succeeded else None,
    )
