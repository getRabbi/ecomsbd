"""Error taxonomy.

Master spec section 46: the API returns a stable machine code, a Bangla user
message, a retryable flag and a reference id::

    {
      "code": "COURIER_PROVIDER_UNAVAILABLE",
      "message_bn": "...",
      "retryable": true,
      "reference_id": "..."
    }

Two rules shape this module:

*   ``retryable`` is part of the contract because the mobile client uses it to
    decide whether a retry is safe. Anything that could duplicate external work
    or money is ``retryable=False`` even when the underlying cause is transient
    (master spec sections 62.7 and 126).
*   A cross-tenant reference is reported to the client as a plain 404. Returning
    403 would confirm that another tenant's record exists.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "AppError",
    "AuthenticationError",
    "BillingVerificationError",
    "ConflictError",
    "EntitlementRequiredError",
    "ErrorCode",
    "ErrorResponse",
    "ForbiddenError",
    "IdempotencyConflictError",
    "NotFoundError",
    "RateLimitedError",
    "ServiceUnavailableError",
    "TenantIsolationError",
    "ValidationError",
    "WebhookSignatureError",
]


class ErrorCode(StrEnum):
    """Stable machine codes. Values are part of the public API contract.

    Never rename a member: the mobile client branches on these strings and an
    older app build must keep working against a newer server (section 110).
    """

    # --- generic -----------------------------------------------------------
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    UNSUPPORTED_APP_VERSION = "UNSUPPORTED_APP_VERSION"
    FEATURE_DISABLED = "FEATURE_DISABLED"

    # --- auth / session ----------------------------------------------------
    UNAUTHENTICATED = "UNAUTHENTICATED"
    INVALID_TOKEN = "INVALID_TOKEN"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    SESSION_REVOKED = "SESSION_REVOKED"
    FORBIDDEN = "FORBIDDEN"

    # --- otp ---------------------------------------------------------------
    OTP_INVALID = "OTP_INVALID"
    OTP_EXPIRED = "OTP_EXPIRED"
    OTP_MAX_ATTEMPTS = "OTP_MAX_ATTEMPTS"
    OTP_RATE_LIMITED = "OTP_RATE_LIMITED"
    OTP_RESEND_TOO_SOON = "OTP_RESEND_TOO_SOON"
    OTP_DELIVERY_FAILED = "OTP_DELIVERY_FAILED"

    # --- phone / identity --------------------------------------------------
    INVALID_PHONE_NUMBER = "INVALID_PHONE_NUMBER"
    AMBIGUOUS_PHONE_NUMBER = "AMBIGUOUS_PHONE_NUMBER"
    #: Email or password was wrong. Deliberately one code for both, so the
    #: response cannot be used to discover which addresses have accounts.
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    EMAIL_ALREADY_REGISTERED = "EMAIL_ALREADY_REGISTERED"
    EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED"
    IDENTITY_LINK_REFUSED = "IDENTITY_LINK_REFUSED"

    # --- money / external safety -------------------------------------------
    IDEMPOTENCY_KEY_CONFLICT = "IDEMPOTENCY_KEY_CONFLICT"
    ENTITLEMENT_REQUIRED = "ENTITLEMENT_REQUIRED"

    # --- inventory (V2.2) --------------------------------------------------
    #: A sale or a decrease would take stock below zero. Never clamped: the
    #: seller is told how many are available instead.
    INSUFFICIENT_STOCK = "INSUFFICIENT_STOCK"

    # --- billing (phase F) -------------------------------------------------
    BILLING_VERIFICATION_FAILED = "BILLING_VERIFICATION_FAILED"
    WEBHOOK_SIGNATURE_INVALID = "WEBHOOK_SIGNATURE_INVALID"

    # --- reserved for later phases -----------------------------------------
    # Declared now so the client contract and Bangla copy are stable before the
    # matching behaviour ships. Master spec section 46.
    BOOKING_AMBIGUOUS = "BOOKING_AMBIGUOUS"
    COURIER_PROVIDER_UNAVAILABLE = "COURIER_PROVIDER_UNAVAILABLE"
    INVALID_COURIER_CREDENTIALS = "INVALID_COURIER_CREDENTIALS"
    ADDRESS_REJECTED = "ADDRESS_REJECTED"
    ORDER_NOT_BOOKABLE = "ORDER_NOT_BOOKABLE"
    #: The provider answered with something that is not its documented shape —
    #: an HTML error page, a truncated body, JSON of the wrong type. Distinct
    #: from "unavailable" because the request probably *did* arrive.
    PROVIDER_PROTOCOL_ERROR = "PROVIDER_PROTOCOL_ERROR"
    #: A provider failure that matched no other classification. Deliberately
    #: not folded into INTERNAL_ERROR: the fault is theirs, and support needs
    #: to be able to count these separately.
    UNKNOWN_PROVIDER_ERROR = "UNKNOWN_PROVIDER_ERROR"
    #: A return was already requested for this parcel. Blocking the second tap
    #: is ecomsbd's own guarantee — Steadfast documents no idempotency.
    RETURN_ALREADY_REQUESTED = "RETURN_ALREADY_REQUESTED"
    #: The courier account's credentials were rejected repeatedly and the
    #: account needs a person to reconnect it. Separate from
    #: INVALID_COURIER_CREDENTIALS, which is one rejected attempt.
    COURIER_ACCOUNT_NEEDS_RECONNECT = "COURIER_ACCOUNT_NEEDS_RECONNECT"
    RISK_PROVIDER_UNAVAILABLE = "RISK_PROVIDER_UNAVAILABLE"
    PAYOUT_IMPORT_INVALID = "PAYOUT_IMPORT_INVALID"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
    SYNC_CONFLICT = "SYNC_CONFLICT"


#: code -> (http status, Bangla message, English message, retryable)
_CATALOG: dict[ErrorCode, tuple[int, str, str, bool]] = {
    ErrorCode.VALIDATION_ERROR: (
        422,
        "দেওয়া তথ্যে সমস্যা আছে। দয়া করে যাচাই করে আবার চেষ্টা করুন।",
        "Some of the submitted values are not valid.",
        False,
    ),
    ErrorCode.NOT_FOUND: (
        404,
        "এই তথ্যটি পাওয়া যায়নি।",
        "The requested resource was not found.",
        False,
    ),
    ErrorCode.CONFLICT: (
        409,
        "এই কাজটি এখনকার অবস্থার সাথে মিলছে না। স্ক্রিন রিফ্রেশ করে দেখুন।",
        "The request conflicts with the current state.",
        False,
    ),
    ErrorCode.RATE_LIMITED: (
        429,
        "অনেক বেশি চেষ্টা হয়েছে। একটু পরে আবার চেষ্টা করুন।",
        "Too many requests. Please try again shortly.",
        True,
    ),
    ErrorCode.INTERNAL_ERROR: (
        500,
        "আমাদের সার্ভারে একটি সমস্যা হয়েছে। আমরা বিষয়টি দেখছি।",
        "An unexpected server error occurred.",
        True,
    ),
    ErrorCode.SERVICE_UNAVAILABLE: (
        503,
        "সার্ভিসটি এখন সাময়িকভাবে পাওয়া যাচ্ছে না। একটু পরে চেষ্টা করুন।",
        "The service is temporarily unavailable.",
        True,
    ),
    ErrorCode.UNSUPPORTED_APP_VERSION: (
        426,
        "অ্যাপটি আপডেট করা প্রয়োজন।",
        "This app version is no longer supported. Please update.",
        False,
    ),
    ErrorCode.FEATURE_DISABLED: (
        403,
        "এই ফিচারটি এখন বন্ধ আছে।",
        "This feature is currently disabled.",
        False,
    ),
    ErrorCode.UNAUTHENTICATED: (
        401,
        "আবার লগইন করুন।",
        "Authentication is required.",
        False,
    ),
    ErrorCode.INVALID_TOKEN: (
        401,
        "সেশনটি আর ব্যবহারযোগ্য নয়। আবার লগইন করুন।",
        "The provided token is not valid.",
        False,
    ),
    ErrorCode.TOKEN_EXPIRED: (
        401,
        "সেশনের সময় শেষ হয়েছে। আবার লগইন করুন।",
        "The token has expired.",
        False,
    ),
    ErrorCode.SESSION_REVOKED: (
        401,
        "এই ডিভাইসের সেশন বাতিল করা হয়েছে। আবার লগইন করুন।",
        "This session has been revoked.",
        False,
    ),
    ErrorCode.FORBIDDEN: (
        403,
        "এই কাজটি করার অনুমতি আপনার নেই।",
        "You do not have permission to perform this action.",
        False,
    ),
    ErrorCode.OTP_INVALID: (
        400,
        "কোডটি ঠিক নয়। আবার দেখুন।",
        "The verification code is incorrect.",
        False,
    ),
    ErrorCode.INVALID_CREDENTIALS: (
        401,
        "ইমেইল বা পাসওয়ার্ড ঠিক নয়।",
        "That email and password do not match an account.",
        False,
    ),
    ErrorCode.EMAIL_ALREADY_REGISTERED: (
        409,
        "এই ইমেইলে ইতিমধ্যে একটি অ্যাকাউন্ট আছে।",
        "An account already exists for this email. Sign in instead.",
        False,
    ),
    ErrorCode.EMAIL_NOT_VERIFIED: (
        403,
        "আগে আপনার ইমেইল যাচাই করুন।",
        "Verify your email address first.",
        False,
    ),
    ErrorCode.IDENTITY_LINK_REFUSED: (
        409,
        "এই সাইন-ইন পদ্ধতি অন্য অ্যাকাউন্টের সাথে যুক্ত।",
        "That sign-in method belongs to a different account.",
        False,
    ),
    ErrorCode.OTP_EXPIRED: (
        400,
        "কোডের মেয়াদ শেষ। নতুন কোড নিন।",
        "The verification code has expired.",
        False,
    ),
    ErrorCode.OTP_MAX_ATTEMPTS: (
        429,
        "অনেকবার ভুল কোড দেওয়া হয়েছে। নতুন কোড নিন।",
        "Too many incorrect attempts. Request a new code.",
        False,
    ),
    ErrorCode.OTP_RATE_LIMITED: (
        429,
        "অনেকবার কোড চাওয়া হয়েছে। কিছুক্ষণ পরে আবার চেষ্টা করুন।",
        "Too many verification codes requested. Try again later.",
        True,
    ),
    ErrorCode.OTP_RESEND_TOO_SOON: (
        429,
        "একটু অপেক্ষা করুন, তারপর আবার কোড চান।",
        "Please wait before requesting another code.",
        True,
    ),
    ErrorCode.OTP_DELIVERY_FAILED: (
        502,
        "কোড পাঠানো যায়নি। একটু পরে আবার চেষ্টা করুন।",
        "The verification code could not be delivered.",
        True,
    ),
    ErrorCode.INVALID_PHONE_NUMBER: (
        422,
        "বাংলাদেশি মোবাইল নম্বরটি সঠিক নয়। যেমন: 01712345678",
        "Not a valid Bangladeshi mobile number.",
        False,
    ),
    ErrorCode.AMBIGUOUS_PHONE_NUMBER: (
        422,
        "একাধিক নম্বর পাওয়া গেছে। কোনটি ব্যবহার করবেন তা বেছে নিন।",
        "Multiple phone numbers were found; an explicit choice is required.",
        False,
    ),
    ErrorCode.IDEMPOTENCY_KEY_CONFLICT: (
        409,
        "একই রিকোয়েস্ট আলাদা তথ্য নিয়ে আবার পাঠানো হয়েছে।",
        "This idempotency key was already used with a different request body.",
        False,
    ),
    ErrorCode.INSUFFICIENT_STOCK: (
        409,
        "পর্যাপ্ত স্টক নেই।",
        "There is not enough stock for this.",
        False,
    ),
    ErrorCode.ENTITLEMENT_REQUIRED: (
        402,
        "এই ফিচারটি আপনার বর্তমান প্ল্যানে নেই।",
        "Your current plan does not include this feature.",
        False,
    ),
    ErrorCode.BILLING_VERIFICATION_FAILED: (
        402,
        "পেমেন্টটি যাচাই করা যায়নি। টাকা কেটে থাকলে চিন্তা করবেন না—সাপোর্টে জানান।",
        "The purchase could not be verified with the billing provider.",
        False,
    ),
    ErrorCode.WEBHOOK_SIGNATURE_INVALID: (
        401,
        "অনুরোধটি যাচাই করা যায়নি।",
        "The webhook signature could not be verified.",
        False,
    ),
    # Reserved-for-later codes still get final copy so the client can ship it.
    ErrorCode.BOOKING_AMBIGUOUS: (
        409,
        "কুরিয়ার বুকিং নিশ্চিত করা যায়নি। আবার বুক করবেন না—আমরা আগের চেষ্টাটি যাচাই করছি।",
        "The booking outcome is unconfirmed. Do not rebook; the attempt is being verified.",
        False,
    ),
    ErrorCode.COURIER_PROVIDER_UNAVAILABLE: (
        503,
        "কুরিয়ার সার্ভার এখন সাড়া দিচ্ছে না। অর্ডারটি আবার বুক করবেন না—আমরা আগে আগের চেষ্টা যাচাই করছি।",
        "The courier provider is not responding.",
        False,
    ),
    ErrorCode.INVALID_COURIER_CREDENTIALS: (
        400,
        "কুরিয়ার অ্যাকাউন্টের তথ্য ঠিক নয়। আবার সংযুক্ত করুন।",
        "The courier credentials were rejected by the provider.",
        False,
    ),
    ErrorCode.ADDRESS_REJECTED: (
        422,
        "ঠিকানাটি কুরিয়ার গ্রহণ করেনি। আরও পরিষ্কার ঠিকানা দিন।",
        "The provider rejected the delivery address.",
        False,
    ),
    ErrorCode.ORDER_NOT_BOOKABLE: (
        409,
        "এই অর্ডারটি এখন বুক করা যাবে না।",
        "This order cannot be booked in its current state.",
        False,
    ),
    ErrorCode.PROVIDER_PROTOCOL_ERROR: (
        502,
        "কুরিয়ার সার্ভার অপ্রত্যাশিত উত্তর দিয়েছে। আবার বুক করবেন না—আমরা যাচাই করছি।",
        "The provider returned a response that does not match its documented shape.",
        False,
    ),
    ErrorCode.UNKNOWN_PROVIDER_ERROR: (
        502,
        "কুরিয়ার সার্ভারে অজানা সমস্যা হয়েছে। আবার বুক করবেন না—আমরা যাচাই করছি।",
        "The provider failed in a way we could not classify.",
        False,
    ),
    ErrorCode.RETURN_ALREADY_REQUESTED: (
        409,
        "এই পার্সেলের জন্য রিটার্ন রিকোয়েস্ট আগেই পাঠানো হয়েছে।",
        "A return has already been requested for this parcel.",
        False,
    ),
    ErrorCode.COURIER_ACCOUNT_NEEDS_RECONNECT: (
        409,
        "কুরিয়ার অ্যাকাউন্টটি আবার সংযুক্ত করতে হবে। সেটিংস থেকে API Key ও Secret Key দিন।",
        "This courier account needs to be reconnected before it can be used.",
        False,
    ),
    ErrorCode.RISK_PROVIDER_UNAVAILABLE: (
        503,
        "ঝুঁকি যাচাইয়ের তথ্য এখন পাওয়া যাচ্ছে না।",
        "The risk data source is unavailable.",
        True,
    ),
    ErrorCode.PAYOUT_IMPORT_INVALID: (
        422,
        "পেআউট ফাইলটি পড়া যায়নি। ফরম্যাট যাচাই করুন।",
        "The payout statement could not be parsed.",
        False,
    ),
    ErrorCode.RECONCILIATION_MISMATCH: (
        409,
        "হিসাব মিলছে না। ম্যানুয়াল রিভিউ প্রয়োজন।",
        "The reconciliation amounts do not match.",
        False,
    ),
    ErrorCode.SYNC_CONFLICT: (
        409,
        "এই তথ্যটি অন্য জায়গা থেকেও বদলানো হয়েছে।",
        "This record changed on the server since your device last synced.",
        False,
    ),
}


class ErrorResponse(BaseModel):
    """Wire format for every non-2xx response."""

    code: ErrorCode
    message_bn: str
    message_en: str
    retryable: bool
    reference_id: str
    details: dict[str, Any] | None = Field(default=None)


class AppError(Exception):
    """Base class for every error the API deliberately returns.

    Anything that is *not* an ``AppError`` is treated as a bug: it is logged with
    a stack trace and returned as ``INTERNAL_ERROR`` with no internal detail.
    """

    code: ErrorCode = ErrorCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str | None = None,
        *,
        code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
        http_status: int | None = None,
        message_bn: str | None = None,
    ) -> None:
        self.code = code or type(self).code
        status, default_bn, default_en, default_retryable = _CATALOG[self.code]
        self.http_status = http_status if http_status is not None else status
        self.message_bn = message_bn or default_bn
        self.message_en = message or default_en
        self.retryable = default_retryable if retryable is None else retryable
        self.details = details
        super().__init__(self.message_en)

    def to_response(self, reference_id: str) -> ErrorResponse:
        return ErrorResponse(
            code=self.code,
            message_bn=self.message_bn,
            message_en=self.message_en,
            retryable=self.retryable,
            reference_id=reference_id,
            details=self.details,
        )


class ValidationError(AppError):
    code = ErrorCode.VALIDATION_ERROR


class NotFoundError(AppError):
    code = ErrorCode.NOT_FOUND


class ConflictError(AppError):
    code = ErrorCode.CONFLICT


class AuthenticationError(AppError):
    code = ErrorCode.UNAUTHENTICATED


class ForbiddenError(AppError):
    code = ErrorCode.FORBIDDEN


class RateLimitedError(AppError):
    code = ErrorCode.RATE_LIMITED

    def __init__(
        self, message: str | None = None, *, retry_after_seconds: int | None = None, **kwargs: Any
    ) -> None:
        super().__init__(message, **kwargs)
        self.retry_after_seconds = retry_after_seconds


class EntitlementRequiredError(AppError):
    code = ErrorCode.ENTITLEMENT_REQUIRED

    def __init__(self, entitlement: str, message: str | None = None, **kwargs: Any) -> None:
        details = dict(kwargs.pop("details", None) or {})
        details["entitlement"] = entitlement
        super().__init__(message, details=details, **kwargs)


class IdempotencyConflictError(AppError):
    code = ErrorCode.IDEMPOTENCY_KEY_CONFLICT


class BillingVerificationError(AppError):
    """A purchase claim could not be turned into a verified entitlement.

    ``retryable`` stays False: re-sending the same unverifiable token produces
    the same answer, and a client that retries in a loop turns one confused
    seller into a load problem.
    """

    code = ErrorCode.BILLING_VERIFICATION_FAILED

    def __init__(self, result: str, message: str | None = None, **kwargs: Any) -> None:
        details = dict(kwargs.pop("details", None) or {})
        details["verification_result"] = result
        super().__init__(message, details=details, **kwargs)


class WebhookSignatureError(AppError):
    code = ErrorCode.WEBHOOK_SIGNATURE_INVALID


class ServiceUnavailableError(AppError):
    code = ErrorCode.SERVICE_UNAVAILABLE


class TenantIsolationError(AppError):
    """A cross-tenant access was attempted.

    Presented to the client as a 404 so the response cannot be used to probe
    whether another tenant's record exists. The real reason is logged at
    ``WARNING`` with the requested and active tenant ids, and is a P0 incident
    signal (master spec section 117).
    """

    code = ErrorCode.NOT_FOUND

    def __init__(
        self,
        message: str,
        *,
        requested_tenant_id: object = None,
        active_tenant_id: object = None,
        entity: str | None = None,
    ) -> None:
        super().__init__(message)
        self.requested_tenant_id = requested_tenant_id
        self.active_tenant_id = active_tenant_id
        self.entity = entity

    def audit_fields(self) -> dict[str, Any]:
        return {
            "violation": "cross_tenant_access",
            "entity": self.entity,
            "requested_tenant_id": str(self.requested_tenant_id),
            "active_tenant_id": str(self.active_tenant_id),
        }
