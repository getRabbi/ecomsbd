import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../commerce/models.dart' show PagedResult;
import '../commerce/repository_support.dart';
import 'models.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/app_locale.dart';

/// Seller-facing labels below are read where no `BuildContext` exists, so
/// they resolve against the active locale directly — the same approach
/// `formatRelative` uses. A language change rebuilds the tree, so the next
/// paint is already in the new language.
String _t(String key) => AppStrings(activeAppLocale).t(key);

/// The money core.
///
/// Read-through cached like the commerce repositories: the summary and the
/// receivable list survive a dead connection, labelled with when they were
/// true. Everything that *moves* money — reconciling, matching, correcting —
/// needs a connection and says so, because a settlement queued offline would
/// look identical to one that happened.
class MoneyRepository extends CachingRepository {
  MoneyRepository({
    required super.api,
    required super.db,
    required this.tenantId,
  });

  @override
  final String? tenantId;

  // --- reading --------------------------------------------------------------

  Future<Sourced<MoneySummary>> summary() async {
    final sourced = await readThrough(
      'money.summary',
      () => api.get('/money/summary'),
    );
    return sourced.map(MoneySummary.fromJson);
  }

  Future<Sourced<List<AgingBand>>> aging({String? provider}) async {
    final sourced = await readThrough(
      'money.aging${provider == null ? '' : '.$provider'}',
      () async {
        final bands = await api.getList(
          '/money/aging',
          query: <String, dynamic>{if (provider != null) 'provider': provider},
        );
        // `readThrough` caches a JSON object, so the list is wrapped.
        return <String, dynamic>{'bands': bands};
      },
    );
    return sourced.map(
      (json) => <AgingBand>[
        for (final band
            in (json['bands'] as List<dynamic>? ?? const <dynamic>[]))
          AgingBand.fromJson(band as Map<String, dynamic>),
      ],
    );
  }

  Future<Sourced<PagedResult<Receivable>>> receivables({
    String? cursor,
    int limit = 30,
    String? status,
    String? provider,
    bool openOnly = false,
  }) async {
    final sourced = await readThrough(
      'money.receivables${cursor == null ? '' : '.$cursor'}'
      '${openOnly ? '.open' : ''}${status == null ? '' : '.$status'}',
      () => api.get(
        '/money/receivables',
        query: pageQuery(
          cursor: cursor,
          limit: limit,
          extra: <String, dynamic>{
            'status': status,
            'provider': provider,
            'open_only': openOnly,
          },
        ),
      ),
    );
    return sourced.map(
      (json) => PagedResult.parse<Receivable>(json, Receivable.fromJson),
    );
  }

  /// Every money event behind one parcel, oldest first.
  ///
  /// The answer to "why does this say ৳0 outstanding?".
  Future<List<LedgerEntry>> ledgerFor(String receivableId) async {
    final entries = await api.getList(
      '/money/receivables/$receivableId/ledger',
    );
    return <LedgerEntry>[
      for (final entry in entries)
        LedgerEntry.fromJson(entry as Map<String, dynamic>),
    ];
  }

  Future<Sourced<PagedResult<Payout>>> payouts({
    String? cursor,
    int limit = 30,
    String? provider,
    String? status,
  }) async {
    final sourced = await readThrough(
      'money.payouts${cursor == null ? '' : '.$cursor'}',
      () => api.get(
        '/payouts',
        query: pageQuery(
          cursor: cursor,
          limit: limit,
          extra: <String, dynamic>{'provider': provider, 'status': status},
        ),
      ),
    );
    return sourced.map(
      (json) => PagedResult.parse<Payout>(json, Payout.fromJson),
    );
  }

  Future<Payout> payout(String payoutId) async {
    return Payout.fromJson(await api.get('/payouts/$payoutId'));
  }

  Future<Sourced<PagedResult<ReconciliationCase>>> cases({
    String? cursor,
    int limit = 30,
    String? status,
    String? kind,
  }) async {
    final sourced = await readThrough(
      'money.cases${cursor == null ? '' : '.$cursor'}'
      '${status == null ? '' : '.$status'}${kind == null ? '' : '.$kind'}',
      () => api.get(
        '/reconciliation/cases',
        query: pageQuery(
          cursor: cursor,
          limit: limit,
          extra: <String, dynamic>{'status': status, 'kind': kind},
        ),
      ),
    );
    return sourced.map(
      (json) => PagedResult.parse<ReconciliationCase>(
        json,
        ReconciliationCase.fromJson,
      ),
    );
  }

  // --- writing --------------------------------------------------------------
  //
  // None of these is queued offline. A settlement sitting in an outbox would
  // be indistinguishable, on the Money screen, from money that actually
  // arrived — and that is the one confusion this whole product exists to
  // prevent (master spec section 1.4).

  Future<Payout> recordManualPayout({
    required int totalPaisa,
    String provider = 'manual',
    DateTime? paidOn,
    String? reference,
    String? note,
  }) async {
    final json = await api.post(
      '/payouts/manual',
      body: <String, dynamic>{
        'provider': provider,
        'total_paisa': totalPaisa,
        if (paidOn != null) 'paid_on': _dateOnly(paidOn),
        if (reference != null && reference.isNotEmpty) 'reference': reference,
        if (note != null && note.isNotEmpty) 'note': note,
      },
      // The seller could tap twice on a bad connection, and two ৳48,050
      // payouts would look entirely plausible.
      idempotencyKey: api.newIdempotencyKey(),
    );
    return Payout.fromJson(json);
  }

  /// Parse a statement without saving it.
  Future<StatementPreview> previewStatement({
    required List<int> bytes,
    required String filename,
  }) async {
    final json = await api.postFile(
      '/payouts/preview',
      bytes: bytes,
      filename: filename,
    );
    return StatementPreview.fromJson(json);
  }

  Future<Payout> importStatement({
    required List<int> bytes,
    required String filename,
    required String provider,
    String? reference,
    DateTime? paidOn,
    String? note,
  }) async {
    final json = await api.postFile(
      '/payouts/import',
      bytes: bytes,
      filename: filename,
      fields: <String, dynamic>{
        'provider': provider,
        if (reference != null && reference.isNotEmpty) 'reference': reference,
        if (paidOn != null) 'paid_on': _dateOnly(paidOn),
        if (note != null && note.isNotEmpty) 'note': note,
      },
    );
    return Payout.fromJson(json);
  }

  /// Match a payout's lines.
  ///
  /// With `shadow: true` the server changes nothing and reports what it would
  /// have done — the honest way to look at a statement before letting it move
  /// money.
  Future<ReconcileReport> reconcile(
    String payoutId, {
    bool shadow = false,
  }) async {
    final json = await api.post(
      '/reconciliation/payouts/$payoutId/reconcile'
      '${shadow ? '?shadow=true' : ''}',
    );
    return ReconcileReport.fromJson(json);
  }

  Future<PayoutLine> matchLine(
    String lineId, {
    required String receivableId,
    required String reason,
    int? amountPaisa,
  }) async {
    final json = await api.post(
      '/reconciliation/lines/$lineId/match',
      body: <String, dynamic>{
        'receivable_id': receivableId,
        'reason': reason,
        if (amountPaisa != null) 'amount_paisa': amountPaisa,
      },
    );
    return PayoutLine.fromJson(json);
  }

  Future<PayoutLine> unmatchLine(
    String lineId, {
    required String reason,
  }) async {
    final json = await api.post(
      '/reconciliation/lines/$lineId/unmatch',
      body: <String, dynamic>{'reason': reason},
    );
    return PayoutLine.fromJson(json);
  }

  Future<int> scanForCases() async {
    final json = await api.post('/reconciliation/scan');
    return json['cases_opened'] as int? ?? 0;
  }

  /// Expected, actual and the difference. Cached like the Money summary so
  /// the screen opens on the last figures while it refreshes.
  Future<Sourced<ReconciliationSummary>> reconciliationSummary() async {
    final sourced = await readThrough(
      'money.reconciliation.summary',
      () => api.get('/reconciliation/summary'),
    );
    return sourced.map(ReconciliationSummary.fromJson);
  }

  /// Parcels compared expected-against-actual, filtered on the server.
  Future<Sourced<PagedResult<ReconciliationItem>>> reconciliationItems({
    String? cursor,
    int limit = 30,
    String? view,
  }) async {
    final extra = switch (view) {
      'discrepancies' => <String, dynamic>{'discrepancies_only': true},
      'unmatched' => <String, dynamic>{
        'status': <String>['UNMATCHED', 'NEEDS_REVIEW', 'DUPLICATE'],
      },
      _ => <String, dynamic>{},
    };
    final sourced = await readThrough(
      'money.reconciliation.items${view == null ? '' : '.$view'}'
      '${cursor == null ? '' : '.$cursor'}',
      () => api.get(
        '/reconciliation/items',
        query: pageQuery(cursor: cursor, limit: limit, extra: extra),
      ),
    );
    return sourced.map(
      (json) => PagedResult.parse<ReconciliationItem>(
        json,
        ReconciliationItem.fromJson,
      ),
    );
  }

  Future<CaseDetail> caseDetail(String caseId) async {
    return CaseDetail.fromJson(await api.get('/reconciliation/cases/$caseId'));
  }

  Future<void> addCaseNote(String caseId, String note) async {
    await api.post(
      '/reconciliation/cases/$caseId/notes',
      body: <String, dynamic>{'note': note},
    );
  }

  /// Accept what the courier kept on one parcel. The server writes it to the
  /// ledger once; a retry finds nothing left to accept.
  Future<int> acceptCharges(String itemId) async {
    final json = await api.post(
      '/reconciliation/items/$itemId/accept-charges',
      body: const <String, dynamic>{},
    );
    return json['accepted_paisa'] as int? ?? 0;
  }

  Future<ReconciliationCase> updateCase(
    String caseId, {
    required String status,
    String? resolution,
  }) async {
    final json = await api.patch(
      '/reconciliation/cases/$caseId',
      body: <String, dynamic>{
        'status': status,
        if (resolution != null) 'resolution': resolution,
      },
    );
    return ReconciliationCase.fromJson(json);
  }

  /// Correct what a parcel is owed.
  ///
  /// States a change, never a new balance, and requires a reason. Master spec
  /// section 134: never directly update a settled total.
  Future<Receivable> correct(
    String receivableId, {
    required int amountPaisa,
    required bool increasesBalance,
    required String reason,
  }) async {
    final json = await api.post(
      '/money/receivables/$receivableId/correction',
      body: <String, dynamic>{
        'amount_paisa': amountPaisa,
        'increases_balance': increasesBalance,
        'reason': reason,
      },
    );
    return Receivable.fromJson(json);
  }

  Future<Receivable> writeOff(
    String receivableId, {
    required String reason,
  }) async {
    final json = await api.post(
      '/money/receivables/$receivableId/write-off',
      body: <String, dynamic>{'reason': reason},
    );
    return Receivable.fromJson(json);
  }

  Future<Receivable> dispute(
    String receivableId, {
    required String reason,
  }) async {
    final json = await api.post(
      '/money/receivables/$receivableId/dispute',
      body: <String, dynamic>{'reason': reason},
    );
    return Receivable.fromJson(json);
  }

  // --- consignments ---------------------------------------------------------

  /// Record that a parcel has gone out with a courier.
  ///
  /// Manual mode. No provider is contacted — booking a real courier arrives
  /// with the Steadfast adapter, and this is the path that works today.
  Future<Map<String, dynamic>> dispatch(
    String orderId, {
    String provider = 'manual',
    String? trackingCode,
    int? codAmountPaisa,
  }) {
    return api.post(
      '/consignments/orders/$orderId/dispatch',
      body: <String, dynamic>{
        'provider': provider,
        if (trackingCode != null && trackingCode.isNotEmpty)
          'tracking_code': trackingCode,
        if (codAmountPaisa != null) 'cod_amount_paisa': codAmountPaisa,
      },
      idempotencyKey: api.newIdempotencyKey(),
    );
  }

  /// Record how a parcel ended.
  Future<Map<String, dynamic>> recordOutcome(
    String consignmentId, {
    required String status,
    List<Map<String, dynamic>> items = const <Map<String, dynamic>>[],
    String? note,
    String? returnReason,
  }) {
    return api.post(
      '/consignments/$consignmentId/outcome',
      body: <String, dynamic>{
        'status': status,
        if (items.isNotEmpty) 'items': items,
        if (note != null && note.isNotEmpty) 'note': note,
        // Master spec section 19 wants the reason. Left out entirely when the
        // seller did not pick one: an invented reason is worse for the return
        // report than a missing one, because it looks like evidence.
        if (returnReason != null) 'return_reason': returnReason,
      },
    );
  }

  Future<Map<String, dynamic>> consignment(String consignmentId) {
    return api.get('/consignments/$consignmentId');
  }

  /// True when a failure means "you need a connection for this".
  static bool needsConnection(Object error) =>
      error is ApiError && error.isOffline;

  static String _dateOnly(DateTime value) =>
      value.toIso8601String().substring(0, 10);
}

/// One line of the money history.
class LedgerEntry {
  const LedgerEntry({
    required this.id,
    required this.occurredAt,
    required this.eventType,
    required this.amount,
    required this.direction,
    required this.bucket,
    required this.source,
    this.sourceRef,
    this.reversalOf,
    this.reason,
  });

  factory LedgerEntry.fromJson(Map<String, dynamic> json) => LedgerEntry(
    id: json['id'] as String,
    occurredAt: DateTime.parse(json['occurred_at'] as String),
    eventType: json['event_type'] as String,
    amount: Money(json['amount_paisa'] as int),
    direction: json['direction'] as String,
    bucket: json['bucket'] as String,
    source: json['source'] as String,
    sourceRef: json['source_ref'] as String?,
    reversalOf: json['reversal_of'] as String?,
    reason: json['reason'] as String?,
  );

  final String id;
  final DateTime occurredAt;
  final String eventType;
  final Money amount;
  final String direction;
  final String bucket;
  final String source;
  final String? sourceRef;

  /// Set when this entry undoes another. Both stay in the history.
  final String? reversalOf;

  final String? reason;

  bool get isCredit => direction == 'CREDIT';
  bool get isReversal => reversalOf != null;

  String get eventLabel => switch (eventType) {
    'DELIVERY_CONFIRMED' => _t('ev.deliveredOwed'),
    'PARTIAL_DELIVERY_CONFIRMED' => _t('ev.partDeliveredOwed'),
    'RETURN_CONFIRMED' => _t('ev.returnedNothingOwed'),
    'PAYOUT_APPLIED' => _t('ev.payoutApplied'),
    'PAYOUT_REVERSED' => _t('ev.payoutReversed'),
    'PROVIDER_DEDUCTION' => _t('ev.providerDeduction'),
    'COURIER_CHARGE_APPLIED' => _t('ev.courierCharge'),
    'RETURN_FEE_APPLIED' => _t('ev.returnFee'),
    'COD_FEE_APPLIED' => _t('ev.codFee'),
    'MANUAL_ADJUSTMENT' => _t('ev.yourCorrection'),
    'WRITE_OFF' => _t('ev.writeOff'),
    'REVERSAL' => _t('ev.reversal'),
    _ => eventType,
  };
}
