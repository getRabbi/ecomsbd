import 'package:meta/meta.dart';

import '../../core/money.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/app_locale.dart';

/// Seller-facing labels below are read where no `BuildContext` exists, so
/// they resolve against the active locale directly — the same approach
/// `formatRelative` uses. A language change rebuilds the tree, so the next
/// paint is already in the new language.
String _t(String key) => AppStrings(activeAppLocale).t(key);

/// Wire models for the money core.
///
/// Every amount arrives as integer paisa and stays that way. Nothing in this
/// file computes a balance: the server owns the ledger, and a client that
/// derived its own total would eventually disagree with it (master spec
/// sections 64, 80).

// --------------------------------------------------------------------------- //
// Summary and aging
// --------------------------------------------------------------------------- //

@immutable
class AgingBand {
  const AgingBand({
    required this.label,
    required this.minDays,
    required this.parcelCount,
    required this.outstanding,
    this.maxDays,
  });

  factory AgingBand.fromJson(Map<String, dynamic> json) => AgingBand(
    label: json['label'] as String,
    minDays: json['min_days'] as int,
    maxDays: json['max_days'] as int?,
    parcelCount: json['parcel_count'] as int,
    outstanding: Money(json['outstanding_paisa'] as int),
  );

  final String label;
  final int minDays;
  final int? maxDays;
  final int parcelCount;
  final Money outstanding;

  /// Money sitting with a courier for over a week is worth chasing.
  bool get isOverdue => minDays >= 8;
}

@immutable
class MoneySummary {
  const MoneySummary({
    required this.outstanding,
    required this.settled,
    required this.unpaidParcelCount,
    required this.courierCharge,
    required this.codFee,
    required this.returnCharge,
    required this.unknownDeduction,
    required this.writeOff,
    required this.unexplainedPayout,
    required this.openCaseCount,
    required this.aging,
  });

  factory MoneySummary.fromJson(Map<String, dynamic> json) => MoneySummary(
    outstanding: Money(json['outstanding_paisa'] as int),
    settled: Money(json['settled_paisa'] as int),
    unpaidParcelCount: json['unpaid_parcel_count'] as int,
    courierCharge: Money(json['courier_charge_paisa'] as int),
    codFee: Money(json['cod_fee_paisa'] as int),
    returnCharge: Money(json['return_charge_paisa'] as int),
    unknownDeduction: Money(json['unknown_deduction_paisa'] as int),
    writeOff: Money(json['write_off_paisa'] as int),
    unexplainedPayout: Money(json['unexplained_payout_paisa'] as int),
    openCaseCount: json['open_case_count'] as int,
    aging: <AgingBand>[
      for (final band in (json['aging'] as List<dynamic>? ?? const <dynamic>[]))
        AgingBand.fromJson(band as Map<String, dynamic>),
    ],
  );

  /// What couriers are holding right now.
  final Money outstanding;

  /// What has actually arrived.
  final Money settled;

  final int unpaidParcelCount;

  final Money courierCharge;
  final Money codFee;
  final Money returnCharge;

  /// Deductions the server could not classify. Shown in their own right —
  /// folding them into "delivery fee" is what master spec section 84 forbids.
  final Money unknownDeduction;

  final Money writeOff;

  /// Payout money that has not been tied to any parcel yet.
  final Money unexplainedPayout;

  final int openCaseCount;
  final List<AgingBand> aging;

  Money get totalDeductions => Money(
    courierCharge.paisa +
        codFee.paisa +
        returnCharge.paisa +
        unknownDeduction.paisa,
  );

  /// Money that has been waiting more than a week.
  Money get overdue {
    var total = 0;
    for (final band in aging) {
      if (band.isOverdue) {
        total += band.outstanding.paisa;
      }
    }
    return Money(total);
  }

  bool get hasUnknownDeductions => unknownDeduction.paisa > 0;
}

// --------------------------------------------------------------------------- //
// Receivables
// --------------------------------------------------------------------------- //

@immutable
class Receivable {
  const Receivable({
    required this.id,
    required this.consignmentId,
    required this.orderId,
    required this.provider,
    required this.status,
    required this.collectible,
    required this.settled,
    required this.deduction,
    required this.adjustment,
    required this.outstanding,
    required this.version,
    required this.createdAt,
    this.eligibleAt,
    this.settledAt,
    this.statusReason,
    this.ageDays,
    this.orderNumber,
  });

  factory Receivable.fromJson(Map<String, dynamic> json) => Receivable(
    id: json['id'] as String,
    consignmentId: json['consignment_id'] as String,
    orderId: json['order_id'] as String,
    provider: json['provider'] as String,
    status: json['status'] as String,
    collectible: Money(json['collectible_paisa'] as int),
    settled: Money(json['settled_paisa'] as int),
    deduction: Money(json['deduction_paisa'] as int),
    adjustment: Money(json['adjustment_paisa'] as int),
    outstanding: Money(json['outstanding_paisa'] as int),
    version: json['version'] as int? ?? 1,
    createdAt: DateTime.parse(json['created_at'] as String),
    eligibleAt: json['eligible_at'] == null
        ? null
        : DateTime.parse(json['eligible_at'] as String),
    settledAt: json['settled_at'] == null
        ? null
        : DateTime.parse(json['settled_at'] as String),
    statusReason: json['status_reason'] as String?,
    ageDays: json['age_days'] as int?,
    orderNumber: json['order_number'] as String?,
  );

  final String id;
  final String consignmentId;
  final String orderId;
  final String provider;
  final String status;

  /// What the courier collected and therefore owes.
  final Money collectible;
  final Money settled;

  /// What the provider took off.
  final Money deduction;
  final Money adjustment;

  /// What is still owed.
  final Money outstanding;

  final int version;
  final DateTime createdAt;
  final DateTime? eligibleAt;
  final DateTime? settledAt;
  final String? statusReason;
  final int? ageDays;
  final String? orderNumber;

  String get statusLabel => switch (status) {
    'NOT_DUE' => _t('rst.notDue'),
    'EXPECTED' => _t('rst.onItsWay'),
    'ELIGIBLE' => _t('rst.waitingForPayment'),
    'PAYOUT_IDENTIFIED' => _t('rst.paymentIdentified'),
    'PARTIALLY_SETTLED' => _t('rst.partPaid'),
    'SETTLED' => _t('rst.paid'),
    'MISMATCHED' => _t('rst.amountMismatch'),
    'DISPUTED' => _t('rst.disputed'),
    'WRITTEN_OFF' => _t('rst.writtenOff'),
    _ => status,
  };

  bool get isOpen => outstanding.paisa > 0;
  bool get isWaitingTooLong => (ageDays ?? 0) >= 8;
}

// --------------------------------------------------------------------------- //
// Payouts
// --------------------------------------------------------------------------- //

@immutable
class PayoutLine {
  const PayoutLine({
    required this.id,
    required this.rowNumber,
    required this.amount,
    required this.applied,
    required this.status,
    required this.raw,
    required this.candidates,
    this.confidence,
    this.providerConsignmentId,
    this.trackingCode,
    this.merchantReference,
    this.deliveredOn,
    this.receivableId,
    this.matchReason,
  });

  factory PayoutLine.fromJson(Map<String, dynamic> json) => PayoutLine(
    id: json['id'] as String,
    rowNumber: json['row_number'] as int,
    amount: Money(json['amount_paisa'] as int),
    applied: Money(json['applied_paisa'] as int),
    status: json['status'] as String,
    confidence: json['confidence'] as String?,
    providerConsignmentId: json['provider_consignment_id'] as String?,
    trackingCode: json['tracking_code'] as String?,
    merchantReference: json['merchant_reference'] as String?,
    deliveredOn: json['delivered_on'] == null
        ? null
        : DateTime.parse(json['delivered_on'] as String),
    receivableId: json['receivable_id'] as String?,
    matchReason: json['match_reason'] as String?,
    raw: Map<String, dynamic>.from(json['raw'] as Map? ?? const {}),
    candidates: <MatchCandidate>[
      for (final candidate
          in (json['candidates'] as List<dynamic>? ?? const <dynamic>[]))
        MatchCandidate.fromJson(candidate as Map<String, dynamic>),
    ],
  );

  final String id;
  final int rowNumber;
  final Money amount;
  final Money applied;
  final String status;
  final String? confidence;
  final String? providerConsignmentId;
  final String? trackingCode;
  final String? merchantReference;
  final DateTime? deliveredOn;
  final String? receivableId;
  final String? matchReason;

  /// The row exactly as the file contained it.
  final Map<String, dynamic> raw;

  /// What the engine considered, kept so a refusal can be explained.
  final List<MatchCandidate> candidates;

  String get reference =>
      providerConsignmentId ??
      trackingCode ??
      merchantReference ??
      'Row $rowNumber';

  String get statusLabel => switch (status) {
    'UNMATCHED' => _t('pls.notMatched'),
    'SUGGESTED' => _t('pls.checkThis'),
    'MATCHED' => _t('pls.matched'),
    'MANUAL_MATCHED' => _t('pls.matchedByYou'),
    'DUPLICATE' => _t('pls.repeatedLine'),
    'UNMAPPABLE' => _t('pls.couldNotRead'),
    'REVERSED' => _t('pls.undone'),
    _ => status,
  };

  bool get isApplied => status == 'MATCHED' || status == 'MANUAL_MATCHED';
  bool get needsAttention =>
      status == 'UNMATCHED' || status == 'SUGGESTED' || status == 'UNMAPPABLE';

  /// The errors the parser recorded, when the row could not be read.
  List<String> get errors => <String>[
    for (final error in (raw['errors'] as List<dynamic>? ?? const <dynamic>[]))
      error.toString(),
  ];
}

/// A parcel the engine considered for a line.
@immutable
class MatchCandidate {
  const MatchCandidate({
    required this.receivableId,
    required this.merchantReference,
    required this.outstanding,
    required this.score,
    required this.signals,
    this.rejectedBecause,
  });

  factory MatchCandidate.fromJson(Map<String, dynamic> json) => MatchCandidate(
    receivableId: json['receivable_id'] as String,
    merchantReference: json['merchant_reference'] as String? ?? '',
    outstanding: Money(json['outstanding_paisa'] as int? ?? 0),
    score: json['score'] as int? ?? 0,
    signals: <String>[
      for (final signal
          in (json['signals'] as List<dynamic>? ?? const <dynamic>[]))
        signal as String,
    ],
    rejectedBecause: json['rejected_because'] as String?,
  );

  final String receivableId;
  final String merchantReference;
  final Money outstanding;
  final int score;
  final List<String> signals;

  /// Why it was disqualified, if it was. Kept rather than hidden so the seller
  /// can see the engine looked at it.
  final String? rejectedBecause;

  bool get isEligible => rejectedBecause == null;

  /// Seller-facing wording for the scoring signals.
  List<String> get signalLabels => <String>[
    for (final signal in signals)
      switch (signal) {
        'exact_consignment_id' => 'Same parcel ID',
        'exact_tracking_code' => 'Same tracking code',
        'exact_merchant_reference' => 'Same order number',
        'exact_amount' => 'Same amount',
        'amount_within_tolerance' => 'Close amount',
        'delivery_date_in_window' => 'Delivery date fits',
        'same_phone' => 'Same phone',
        'order_number_fragment' => 'Similar order number',
        _ => signal,
      },
  ];
}

@immutable
class PayoutAdjustment {
  const PayoutAdjustment({
    required this.id,
    required this.type,
    required this.amount,
    this.providerLabel,
    this.rawText,
    this.recognizedRule,
  });

  factory PayoutAdjustment.fromJson(Map<String, dynamic> json) =>
      PayoutAdjustment(
        id: json['id'] as String,
        type: json['type'] as String,
        amount: Money(json['amount_paisa'] as int),
        providerLabel: json['provider_label'] as String?,
        rawText: json['raw_text'] as String?,
        recognizedRule: json['recognized_rule'] as String?,
      );

  final String id;
  final String type;
  final Money amount;
  final String? providerLabel;
  final String? rawText;
  final String? recognizedRule;

  /// True when the server could not name this deduction. Shown as such rather
  /// than filed under a familiar heading (master spec section 84).
  bool get isUnknown => type == 'UNKNOWN_DEDUCTION';

  String get typeLabel => switch (type) {
    'COD_FEE' => _t('ded.codFee'),
    'DELIVERY_FEE' => _t('ded.deliveryCharge'),
    'RETURN_FEE' => _t('ded.returnCharge'),
    'TAX' => _t('ded.tax'),
    'BONUS' => _t('ded.bonus'),
    'PENALTY' => _t('ded.penalty'),
    'MANUAL_ADJUSTMENT' => _t('ded.adjustment'),
    'UNKNOWN_DEDUCTION' => _t('ded.unexplained'),
    _ => type,
  };
}

@immutable
class Payout {
  const Payout({
    required this.id,
    required this.provider,
    required this.source,
    required this.status,
    required this.total,
    required this.applied,
    required this.unexplained,
    required this.receivedAt,
    required this.createdAt,
    this.providerReference,
    this.paidOn,
    this.note,
    this.sourceFileId,
    this.lines = const <PayoutLine>[],
    this.adjustments = const <PayoutAdjustment>[],
  });

  factory Payout.fromJson(Map<String, dynamic> json) => Payout(
    id: json['id'] as String,
    provider: json['provider'] as String,
    providerReference: json['provider_reference'] as String?,
    source: json['source'] as String,
    status: json['status'] as String,
    total: Money(json['total_paisa'] as int),
    applied: Money(json['applied_paisa'] as int),
    unexplained: Money(json['unexplained_paisa'] as int),
    paidOn: json['paid_on'] == null
        ? null
        : DateTime.parse(json['paid_on'] as String),
    receivedAt: DateTime.parse(json['received_at'] as String),
    note: json['note'] as String?,
    sourceFileId: json['source_file_id'] as String?,
    createdAt: DateTime.parse(json['created_at'] as String),
    lines: <PayoutLine>[
      for (final line in (json['lines'] as List<dynamic>? ?? const <dynamic>[]))
        PayoutLine.fromJson(line as Map<String, dynamic>),
    ],
    adjustments: <PayoutAdjustment>[
      for (final adjustment
          in (json['adjustments'] as List<dynamic>? ?? const <dynamic>[]))
        PayoutAdjustment.fromJson(adjustment as Map<String, dynamic>),
    ],
  );

  final String id;
  final String provider;
  final String? providerReference;
  final String source;
  final String status;

  /// What the provider says it sent.
  final Money total;

  /// What has been tied to a parcel.
  final Money applied;

  /// What has not. Shown as-is: a payout that does not fully explain itself is
  /// the normal state of a fresh import.
  final Money unexplained;

  final DateTime? paidOn;
  final DateTime receivedAt;
  final String? note;
  final String? sourceFileId;
  final DateTime createdAt;
  final List<PayoutLine> lines;
  final List<PayoutAdjustment> adjustments;

  String get statusLabel => switch (status) {
    'RECEIVED' => _t('pst.notMatchedYet'),
    'PARTIALLY_RECONCILED' => _t('pst.partlyMatched'),
    'RECONCILED' => _t('pst.fullyMatched'),
    _ => status,
  };

  String get sourceLabel => switch (source) {
    'API' => _t('psr.fromCourier'),
    'STATEMENT' => _t('psr.fromStatement'),
    'MANUAL' => _t('psr.enteredByHand'),
    _ => source,
  };

  int get linesNeedingAttention =>
      lines.where((line) => line.needsAttention).length;

  bool get isFullyExplained => unexplained.isZero;
}

/// What a reconciliation run did, or would have done.
@immutable
class ReconcileReport {
  const ReconcileReport({
    required this.payoutId,
    required this.shadow,
    required this.exactMatches,
    required this.suggested,
    required this.unresolved,
    required this.applied,
    required this.casesOpened,
  });

  factory ReconcileReport.fromJson(Map<String, dynamic> json) =>
      ReconcileReport(
        payoutId: json['payout_id'] as String,
        shadow: json['shadow'] as bool? ?? false,
        exactMatches: json['exact_matches'] as int? ?? 0,
        suggested: json['suggested'] as int? ?? 0,
        unresolved: json['unresolved'] as int? ?? 0,
        applied: Money(json['applied_paisa'] as int? ?? 0),
        casesOpened: json['cases_opened'] as int? ?? 0,
      );

  final String payoutId;

  /// True when the run wrote nothing.
  final bool shadow;

  final int exactMatches;
  final int suggested;
  final int unresolved;
  final Money applied;
  final int casesOpened;

  int get totalLines => exactMatches + suggested + unresolved;
}

/// A parsed statement, before anything is created.
@immutable
class StatementPreview {
  const StatementPreview({
    required this.detectedHeaders,
    required this.columnMapping,
    required this.rowCount,
    required this.invalidRowCount,
    required this.total,
    required this.rows,
  });

  factory StatementPreview.fromJson(
    Map<String, dynamic> json,
  ) => StatementPreview(
    detectedHeaders: <String>[
      for (final header
          in (json['detected_headers'] as List<dynamic>? ?? const <dynamic>[]))
        header as String,
    ],
    columnMapping: <String, String>{
      for (final entry
          in (json['column_mapping'] as Map<String, dynamic>? ?? const {})
              .entries)
        entry.key: entry.value as String,
    },
    rowCount: json['row_count'] as int? ?? 0,
    invalidRowCount: json['invalid_row_count'] as int? ?? 0,
    total: Money(json['total_paisa'] as int? ?? 0),
    rows: <StatementRow>[
      for (final row in (json['rows'] as List<dynamic>? ?? const <dynamic>[]))
        StatementRow.fromJson(row as Map<String, dynamic>),
    ],
  );

  final List<String> detectedHeaders;
  final Map<String, String> columnMapping;
  final int rowCount;
  final int invalidRowCount;
  final Money total;
  final List<StatementRow> rows;

  bool get hasProblems => invalidRowCount > 0;
}

@immutable
class StatementRow {
  const StatementRow({
    required this.rowNumber,
    required this.errors,
    this.amount,
    this.merchantReference,
    this.consignmentId,
    this.trackingCode,
    this.feeLabel,
  });

  factory StatementRow.fromJson(Map<String, dynamic> json) => StatementRow(
    rowNumber: json['row_number'] as int,
    amount: json['amount_paisa'] == null
        ? null
        : Money(json['amount_paisa'] as int),
    merchantReference: json['merchant_reference'] as String?,
    consignmentId: json['consignment_id'] as String?,
    trackingCode: json['tracking_code'] as String?,
    feeLabel: json['fee_label'] as String?,
    errors: <String>[
      for (final error
          in (json['errors'] as List<dynamic>? ?? const <dynamic>[]))
        error as String,
    ],
  );

  final int rowNumber;

  /// `null` when the file's amount could not be read. Never coerced to zero.
  final Money? amount;

  final String? merchantReference;
  final String? consignmentId;
  final String? trackingCode;
  final String? feeLabel;
  final List<String> errors;

  bool get isValid => errors.isEmpty;
  String get reference =>
      consignmentId ?? trackingCode ?? merchantReference ?? 'Row $rowNumber';
}

// --------------------------------------------------------------------------- //
// Cases
// --------------------------------------------------------------------------- //

@immutable
class ReconciliationCase {
  const ReconciliationCase({
    required this.id,
    required this.kind,
    required this.status,
    required this.priority,
    required this.amount,
    required this.summary,
    required this.detail,
    required this.openedAt,
    this.receivableId,
    this.payoutId,
    this.payoutLineId,
    this.consignmentId,
    this.resolvedAt,
    this.resolution,
  });

  factory ReconciliationCase.fromJson(Map<String, dynamic> json) =>
      ReconciliationCase(
        id: json['id'] as String,
        kind: json['kind'] as String,
        status: json['status'] as String,
        priority: json['priority'] as String,
        amount: Money(json['amount_paisa'] as int),
        summary: json['summary'] as String,
        detail: Map<String, dynamic>.from(json['detail'] as Map? ?? const {}),
        receivableId: json['receivable_id'] as String?,
        payoutId: json['payout_id'] as String?,
        payoutLineId: json['payout_line_id'] as String?,
        consignmentId: json['consignment_id'] as String?,
        openedAt: DateTime.parse(json['opened_at'] as String),
        resolvedAt: json['resolved_at'] == null
            ? null
            : DateTime.parse(json['resolved_at'] as String),
        resolution: json['resolution'] as String?,
      );

  final String id;
  final String kind;
  final String status;
  final String priority;
  final Money amount;

  /// Written by the server, in plain words. The UI shows it as-is so support
  /// and the seller are reading the same sentence.
  final String summary;

  final Map<String, dynamic> detail;
  final String? receivableId;
  final String? payoutId;
  final String? payoutLineId;
  final String? consignmentId;
  final DateTime openedAt;
  final DateTime? resolvedAt;
  final String? resolution;

  bool get isOpen => status == 'OPEN' || status == 'IN_PROGRESS';
  bool get isHighPriority => priority == 'HIGH';

  String get kindLabel => switch (kind) {
    'DELIVERED_BUT_UNPAID' => _t('ck.deliveredUnpaid'),
    'UNDERPAID' => _t('ck.underpaid'),
    'OVERPAID' => _t('ck.overpaid'),
    'UNKNOWN_DEDUCTION' => _t('ded.unexplained'),
    'DUPLICATE_PAYOUT_LINE' => _t('ck.duplicateLine'),
    'UNMAPPABLE_PAYOUT' => _t('ck.unmappable'),
    'STALE_IN_TRANSIT' => _t('ck.staleInTransit'),
    'RETURNED_NOT_RESTOCKED' => _t('ck.returnNotRestocked'),
    'CHARGE_MISMATCH' => _t('rv2.kind.chargeMismatch'),
    'MISSING_COD' => _t('rv2.kind.missingCod'),
    'RETURN_CHARGE_MISMATCH' => _t('rv2.kind.returnChargeMismatch'),
    _ => kind,
  };

  String get statusLabel => switch (status) {
    'OPEN' => _t('cst.open'),
    'IN_PROGRESS' => _t('cst.withCourier'),
    'RESOLVED' => _t('cst.resolved'),
    'DISMISSED' => _t('cst.dismissed'),
    _ => status,
  };
}

// --------------------------------------------------------------------------- //
// Reconciliation V2: expected against actual
// --------------------------------------------------------------------------- //

Money? _moneyOrNull(Object? value) =>
    value == null ? null : Money(value as int);

/// Headline figures for the reconciliation screen. Every number is the
/// server's; nothing here is summed on the device.
@immutable
class ReconciliationSummary {
  const ReconciliationSummary({
    required this.matched,
    required this.discrepancies,
    required this.unmatched,
    required this.expected,
    required this.actual,
    required this.difference,
    required this.unmatchedAmount,
    required this.chargesPending,
    required this.openCases,
  });

  factory ReconciliationSummary.fromJson(Map<String, dynamic> json) =>
      ReconciliationSummary(
        matched: json['matched'] as int? ?? 0,
        discrepancies: json['discrepancies'] as int? ?? 0,
        unmatched: json['unmatched'] as int? ?? 0,
        expected: Money(json['expected_paisa'] as int? ?? 0),
        actual: Money(json['actual_paisa'] as int? ?? 0),
        difference: Money(json['difference_paisa'] as int? ?? 0),
        unmatchedAmount: Money(json['unmatched_paisa'] as int? ?? 0),
        chargesPending: Money(json['charges_pending_paisa'] as int? ?? 0),
        openCases: json['open_cases'] as int? ?? 0,
      );

  final int matched;
  final int discrepancies;
  final int unmatched;

  /// What the courier should have paid, across the parcels compared.
  final Money expected;

  /// What it did pay for those parcels.
  final Money actual;

  /// `actual - expected`. Negative: the seller received less.
  final Money difference;

  /// Money on rows no parcel could be found for.
  final Money unmatchedAmount;
  final Money chargesPending;
  final int openCases;

  bool get isEmpty => matched == 0 && discrepancies == 0 && unmatched == 0;
}

/// One parcel's expected against actual, or one statement row nobody could
/// place.
@immutable
class ReconciliationItem {
  const ReconciliationItem({
    required this.id,
    required this.status,
    required this.provider,
    required this.actualCod,
    required this.actualCharge,
    required this.actualNet,
    required this.chargesPending,
    required this.lineCount,
    this.reference,
    this.trackingCode,
    this.settlementDate,
    this.expectedCod,
    this.expectedCharge,
    this.expectedChargeSource,
    this.expectedNet,
    this.difference,
    this.caseId,
    this.caseStatus,
    this.unknownDeduction = false,
  });

  factory ReconciliationItem.fromJson(Map<String, dynamic> json) {
    final detail = Map<String, dynamic>.from(
      json['detail'] as Map? ?? const {},
    );
    return ReconciliationItem(
      id: json['id'] as String,
      status: json['status'] as String,
      provider: json['provider'] as String? ?? '',
      reference: json['merchant_reference'] as String?,
      trackingCode: json['tracking_code'] as String?,
      settlementDate: json['settlement_date'] == null
          ? null
          : DateTime.parse(json['settlement_date'] as String),
      expectedCod: _moneyOrNull(json['expected_cod_paisa']),
      actualCod: Money(json['actual_cod_paisa'] as int? ?? 0),
      expectedCharge: _moneyOrNull(json['expected_charge_paisa']),
      expectedChargeSource: json['expected_charge_source'] as String?,
      actualCharge: Money(json['actual_charge_paisa'] as int? ?? 0),
      expectedNet: _moneyOrNull(json['expected_net_paisa']),
      actualNet: Money(json['actual_net_paisa'] as int? ?? 0),
      difference: _moneyOrNull(json['difference_paisa']),
      chargesPending: Money(json['charges_pending_paisa'] as int? ?? 0),
      lineCount: json['line_count'] as int? ?? 0,
      caseId: json['case_id'] as String?,
      caseStatus: json['case_status'] as String?,
      unknownDeduction: detail['unknown_deduction'] == true,
    );
  }

  final String id;
  final String status;
  final String provider;
  final String? reference;
  final String? trackingCode;
  final DateTime? settlementDate;
  final Money? expectedCod;
  final Money actualCod;

  /// Null when no charge was on record: the charge is then unverified, not
  /// zero.
  final Money? expectedCharge;
  final String? expectedChargeSource;
  final Money actualCharge;
  final Money? expectedNet;
  final Money actualNet;
  final Money? difference;
  final Money chargesPending;
  final int lineCount;
  final String? caseId;
  final String? caseStatus;
  final bool unknownDeduction;

  bool get chargeVerified => expectedCharge != null;
  bool get hasPendingCharges => !chargesPending.isZero;

  bool get isDiscrepancy => const <String>{
    'PARTIAL',
    'AMOUNT_MISMATCH',
    'CHARGE_MISMATCH',
    'MISSING_COD',
  }.contains(status);

  String get statusLabel => switch (status) {
    'MATCHED' => _t('rv2.st.matched'),
    'PARTIAL' => _t('rv2.st.partial'),
    'UNMATCHED' => _t('rv2.st.unmatched'),
    'DUPLICATE' => _t('rv2.st.duplicate'),
    'AMOUNT_MISMATCH' => _t('rv2.st.amountMismatch'),
    'CHARGE_MISMATCH' => _t('rv2.st.chargeMismatch'),
    'MISSING_COD' => _t('rv2.st.missingCod'),
    'RETURN_ADJUSTMENT' => _t('rv2.st.returnAdjustment'),
    'NEEDS_REVIEW' => _t('rv2.st.needsReview'),
    _ => status,
  };
}

/// One thing that happened to a case.
@immutable
class CaseEvent {
  const CaseEvent({
    required this.id,
    required this.action,
    required this.createdAt,
    this.note,
    this.byPerson = true,
  });

  factory CaseEvent.fromJson(Map<String, dynamic> json) => CaseEvent(
    id: json['id'] as String,
    action: json['action'] as String,
    note: json['note'] as String?,
    byPerson: json['actor_user_id'] != null,
    createdAt: DateTime.parse(json['created_at'] as String),
  );

  final String id;
  final String action;
  final String? note;

  /// False for the engine's own actions, so they read differently.
  final bool byPerson;
  final DateTime createdAt;

  String get actionLabel => switch (action) {
    'OPENED' => _t('rv2.ev.opened'),
    'NOTE' => _t('rv2.ev.note'),
    'STATUS_CHANGED' => _t('rv2.ev.statusChanged'),
    'REOPENED' => _t('rv2.ev.reopened'),
    'AUTO_RESOLVED' => _t('rv2.ev.autoResolved'),
    'MANUAL_MATCH' => _t('rv2.ev.manualMatch'),
    'CHARGES_ACCEPTED' => _t('rv2.ev.chargesAccepted'),
    _ => action,
  };
}

/// A case with its history.
@immutable
class CaseDetail {
  const CaseDetail({required this.item, required this.events, this.row});

  factory CaseDetail.fromJson(Map<String, dynamic> json) => CaseDetail(
    item: ReconciliationCase.fromJson(json),
    events: <CaseEvent>[
      for (final event in json['events'] as List? ?? const <Object>[])
        CaseEvent.fromJson(event as Map<String, dynamic>),
    ],
    row: json['item'] == null
        ? null
        : ReconciliationItem.fromJson(json['item'] as Map<String, dynamic>),
  );

  final ReconciliationCase item;
  final List<CaseEvent> events;

  /// The expected-against-actual row the case is about, when there is one.
  final ReconciliationItem? row;
}
