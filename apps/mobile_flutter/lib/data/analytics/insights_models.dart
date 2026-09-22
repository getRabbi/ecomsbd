import '../../core/money.dart';
import 'rto_models.dart';

/// Advanced Insights, as the server computed it.
///
/// Nothing here computes a business figure. Money is the server's paisa,
/// changes and rates are the server's basis points, and a value the server
/// sent as `null` stays null — "not enough data" or "not yours to see" — and
/// is never shown as zero.

int _int(Object? value) => (value as num?)?.toInt() ?? 0;
int? _intOrNull(Object? value) => (value as num?)?.toInt();
Money? _moneyOrNull(Object? value) =>
    value == null ? null : Money((value as num).toInt());
DateTime? _dateOrNull(Object? value) =>
    value == null ? null : DateTime.tryParse(value as String);
List<Map<String, dynamic>> _rows(Object? value) => <Map<String, dynamic>>[
  for (final row in value as List<dynamic>? ?? const <dynamic>[])
    row as Map<String, dynamic>,
];

/// `12.5%` from basis points, formatted only.
String bpsLabel(int bps) {
  final sign = bps < 0 ? '-' : '';
  final abs = bps.abs();
  final whole = abs ~/ 100;
  final tenth = (abs % 100) ~/ 10;
  return tenth == 0 ? '$sign$whole%' : '$sign$whole.$tenth%';
}

/// `+12%` / `-8%`, for a change the server chose to state.
String changeLabel(int bps) => bps > 0 ? '+${bpsLabel(bps)}' : bpsLabel(bps);

/// The ranges the screen offers. The server also accepts a custom range.
const List<int> insightRanges = <int>[7, 30, 90];

class InsightWindow {
  const InsightWindow({
    required this.since,
    required this.until,
    required this.days,
  });

  factory InsightWindow.fromJson(Map<String, dynamic>? json) {
    final j = json ?? const <String, dynamic>{};
    return InsightWindow(
      since: _dateOrNull(j['since']),
      until: _dateOrNull(j['until']),
      days: _int(j['days']),
    );
  }

  final DateTime? since;
  final DateTime? until;
  final int days;
}

/// A figure, the same figure one period earlier, and the change if stated.
class Comparison {
  const Comparison({
    required this.current,
    required this.previous,
    this.changeBps,
  });

  factory Comparison.fromJson(Map<String, dynamic>? json) {
    final j = json ?? const <String, dynamic>{};
    return Comparison(
      current: _int(j['current']),
      previous: _int(j['previous']),
      changeBps: _intOrNull(j['change_basis_points']),
    );
  }

  final int current;
  final int previous;

  /// Null when the earlier base was too small for a percentage to mean
  /// anything. The screen then says nothing about change.
  final int? changeBps;
}

/// A deterministic fact from the server, worded here from its code.
class InsightExplanation {
  const InsightExplanation({required this.code, required this.params});

  factory InsightExplanation.fromJson(Map<String, dynamic> json) =>
      InsightExplanation(
        code: json['code'] as String? ?? '',
        params: Map<String, Object?>.from(
          json['params'] as Map<String, dynamic>? ?? const <String, dynamic>{},
        ),
      );

  final String code;
  final Map<String, Object?> params;
}

List<InsightExplanation> _explanations(Object? value) => <InsightExplanation>[
  for (final row in _rows(value)) InsightExplanation.fromJson(row),
];

class MoneyOverview {
  const MoneyOverview({
    required this.revenue,
    required this.profit,
    required this.profitQuality,
    required this.received,
    required this.receivable,
    required this.overdue,
    required this.inTransit,
    required this.openCaseCount,
    required this.openCase,
    required this.discrepancyCount,
    required this.discrepancy,
  });

  factory MoneyOverview.fromJson(Map<String, dynamic> j) => MoneyOverview(
    revenue: Comparison.fromJson(j['revenue'] as Map<String, dynamic>?),
    profit: Comparison.fromJson(j['profit'] as Map<String, dynamic>?),
    profitQuality: <String, int>{
      for (final e
          in (j['profit_quality'] as Map<String, dynamic>? ??
                  const <String, dynamic>{})
              .entries)
        e.key: _int(e.value),
    },
    received: Comparison.fromJson(j['received'] as Map<String, dynamic>?),
    receivable: Money(_int(j['receivable_paisa'])),
    overdue: Money(_int(j['overdue_paisa'])),
    inTransit: Money(_int(j['in_transit_paisa'])),
    openCaseCount: _int(j['open_case_count']),
    openCase: Money(_int(j['open_case_paisa'])),
    discrepancyCount: _int(j['discrepancy_count']),
    discrepancy: Money(_int(j['discrepancy_paisa'])),
  );

  final Comparison revenue;
  final Comparison profit;
  final Map<String, int> profitQuality;
  final Comparison received;
  final Money receivable;
  final Money overdue;
  final Money inTransit;
  final int openCaseCount;
  final Money openCase;
  final int discrepancyCount;
  final Money discrepancy;

  int get missingParcels => profitQuality['MISSING'] ?? 0;
  int get estimatedParcels => profitQuality['ESTIMATED'] ?? 0;
}

class StockOverview {
  const StockOverview({
    required this.totalUnits,
    required this.lowStockItems,
    required this.outOfStockItems,
    required this.slowMovingItems,
    required this.slowMovingDays,
  });

  factory StockOverview.fromJson(Map<String, dynamic> j) => StockOverview(
    totalUnits: _int(j['total_units']),
    lowStockItems: _int(j['low_stock_items']),
    outOfStockItems: _int(j['out_of_stock_items']),
    slowMovingItems: _int(j['slow_moving_items']),
    slowMovingDays: _int(j['slow_moving_days']),
  );

  final int totalUnits;
  final int lowStockItems;
  final int outOfStockItems;
  final int slowMovingItems;
  final int slowMovingDays;
}

class InsightsOverview {
  const InsightsOverview({
    required this.window,
    required this.orders,
    required this.delivered,
    required this.rto,
    required this.rtoPrevious,
    required this.rtoTrend,
    required this.explanations,
    this.money,
    this.moneyLocked,
    this.stock,
  });

  factory InsightsOverview.fromJson(Map<String, dynamic> j) => InsightsOverview(
    window: InsightWindow.fromJson(j['window'] as Map<String, dynamic>?),
    orders: Comparison.fromJson(j['orders'] as Map<String, dynamic>?),
    delivered: Comparison.fromJson(j['delivered'] as Map<String, dynamic>?),
    rto: RtoCounts.fromJson(j['rto'] as Map<String, dynamic>?),
    rtoPrevious: RtoCounts.fromJson(j['rto_previous'] as Map<String, dynamic>?),
    rtoTrend: j['rto_trend'] as String? ?? 'INSUFFICIENT_DATA',
    money: j['money'] == null
        ? null
        : MoneyOverview.fromJson(j['money'] as Map<String, dynamic>),
    moneyLocked: j['money_locked'] as String?,
    stock: j['stock'] == null
        ? null
        : StockOverview.fromJson(j['stock'] as Map<String, dynamic>),
    explanations: _explanations(j['explanations']),
  );

  final InsightWindow window;
  final Comparison orders;
  final Comparison delivered;
  final RtoCounts rto;
  final RtoCounts rtoPrevious;
  final String rtoTrend;
  final MoneyOverview? money;

  /// `PERMISSION` or `PLAN` when money was withheld, else null.
  final String? moneyLocked;
  final StockOverview? stock;
  final List<InsightExplanation> explanations;
}

class TrendBucket {
  const TrendBucket({
    required this.start,
    required this.orders,
    required this.parcels,
    this.revenue,
    this.profit,
  });

  factory TrendBucket.fromJson(Map<String, dynamic> j) => TrendBucket(
    start: _dateOrNull(j['start']),
    orders: _int(j['orders']),
    parcels: _int(j['parcels']),
    revenue: _moneyOrNull(j['revenue_paisa']),
    profit: _moneyOrNull(j['profit_paisa']),
  );

  final DateTime? start;
  final int orders;
  final int parcels;
  final Money? revenue;
  final Money? profit;
}

class InsightsTrend {
  const InsightsTrend({
    required this.granularity,
    required this.buckets,
    this.moneyLocked,
  });

  factory InsightsTrend.fromJson(Map<String, dynamic> j) => InsightsTrend(
    granularity: j['granularity'] as String? ?? 'day',
    buckets: <TrendBucket>[
      for (final row in _rows(j['buckets'])) TrendBucket.fromJson(row),
    ],
    moneyLocked: j['money_locked'] as String?,
  );

  final String granularity;
  final List<TrendBucket> buckets;
  final String? moneyLocked;

  bool get isEmpty => buckets.every((b) => b.orders == 0 && b.parcels == 0);
}

class ProductInsight {
  const ProductInsight({
    required this.name,
    required this.parcels,
    required this.unitsDelivered,
    required this.rto,
    required this.rtoValue,
    required this.stockStatus,
    required this.unitsBooked,
    required this.slowMoving,
    required this.fastMoving,
    this.productId,
    this.revenue,
    this.profit,
    this.marginBps,
    this.profitQuality,
    this.stockOnHand,
    this.lastSaleAt,
  });

  factory ProductInsight.fromJson(Map<String, dynamic> j) => ProductInsight(
    productId: j['product_id'] as String?,
    name: j['name'] as String? ?? '',
    parcels: _int(j['parcels']),
    unitsDelivered: _int(j['units_delivered']),
    revenue: _moneyOrNull(j['revenue_paisa']),
    profit: _moneyOrNull(j['profit_paisa']),
    marginBps: _intOrNull(j['margin_basis_points']),
    profitQuality: j['profit_quality'] as String?,
    rto: RtoCounts.fromJson(j['rto'] as Map<String, dynamic>?),
    rtoValue: Money(_int(j['rto_value_paisa'])),
    stockStatus: j['stock_status'] as String? ?? 'NONE',
    stockOnHand: _intOrNull(j['stock_on_hand']),
    unitsBooked: _int(j['units_booked']),
    lastSaleAt: _dateOrNull(j['last_sale_at']),
    slowMoving: j['slow_moving'] as bool? ?? false,
    fastMoving: j['fast_moving'] as bool? ?? false,
  );

  final String? productId;
  final String name;
  final int parcels;
  final int unitsDelivered;
  final Money? revenue;

  /// Null when a cost is unknown for some parcel: not a figure at all.
  final Money? profit;
  final int? marginBps;
  final String? profitQuality;
  final RtoCounts rto;
  final Money rtoValue;
  final String stockStatus;
  final int? stockOnHand;
  final int unitsBooked;
  final DateTime? lastSaleAt;
  final bool slowMoving;
  final bool fastMoving;
}

class ProductInsightPage {
  const ProductInsightPage({
    required this.items,
    required this.counts,
    required this.hasMore,
    required this.slowMovingDays,
    this.moneyLocked,
  });

  factory ProductInsightPage.fromJson(Map<String, dynamic> j) =>
      ProductInsightPage(
        items: <ProductInsight>[
          for (final row in _rows(j['items'])) ProductInsight.fromJson(row),
        ],
        counts: <String, int>{
          for (final e
              in (j['counts'] as Map<String, dynamic>? ??
                      const <String, dynamic>{})
                  .entries)
            e.key: _int(e.value),
        },
        hasMore: j['has_more'] as bool? ?? false,
        slowMovingDays: _int(j['slow_moving_days']),
        moneyLocked: j['money_locked'] as String?,
      );

  final List<ProductInsight> items;
  final Map<String, int> counts;
  final bool hasMore;
  final int slowMovingDays;
  final String? moneyLocked;
}

class CourierScorecard {
  const CourierScorecard({
    required this.provider,
    required this.counts,
    required this.trend,
    required this.inTransitNow,
    required this.stuckNow,
    this.outstanding,
    this.overdue,
    this.payoutDelayDays,
    this.payoutDelayReliable = false,
    this.discrepancyCount,
    this.discrepancy,
  });

  factory CourierScorecard.fromJson(Map<String, dynamic> j) {
    final delay = j['payout_delay'] as Map<String, dynamic>?;
    return CourierScorecard(
      provider: j['provider'] as String? ?? '',
      counts: RtoCounts.fromJson(j['counts'] as Map<String, dynamic>?),
      trend: j['trend'] as String? ?? 'INSUFFICIENT_DATA',
      inTransitNow: _int(j['in_transit_now']),
      stuckNow: _int(j['stuck_now']),
      outstanding: _moneyOrNull(j['outstanding_paisa']),
      overdue: _moneyOrNull(j['overdue_paisa']),
      payoutDelayDays: delay == null ? null : _int(delay['median_days']),
      payoutDelayReliable: delay?['reliable'] as bool? ?? false,
      discrepancyCount: _intOrNull(j['discrepancy_count']),
      discrepancy: _moneyOrNull(j['discrepancy_paisa']),
    );
  }

  final String provider;
  final RtoCounts counts;
  final String trend;
  final int inTransitNow;
  final int stuckNow;
  final Money? outstanding;
  final Money? overdue;
  final int? payoutDelayDays;
  final bool payoutDelayReliable;
  final int? discrepancyCount;
  final Money? discrepancy;
}

class CourierInsights {
  const CourierInsights({
    required this.items,
    required this.excluded,
    this.moneyLocked,
  });

  factory CourierInsights.fromJson(Map<String, dynamic> j) => CourierInsights(
    items: <CourierScorecard>[
      for (final row in _rows(j['items'])) CourierScorecard.fromJson(row),
    ],
    excluded: <String>[
      for (final p
          in j['excluded_providers'] as List<dynamic>? ?? const <dynamic>[])
        p as String,
    ],
    moneyLocked: j['money_locked'] as String?,
  );

  final List<CourierScorecard> items;
  final List<String> excluded;
  final String? moneyLocked;
}

class AgingShare {
  const AgingShare({
    required this.label,
    required this.count,
    required this.amount,
    required this.minDays,
    this.maxDays,
  });

  factory AgingShare.fromJson(Map<String, dynamic> j) => AgingShare(
    label: j['label'] as String? ?? '',
    count: _int(j['count']),
    amount: Money(_int(j['amount_paisa'])),
    minDays: _int(j['min_days']),
    maxDays: _intOrNull(j['max_days']),
  );

  final String label;
  final int count;
  final Money amount;
  final int minDays;
  final int? maxDays;
}

class CashInsights {
  const CashInsights({
    required this.received,
    required this.receivable,
    required this.overdue,
    required this.inTransit,
    required this.inTransitCount,
    required this.aging,
    required this.couriers,
    required this.forecast,
    required this.explanations,
  });

  factory CashInsights.fromJson(Map<String, dynamic> j) => CashInsights(
    received: Comparison.fromJson(j['received'] as Map<String, dynamic>?),
    receivable: Money(_int(j['receivable_paisa'])),
    overdue: Money(_int(j['overdue_paisa'])),
    inTransit: Money(_int(j['in_transit_paisa'])),
    inTransitCount: _int(j['in_transit_count']),
    aging: <AgingShare>[
      for (final row in _rows(j['aging'])) AgingShare.fromJson(row),
    ],
    couriers: <(String, Money, Money)>[
      for (final row in _rows(j['couriers']))
        (
          row['provider'] as String? ?? '',
          Money(_int(row['outstanding_paisa'])),
          Money(_int(row['overdue_paisa'])),
        ),
    ],
    forecast: <(String, Money)>[
      for (final row in _rows(j['forecast']))
        (row['key'] as String? ?? '', Money(_int(row['amount_paisa']))),
    ],
    explanations: _explanations(j['explanations']),
  );

  final Comparison received;
  final Money receivable;
  final Money overdue;
  final Money inTransit;
  final int inTransitCount;
  final List<AgingShare> aging;

  /// `(provider, outstanding, overdue)`.
  final List<(String, Money, Money)> couriers;

  /// ESTIMATE, from each courier's own payment history.
  final List<(String, Money)> forecast;
  final List<InsightExplanation> explanations;
}

class CustomerInsights {
  const CustomerInsights({
    required this.totalCustomers,
    required this.active,
    required this.newCustomers,
    required this.returning,
    required this.ordersWithCustomer,
    required this.repeatOrders,
    required this.sufficient,
    required this.repeatCustomers,
    required this.multiDeliveryCustomers,
    required this.repeatRtoCustomers,
    required this.explanations,
    this.repeatOrderRateBps,
  });

  factory CustomerInsights.fromJson(Map<String, dynamic> j) => CustomerInsights(
    totalCustomers: _int(j['total_customers']),
    active: Comparison.fromJson(j['active'] as Map<String, dynamic>?),
    newCustomers: Comparison.fromJson(j['new'] as Map<String, dynamic>?),
    returning: _int(j['returning']),
    ordersWithCustomer: _int(j['orders_with_customer']),
    repeatOrders: _int(j['repeat_orders']),
    repeatOrderRateBps: _intOrNull(j['repeat_order_rate_basis_points']),
    sufficient: j['sufficient'] as bool? ?? false,
    repeatCustomers: _int(j['repeat_customers']),
    multiDeliveryCustomers: _int(j['multi_delivery_customers']),
    repeatRtoCustomers: _int(j['repeat_rto_customers']),
    explanations: _explanations(j['explanations']),
  );

  final int totalCustomers;
  final Comparison active;
  final Comparison newCustomers;
  final int returning;
  final int ordersWithCustomer;
  final int repeatOrders;
  final int? repeatOrderRateBps;
  final bool sufficient;
  final int repeatCustomers;
  final int multiDeliveryCustomers;
  final int repeatRtoCustomers;
  final List<InsightExplanation> explanations;
}

class StockItemInsight {
  const StockItemInsight({
    required this.name,
    required this.stockOnHand,
    required this.status,
    required this.unitsBooked,
    required this.slowMoving,
    this.variantName,
    this.lastSaleAt,
  });

  factory StockItemInsight.fromJson(Map<String, dynamic> j) => StockItemInsight(
    name: j['name'] as String? ?? '',
    variantName: j['variant_name'] as String?,
    stockOnHand: _int(j['stock_on_hand']),
    status: j['status'] as String? ?? 'OK',
    unitsBooked: _int(j['units_booked']),
    lastSaleAt: _dateOrNull(j['last_sale_at']),
    slowMoving: j['slow_moving'] as bool? ?? false,
  );

  final String name;
  final String? variantName;
  final int stockOnHand;
  final String status;
  final int unitsBooked;
  final DateTime? lastSaleAt;
  final bool slowMoving;

  String get label => variantName == null ? name : '$name · $variantName';
}

class InventoryInsights {
  const InventoryInsights({
    required this.totalUnits,
    required this.lowStockItems,
    required this.outOfStockItems,
    required this.counts,
    required this.items,
    required this.hasMore,
    required this.slowMovingDays,
    required this.explanations,
  });

  factory InventoryInsights.fromJson(Map<String, dynamic> j) =>
      InventoryInsights(
        totalUnits: _int(j['total_units']),
        lowStockItems: _int(j['low_stock_items']),
        outOfStockItems: _int(j['out_of_stock_items']),
        counts: <String, int>{
          for (final e
              in (j['counts'] as Map<String, dynamic>? ??
                      const <String, dynamic>{})
                  .entries)
            e.key: _int(e.value),
        },
        items: <StockItemInsight>[
          for (final row in _rows(j['items'])) StockItemInsight.fromJson(row),
        ],
        hasMore: j['has_more'] as bool? ?? false,
        slowMovingDays: _int(j['slow_moving_days']),
        explanations: _explanations(j['explanations']),
      );

  final int totalUnits;
  final int lowStockItems;
  final int outOfStockItems;
  final Map<String, int> counts;
  final List<StockItemInsight> items;
  final bool hasMore;
  final int slowMovingDays;
  final List<InsightExplanation> explanations;
}
