import 'package:flutter/foundation.dart';

import '../../l10n/app_locale.dart';

/// Stable machine codes returned by the API.
///
/// Mirrors `backend/app/core/errors.py`. Values are part of the contract: an
/// older app build must keep working against a newer server (master spec
/// section 110), so a code is never renamed, only added.
class ApiErrorCode {
  const ApiErrorCode._();

  static const String validation = 'VALIDATION_ERROR';
  static const String notFound = 'NOT_FOUND';
  static const String conflict = 'CONFLICT';
  static const String rateLimited = 'RATE_LIMITED';
  static const String internal = 'INTERNAL_ERROR';
  static const String serviceUnavailable = 'SERVICE_UNAVAILABLE';
  static const String unsupportedAppVersion = 'UNSUPPORTED_APP_VERSION';
  static const String featureDisabled = 'FEATURE_DISABLED';

  static const String unauthenticated = 'UNAUTHENTICATED';
  static const String invalidToken = 'INVALID_TOKEN';
  static const String tokenExpired = 'TOKEN_EXPIRED';
  static const String sessionRevoked = 'SESSION_REVOKED';
  static const String forbidden = 'FORBIDDEN';

  static const String otpInvalid = 'OTP_INVALID';
  static const String otpExpired = 'OTP_EXPIRED';
  static const String otpMaxAttempts = 'OTP_MAX_ATTEMPTS';
  static const String otpRateLimited = 'OTP_RATE_LIMITED';
  static const String otpResendTooSoon = 'OTP_RESEND_TOO_SOON';
  static const String otpDeliveryFailed = 'OTP_DELIVERY_FAILED';

  static const String invalidPhoneNumber = 'INVALID_PHONE_NUMBER';
  static const String ambiguousPhoneNumber = 'AMBIGUOUS_PHONE_NUMBER';

  static const String entitlementRequired = 'ENTITLEMENT_REQUIRED';
  static const String idempotencyConflict = 'IDEMPOTENCY_KEY_CONFLICT';

  /// A sale or decrease would take stock below zero (Inventory V2).
  static const String insufficientStock = 'INSUFFICIENT_STOCK';

  /// The courier outcome is unknown. The app must NOT offer a plain retry.
  static const String bookingAmbiguous = 'BOOKING_AMBIGUOUS';
  static const String courierUnavailable = 'COURIER_PROVIDER_UNAVAILABLE';

  /// No connection. Produced by the client, not the server.
  static const String offline = 'CLIENT_OFFLINE';

  /// The server took too long to answer. Produced by the client.
  static const String timeout = 'CLIENT_TIMEOUT';
}

/// A typed API failure.
///
/// The server sends both a Bangla and an English message with every error, so
/// the app displays one of those rather than inventing its own copy — the
/// wording of a money error is a product decision that belongs on the server
/// (master spec section 46). Which one is shown follows the language the
/// seller selected.
@immutable
class ApiError implements Exception {
  const ApiError({
    required this.code,
    required this.messageBn,
    required this.messageEn,
    required this.retryable,
    this.referenceId,
    this.details,
    this.statusCode,
  });

  factory ApiError.fromJson(Map<String, dynamic> json, {int? statusCode}) {
    return ApiError(
      code: (json['code'] as String?) ?? ApiErrorCode.internal,
      messageBn: (json['message_bn'] as String?) ?? '',
      messageEn: (json['message_en'] as String?) ?? 'Something went wrong.',
      retryable: (json['retryable'] as bool?) ?? false,
      referenceId: json['reference_id'] as String?,
      details: json['details'] as Map<String, dynamic>?,
      statusCode: statusCode,
    );
  }

  /// No usable connection.
  ///
  /// Shown wherever a screen prints an error's message, so it names only the
  /// cause and the fix; what still works offline differs by screen.
  factory ApiError.offline() => const ApiError(
    code: ApiErrorCode.offline,
    messageBn: 'ইন্টারনেট সংযোগ নেই। সংযোগ পরীক্ষা করে আবার চেষ্টা করুন।',
    messageEn: 'No internet connection. Check your connection and try again.',
    retryable: true,
  );

  /// A read the server was too slow to answer. Nothing changed, so a retry is
  /// safe. (A write that timed out is ambiguous and is not built here.)
  factory ApiError.timeout() => const ApiError(
    code: ApiErrorCode.timeout,
    messageBn: 'সার্ভার সাড়া দিতে দেরি করছে। একটু পরে আবার চেষ্টা করুন।',
    messageEn: 'The server is taking too long to respond. Please try again.',
    retryable: true,
  );

  /// [error] as an [ApiError], for display. Anything else — a parsing bug, a
  /// platform failure — becomes a generic retryable error, so its raw text
  /// never reaches the screen.
  factory ApiError.from(Object error) =>
      error is ApiError ? error : ApiError.unexpected(error);

  /// A transport or parsing failure with no structured body.
  factory ApiError.unexpected(Object cause) => ApiError(
    code: ApiErrorCode.internal,
    messageBn: 'কিছু একটা সমস্যা হয়েছে। আবার চেষ্টা করুন।',
    messageEn: 'Something went wrong. Please try again.',
    retryable: true,
    details: <String, dynamic>{'cause': cause.toString()},
  );

  final String code;
  final String messageBn;
  final String messageEn;

  /// Whether a plain retry is safe.
  ///
  /// The server sets this false for anything that could duplicate money or
  /// external work, even when the underlying cause is transient. The UI must
  /// respect it rather than deciding for itself (master spec sections 62, 126).
  final bool retryable;

  final String? referenceId;
  final Map<String, dynamic>? details;
  final int? statusCode;

  bool get isAuthFailure =>
      code == ApiErrorCode.unauthenticated ||
      code == ApiErrorCode.invalidToken ||
      code == ApiErrorCode.tokenExpired ||
      code == ApiErrorCode.sessionRevoked;

  /// True when the session is gone for good and the app must return to login.
  /// A merely expired access token is refreshable and is not included.
  bool get requiresReauthentication =>
      code == ApiErrorCode.sessionRevoked ||
      code == ApiErrorCode.invalidToken ||
      code == ApiErrorCode.unauthenticated;

  bool get isOffline => code == ApiErrorCode.offline;

  bool get isTimeout => code == ApiErrorCode.timeout;

  /// The shop's plan does not include this. Not a failure: the screen should
  /// say so and point at the plans, not offer a retry that cannot succeed.
  bool get isPlanLimited => code == ApiErrorCode.entitlementRequired;

  /// Remaining OTP attempts, when the server reported them.
  int? get attemptsRemaining => details?['attempts_remaining'] as int?;

  /// Where a sign-in failed, in a form safe to show and log, such as
  /// `APPLE_SUPABASE_REJECTED 400 provider_disabled`. It never holds a token,
  /// a nonce, an address or a provider's user id. Null for other errors.
  String? get diagnostic => details?['diagnostic'] as String?;

  /// This error with [diagnostic] attached; everything else is kept.
  ApiError withDiagnostic(String diagnostic) => ApiError(
    code: code,
    messageBn: messageBn,
    messageEn: messageEn,
    retryable: retryable,
    referenceId: referenceId,
    details: <String, dynamic>{...?details, 'diagnostic': diagnostic},
    statusCode: statusCode,
  );

  /// Seller-facing message, in the selected language.
  ///
  /// Falls back to the other language rather than showing nothing: an error
  /// the server only worded in one language is still better than a blank.
  String get displayMessage {
    final preferred = activeAppLocale == AppLocale.en ? messageEn : messageBn;
    if (preferred.isNotEmpty) return preferred;
    final fallback = activeAppLocale == AppLocale.en ? messageBn : messageEn;
    return fallback;
  }

  @override
  String toString() => 'ApiError($code, $messageEn, ref=$referenceId)';
}
