import '../../core/money.dart';

/// Return / RTO intelligence, as the server computed it.
///
/// Nothing here computes a rate: every percentage is the server's basis-point
/// figure, shown next to the counts it came from, so the phone and the web
/// always say the same thing.

int _int(Object? value) => (value as num?)?.toInt() ?? 0;
int? _intOrNull(Object? value) => (value as num?)?.toInt();

String _pct(int bps) {
  final whole = bps ~/ 100;
  final tenth = (bps % 100) ~/ 10;
  return tenth == 0 ? '$whole%' : '$whole.$tenth%';
}

/// Completed parcels by outcome. RTO = returned + cancelled at the courier.
class RtoCounts {
  const RtoCounts({
    required this.completed,
    required this.delivered,
    required this.partial,
    required this.rto,
    required this.returned,
    required this.courierCancelled,
    required this.lost,
    required this.sufficient,
    this.rtoRateBps,
    this.successRateBps,
  });

  factory RtoCounts.fromJson(Map<String, dynamic>? json) {
    final j = json ?? const <String, dynamic>{};
    return RtoCounts(
      completed: _int(j['completed']),
      delivered: _int(j['delivered']),
      partial: _int(j['partial']),
      rto: _int(j['rto']),
      returned: _int(j['returned']),
      courierCancelled: _int(j['courier_cancelled']),
      lost: _int(j['lost']),
      rtoRateBps: _intOrNull(j['rto_rate_basis_points']),
      successRateBps: _intOrNull(j['success_rate_basis_points']),
      sufficient: j['sufficient'] as bool? ?? false,
    );
  }

  static const RtoCounts empty = RtoCounts(
    completed: 0,
    delivered: 0,
    partial: 0,
    rto: 0,
    returned: 0,
    courierCancelled: 0,
    lost: 0,
    sufficient: false,
  );

  final int completed;
  final int delivered;
  final int partial;
  final int rto;
  final int returned;
  final int courierCancelled;
  final int lost;
  final int? rtoRateBps;
  final int? successRateBps;

  /// Enough completed parcels for the rate to be compared or trended.
  final bool sufficient;

  /// `12.5%`, or `—` with nothing completed.
  String get rateLabel => rtoRateBps == null ? '—' : _pct(rtoRateBps!);
}

class RtoWindow {
  const RtoWindow({required this.days, required this.counts});

  factory RtoWindow.fromJson(Map<String, dynamic> json) => RtoWindow(
    days: _int(json['days']),
    counts: RtoCounts.fromJson(json['counts'] as Map<String, dynamic>?),
  );

  final int days;
  final RtoCounts counts;
}

class RtoSummary {
  const RtoSummary({
    required this.days,
    required this.counts,
    required this.openNow,
    required this.cancelledBeforeDispatch,
    required this.windows,
    required this.minSample,
  });

  factory RtoSummary.fromJson(Map<String, dynamic> json) => RtoSummary(
    days: _int(json['days']),
    counts: RtoCounts.fromJson(json['counts'] as Map<String, dynamic>?),
    openNow: _int(json['open_now']),
    cancelledBeforeDispatch: _int(json['cancelled_before_dispatch']),
    windows: <RtoWindow>[
      for (final row in json['windows'] as List<dynamic>? ?? const <dynamic>[])
        RtoWindow.fromJson(row as Map<String, dynamic>),
    ],
    minSample: _int(
      (json['definition'] as Map<String, dynamic>?)?['min_sample'] ?? 10,
    ),
  );

  final int days;
  final RtoCounts counts;
  final int openNow;
  final int cancelledBeforeDispatch;
  final List<RtoWindow> windows;
  final int minSample;
}

class RtoWeek {
  const RtoWeek({required this.weekStart, required this.counts});

  factory RtoWeek.fromJson(Map<String, dynamic> json) => RtoWeek(
    weekStart: DateTime.tryParse(json['week_start'] as String? ?? ''),
    counts: RtoCounts.fromJson(json['counts'] as Map<String, dynamic>?),
  );

  final DateTime? weekStart;
  final RtoCounts counts;
}

class ProductRto {
  const ProductRto({
    required this.productName,
    required this.counts,
    required this.rtoValue,
    this.productId,
  });

  factory ProductRto.fromJson(Map<String, dynamic> json) => ProductRto(
    productId: json['product_id'] as String?,
    productName: json['product_name'] as String? ?? '',
    counts: RtoCounts.fromJson(json['counts'] as Map<String, dynamic>?),
    rtoValue: Money(_int(json['rto_value_paisa'])),
  );

  final String? productId;
  final String productName;
  final RtoCounts counts;

  /// Line value of this product inside returned parcels.
  final Money rtoValue;
}

/// One server page of products. The list is paged on the server; the phone
/// never holds more than it has asked for.
class ProductRtoPage {
  const ProductRtoPage({
    required this.items,
    required this.total,
    required this.offset,
    required this.hasMore,
  });

  factory ProductRtoPage.fromJson(Map<String, dynamic> json) => ProductRtoPage(
    items: <ProductRto>[
      for (final row in json['items'] as List<dynamic>? ?? const <dynamic>[])
        ProductRto.fromJson(row as Map<String, dynamic>),
    ],
    total: _int(json['total']),
    offset: _int(json['offset']),
    hasMore: json['has_more'] as bool? ?? false,
  );

  final List<ProductRto> items;
  final int total;
  final int offset;
  final bool hasMore;
}

class CourierRto {
  const CourierRto({
    required this.provider,
    required this.counts,
    required this.recent,
    required this.previous,
    required this.trend,
    required this.inTransitNow,
  });

  factory CourierRto.fromJson(Map<String, dynamic> json) => CourierRto(
    provider: json['provider'] as String? ?? '',
    counts: RtoCounts.fromJson(json['counts'] as Map<String, dynamic>?),
    recent: RtoCounts.fromJson(json['recent'] as Map<String, dynamic>?),
    previous: RtoCounts.fromJson(json['previous'] as Map<String, dynamic>?),
    trend: json['trend'] as String? ?? 'INSUFFICIENT_DATA',
    inTransitNow: _int(json['in_transit_now']),
  );

  final String provider;
  final RtoCounts counts;
  final RtoCounts recent;
  final RtoCounts previous;

  /// `UP`, `DOWN`, `FLAT` or `INSUFFICIENT_DATA`.
  final String trend;
  final int inTransitNow;
}

class CourierRtoReport {
  const CourierRtoReport({required this.items, required this.excluded});

  factory CourierRtoReport.fromJson(
    Map<String, dynamic> json,
  ) => CourierRtoReport(
    items: <CourierRto>[
      for (final row in json['items'] as List<dynamic>? ?? const <dynamic>[])
        CourierRto.fromJson(row as Map<String, dynamic>),
    ],
    excluded: <String>[
      for (final p
          in json['excluded_providers'] as List<dynamic>? ?? const <dynamic>[])
        p as String,
    ],
  );

  final List<CourierRto> items;
  final List<String> excluded;
}

class AreaRow {
  const AreaRow({required this.label, required this.counts});

  final String label;
  final RtoCounts counts;
}

class AreaRtoReport {
  const AreaRtoReport({
    required this.reliable,
    required this.items,
    this.coverageBps,
  });

  factory AreaRtoReport.fromJson(Map<String, dynamic> json) => AreaRtoReport(
    reliable: json['status'] == 'ACTIVE',
    coverageBps: _intOrNull(json['coverage_basis_points']),
    items: <AreaRow>[
      for (final row in json['items'] as List<dynamic>? ?? const <dynamic>[])
        AreaRow(
          label: (row as Map<String, dynamic>)['label'] as String? ?? '',
          counts: RtoCounts.fromJson(row['counts'] as Map<String, dynamic>?),
        ),
    ],
  );

  /// `false` is the server's `DATA_NOT_RELIABLE`: no rows are compared.
  final bool reliable;
  final int? coverageBps;
  final List<AreaRow> items;

  String get coverageLabel => coverageBps == null ? '0%' : _pct(coverageBps!);
}

/// A factual pattern, with the counts that make it true.
class RtoObservation {
  const RtoObservation({
    required this.code,
    required this.parcelCount,
    required this.rtoCount,
    required this.deliveredCount,
    required this.earlierParcelCount,
    required this.earlierRtoCount,
    this.productName,
  });

  factory RtoObservation.fromJson(Map<String, dynamic> json) => RtoObservation(
    code: json['code'] as String? ?? '',
    parcelCount: _int(json['parcel_count']),
    rtoCount: _int(json['rto_count']),
    deliveredCount: _int(json['delivered_count']),
    earlierParcelCount: _int(json['earlier_parcel_count']),
    earlierRtoCount: _int(json['earlier_rto_count']),
    productName: json['product_name'] as String?,
  );

  final String code;
  final int parcelCount;
  final int rtoCount;
  final int deliveredCount;
  final int earlierParcelCount;
  final int earlierRtoCount;
  final String? productName;

  /// Catalogue key, or `null` for a code this build does not know.
  String? get labelKey => switch (code) {
    'REPEAT_RTO' ||
    'RTO_AFTER_DELIVERIES' ||
    'WORSENING' ||
    'IMPROVING' ||
    'PRODUCT_REPEAT_RTO' => 'rto.obs.$code',
    _ => null,
  };

  Map<String, Object?> get vars => <String, Object?>{
    'rto': rtoCount,
    'parcels': parcelCount,
    'delivered': deliveredCount,
    'earlier': earlierParcelCount,
    'earlierRto': earlierRtoCount,
    'earlierDelivered': earlierParcelCount - earlierRtoCount,
    'product': productName ?? '',
  };

  static List<RtoObservation> listFrom(Object? raw) => <RtoObservation>[
    for (final row in raw as List<dynamic>? ?? const <dynamic>[])
      RtoObservation.fromJson(row as Map<String, dynamic>),
  ];
}

class ParcelEvent {
  const ParcelEvent({
    required this.orderNumber,
    required this.outcome,
    required this.provider,
    this.at,
  });

  factory ParcelEvent.fromJson(Map<String, dynamic> json) => ParcelEvent(
    orderNumber: json['order_number'] as String? ?? '',
    outcome: json['outcome'] as String? ?? '',
    provider: json['provider'] as String? ?? '',
    at: DateTime.tryParse(json['at'] as String? ?? ''),
  );

  final String orderNumber;

  /// `DELIVERED`, `PARTIAL`, `RTO`, `LOST` or `IN_TRANSIT`.
  final String outcome;
  final String provider;
  final DateTime? at;

  static List<ParcelEvent> listFrom(Object? raw) => <ParcelEvent>[
    for (final row in raw as List<dynamic>? ?? const <dynamic>[])
      ParcelEvent.fromJson(row as Map<String, dynamic>),
  ];
}

class CustomerPattern {
  const CustomerPattern({
    required this.customerId,
    required this.counts,
    required this.observations,
    this.name,
    this.phoneMasked,
  });

  factory CustomerPattern.fromJson(Map<String, dynamic> json) =>
      CustomerPattern(
        customerId: json['customer_id'] as String? ?? '',
        name: json['name'] as String?,
        phoneMasked: json['phone_masked'] as String?,
        counts: RtoCounts.fromJson(json['counts'] as Map<String, dynamic>?),
        observations: RtoObservation.listFrom(json['observations']),
      );

  final String customerId;
  final String? name;
  final String? phoneMasked;
  final RtoCounts counts;
  final List<RtoObservation> observations;
}

class CustomerRtoHistory {
  const CustomerRtoHistory({
    required this.customerId,
    required this.orderCount,
    required this.counts,
    required this.inTransit,
    required this.cancelledBeforeDispatch,
    required this.recent,
    required this.observations,
    required this.riskState,
    this.name,
    this.phoneMasked,
    this.lastOrderAt,
  });

  factory CustomerRtoHistory.fromJson(Map<String, dynamic> json) =>
      CustomerRtoHistory(
        customerId: json['customer_id'] as String? ?? '',
        name: json['name'] as String?,
        phoneMasked: json['phone_masked'] as String?,
        orderCount: _int(json['order_count']),
        counts: RtoCounts.fromJson(json['counts'] as Map<String, dynamic>?),
        inTransit: _int(json['in_transit_count']),
        cancelledBeforeDispatch: _int(json['cancelled_before_dispatch']),
        lastOrderAt: DateTime.tryParse(json['last_order_at'] as String? ?? ''),
        recent: ParcelEvent.listFrom(json['recent']),
        observations: RtoObservation.listFrom(json['observations']),
        riskState: json['risk_state'] as String? ?? 'INSUFFICIENT_DATA',
      );

  final String customerId;
  final String? name;
  final String? phoneMasked;
  final int orderCount;
  final RtoCounts counts;
  final int inTransit;
  final int cancelledBeforeDispatch;
  final DateTime? lastOrderAt;
  final List<ParcelEvent> recent;
  final List<RtoObservation> observations;
  final String riskState;
}
