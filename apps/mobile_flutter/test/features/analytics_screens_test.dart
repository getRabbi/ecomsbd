import 'package:ecomsbd/design/charts/bar_charts.dart';
import 'package:ecomsbd/design/charts/donut_chart.dart';
import 'package:ecomsbd/design/charts/line_chart.dart';
import 'package:ecomsbd/features/expenses/expenses_screen.dart';
import 'package:ecomsbd/features/home/home_screen.dart';
import 'package:ecomsbd/features/insights/profit_detail_screen.dart';
import 'package:ecomsbd/features/notifications/notification_centre_screen.dart';
import 'package:flutter_test/flutter_test.dart';

import '../helpers.dart';
import 'commerce_harness.dart';

/// The Phase E screens, against a fake server.
///
/// The assertions concentrate on what would mislead a seller about money: a
/// figure printed as exact when it is estimated, a ranking drawn from three
/// parcels, an expense that looks applied when it has reached nothing, or a
/// dashboard that quietly falls back to plausible demo numbers when a call
/// fails.
void main() {
  group('Home', () {
    testWidgets("shows today's figures from the server", (tester) async {
      final harness = analyticsHarness();
      await pumpCommerceScreen(tester, const HomeScreen(), harness: harness);

      expect(find.textContaining('৳4,805'), findsWidgets);
      expect(find.textContaining('2 orders today'), findsOneWidget);
      expectNoOverflow(tester);
    });

    testWidgets('an estimated profit is marked, not printed as exact', (
      tester,
    ) async {
      final harness = analyticsHarness(home: homeJson(estimatedParcels: 2));
      await pumpCommerceScreen(tester, const HomeScreen(), harness: harness);

      // Master spec section 135: an estimate never renders like a settled
      // figure. The hero carries the quality badge and the panel says why.
      expect(find.textContaining('2 parcels still estimated'), findsOneWidget);
    });

    testWidgets('COD with no arrival date says so rather than implying one', (
      tester,
    ) async {
      final harness = analyticsHarness(
        home: homeJson(expectedToday: 0, unforecast: 480_500),
      );
      await pumpCommerceScreen(tester, const HomeScreen(), harness: harness);

      // Section 140: courier settlement timing is never invented. Without
      // history there is no date, and the card says that instead of showing
      // the money as due.
      expect(find.textContaining('No arrival date yet'), findsOneWidget);
    });

    testWidgets('the alerts are the ones the server raised', (tester) async {
      final harness = analyticsHarness(
        home: homeJson(
          alerts: <Map<String, dynamic>>[
            alertJson('DELIVERED_BUT_UNPAID', 'CRITICAL', 7, 895_000),
            alertJson('UNDERPAID', 'WARNING', 3, 42_000),
          ],
        ),
      );
      await pumpCommerceScreen(tester, const HomeScreen(), harness: harness);

      // Section 23's own wording and figures, from the server's counts.
      expect(find.text('Delivered but unpaid'), findsOneWidget);
      expect(find.textContaining('7 parcels · ৳8,950'), findsOneWidget);
      expect(find.text('Underpaid'), findsOneWidget);
      expectNoOverflow(tester);
    });

    testWidgets('a failed load says so instead of showing demo numbers', (
      tester,
    ) async {
      final harness = analyticsHarness()..offline = true;
      await pumpCommerceScreen(tester, const HomeScreen(), harness: harness);

      // A dashboard that fell back to plausible fixtures would be worse than
      // one that fails: the seller could not tell the difference.
      expect(find.text('No connection'), findsOneWidget);
      expect(find.textContaining('৳4,805'), findsNothing);
    });

    testWidgets('the prototype charts all render, at 360dp and wider', (
      tester,
    ) async {
      final harness = analyticsHarness();
      await pumpCommerceScreen(tester, const HomeScreen(), harness: harness);

      expect(find.byType(ProfitTrendChart), findsOneWidget);
      expect(find.byType(CodDonutChart), findsOneWidget);
      await scrollTo(tester, find.byType(DeliveryFunnelChart));
      expect(find.byType(DeliveryFunnelChart), findsOneWidget);
      expectNoOverflow(tester);
    });

    testWidgets('does not overflow on a larger phone', (tester) async {
      final harness = analyticsHarness();
      await pumpCommerceScreen(
        tester,
        const HomeScreen(),
        harness: harness,
        size: largePhone,
      );
      expectNoOverflow(tester);
    });

    testWidgets('a quiet shop gets an explanation, not an empty chart', (
      tester,
    ) async {
      final harness = analyticsHarness(
        profit: profitJson(parcelCount: 0, contributionProfit: 0),
      );
      await pumpCommerceScreen(tester, const HomeScreen(), harness: harness);
      await scrollTo(
        tester,
        find.textContaining('No settled parcels in the last 30 days'),
      );

      expect(
        find.textContaining('No settled parcels in the last 30 days'),
        findsWidgets,
      );
    });
  });

  group('Insights → Profit & costs', () {
    testWidgets('the headline carries how much of it is measured', (
      tester,
    ) async {
      final harness = analyticsHarness(
        profit: profitJson(
          quality: <String, int>{'ACTUAL': 8, 'ESTIMATED': 4, 'MISSING': 0},
          parcelCount: 12,
        ),
      );
      await pumpCommerceScreen(
        tester,
        const ProfitDetailScreen(),
        harness: harness,
      );

      expect(find.textContaining('8 of 12 parcels settled'), findsOneWidget);
      expect(find.textContaining('4 parcels not settled yet'), findsOneWidget);
      expectNoOverflow(tester);
    });

    testWidgets('unallocated ad spend is shown rather than blended in', (
      tester,
    ) async {
      final harness = analyticsHarness(
        profit: profitJson(unallocatedAdSpend: 500_000),
      );
      await pumpCommerceScreen(
        tester,
        const ProfitDetailScreen(),
        harness: harness,
      );

      // Section 86: ad money that reached no parcel is a real cost, and
      // smearing it across unrelated orders would flatter their margins.
      expect(
        find.textContaining('has not been allocated to any parcel'),
        findsOneWidget,
      );
    });

    testWidgets('a courier below the sample threshold gets no rank', (
      tester,
    ) async {
      final harness = analyticsHarness(
        returns: returnsJson(
          byCourier: <Map<String, dynamic>>[
            rateLineJson('steadfast', parcels: 3, returns: 1, enough: false),
          ],
        ),
      );
      await pumpCommerceScreen(
        tester,
        const ProfitDetailScreen(),
        harness: harness,
      );
      await scrollTo(tester, find.text('No rank yet'));

      // Section 24: do not show unreliable rankings before enough sample
      // exists, and make the rule visible.
      expect(find.text('No rank yet'), findsWidgets);
    });

    testWidgets('returns with no reason are counted, not hidden', (
      tester,
    ) async {
      final harness = analyticsHarness(
        returns: returnsJson(
          returnCount: 4,
          byReason: <String, dynamic>{'CUSTOMER_REFUSED': 2},
          unknownReasonCount: 2,
        ),
      );
      await pumpCommerceScreen(
        tester,
        const ProfitDetailScreen(),
        harness: harness,
      );
      await scrollTo(tester, find.text('Customer refused'));

      expect(find.text('Customer refused'), findsOneWidget);
      expect(
        find.textContaining('2 returns with no reason recorded'),
        findsOneWidget,
      );
    });
  });

  group('Expenses', () {
    testWidgets('an unallocated expense says it has changed nothing', (
      tester,
    ) async {
      final harness = analyticsHarness()
        ..adapter.onJson(
          'GET',
          '/expenses',
          page(<Map<String, dynamic>>[expenseJson()]),
        );
      await pumpCommerceScreen(
        tester,
        const ExpensesScreen(),
        harness: harness,
      );

      expect(find.text('Not allocated'), findsOneWidget);
      expect(find.textContaining('has not reached any parcel'), findsOneWidget);
      expectNoOverflow(tester);
    });

    testWidgets('rent is not offered a per-parcel split', (tester) async {
      final harness = analyticsHarness()
        ..adapter.onJson(
          'GET',
          '/expenses',
          page(<Map<String, dynamic>>[
            expenseJson(kind: 'FIXED', description: 'Shop rent'),
          ]),
        );
      await pumpCommerceScreen(
        tester,
        const ExpensesScreen(),
        harness: harness,
      );

      // Section 86 warns against pretending fixed-cost allocation is
      // accounting-grade, so the button is absent and the row says why.
      expect(find.text('Allocate to parcels'), findsNothing);
      expect(find.text('Below the line'), findsOneWidget);
      expect(find.textContaining('below contribution profit'), findsOneWidget);
    });

    testWidgets('spend that reached no parcel is called out', (tester) async {
      final harness = analyticsHarness()
        ..adapter.onJson(
          'GET',
          '/expenses',
          page(<Map<String, dynamic>>[
            expenseJson(allocated: 0, allocatedAt: '2026-09-10T06:00:00Z'),
          ]),
        );
      await pumpCommerceScreen(
        tester,
        const ExpensesScreen(),
        harness: harness,
      );

      expect(find.text('Reached no parcel'), findsOneWidget);
    });

    testWidgets('re-allocating asks why before it moves a figure', (
      tester,
    ) async {
      final harness = analyticsHarness()
        ..adapter.onJson(
          'GET',
          '/expenses',
          page(<Map<String, dynamic>>[
            expenseJson(allocated: 10_000, allocatedAt: '2026-09-10T06:00:00Z'),
          ]),
        );
      await pumpCommerceScreen(
        tester,
        const ExpensesScreen(),
        harness: harness,
      );

      await tester.tap(find.text('Allocate again'));
      await settle(tester);

      // Section 86: a profit figure the seller has already read does not
      // change silently.
      expect(find.text('Why re-allocate?'), findsOneWidget);
      expect(
        find.textContaining('changes profit figures you have already seen'),
        findsOneWidget,
      );
    });
  });

  group('Notification centre', () {
    testWidgets('everything the server raised is here, read or not', (
      tester,
    ) async {
      final harness = analyticsHarness()
        ..adapter.onJson(
          'GET',
          '/notifications',
          page(<Map<String, dynamic>>[
            notificationJson(
              title: 'Delivered but not paid: ৳8,950',
              severity: 'CRITICAL',
            ),
            notificationJson(
              id: 'n2',
              kind: 'WEEKLY_SUMMARY',
              severity: 'INFO',
              title: 'Your week: ৳12,400 profit',
              readAt: '2026-09-10T06:00:00Z',
            ),
          ]),
        );
      await pumpCommerceScreen(
        tester,
        const NotificationCentreScreen(),
        harness: harness,
      );

      // Section 94: push is not enough, so the centre holds both.
      expect(find.text('Delivered but not paid: ৳8,950'), findsOneWidget);
      expect(find.text('Your week: ৳12,400 profit'), findsOneWidget);
      expect(find.text('Losing money'), findsOneWidget);
      expect(find.text('For info'), findsOneWidget);
      expectNoOverflow(tester);
    });

    testWidgets('the Friday summary opens the figures it was sent with', (
      tester,
    ) async {
      final harness = analyticsHarness()
        ..adapter.onJson(
          'GET',
          '/notifications',
          page(<Map<String, dynamic>>[
            notificationJson(
              kind: 'WEEKLY_SUMMARY',
              severity: 'INFO',
              title: 'Your week',
              readAt: '2026-09-10T06:00:00Z',
              payload: weeklyPayload(),
            ),
          ]),
        );
      await pumpCommerceScreen(
        tester,
        const NotificationCentreScreen(),
        harness: harness,
      );

      await tester.tap(find.text('Your week'));
      await settle(tester);

      // Read out of the notification's own payload: recalculating it from
      // today's data would rewrite what the seller was told on Friday.
      expect(find.text('Contribution profit'), findsOneWidget);
      expect(find.text('৳12,400'), findsOneWidget);
      // Section 24's rule, visible in place of the ranking.
      expect(find.text('No rank yet'), findsWidgets);
      expectNoOverflow(tester);
    });

    testWidgets('an empty centre explains what would appear', (tester) async {
      final harness = analyticsHarness()
        ..adapter.onJson(
          'GET',
          '/notifications',
          page(<Map<String, dynamic>>[]),
        );
      await pumpCommerceScreen(
        tester,
        const NotificationCentreScreen(),
        harness: harness,
      );

      expect(find.text('Nothing needs you right now'), findsOneWidget);
      expect(
        find.textContaining('delivered parcels that were never paid for'),
        findsOneWidget,
      );
    });
  });
}

// --------------------------------------------------------------------------- //
// Harness and fixtures
// --------------------------------------------------------------------------- //

/// A harness with every Phase E read already answered.
///
/// The screens fire several calls on open; leaving one unrouted would make a
/// test fail for a reason unrelated to what it is checking.
CommerceHarness analyticsHarness({
  Map<String, dynamic>? home,
  Map<String, dynamic>? profit,
  Map<String, dynamic>? returns,
  List<Map<String, dynamic>>? products,
}) {
  final harness = CommerceHarness();
  harness.adapter
    ..onJson('GET', '/analytics/home', home ?? homeJson())
    ..onJson('GET', '/analytics/profit', profit ?? profitJson())
    ..onJson('GET', '/analytics/returns', returns ?? returnsJson())
    ..onJson('GET', '/analytics/products', products ?? <Map<String, dynamic>>[])
    ..onJson('GET', '/money/summary', moneySummaryJson())
    ..onJson('GET', '/expenses', page(<Map<String, dynamic>>[]))
    ..onJson('GET', '/notifications', page(<Map<String, dynamic>>[]))
    ..onJson('GET', '/notifications/unread-count', <String, dynamic>{
      'unread': 0,
    });
  return harness;
}

Map<String, dynamic> alertJson(
  String kind,
  String severity,
  int count,
  int amount,
) => <String, dynamic>{
  'kind': kind,
  'severity': severity,
  'count': count,
  'amount_paisa': amount,
};

Map<String, dynamic> homeJson({
  int ordersToday = 2,
  int deliveredToday = 1,
  int returnedToday = 0,
  int grossSales = 200_000,
  int realizedRevenue = 140_500,
  int contributionProfit = 75_500,
  int codOutstanding = 480_500,
  int expectedToday = 140_500,
  int unforecast = 0,
  int overdue = 0,
  int mismatch = 0,
  int mismatchCount = 0,
  int returnLoss = 0,
  int estimatedParcels = 0,
  int incompleteParcels = 0,
  List<Map<String, dynamic>>? alerts,
}) => <String, dynamic>{
  'as_of': '2026-09-10',
  'orders_today': ordersToday,
  'delivered_today': deliveredToday,
  'returned_today': returnedToday,
  'gross_sales_paisa': grossSales,
  'realized_revenue_paisa': realizedRevenue,
  'contribution_profit_paisa': contributionProfit,
  'cod_outstanding_paisa': codOutstanding,
  'cod_expected_today_paisa': expectedToday,
  'cod_unforecast_paisa': unforecast,
  'cod_overdue_paisa': overdue,
  'mismatch_paisa': mismatch,
  'mismatch_count': mismatchCount,
  'return_loss_paisa': returnLoss,
  'estimated_parcels': estimatedParcels,
  'incomplete_parcels': incompleteParcels,
  'alerts': alerts ?? <Map<String, dynamic>>[],
};

Map<String, dynamic> profitJson({
  int parcelCount = 12,
  int realizedRevenue = 1_686_000,
  int contributionProfit = 480_000,
  int unallocatedAdSpend = 0,
  int? marginBasisPoints = 2847,
  Map<String, int>? quality,
  List<Map<String, dynamic>>? series,
  List<Map<String, dynamic>>? funnel,
}) => <String, dynamic>{
  'since': '2026-08-12',
  'until': '2026-09-10',
  'parcel_count': parcelCount,
  'realized_revenue_paisa': realizedRevenue,
  'item_cost_paisa': 900_000,
  'delivery_charge_paisa': 96_000,
  'cod_fee_paisa': 16_860,
  'return_charge_paisa': 0,
  'packaging_paisa': 0,
  'ad_cost_paisa': 0,
  'write_off_cost_paisa': 0,
  'contribution_profit_paisa': contributionProfit,
  'unallocated_ad_spend_paisa': unallocatedAdSpend,
  'fixed_cost_paisa': 0,
  'operating_profit_paisa': contributionProfit - unallocatedAdSpend,
  'margin_basis_points': parcelCount == 0 ? null : marginBasisPoints,
  'quality':
      quality ??
      <String, int>{'ACTUAL': parcelCount, 'ESTIMATED': 0, 'MISSING': 0},
  'series':
      series ??
      <Map<String, dynamic>>[
        for (var day = 1; day <= 3; day++)
          <String, dynamic>{
            'business_date': '2026-09-0$day',
            'parcel_count': parcelCount == 0 ? 0 : 4,
            'realized_revenue_paisa': parcelCount == 0 ? 0 : 562_000,
            'contribution_profit_paisa': parcelCount == 0 ? 0 : 160_000,
          },
      ],
  'funnel':
      funnel ??
      <Map<String, dynamic>>[
        <String, dynamic>{'label': 'Dispatched', 'count': parcelCount},
        <String, dynamic>{'label': 'In transit', 'count': 0},
        <String, dynamic>{'label': 'Delivered', 'count': parcelCount},
        <String, dynamic>{'label': 'Returned', 'count': 0},
        <String, dynamic>{'label': 'Lost or damaged', 'count': 0},
      ],
};

Map<String, dynamic> rateLineJson(
  String label, {
  int parcels = 10,
  int returns = 1,
  int loss = 0,
  bool enough = true,
}) => <String, dynamic>{
  'label': label,
  'parcel_count': parcels,
  'return_count': returns,
  'return_rate_basis_points': parcels == 0
      ? 0
      : (returns * 10000 / parcels).round(),
  'loss_paisa': loss,
  'has_enough_sample': enough,
};

Map<String, dynamic> returnsJson({
  int parcelCount = 12,
  int returnCount = 0,
  int directLoss = 0,
  Map<String, dynamic>? byReason,
  int unknownReasonCount = 0,
  List<Map<String, dynamic>>? byProduct,
  List<Map<String, dynamic>>? byArea,
  List<Map<String, dynamic>>? byCourier,
}) => <String, dynamic>{
  'since': '2026-08-12',
  'until': '2026-09-10',
  'parcel_count': parcelCount,
  'return_count': returnCount,
  'return_rate_basis_points': parcelCount == 0
      ? null
      : (returnCount * 10000 / parcelCount).round(),
  'direct_loss_paisa': directLoss,
  'outward_delivery_cost_paisa': 0,
  'return_delivery_cost_paisa': 0,
  'packaging_loss_paisa': 0,
  'write_off_paisa': 0,
  'by_reason': byReason ?? <String, dynamic>{},
  'unknown_reason_count': unknownReasonCount,
  'by_product': byProduct ?? <Map<String, dynamic>>[],
  'by_area': byArea ?? <Map<String, dynamic>>[],
  'by_courier': byCourier ?? <Map<String, dynamic>>[],
};

Map<String, dynamic> expenseJson({
  String id = 'e1',
  String kind = 'AD_SPEND',
  int amount = 10_000,
  int allocated = 0,
  String description = 'Facebook boost',
  String? allocatedAt,
}) => <String, dynamic>{
  'id': id,
  'kind': kind,
  'amount_paisa': amount,
  'allocated_paisa': allocated,
  'unallocated_paisa': amount - allocated,
  'period_start': '2026-09-10',
  'period_end': '2026-09-10',
  'description': description,
  'product_id': null,
  'preferred_method': 'EQUAL_PER_DELIVERED_ORDER',
  'allocated_at': allocatedAt,
  'created_at': '2026-09-10T06:00:00Z',
};

Map<String, dynamic> notificationJson({
  String id = 'n1',
  String kind = 'DELIVERED_BUT_UNPAID',
  String severity = 'CRITICAL',
  String title = 'Delivered but not paid',
  String body = '7 parcels reached the customer and the money has not arrived.',
  int amount = 895_000,
  String? readAt,
  Map<String, dynamic>? payload,
}) => <String, dynamic>{
  'id': id,
  'kind': kind,
  'severity': severity,
  'title': title,
  'body': body,
  'entity_type': null,
  'entity_id': null,
  'amount_paisa': amount,
  'item_count': 7,
  'business_date': '2026-09-10',
  'read_at': readAt,
  'payload': payload ?? <String, dynamic>{},
  'created_at': '2026-09-10T06:00:00Z',
};

Map<String, dynamic> weeklyPayload() => <String, dynamic>{
  'week_start': '2026-09-04',
  'week_end': '2026-09-10',
  'order_count': 18,
  'delivered_count': 14,
  'return_count': 3,
  'return_loss_paisa': 18_000,
  'sales_paisa': 1_686_000,
  'contribution_profit_paisa': 1_240_000,
  'cod_outstanding_paisa': 480_500,
  'overdue_paisa': 0,
  'mismatch_count': 1,
  'ad_spend_paisa': 500_000,
  // Section 24: below the sample threshold the server sends the reason
  // instead of a ranking, and the sheet shows the reason.
  'best_product': null,
  'worst_product': null,
  'best_courier': null,
  'worst_courier': null,
  'ranking_note': 'Not enough sales yet to rank products.',
  'courier_note': 'A courier needs 5 finished parcels.',
};
