import '../../core/api/api_error.dart';
import '../commerce/models.dart' show PagedResult;
import '../commerce/repository_support.dart';
import 'models.dart';

/// Home, Insights, expenses and the notification centre.
///
/// Reads are cached read-through like the rest of the app, so a seller on a
/// dead connection still sees this morning's figures, labelled with when they
/// were true (master spec section 126). Writes are not: recording an expense
/// or allocating one changes profit figures on the server, and a queued
/// allocation would look identical to one that had happened.
class AnalyticsRepository extends CachingRepository {
  AnalyticsRepository({
    required super.api,
    required super.db,
    required this.tenantId,
  });

  @override
  final String? tenantId;

  // --- reading --------------------------------------------------------------

  /// Master spec section 1.1's daily money control screen.
  Future<Sourced<HomeMetrics>> home({DateTime? asOf}) async {
    final day = asOf == null ? null : _isoDate(asOf);
    final sourced = await readThrough(
      'analytics.home${day == null ? '' : '.$day'}',
      () => api.get(
        '/analytics/home',
        query: <String, dynamic>{if (day != null) 'as_of': day},
      ),
    );
    return sourced.map(HomeMetrics.fromJson);
  }

  Future<Sourced<ProfitReport>> profit({
    DateTime? since,
    DateTime? until,
  }) async {
    final sourced = await readThrough(
      'analytics.profit${_windowKey(since, until)}',
      () => api.get('/analytics/profit', query: _window(since, until)),
    );
    return sourced.map(ProfitReport.fromJson);
  }

  Future<Sourced<ReturnReport>> returns({
    DateTime? since,
    DateTime? until,
  }) async {
    final sourced = await readThrough(
      'analytics.returns${_windowKey(since, until)}',
      () => api.get('/analytics/returns', query: _window(since, until)),
    );
    return sourced.map(ReturnReport.fromJson);
  }

  Future<Sourced<List<ProductLine>>> products({
    DateTime? since,
    DateTime? until,
    int limit = 50,
  }) async {
    final sourced = await readThrough(
      'analytics.products${_windowKey(since, until)}',
      () async {
        final rows = await api.getList(
          '/analytics/products',
          query: <String, dynamic>{..._window(since, until), 'limit': limit},
        );
        // `readThrough` caches a JSON object, so the list is wrapped.
        return <String, dynamic>{'rows': rows};
      },
    );
    return sourced.map(
      (json) => <ProductLine>[
        for (final row in (json['rows'] as List<dynamic>? ?? const <dynamic>[]))
          ProductLine.fromJson(row as Map<String, dynamic>),
      ],
    );
  }

  Future<Sourced<WeeklySummary>> weeklySummary({DateTime? weekEnd}) async {
    final day = weekEnd == null ? null : _isoDate(weekEnd);
    final sourced = await readThrough(
      'analytics.weekly${day == null ? '' : '.$day'}',
      () => api.get(
        '/analytics/weekly-summary',
        query: <String, dynamic>{if (day != null) 'week_end': day},
      ),
    );
    return sourced.map(WeeklySummary.fromJson);
  }

  // --- expenses -------------------------------------------------------------

  Future<Sourced<PagedResult<Expense>>> expenses({
    String? cursor,
    int limit = 30,
    ExpenseKind? kind,
  }) async {
    final sourced = await readThrough(
      'analytics.expenses${cursor == null ? '' : '.$cursor'}'
      '${kind == null ? '' : '.${kind.wire}'}',
      () => api.get(
        '/expenses',
        query: pageQuery(
          cursor: cursor,
          limit: limit,
          extra: <String, dynamic>{'kind': kind?.wire},
        ),
      ),
    );
    return sourced.map(
      (json) => PagedResult.parse<Expense>(json, Expense.fromJson),
    );
  }

  /// Record money spent. This changes no profit figure on its own.
  ///
  /// Master spec section 86: an expense reaches orders only through an
  /// explicit allocation, so the seller always knows which number moved.
  Future<Expense> recordExpense({
    required ExpenseKind kind,
    required int amountPaisa,
    required DateTime periodStart,
    required DateTime periodEnd,
    required String description,
    String? productId,
    AllocationMethod method = AllocationMethod.equalPerDeliveredOrder,
  }) async {
    final json = await api.post(
      '/expenses',
      body: <String, dynamic>{
        'kind': kind.wire,
        'amount_paisa': amountPaisa,
        'period_start': _isoDate(periodStart),
        'period_end': _isoDate(periodEnd),
        'description': description,
        if (productId != null) 'product_id': productId,
        'preferred_method': method.wire,
      },
    );
    return Expense.fromJson(json);
  }

  /// Push an expense down onto the parcels it paid for.
  ///
  /// [reason] is required when re-allocating: it rewrites profit figures the
  /// seller may already have read, and section 86 forbids doing that
  /// silently. The server enforces it; this only carries it.
  Future<AllocationResult> allocateExpense(
    String expenseId, {
    AllocationMethod? method,
    String? reason,
  }) async {
    final json = await api.post(
      '/expenses/$expenseId/allocate',
      body: <String, dynamic>{
        if (method != null) 'method': method.wire,
        if (reason != null && reason.isNotEmpty) 'reason': reason,
      },
    );
    return AllocationResult.fromJson(json);
  }

  // --- charges --------------------------------------------------------------

  Future<List<ConsignmentCharge>> chargesFor(String consignmentId) async {
    final rows = await api.getList('/consignments/$consignmentId/charges');
    return <ConsignmentCharge>[
      for (final row in rows)
        ConsignmentCharge.fromJson(row as Map<String, dynamic>),
    ];
  }

  /// Record what a parcel cost.
  ///
  /// This is how profit becomes real in manual courier mode. Without it every
  /// parcel's figure is revenue minus goods, which flatters every margin in
  /// the shop by whatever the courier actually charged.
  Future<ConsignmentCharge> recordCharge(
    String consignmentId, {
    required ChargeKind kind,
    required int amountPaisa,
    ChargeSource source = ChargeSource.seller,
    String? providerLabel,
    String? reason,
  }) async {
    final json = await api.post(
      '/consignments/$consignmentId/charges',
      body: <String, dynamic>{
        'kind': kind.wire,
        'amount_paisa': amountPaisa,
        'source': source.wire,
        if (providerLabel != null) 'provider_label': providerLabel,
        if (reason != null && reason.isNotEmpty) 'reason': reason,
      },
    );
    return ConsignmentCharge.fromJson(json);
  }

  // --- notifications --------------------------------------------------------

  Future<Sourced<PagedResult<AppNotification>>> notifications({
    String? cursor,
    int limit = 30,
    bool unreadOnly = false,
  }) async {
    final sourced = await readThrough(
      'analytics.notifications${cursor == null ? '' : '.$cursor'}'
      '${unreadOnly ? '.unread' : ''}',
      () => api.get(
        '/notifications',
        query: pageQuery(
          cursor: cursor,
          limit: limit,
          extra: <String, dynamic>{'unread_only': unreadOnly},
        ),
      ),
    );
    return sourced.map(
      (json) =>
          PagedResult.parse<AppNotification>(json, AppNotification.fromJson),
    );
  }

  /// The badge count.
  ///
  /// Not cached: a stale badge is worse than none. It fails quietly to zero
  /// offline, because a red dot the seller cannot clear is its own annoyance.
  Future<int> unreadCount() async {
    try {
      final json = await api.get('/notifications/unread-count');
      return json['unread'] as int? ?? 0;
    } on ApiError catch (error) {
      if (error.isOffline) return 0;
      rethrow;
    }
  }

  Future<AppNotification> markRead(String notificationId) async {
    final json = await api.post('/notifications/$notificationId/read');
    return AppNotification.fromJson(json);
  }

  Future<int> markAllRead() async {
    final json = await api.post('/notifications/read-all');
    return json['unread'] as int? ?? 0;
  }

  // --- helpers --------------------------------------------------------------

  Map<String, dynamic> _window(DateTime? since, DateTime? until) =>
      <String, dynamic>{
        if (since != null) 'since': _isoDate(since),
        if (until != null) 'until': _isoDate(until),
      };

  String _windowKey(DateTime? since, DateTime? until) {
    if (since == null && until == null) return '';
    return '.${since == null ? '' : _isoDate(since)}'
        '-${until == null ? '' : _isoDate(until)}';
  }

  static String _isoDate(DateTime value) =>
      '${value.year.toString().padLeft(4, '0')}-'
      '${value.month.toString().padLeft(2, '0')}-'
      '${value.day.toString().padLeft(2, '0')}';
}
