import 'package:flutter/foundation.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/app_locale.dart';

/// Seller-facing labels below are read where no `BuildContext` exists, so
/// they resolve against the active locale directly — the same approach
/// `formatRelative` uses. A language change rebuilds the tree, so the next
/// paint is already in the new language.
String _t(String key) => AppStrings(activeAppLocale).t(key);

/// Courier integration models.
///
/// Mirrors `backend/app/api/v1/couriers.py`. Note what is absent: there is no
/// field here that could hold an API key or a secret, because the server has no
/// response that contains one. The masked identifier is the only
/// credential-derived value the app ever sees, and it is not reversible.

/// Where a courier account stands.
enum CourierAccountStatus {
  connected,
  needsReconnect,
  disconnected,
  unknown;

  static CourierAccountStatus parse(String? value) => switch (value) {
    'CONNECTED' => CourierAccountStatus.connected,
    'NEEDS_RECONNECT' => CourierAccountStatus.needsReconnect,
    'DISCONNECTED' => CourierAccountStatus.disconnected,
    _ => CourierAccountStatus.unknown,
  };

  String get label => switch (this) {
    CourierAccountStatus.connected => _t('provider.connected'),
    CourierAccountStatus.needsReconnect => _t('provider.needsReconnect'),
    CourierAccountStatus.disconnected => _t('provider.notConnected'),
    CourierAccountStatus.unknown => _t('provider.unknown'),
  };

  bool get canBook => this == CourierAccountStatus.connected;
}

/// The four outcomes of checking credentials.
///
/// The last two exist so the app can say "we could not check" rather than
/// "your key is wrong" — telling a seller their working key is wrong because
/// the courier had a bad minute is how trust in this screen is lost.
enum CredentialCheck {
  valid,
  invalid,
  providerUnavailable,
  unknown;

  static CredentialCheck parse(String? value) => switch (value) {
    'VALID' => CredentialCheck.valid,
    'INVALID' => CredentialCheck.invalid,
    'PROVIDER_UNAVAILABLE' => CredentialCheck.providerUnavailable,
    _ => CredentialCheck.unknown,
  };

  bool get isConclusive =>
      this == CredentialCheck.valid || this == CredentialCheck.invalid;
}

@immutable
class CourierAccount {
  const CourierAccount({
    required this.id,
    required this.provider,
    required this.status,
    required this.connected,
    required this.needsReconnect,
    required this.capabilities,
    this.label,
    this.maskedIdentifier,
    this.lastVerifiedAt,
    this.lastValidationResult,
    this.lastValidationMessage,
    this.reportedBalancePaisa,
    this.reportedBalanceAt,
  });

  factory CourierAccount.fromJson(Map<String, dynamic> json) {
    return CourierAccount(
      id: json['id'] as String,
      provider: json['provider'] as String,
      status: CourierAccountStatus.parse(json['status'] as String?),
      connected: (json['connected'] as bool?) ?? false,
      needsReconnect: (json['needs_reconnect'] as bool?) ?? false,
      capabilities: <String, String>{
        for (final entry
            in (json['capabilities'] as Map<String, dynamic>? ??
                    const <String, dynamic>{})
                .entries)
          entry.key: '${entry.value}',
      },
      label: json['label'] as String?,
      maskedIdentifier: json['masked_identifier'] as String?,
      lastVerifiedAt: DateTime.tryParse(
        (json['last_verified_at'] as String?) ?? '',
      ),
      lastValidationResult: json['last_validation_result'] as String?,
      lastValidationMessage: json['last_validation_message'] as String?,
      reportedBalancePaisa: json['reported_balance_paisa'] as int?,
      reportedBalanceAt: DateTime.tryParse(
        (json['reported_balance_at'] as String?) ?? '',
      ),
    );
  }

  final String id;
  final String provider;
  final CourierAccountStatus status;
  final bool connected;
  final bool needsReconnect;

  /// Capability name -> `"true"` / `"false"` / `"unknown"`.
  ///
  /// The UI reacts to these rather than to the provider name (master spec
  /// section 75), so a courier without a return API shows "contact the
  /// courier" instead of a button that cannot work.
  final Map<String, String> capabilities;

  final String? label;

  /// `****abcd` — the last four characters of the API key. Enough to recognise
  /// which account is connected, useless to anyone who obtains it.
  final String? maskedIdentifier;

  final DateTime? lastVerifiedAt;
  final String? lastValidationResult;
  final String? lastValidationMessage;

  /// The courier's *own* reported account balance.
  ///
  /// Never added to, or compared with, the COD outstanding figure on the Money
  /// screen: they measure different things, and a screen that merged them
  /// would be wrong in a way nobody could unpick.
  final int? reportedBalancePaisa;
  final DateTime? reportedBalanceAt;

  bool supports(String capability) => capabilities[capability] == 'true';

  /// True when the documentation is silent — distinct from a capability the
  /// provider positively does not have.
  bool isUnknown(String capability) =>
      (capabilities[capability] ?? 'unknown') == 'unknown';

  String get displayName => label ?? _providerName(provider);
}

String _providerName(String provider) => switch (provider) {
  'steadfast' => 'Steadfast',
  'pathao' => 'Pathao',
  'redx' => 'RedX',
  'manual' => 'Manual',
  _ => provider,
};

@immutable
class ConnectionTestResult {
  const ConnectionTestResult({
    required this.result,
    required this.message,
    this.account,
  });

  factory ConnectionTestResult.fromJson(Map<String, dynamic> json) {
    return ConnectionTestResult(
      result: CredentialCheck.parse(json['result'] as String?),
      message: (json['message'] as String?) ?? '',
      account: json['account'] == null
          ? null
          : CourierAccount.fromJson(json['account'] as Map<String, dynamic>),
    );
  }

  final CredentialCheck result;
  final String message;
  final CourierAccount? account;
}

/// What a provider's documentation does and does not say.
@immutable
class ProviderEvidence {
  const ProviderEvidence({
    required this.provider,
    required this.capabilities,
    required this.unknowns,
    required this.blockers,
    this.documentationVersion,
    this.verifiedAt,
    this.manualFallback,
  });

  factory ProviderEvidence.fromJson(Map<String, dynamic> json) {
    return ProviderEvidence(
      provider: json['provider'] as String,
      capabilities: <String, String>{
        for (final entry
            in (json['capabilities'] as Map<String, dynamic>? ??
                    const <String, dynamic>{})
                .entries)
          entry.key: '${entry.value}',
      },
      unknowns: <String, String>{
        for (final entry
            in (json['unknowns'] as Map<String, dynamic>? ??
                    const <String, dynamic>{})
                .entries)
          entry.key: '${entry.value}',
      },
      blockers: <String>[
        for (final blocker
            in (json['blockers'] as List<dynamic>? ?? const <dynamic>[]))
          '$blocker',
      ],
      documentationVersion: json['documentation_version'] as String?,
      verifiedAt: DateTime.tryParse((json['verified_at'] as String?) ?? ''),
      manualFallback: json['manual_fallback'] as String?,
    );
  }

  final String provider;
  final Map<String, String> capabilities;
  final Map<String, String> unknowns;
  final List<String> blockers;
  final String? documentationVersion;
  final DateTime? verifiedAt;
  final String? manualFallback;

  bool get hasNoWebhook => (capabilities['webhook'] ?? 'unknown') != 'true';
}

// --------------------------------------------------------------------------- //
// Booking
// --------------------------------------------------------------------------- //

/// What happened to one order in a booking request.
///
/// [ambiguous] is a first-class outcome, not an error. The UI renders it as
/// "we are checking with the courier" and offers **no retry**, because a retry
/// is how a shop ships two parcels and pays for both.
enum BookingOutcome {
  booked,
  ambiguous,
  failed;

  static BookingOutcome parse(String? value) => switch (value) {
    'BOOKED' => BookingOutcome.booked,
    'BOOKING_UNKNOWN' => BookingOutcome.ambiguous,
    'BOOKING' => BookingOutcome.ambiguous,
    _ => BookingOutcome.failed,
  };

  String get label => switch (this) {
    BookingOutcome.booked => 'Booked',
    BookingOutcome.ambiguous => 'Checking result',
    BookingOutcome.failed => 'Failed safely',
  };
}

@immutable
class BookingItem {
  const BookingItem({
    required this.orderId,
    required this.merchantReference,
    required this.outcome,
    this.consignmentId,
    this.trackingCode,
    this.errorCode,
    this.message,
  });

  factory BookingItem.fromJson(Map<String, dynamic> json) {
    return BookingItem(
      orderId: json['order_id'] as String,
      merchantReference: (json['merchant_reference'] as String?) ?? '',
      outcome: BookingOutcome.parse(json['outcome'] as String?),
      consignmentId: json['consignment_id'] as String?,
      trackingCode: json['tracking_code'] as String?,
      errorCode: json['error_code'] as String?,
      message: json['message'] as String?,
    );
  }

  final String orderId;
  final String merchantReference;
  final BookingOutcome outcome;
  final String? consignmentId;
  final String? trackingCode;
  final String? errorCode;
  final String? message;
}

@immutable
class BookingReport {
  const BookingReport({
    required this.provider,
    required this.booked,
    required this.ambiguous,
    required this.failed,
    required this.items,
    this.batchId,
  });

  factory BookingReport.fromJson(Map<String, dynamic> json) {
    return BookingReport(
      provider: (json['provider'] as String?) ?? 'steadfast',
      booked: (json['booked'] as int?) ?? 0,
      ambiguous: (json['ambiguous'] as int?) ?? 0,
      failed: (json['failed'] as int?) ?? 0,
      items: <BookingItem>[
        for (final item
            in (json['items'] as List<dynamic>? ?? const <dynamic>[]))
          BookingItem.fromJson(item as Map<String, dynamic>),
      ],
      batchId: json['batch_id'] as String?,
    );
  }

  final String provider;
  final int booked;
  final int ambiguous;
  final int failed;
  final List<BookingItem> items;
  final String? batchId;

  int get total => booked + ambiguous + failed;

  /// Whether any order needs a person to look at it before anything is retried.
  bool get hasAmbiguous => ambiguous > 0;
}

// --------------------------------------------------------------------------- //
// Tracking
// --------------------------------------------------------------------------- //

@immutable
class BookingAttempt {
  const BookingAttempt({
    required this.id,
    required this.attemptNumber,
    required this.state,
    required this.merchantReference,
    required this.recoveryAttempts,
    required this.startedAt,
    this.providerConsignmentId,
    this.trackingCode,
    this.errorCode,
    this.recoveryNote,
    this.completedAt,
  });

  factory BookingAttempt.fromJson(Map<String, dynamic> json) {
    return BookingAttempt(
      id: json['id'] as String,
      attemptNumber: (json['attempt_number'] as int?) ?? 1,
      state: (json['state'] as String?) ?? 'PENDING',
      merchantReference: (json['merchant_reference'] as String?) ?? '',
      recoveryAttempts: (json['recovery_attempts'] as int?) ?? 0,
      startedAt:
          DateTime.tryParse((json['started_at'] as String?) ?? '') ??
          DateTime.now(),
      providerConsignmentId: json['provider_consignment_id'] as String?,
      trackingCode: json['tracking_code'] as String?,
      errorCode: json['error_code'] as String?,
      recoveryNote: json['recovery_note'] as String?,
      completedAt: DateTime.tryParse((json['completed_at'] as String?) ?? ''),
    );
  }

  final String id;
  final int attemptNumber;
  final String state;
  final String merchantReference;
  final int recoveryAttempts;
  final DateTime startedAt;
  final String? providerConsignmentId;
  final String? trackingCode;
  final String? errorCode;
  final String? recoveryNote;
  final DateTime? completedAt;

  bool get isUnresolved => state == 'UNKNOWN' || state == 'MANUAL_REVIEW';
  bool get needsAPerson => state == 'MANUAL_REVIEW';
}

/// One observation of what the courier said.
///
/// [observedAt] is when ecomsbd asked, not when the courier acted. Steadfast's
/// status response carries no timestamp, so the timeline says "checked" rather
/// than implying the courier moved the parcel at that moment.
@immutable
class CourierEvent {
  const CourierEvent({
    required this.kind,
    required this.source,
    required this.observedAt,
    required this.lastSeenAt,
    required this.observationCount,
    required this.statusUndocumented,
    this.rawStatus,
    this.normalizedStatus,
  });

  factory CourierEvent.fromJson(Map<String, dynamic> json) {
    return CourierEvent(
      kind: (json['kind'] as String?) ?? 'STATUS',
      source: (json['source'] as String?) ?? 'POLL',
      observedAt:
          DateTime.tryParse((json['observed_at'] as String?) ?? '') ??
          DateTime.now(),
      lastSeenAt:
          DateTime.tryParse((json['last_seen_at'] as String?) ?? '') ??
          DateTime.now(),
      observationCount: (json['observation_count'] as int?) ?? 1,
      statusUndocumented: (json['status_undocumented'] as bool?) ?? false,
      rawStatus: json['raw_status'] as String?,
      normalizedStatus: json['normalized_status'] as String?,
    );
  }

  final String kind;
  final String source;
  final DateTime observedAt;
  final DateTime lastSeenAt;
  final int observationCount;

  /// True when the courier sent a status this build has never seen documented.
  /// Shown to the seller as the courier's own words, with no interpretation.
  final bool statusUndocumented;

  final String? rawStatus;
  final String? normalizedStatus;
}

@immutable
class ConsignmentTracking {
  const ConsignmentTracking({required this.attempts, required this.events});

  factory ConsignmentTracking.fromJson(Map<String, dynamic> json) {
    return ConsignmentTracking(
      attempts: <BookingAttempt>[
        for (final attempt
            in (json['attempts'] as List<dynamic>? ?? const <dynamic>[]))
          BookingAttempt.fromJson(attempt as Map<String, dynamic>),
      ],
      events: <CourierEvent>[
        for (final event
            in (json['events'] as List<dynamic>? ?? const <dynamic>[]))
          CourierEvent.fromJson(event as Map<String, dynamic>),
      ],
    );
  }

  final List<BookingAttempt> attempts;
  final List<CourierEvent> events;

  BookingAttempt? get unresolvedAttempt {
    for (final attempt in attempts) {
      if (attempt.isUnresolved) {
        return attempt;
      }
    }
    return null;
  }
}

// --------------------------------------------------------------------------- //
// Returns and payments
// --------------------------------------------------------------------------- //

@immutable
class CourierReturnRequest {
  const CourierReturnRequest({
    required this.id,
    required this.consignmentId,
    required this.state,
    required this.message,
    this.providerReturnId,
    this.providerStatus,
  });

  factory CourierReturnRequest.fromJson(Map<String, dynamic> json) {
    return CourierReturnRequest(
      id: json['id'] as String,
      consignmentId: json['consignment_id'] as String,
      state: (json['state'] as String?) ?? 'UNKNOWN',
      message: (json['message'] as String?) ?? '',
      providerReturnId: json['provider_return_id'] as String?,
      providerStatus: json['provider_status'] as String?,
    );
  }

  final String id;
  final String consignmentId;
  final String state;
  final String message;
  final String? providerReturnId;
  final String? providerStatus;

  /// A request whose answer was lost. Blocks another one, exactly as an
  /// ambiguous booking blocks a rebooking.
  bool get isAmbiguous => state == 'UNKNOWN';

  bool get isFinished => state == 'COMPLETED' || state == 'CANCELLED';

  String get label => switch (state) {
    'REQUESTED' => 'Return requested',
    'ACKNOWLEDGED' => 'Courier accepted',
    'IN_PROGRESS' => 'Coming back',
    'COMPLETED' => 'Return complete',
    'CANCELLED' => 'Return cancelled',
    'FAILED' => 'Return failed',
    _ => 'Checking result',
  };
}

@immutable
class ProviderPayment {
  const ProviderPayment({
    required this.id,
    required this.providerPaymentId,
    required this.syncState,
    required this.firstSeenAt,
    required this.lastSeenAt,
    required this.observedFields,
    required this.schemaUnverified,
    this.providerReference,
    this.totalPaisa,
    this.paidAt,
    this.consignmentCount,
    this.payoutId,
    this.errorMessage,
  });

  factory ProviderPayment.fromJson(Map<String, dynamic> json) {
    return ProviderPayment(
      id: json['id'] as String,
      providerPaymentId: json['provider_payment_id'] as String,
      syncState: (json['sync_state'] as String?) ?? 'SEEN',
      firstSeenAt:
          DateTime.tryParse((json['first_seen_at'] as String?) ?? '') ??
          DateTime.now(),
      lastSeenAt:
          DateTime.tryParse((json['last_seen_at'] as String?) ?? '') ??
          DateTime.now(),
      observedFields: <String>[
        for (final field
            in (json['observed_fields'] as List<dynamic>? ?? const <dynamic>[]))
          '$field',
      ],
      schemaUnverified: (json['schema_unverified'] as bool?) ?? true,
      providerReference: json['provider_reference'] as String?,
      totalPaisa: json['total_paisa'] as int?,
      paidAt: DateTime.tryParse((json['paid_at'] as String?) ?? ''),
      consignmentCount: json['consignment_count'] as int?,
      payoutId: json['payout_id'] as String?,
      errorMessage: json['error_message'] as String?,
    );
  }

  final String id;
  final String providerPaymentId;
  final String syncState;
  final DateTime firstSeenAt;
  final DateTime lastSeenAt;

  /// Which field names the courier actually sent.
  ///
  /// Steadfast documents no response schema for its payments endpoints, so the
  /// names behind these numbers were inferred. A seller comparing this against
  /// their courier portal deserves to know that, which is why
  /// [schemaUnverified] is surfaced rather than hidden.
  final List<String> observedFields;
  final bool schemaUnverified;

  final String? providerReference;
  final int? totalPaisa;
  final DateTime? paidAt;
  final int? consignmentCount;
  final String? payoutId;
  final String? errorMessage;

  bool get needsAttention => syncState == 'CHANGED' || syncState == 'FAILED';

  String get stateLabel => switch (syncState) {
    'SEEN' => _t('sst.seen'),
    'DETAILED' => _t('sst.read'),
    'IMPORTED' => _t('sst.imported'),
    'CHANGED' => _t('sst.changedAtCourier'),
    'FAILED' => _t('sst.couldNotRead'),
    _ => syncState,
  };
}
