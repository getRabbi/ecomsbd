import 'package:meta/meta.dart';

import '../../core/money.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';

/// These labels are read from const enums, so they resolve against the active
/// locale when asked rather than when constructed.
String _t(String key) => AppStrings(activeAppLocale).t(key);

/// Wire models for Home, Insights, expenses and the notification centre.
///
/// Every amount arrives as integer paisa and every rate as basis points, so
/// nothing here rounds a figure the server already decided. Nothing computes
/// a total either: the server owns the profit calculation, and a client that
/// derived its own would eventually disagree with the Money screen (master
/// spec sections 64, 80, 135).

// --------------------------------------------------------------------------- //
// Alerts
// --------------------------------------------------------------------------- //

/// One of master spec section 23's four lines.
@immutable
class AlertLine {
  const AlertLine({
    required this.kind,
    required this.severity,
    required this.count,
    required this.amount,
  });

  factory AlertLine.fromJson(Map<String, dynamic> json) => AlertLine(
    kind: json['kind'] as String,
    severity: json['severity'] as String,
    count: json['count'] as int,
    amount: Money(json['amount_paisa'] as int),
  );

  final String kind;
  final String severity;
  final int count;
  final Money amount;

  /// Seller-facing title, in the words section 23 uses.
  String get title => switch (kind) {
    'DELIVERED_BUT_UNPAID' => 'Delivered but unpaid',
    'UNDERPAID' => 'Underpaid',
    'STALE_IN_TRANSIT' => '15+ days in transit',
    'RETURNED_NOT_RESTOCKED' => 'Returned but stock not restored',
    'RETURN_SPIKE' => 'More returns than usual',
    _ => kind,
  };

  /// The money at stake, which is what decides the order a seller works
  /// through them. Restocking is the one line where the amount is not the
  /// point — the units are.
  String get detail {
    final parcels = '$count parcel${count == 1 ? '' : 's'}';
    if (kind == 'RETURNED_NOT_RESTOCKED') {
      return '$parcels never added back to stock';
    }
    return '$parcels · ${amount.format()}';
  }
}

// --------------------------------------------------------------------------- //
// Home
// --------------------------------------------------------------------------- //

/// Master spec section 1.1's daily money control screen.
@immutable
class HomeMetrics {
  const HomeMetrics({
    required this.asOf,
    required this.ordersToday,
    required this.deliveredToday,
    required this.returnedToday,
    required this.grossSales,
    required this.realizedRevenue,
    required this.contributionProfit,
    required this.codOutstanding,
    required this.codExpectedToday,
    required this.codUnforecast,
    required this.codOverdue,
    required this.mismatch,
    required this.mismatchCount,
    required this.returnLoss,
    required this.estimatedParcels,
    required this.incompleteParcels,
    required this.alerts,
  });

  factory HomeMetrics.fromJson(Map<String, dynamic> json) => HomeMetrics(
    asOf: DateTime.parse(json['as_of'] as String),
    ordersToday: json['orders_today'] as int,
    deliveredToday: json['delivered_today'] as int,
    returnedToday: json['returned_today'] as int,
    grossSales: Money(json['gross_sales_paisa'] as int),
    realizedRevenue: Money(json['realized_revenue_paisa'] as int),
    contributionProfit: Money(json['contribution_profit_paisa'] as int),
    codOutstanding: Money(json['cod_outstanding_paisa'] as int),
    codExpectedToday: Money(json['cod_expected_today_paisa'] as int),
    codUnforecast: Money(json['cod_unforecast_paisa'] as int),
    codOverdue: Money(json['cod_overdue_paisa'] as int),
    mismatch: Money(json['mismatch_paisa'] as int),
    mismatchCount: json['mismatch_count'] as int,
    returnLoss: Money(json['return_loss_paisa'] as int),
    estimatedParcels: json['estimated_parcels'] as int,
    incompleteParcels: json['incomplete_parcels'] as int,
    alerts: <AlertLine>[
      for (final alert
          in (json['alerts'] as List<dynamic>? ?? const <dynamic>[]))
        AlertLine.fromJson(alert as Map<String, dynamic>),
    ],
  );

  final DateTime asOf;
  final int ordersToday;
  final int deliveredToday;
  final int returnedToday;
  final Money grossSales;
  final Money realizedRevenue;
  final Money contributionProfit;
  final Money codOutstanding;
  final Money codExpectedToday;

  /// Outstanding COD nobody can put an arrival date on yet, because this shop
  /// has no settlement history with that courier. Shown rather than folded
  /// into "expected", so the screen never implies money is late when nobody
  /// knows when it was due.
  final Money codUnforecast;
  final Money codOverdue;
  final Money mismatch;
  final int mismatchCount;
  final Money returnLoss;

  /// How many of the day's parcels carry an estimate rather than a measured
  /// figure. Master spec section 135: an estimate never prints as exact.
  final int estimatedParcels;
  final int incompleteParcels;
  final List<AlertLine> alerts;

  bool get hasEstimates => estimatedParcels > 0 || incompleteParcels > 0;

  /// What the seller can be told about the day's profit, in one line.
  String get profitCaveat {
    if (incompleteParcels > 0) {
      return '$deliveredToday delivered. $incompleteParcels '
          'parcel${incompleteParcels == 1 ? '' : 's'} missing a cost, so the '
          'figure is incomplete.';
    }
    if (estimatedParcels > 0) {
      return '$deliveredToday delivered. $estimatedParcels '
          'parcel${estimatedParcels == 1 ? '' : 's'} still estimated.';
    }
    return '$deliveredToday delivered. Every figure is settled.';
  }
}

// --------------------------------------------------------------------------- //
// Profit
// --------------------------------------------------------------------------- //

/// One day of the profit trend.
@immutable
class DayPoint {
  const DayPoint({
    required this.businessDate,
    required this.parcelCount,
    required this.realizedRevenue,
    required this.contributionProfit,
  });

  factory DayPoint.fromJson(Map<String, dynamic> json) => DayPoint(
    businessDate: DateTime.parse(json['business_date'] as String),
    parcelCount: json['parcel_count'] as int,
    realizedRevenue: Money(json['realized_revenue_paisa'] as int),
    contributionProfit: Money(json['contribution_profit_paisa'] as int),
  );

  final DateTime businessDate;
  final int parcelCount;
  final Money realizedRevenue;
  final Money contributionProfit;
}

/// One step of the delivery funnel.
@immutable
class FunnelStage {
  const FunnelStage({required this.label, required this.count});

  factory FunnelStage.fromJson(Map<String, dynamic> json) =>
      FunnelStage(label: json['label'] as String, count: json['count'] as int);

  final String label;
  final int count;
}

@immutable
class ProfitReport {
  const ProfitReport({
    required this.since,
    required this.until,
    required this.parcelCount,
    required this.realizedRevenue,
    required this.itemCost,
    required this.deliveryCharge,
    required this.codFee,
    required this.returnCharge,
    required this.packaging,
    required this.adCost,
    required this.writeOffCost,
    required this.contributionProfit,
    required this.unallocatedAdSpend,
    required this.fixedCost,
    required this.operatingProfit,
    required this.quality,
    required this.series,
    required this.funnel,
    this.marginBasisPoints,
  });

  factory ProfitReport.fromJson(Map<String, dynamic> json) => ProfitReport(
    since: DateTime.parse(json['since'] as String),
    until: DateTime.parse(json['until'] as String),
    parcelCount: json['parcel_count'] as int,
    realizedRevenue: Money(json['realized_revenue_paisa'] as int),
    itemCost: Money(json['item_cost_paisa'] as int),
    deliveryCharge: Money(json['delivery_charge_paisa'] as int),
    codFee: Money(json['cod_fee_paisa'] as int),
    returnCharge: Money(json['return_charge_paisa'] as int),
    packaging: Money(json['packaging_paisa'] as int),
    adCost: Money(json['ad_cost_paisa'] as int),
    writeOffCost: Money(json['write_off_cost_paisa'] as int),
    contributionProfit: Money(json['contribution_profit_paisa'] as int),
    unallocatedAdSpend: Money(json['unallocated_ad_spend_paisa'] as int),
    fixedCost: Money(json['fixed_cost_paisa'] as int),
    operatingProfit: Money(json['operating_profit_paisa'] as int),
    marginBasisPoints: json['margin_basis_points'] as int?,
    quality: <String, int>{
      for (final entry
          in (json['quality'] as Map<String, dynamic>? ?? const {}).entries)
        entry.key: entry.value as int,
    },
    series: <DayPoint>[
      for (final point
          in (json['series'] as List<dynamic>? ?? const <dynamic>[]))
        DayPoint.fromJson(point as Map<String, dynamic>),
    ],
    funnel: <FunnelStage>[
      for (final stage
          in (json['funnel'] as List<dynamic>? ?? const <dynamic>[]))
        FunnelStage.fromJson(stage as Map<String, dynamic>),
    ],
  );

  final DateTime since;
  final DateTime until;
  final int parcelCount;
  final Money realizedRevenue;
  final Money itemCost;
  final Money deliveryCharge;
  final Money codFee;
  final Money returnCharge;
  final Money packaging;
  final Money adCost;
  final Money writeOffCost;
  final Money contributionProfit;

  /// Ad money that reached no parcel. Kept out of contribution profit and
  /// shown on its own (master spec section 86).
  final Money unallocatedAdSpend;
  final Money fixedCost;
  final Money operatingProfit;

  /// Null when nothing was collected — not zero, which would read as
  /// "we sold at cost".
  final int? marginBasisPoints;

  /// `{ACTUAL: 12, ESTIMATED: 3, MISSING: 0}`.
  final Map<String, int> quality;

  /// Daily profit across the window, oldest first, empty days included so a
  /// quiet Tuesday reads as a quiet Tuesday.
  final List<DayPoint> series;

  /// Dispatched → in transit → delivered → returned → lost.
  final List<FunnelStage> funnel;

  int get actualCount => quality['ACTUAL'] ?? 0;
  int get estimatedCount => quality['ESTIMATED'] ?? 0;
  int get missingCount => quality['MISSING'] ?? 0;

  String? get marginLabel {
    final basisPoints = marginBasisPoints;
    if (basisPoints == null) return null;
    return '${(basisPoints / 100).toStringAsFixed(1)}% margin';
  }
}

// --------------------------------------------------------------------------- //
// Returns
// --------------------------------------------------------------------------- //

/// A return rate for one product, area or courier.
@immutable
class RateLine {
  const RateLine({
    required this.label,
    required this.parcelCount,
    required this.returnCount,
    required this.returnRateBasisPoints,
    required this.loss,
    required this.hasEnoughSample,
  });

  factory RateLine.fromJson(Map<String, dynamic> json) => RateLine(
    label: json['label'] as String,
    parcelCount: json['parcel_count'] as int,
    returnCount: json['return_count'] as int,
    returnRateBasisPoints: json['return_rate_basis_points'] as int,
    loss: Money(json['loss_paisa'] as int),
    hasEnoughSample: json['has_enough_sample'] as bool,
  );

  final String label;
  final int parcelCount;
  final int returnCount;
  final int returnRateBasisPoints;
  final Money loss;

  /// False means "shown for completeness, do not act on it yet" — master
  /// spec section 24's sample-size rule, which must be visible.
  final bool hasEnoughSample;

  String get rateLabel =>
      '${(returnRateBasisPoints / 100).toStringAsFixed(1)}%';
}

@immutable
class ReturnReport {
  const ReturnReport({
    required this.since,
    required this.until,
    required this.parcelCount,
    required this.returnCount,
    required this.directLoss,
    required this.outwardDeliveryCost,
    required this.returnDeliveryCost,
    required this.packagingLoss,
    required this.writeOff,
    required this.byReason,
    required this.unknownReasonCount,
    required this.byProduct,
    required this.byArea,
    required this.byCourier,
    this.returnRateBasisPoints,
  });

  factory ReturnReport.fromJson(Map<String, dynamic> json) => ReturnReport(
    since: DateTime.parse(json['since'] as String),
    until: DateTime.parse(json['until'] as String),
    parcelCount: json['parcel_count'] as int,
    returnCount: json['return_count'] as int,
    returnRateBasisPoints: json['return_rate_basis_points'] as int?,
    directLoss: Money(json['direct_loss_paisa'] as int),
    outwardDeliveryCost: Money(json['outward_delivery_cost_paisa'] as int),
    returnDeliveryCost: Money(json['return_delivery_cost_paisa'] as int),
    packagingLoss: Money(json['packaging_loss_paisa'] as int),
    writeOff: Money(json['write_off_paisa'] as int),
    byReason: <String, int>{
      for (final entry
          in (json['by_reason'] as Map<String, dynamic>? ?? const {}).entries)
        entry.key: entry.value as int,
    },
    unknownReasonCount: json['unknown_reason_count'] as int,
    byProduct: _rates(json['by_product']),
    byArea: _rates(json['by_area']),
    byCourier: _rates(json['by_courier']),
  );

  static List<RateLine> _rates(Object? raw) => <RateLine>[
    for (final line in (raw as List<dynamic>? ?? const <dynamic>[]))
      RateLine.fromJson(line as Map<String, dynamic>),
  ];

  final DateTime since;
  final DateTime until;
  final int parcelCount;
  final int returnCount;
  final int? returnRateBasisPoints;
  final Money directLoss;
  final Money outwardDeliveryCost;
  final Money returnDeliveryCost;
  final Money packagingLoss;
  final Money writeOff;
  final Map<String, int> byReason;

  /// Returns with no reason recorded. How much of the picture is missing is
  /// itself information (master spec section 19).
  final int unknownReasonCount;
  final List<RateLine> byProduct;
  final List<RateLine> byArea;
  final List<RateLine> byCourier;

  String? get rateLabel {
    final basisPoints = returnRateBasisPoints;
    if (basisPoints == null) return null;
    return '${(basisPoints / 100).toStringAsFixed(1)}%';
  }
}

// --------------------------------------------------------------------------- //
// Products
// --------------------------------------------------------------------------- //

@immutable
class ProductLine {
  const ProductLine({
    required this.productName,
    required this.parcelCount,
    required this.unitsDelivered,
    required this.revenue,
    required this.profit,
    required this.returnCount,
    required this.hasEnoughSample,
    this.marginBasisPoints,
  });

  factory ProductLine.fromJson(Map<String, dynamic> json) => ProductLine(
    productName: json['product_name'] as String,
    parcelCount: json['parcel_count'] as int,
    unitsDelivered: json['units_delivered'] as int,
    revenue: Money(json['revenue_paisa'] as int),
    profit: Money(json['profit_paisa'] as int),
    returnCount: json['return_count'] as int,
    marginBasisPoints: json['margin_basis_points'] as int?,
    hasEnoughSample: json['has_enough_sample'] as bool,
  );

  final String productName;
  final int parcelCount;
  final int unitsDelivered;
  final Money revenue;
  final Money profit;
  final int returnCount;
  final int? marginBasisPoints;
  final bool hasEnoughSample;
}

// --------------------------------------------------------------------------- //
// Expenses
// --------------------------------------------------------------------------- //

enum ExpenseKind {
  adSpend('AD_SPEND', 'ek.adSpend'),
  fixed('FIXED', 'ek.fixed'),
  other('OTHER', 'ek.other');

  const ExpenseKind(this.wire, this._labelKey);

  final String wire;
  final String _labelKey;

  /// Resolved when read, not when constructed: an enum constructor has to be
  /// const, so only the key can live there. The wording follows the language.
  String get label => _t(_labelKey);

  static ExpenseKind parse(String value) => ExpenseKind.values.firstWhere(
    (kind) => kind.wire == value,
    orElse: () => ExpenseKind.other,
  );

  /// Only ad spend is pushed down onto parcels. Master spec section 86 warns
  /// against pretending fixed-cost allocation is accounting-grade, so rent
  /// stays below contribution profit.
  bool get isAllocatable => this == ExpenseKind.adSpend;
}

enum AllocationMethod {
  equalPerDeliveredOrder('EQUAL_PER_DELIVERED_ORDER', 'am.equalPerDelivered'),
  proportionalToRevenue('PROPORTIONAL_TO_REVENUE', 'am.byOrderValue'),
  productTaggedPerUnit('PRODUCT_TAGGED_PER_UNIT', 'am.perUnit');

  const AllocationMethod(this.wire, this._labelKey);

  final String wire;
  final String _labelKey;

  String get label => _t(_labelKey);

  static AllocationMethod parse(String value) =>
      AllocationMethod.values.firstWhere(
        (method) => method.wire == value,
        orElse: () => AllocationMethod.equalPerDeliveredOrder,
      );
}

@immutable
class Expense {
  const Expense({
    required this.id,
    required this.kind,
    required this.amount,
    required this.allocated,
    required this.unallocated,
    required this.periodStart,
    required this.periodEnd,
    required this.description,
    required this.preferredMethod,
    required this.createdAt,
    this.productId,
    this.allocatedAt,
  });

  factory Expense.fromJson(Map<String, dynamic> json) => Expense(
    id: json['id'] as String,
    kind: ExpenseKind.parse(json['kind'] as String),
    amount: Money(json['amount_paisa'] as int),
    allocated: Money(json['allocated_paisa'] as int),
    unallocated: Money(json['unallocated_paisa'] as int),
    periodStart: DateTime.parse(json['period_start'] as String),
    periodEnd: DateTime.parse(json['period_end'] as String),
    description: json['description'] as String,
    productId: json['product_id'] as String?,
    preferredMethod: AllocationMethod.parse(json['preferred_method'] as String),
    allocatedAt: json['allocated_at'] == null
        ? null
        : DateTime.parse(json['allocated_at'] as String),
    createdAt: DateTime.parse(json['created_at'] as String),
  );

  final String id;
  final ExpenseKind kind;
  final Money amount;
  final Money allocated;
  final Money unallocated;
  final DateTime periodStart;
  final DateTime periodEnd;
  final String description;
  final String? productId;
  final AllocationMethod preferredMethod;
  final DateTime? allocatedAt;
  final DateTime createdAt;

  bool get isAllocated => allocatedAt != null;

  /// True when the spend was allocated and still reached nothing — a period
  /// with no deliveries. Worth saying out loud rather than showing ৳0.
  bool get reachedNothing => isAllocated && allocated.paisa == 0;
}

@immutable
class AllocationResult {
  const AllocationResult({
    required this.expenseId,
    required this.method,
    required this.parcelCount,
    required this.allocated,
    required this.unallocated,
    required this.reachedNothing,
  });

  factory AllocationResult.fromJson(Map<String, dynamic> json) =>
      AllocationResult(
        expenseId: json['expense_id'] as String,
        method: AllocationMethod.parse(json['method'] as String),
        parcelCount: json['parcel_count'] as int,
        allocated: Money(json['allocated_paisa'] as int),
        unallocated: Money(json['unallocated_paisa'] as int),
        reachedNothing: json['reached_nothing'] as bool,
      );

  final String expenseId;
  final AllocationMethod method;
  final int parcelCount;
  final Money allocated;
  final Money unallocated;
  final bool reachedNothing;
}

// --------------------------------------------------------------------------- //
// Notifications
// --------------------------------------------------------------------------- //

enum NotificationSeverity {
  info('INFO'),
  action('ACTION'),
  warning('WARNING'),
  critical('CRITICAL');

  const NotificationSeverity(this.wire);

  final String wire;

  static NotificationSeverity parse(String value) =>
      NotificationSeverity.values.firstWhere(
        (severity) => severity.wire == value,
        orElse: () => NotificationSeverity.info,
      );
}

@immutable
class AppNotification {
  const AppNotification({
    required this.id,
    required this.kind,
    required this.severity,
    required this.title,
    required this.body,
    required this.amount,
    required this.itemCount,
    required this.businessDate,
    required this.createdAt,
    required this.payload,
    this.entityType,
    this.entityId,
    this.readAt,
  });

  factory AppNotification.fromJson(Map<String, dynamic> json) =>
      AppNotification(
        id: json['id'] as String,
        kind: json['kind'] as String,
        severity: NotificationSeverity.parse(json['severity'] as String),
        title: json['title'] as String,
        body: json['body'] as String,
        entityType: json['entity_type'] as String?,
        entityId: json['entity_id'] as String?,
        amount: Money(json['amount_paisa'] as int),
        itemCount: json['item_count'] as int,
        businessDate: DateTime.parse(json['business_date'] as String),
        readAt: json['read_at'] == null
            ? null
            : DateTime.parse(json['read_at'] as String),
        payload: json['payload'] as Map<String, dynamic>? ?? const {},
        createdAt: DateTime.parse(json['created_at'] as String),
      );

  final String id;
  final String kind;
  final NotificationSeverity severity;
  final String title;
  final String body;
  final String? entityType;
  final String? entityId;
  final Money amount;
  final int itemCount;
  final DateTime businessDate;
  final DateTime? readAt;
  final Map<String, dynamic> payload;
  final DateTime createdAt;

  bool get isRead => readAt != null;

  /// Whether tapping it can open the thing it is about. Master spec section
  /// 94: a notification that lands on a dashboard makes the seller hunt for
  /// what it was about.
  bool get hasDeepLink => entityType != null && entityId != null;
}

// --------------------------------------------------------------------------- //
// Weekly summary
// --------------------------------------------------------------------------- //

/// A ranked entry, or the reason there isn't one.
@immutable
class RankedEntry {
  const RankedEntry({required this.label, required this.value});

  static RankedEntry? tryParse(Object? raw) {
    if (raw is! List || raw.length != 2) return null;
    return RankedEntry(label: raw[0] as String, value: raw[1] as int);
  }

  final String label;
  final int value;
}

@immutable
class WeeklySummary {
  const WeeklySummary({
    required this.weekStart,
    required this.weekEnd,
    required this.orderCount,
    required this.deliveredCount,
    required this.returnCount,
    required this.returnLoss,
    required this.sales,
    required this.contributionProfit,
    required this.codOutstanding,
    required this.overdue,
    required this.mismatchCount,
    required this.adSpend,
    required this.alerts,
    this.returnRateBasisPoints,
    this.bestProduct,
    this.worstProduct,
    this.bestCourier,
    this.worstCourier,
    this.rankingNote,
    this.courierNote,
  });

  factory WeeklySummary.fromJson(Map<String, dynamic> json) => WeeklySummary(
    weekStart: DateTime.parse(json['week_start'] as String),
    weekEnd: DateTime.parse(json['week_end'] as String),
    orderCount: json['order_count'] as int,
    deliveredCount: json['delivered_count'] as int,
    returnCount: json['return_count'] as int,
    returnLoss: Money(json['return_loss_paisa'] as int),
    sales: Money(json['sales_paisa'] as int),
    contributionProfit: Money(json['contribution_profit_paisa'] as int),
    codOutstanding: Money(json['cod_outstanding_paisa'] as int),
    overdue: Money(json['overdue_paisa'] as int),
    mismatchCount: json['mismatch_count'] as int,
    adSpend: Money(json['ad_spend_paisa'] as int),
    returnRateBasisPoints: json['return_rate_basis_points'] as int?,
    bestProduct: RankedEntry.tryParse(json['best_product']),
    worstProduct: RankedEntry.tryParse(json['worst_product']),
    bestCourier: RankedEntry.tryParse(json['best_courier']),
    worstCourier: RankedEntry.tryParse(json['worst_courier']),
    rankingNote: json['ranking_note'] as String?,
    courierNote: json['courier_note'] as String?,
    alerts: <AlertLine>[
      for (final alert
          in (json['alerts'] as List<dynamic>? ?? const <dynamic>[]))
        AlertLine.fromJson(alert as Map<String, dynamic>),
    ],
  );

  final DateTime weekStart;
  final DateTime weekEnd;
  final int orderCount;
  final int deliveredCount;
  final int returnCount;
  final Money returnLoss;
  final Money sales;
  final Money contributionProfit;
  final Money codOutstanding;
  final Money overdue;
  final int mismatchCount;
  final Money adSpend;
  final int? returnRateBasisPoints;
  final RankedEntry? bestProduct;
  final RankedEntry? worstProduct;
  final RankedEntry? bestCourier;
  final RankedEntry? worstCourier;

  /// Why a product ranking was withheld. Master spec section 24 requires the
  /// sample-size rule to be visible, so this is shown in place of the tile
  /// rather than leaving a blank.
  final String? rankingNote;
  final String? courierNote;
  final List<AlertLine> alerts;
}

// --------------------------------------------------------------------------- //
// Charges
// --------------------------------------------------------------------------- //

enum ChargeKind {
  delivery('DELIVERY', 'Delivery charge'),
  codFee('COD_FEE', 'COD fee'),
  returnCharge('RETURN', 'Return charge'),
  packaging('PACKAGING', 'Packaging'),
  paymentFee('PAYMENT_FEE', 'Payment fee'),
  other('OTHER', 'Other');

  const ChargeKind(this.wire, this.label);

  final String wire;
  final String label;

  static ChargeKind parse(String value) => ChargeKind.values.firstWhere(
    (kind) => kind.wire == value,
    orElse: () => ChargeKind.other,
  );
}

/// Where a charge figure came from (master spec section 85's hierarchy).
///
/// The seller entering a number off a receipt is a different claim from a
/// settlement statement, and the difference is recorded rather than assumed.
enum ChargeSource {
  settled('SETTLED', 'Off a statement'),
  booked('BOOKED', 'Quoted at booking'),
  seller('SELLER', 'Entered by you'),
  estimate('ESTIMATE', 'Estimated'),
  unknown('UNKNOWN', 'Unknown');

  const ChargeSource(this.wire, this.label);

  final String wire;
  final String label;

  static ChargeSource parse(String value) => ChargeSource.values.firstWhere(
    (source) => source.wire == value,
    orElse: () => ChargeSource.unknown,
  );
}

@immutable
class ConsignmentCharge {
  const ConsignmentCharge({
    required this.id,
    required this.consignmentId,
    required this.kind,
    required this.source,
    required this.amount,
    required this.occurredAt,
    this.providerLabel,
    this.reason,
  });

  factory ConsignmentCharge.fromJson(Map<String, dynamic> json) =>
      ConsignmentCharge(
        id: json['id'] as String,
        consignmentId: json['consignment_id'] as String,
        kind: ChargeKind.parse(json['kind'] as String),
        source: ChargeSource.parse(json['source'] as String),
        amount: Money(json['amount_paisa'] as int),
        providerLabel: json['provider_label'] as String?,
        reason: json['reason'] as String?,
        occurredAt: DateTime.parse(json['occurred_at'] as String),
      );

  final String id;
  final String consignmentId;
  final ChargeKind kind;
  final ChargeSource source;
  final Money amount;
  final String? providerLabel;
  final String? reason;
  final DateTime occurredAt;
}

/// The reasons master spec section 19 lists, verbatim.
///
/// "Risk" wording, never a defamatory label about a person — section 19 is
/// explicit about that, and `fakeOrderSuspicion` is worded accordingly.
enum ReturnReason {
  customerUnreachable('CUSTOMER_UNREACHABLE', 'rr.unreachable'),
  customerRefused('CUSTOMER_REFUSED', 'rr.refused'),
  wrongProduct('WRONG_PRODUCT', 'rr.wrongProduct'),
  wrongSize('WRONG_SIZE', 'rr.wrongSize'),
  damaged('DAMAGED', 'rr.damaged'),
  delayedDelivery('DELAYED_DELIVERY', 'rr.tooLate'),
  changedMind('CHANGED_MIND', 'rr.changedMind'),
  fakeOrderSuspicion('FAKE_ORDER_SUSPICION', 'rr.suspectedInvalid'),
  courierIssue('COURIER_ISSUE', 'rr.courierIssue'),
  merchantIssue('MERCHANT_ISSUE', 'rr.ourMistake'),
  other('OTHER', 'rr.other');

  const ReturnReason(this.wire, this._labelKey);

  final String wire;
  final String _labelKey;

  String get label => _t(_labelKey);

  static ReturnReason? tryParse(String? value) {
    if (value == null) return null;
    for (final reason in ReturnReason.values) {
      if (reason.wire == value) return reason;
    }
    return null;
  }
}
