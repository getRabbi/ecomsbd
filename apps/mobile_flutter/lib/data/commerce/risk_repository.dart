import '../../core/api/api_client.dart';
import '../../design/components/badges.dart';
import '../analytics/rto_models.dart';

/// Why a band came out the way it did, in the server's machine codes.
///
/// The API ships codes rather than sentences so the copy lives in one place —
/// this app's own catalogue — and is translated once rather than twice.
enum RiskReason {
  noOrders,
  smallSample,
  strongDeliveryRate,
  mixedDeliveryRate,
  weakDeliveryRate,
  hasReturns,
  hasCancellations,
  ownShopHistoryOnly,
  unknown;

  static RiskReason parse(String code) => switch (code) {
    'NO_ORDERS' => RiskReason.noOrders,
    'SMALL_SAMPLE' => RiskReason.smallSample,
    'STRONG_DELIVERY_RATE' => RiskReason.strongDeliveryRate,
    'MIXED_DELIVERY_RATE' => RiskReason.mixedDeliveryRate,
    'WEAK_DELIVERY_RATE' => RiskReason.weakDeliveryRate,
    'HAS_RETURNS' => RiskReason.hasReturns,
    'HAS_CANCELLATIONS' => RiskReason.hasCancellations,
    'OWN_SHOP_HISTORY_ONLY' => RiskReason.ownShopHistoryOnly,
    _ => RiskReason.unknown,
  };

  /// The catalogue key for this reason's sentence.
  String get labelKey => switch (this) {
    RiskReason.noOrders => 'rc.reason.noOrders',
    RiskReason.smallSample => 'rc.reason.smallSample',
    RiskReason.strongDeliveryRate => 'rc.reason.strongRate',
    RiskReason.mixedDeliveryRate => 'rc.reason.mixedRate',
    RiskReason.weakDeliveryRate => 'rc.reason.weakRate',
    RiskReason.hasReturns => 'rc.reason.hasReturns',
    RiskReason.hasCancellations => 'rc.reason.hasCancellations',
    RiskReason.ownShopHistoryOnly => 'rc.reason.ownShopOnly',
    RiskReason.unknown => 'rc.reason.unknown',
  };
}

/// One customer's delivery history with *this* shop, and the band it implies.
///
/// Every number the screen shows comes from this object, and the band is a
/// function of two of them. That is the point: a seller who disagrees with
/// "High risk" can read the counts directly underneath it (master spec
/// sections 24, 121, 130).
class RiskCheck {
  const RiskCheck({
    required this.found,
    required this.state,
    required this.orderCount,
    required this.deliveredCount,
    required this.returnedCount,
    required this.cancelledCount,
    required this.terminalCount,
    required this.reasons,
    this.customerId,
    this.name,
    this.phoneMasked,
    this.successRateBasisPoints,
    this.firstOrderAt,
    this.lastOrderAt,
    this.checksRemaining,
    this.parcels,
    this.inTransitCount = 0,
    this.recent = const <ParcelEvent>[],
    this.observations = const <RtoObservation>[],
  });

  factory RiskCheck.fromJson(Map<String, dynamic> json) {
    return RiskCheck(
      found: json['found'] as bool? ?? false,
      state: json['state'] as String? ?? 'INSUFFICIENT_DATA',
      customerId: json['customer_id'] as String?,
      name: json['name'] as String?,
      phoneMasked: json['phone_masked'] as String?,
      orderCount: json['order_count'] as int? ?? 0,
      deliveredCount: json['delivered_count'] as int? ?? 0,
      returnedCount: json['returned_count'] as int? ?? 0,
      cancelledCount: json['cancelled_count'] as int? ?? 0,
      terminalCount: json['terminal_count'] as int? ?? 0,
      successRateBasisPoints: json['success_rate_basis_points'] as int?,
      firstOrderAt: DateTime.tryParse(json['first_order_at'] as String? ?? ''),
      lastOrderAt: DateTime.tryParse(json['last_order_at'] as String? ?? ''),
      reasons: <RiskReason>[
        for (final code in (json['reasons'] as List<dynamic>? ?? <dynamic>[]))
          RiskReason.parse(code as String),
      ],
      checksRemaining: json['checks_remaining'] as int?,
      parcels: json['parcels'] == null
          ? null
          : RtoCounts.fromJson(json['parcels'] as Map<String, dynamic>),
      inTransitCount: json['in_transit_count'] as int? ?? 0,
      recent: ParcelEvent.listFrom(json['recent']),
      observations: RtoObservation.listFrom(json['observations']),
    );
  }

  final bool found;

  /// `LOW`, `MEDIUM`, `HIGH` or `INSUFFICIENT_DATA`.
  final String state;

  final String? customerId;
  final String? name;
  final String? phoneMasked;

  final int orderCount;
  final int deliveredCount;
  final int returnedCount;
  final int cancelledCount;

  /// Orders that reached an outcome — the denominator behind the rate.
  final int terminalCount;
  final int? successRateBasisPoints;

  final DateTime? firstOrderAt;
  final DateTime? lastOrderAt;
  final List<RiskReason> reasons;

  /// Checks left in today's quota; `null` when the plan is unlimited.
  final int? checksRemaining;

  /// The same history as parcels (V2.2): RTO = returned + cancelled at the
  /// courier, over completed parcels only. `null` for an unknown number.
  final RtoCounts? parcels;
  final int inTransitCount;
  final List<ParcelEvent> recent;
  final List<RtoObservation> observations;

  /// How the badge should render this.
  ///
  /// `INSUFFICIENT_DATA` maps to [RiskLevel.unknown] rather than inventing a
  /// fifth band: "no history" is exactly what the neutral chip already says.
  RiskLevel get level => switch (state) {
    'LOW' => RiskLevel.low,
    'MEDIUM' => RiskLevel.medium,
    'HIGH' => RiskLevel.high,
    _ => RiskLevel.unknown,
  };

  bool get hasEnoughHistory => state != 'INSUFFICIENT_DATA';

  /// Success rate as a whole percentage, or `null` with no finished orders.
  int? get successPercent => successRateBasisPoints == null
      ? null
      : (successRateBasisPoints! / 100).round();
}

/// Risk lookups.
///
/// Not a `CachingRepository`, and not for the usual reason. A cached band would
/// be *stale*, but worse than that it would be free: the daily quota exists so
/// this endpoint cannot be used to probe numbers in bulk, and answering from
/// disk would quietly defeat it. Every check is a real, metered, audited call.
class RiskRepository {
  RiskRepository({required this.api});

  final ApiClient api;

  Future<RiskCheck> check(String phone) async {
    final json = await api.get(
      '/customers/risk-check',
      query: <String, dynamic>{'phone': phone},
    );
    return RiskCheck.fromJson(json);
  }
}
