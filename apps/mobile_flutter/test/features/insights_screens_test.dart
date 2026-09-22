import 'package:ecomsbd/features/insights/insight_screens.dart';
import 'package:ecomsbd/features/insights/insight_widgets.dart';
import 'package:ecomsbd/features/insights/insights_screen.dart';
import 'package:ecomsbd/features/insights/profit_detail_screen.dart';
import 'package:ecomsbd/features/insights/rto_screen.dart';
import 'package:ecomsbd/l10n/app_strings_data.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'analytics_screens_test.dart' show analyticsHarness;
import 'commerce_harness.dart';

Map<String, dynamic> comparison(int current) => <String, dynamic>{
  'current': current,
  'previous': 0,
  'change_basis_points': null,
};

Map<String, dynamic> overview({bool locked = false}) => <String, dynamic>{
  'orders': comparison(12),
  'delivered': comparison(9),
  'rto': <String, dynamic>{
    'completed': 12,
    'rto': 3,
    'sufficient': true,
    'rto_rate_basis_points': 2500,
  },
  'money_locked': locked ? 'PERMISSION' : null,
  'money': locked
      ? null
      : <String, dynamic>{
          'revenue': comparison(125000),
          'profit': comparison(42000),
          'received': comparison(75000),
          'receivable_paisa': 895000,
          'overdue_paisa': 100000,
          'discrepancy_count': 2,
          'discrepancy_paisa': 20000,
          'profit_quality': <String, int>{'MISSING': 1},
        },
  'stock': <String, dynamic>{
    'low_stock_items': 2,
    'out_of_stock_items': 1,
    'slow_moving_items': 3,
    'slow_moving_days': 30,
  },
  'explanations': <Map<String, dynamic>>[
    <String, dynamic>{
      'code': 'OUT_OF_STOCK',
      'params': <String, int>{'count': 1},
    },
  ],
};

CommerceHarness insightsHarness({Map<String, dynamic>? data}) =>
    analyticsHarness()
      ..adapter.onJson(
        'GET',
        '/analytics/insights/overview',
        data ?? overview(),
      )
      ..adapter.onJson('GET', '/analytics/insights/trend', <String, dynamic>{
        'granularity': 'day',
        'buckets': <Map<String, dynamic>>[
          <String, dynamic>{
            'start': '2026-09-20',
            'orders': 12,
            'parcels': 12,
            'profit_paisa': 42000,
            'revenue_paisa': 125000,
          },
        ],
      })
      ..adapter.onJson('GET', '/analytics/rto/summary', <String, dynamic>{});

void main() {
  testWidgets(
    'overview renders server money, sample quality and range changes',
    (tester) async {
      final harness = insightsHarness();
      await pumpCommerceScreen(
        tester,
        const InsightsScreen(),
        harness: harness,
      );
      expect(find.text('DELIVERED SALES'), findsOneWidget);
      expect(find.text('৳1,250'), findsOneWidget);
      expect(find.text('1 parcels without cost'), findsOneWidget);
      expect(find.text('No earlier base to compare'), findsWidgets);
      for (final days in <int>[7, 90]) {
        await tester.tap(find.text('$days days'));
        await settle(tester);
        for (final section in <String>['overview', 'trend']) {
          expect(
            harness.adapter
                .to('GET', '/analytics/insights/$section')
                .last
                .query['days'],
            days,
          );
        }
      }
      await scrollTo(tester, find.byType(InsightBarChart));
      expect(
        tester.widget<InsightBarChart>(find.byType(InsightBarChart)).values,
        <int>[42000],
      );
      expectNoOverflow(tester);
    },
  );

  testWidgets(
    'empty and restricted figures are explained without fabricated money',
    (tester) async {
      final data = overview(locked: true)..['rto'] = <String, dynamic>{};
      await pumpCommerceScreen(
        tester,
        const InsightsScreen(),
        harness: insightsHarness(data: data),
      );
      expect(find.text('Not enough data'), findsOneWidget);
      expect(
        find.text('Your role does not include money figures.'),
        findsOneWidget,
      );
      expect(find.text('DELIVERED SALES'), findsNothing);
      await scrollTo(tester, find.text('Couriers'));
      expect(find.text('Cash & COD'), findsNothing);
      expect(find.text('Profit & costs'), findsNothing);
      expectNoOverflow(tester);
    },
  );

  testWidgets(
    'overview opens the moved Profit detail and existing RTO drill-down',
    (tester) async {
      await pumpCommerceScreen(
        tester,
        const InsightsScreen(),
        harness: insightsHarness(),
      );
      await scrollTo(tester, find.text('Profit & costs'));
      await tester.tap(find.text('Profit & costs'));
      await settle(tester);
      expect(find.byType(ProfitDetailScreen), findsOneWidget);
      await tester.pageBack();
      await settle(tester, frames: 20);
      await scrollTo(tester, find.text('Returns & RTO'));
      await tester.ensureVisible(find.text('Returns & RTO'));
      await tester.pump();
      await tester.tap(find.text('Returns & RTO'));
      await settle(tester);
      expect(find.byType(RtoScreen), findsOneWidget);
    },
  );

  testWidgets(
    'cash keeps received, receivable, overdue and in-transit separate',
    (tester) async {
      final harness = insightsHarness()
        ..adapter.onJson('GET', '/analytics/insights/cash', <String, dynamic>{
          'received': comparison(123400),
          'receivable_paisa': 895000,
          'overdue_paisa': 56700,
          'in_transit_paisa': 234500,
          'in_transit_count': 3,
          'aging': <Map<String, dynamic>>[
            <String, dynamic>{
              'label': '8–14 days',
              'min_days': 8,
              'max_days': 14,
              'count': 2,
              'amount_paisa': 56700,
            },
          ],
        });
      await pumpCommerceScreen(
        tester,
        const InsightsCashScreen(),
        harness: harness,
      );
      for (final label in <String>[
        'COD received',
        'Outstanding COD',
        'Overdue COD',
        'COD on the road',
      ]) {
        expect(find.text(label.toUpperCase()), findsOneWidget);
      }
      expect(find.text('৳8,950'), findsOneWidget);
      await scrollTo(tester, find.text('8–14 days'));
      expect(find.textContaining('৳567'), findsWidgets);
      expectNoOverflow(tester);
    },
  );

  testWidgets(
    'products retain unknown profit and request server filters and pages',
    (tester) async {
      final harness = insightsHarness();
      harness.adapter.on(
        'GET',
        '/analytics/insights/products',
        (request) => FakeReply(<String, dynamic>{
          'counts': <String, int>{'all': 21, 'slow_moving': 1},
          'slow_moving_days': 30,
          'has_more': request.query['offset'] == 0,
          'items': <Map<String, dynamic>>[
            <String, dynamic>{
              'name': request.query['offset'] == 0
                  ? 'Cotton Abaya'
                  : 'Linen Scarf',
              'parcels': 2,
              'revenue_paisa': 125000,
              'profit_paisa': null,
              'stock_status': 'LOW',
              'stock_on_hand': 2,
            },
          ],
        }),
      );
      await pumpCommerceScreen(
        tester,
        const InsightsProductsScreen(),
        harness: harness,
      );
      expect(find.text('Profit unknown'), findsOneWidget);
      await scrollTo(tester, find.text('Load more'));
      await tester.tap(find.text('Load more'));
      await settle(tester);
      expect(
        harness.adapter
            .to('GET', '/analytics/insights/products')
            .last
            .query['offset'],
        1,
      );
      expect(find.text('Linen Scarf'), findsOneWidget);
      await scrollTo(tester, find.text('Slow-moving · 1'));
      await tester.tap(find.text('Slow-moving · 1'));
      await settle(tester);
      expect(
        harness.adapter
            .to('GET', '/analytics/insights/products')
            .last
            .query['category'],
        'slow_moving',
      );
      expect(
        harness.adapter
            .to('GET', '/analytics/insights/products')
            .last
            .query['offset'],
        0,
      );
      expectNoOverflow(tester);
    },
  );

  testWidgets(
    'courier facts qualify thin samples and show finance discrepancies',
    (tester) async {
      final harness = insightsHarness()
        ..adapter.onJson(
          'GET',
          '/analytics/insights/couriers',
          <String, dynamic>{
            'items': <Map<String, dynamic>>[
              <String, dynamic>{
                'provider': 'steadfast',
                'counts': <String, dynamic>{
                  'completed': 3,
                  'rto': 1,
                  'sufficient': false,
                },
                'stuck_now': 2,
                'outstanding_paisa': 895000,
                'overdue_paisa': 100000,
                'discrepancy_count': 1,
                'discrepancy_paisa': 20000,
              },
            ],
          },
        );
      await pumpCommerceScreen(
        tester,
        const InsightsCouriersScreen(),
        harness: harness,
      );
      expect(find.text('Steadfast'), findsOneWidget);
      expect(find.textContaining('Limited data'), findsWidgets);
      await scrollTo(tester, find.text('Mismatches'));
      expect(find.text('1 · ৳200'), findsOneWidget);
      expect(find.text('Not enough data'), findsOneWidget);
      expectNoOverflow(tester);
    },
  );

  testWidgets('inventory describes factual inactivity and variant stock', (
    tester,
  ) async {
    final harness = insightsHarness()
      ..adapter.onJson(
        'GET',
        '/analytics/insights/inventory',
        <String, dynamic>{
          'total_units': 8,
          'low_stock_items': 1,
          'out_of_stock_items': 0,
          'slow_moving_days': 30,
          'counts': <String, int>{'slow_moving': 1},
          'items': <Map<String, dynamic>>[
            <String, dynamic>{
              'name': 'Abaya',
              'variant_name': 'Blue XL',
              'stock_on_hand': 8,
              'status': 'LOW',
              'slow_moving': true,
            },
          ],
        },
      );
    await pumpCommerceScreen(
      tester,
      const InsightsInventoryScreen(),
      harness: harness,
    );
    await scrollTo(tester, find.text('Abaya · Blue XL'));
    expect(find.text('No sale in 30 days · 8 in stock'), findsOneWidget);
    expectNoOverflow(tester);
  });

  testWidgets(
    'customers qualify repeat share and link to own-shop RTO history',
    (tester) async {
      final harness = insightsHarness()
        ..adapter
            .onJson('GET', '/analytics/insights/customers', <String, dynamic>{
              'active': comparison(3),
              'new': comparison(1),
              'returning': 2,
              'orders_with_customer': 4,
              'repeat_orders': 2,
              'sufficient': false,
              'repeat_order_rate_basis_points': 5000,
              'repeat_rto_customers': 1,
            });
      await pumpCommerceScreen(
        tester,
        const InsightsCustomersScreen(),
        harness: harness,
      );
      expect(find.text('Limited data'), findsOneWidget);
      expect(find.text('50%'), findsNothing);
      await scrollTo(tester, find.text('See repeat-return patterns'));
      await tester.tap(find.text('See repeat-return patterns'));
      await settle(tester);
      expect(find.byType(RtoScreen), findsOneWidget);
    },
  );

  testWidgets('Bangla overview uses translated Insights strings', (
    tester,
  ) async {
    await pumpCommerceScreen(
      tester,
      Builder(
        builder: (context) => Localizations.override(
          context: context,
          locale: const Locale('bn'),
          child: const InsightsScreen(),
        ),
      ),
      harness: insightsHarness(),
    );
    expect(find.text(banglaStrings['ins.revenue']!), findsOneWidget);
    expect(find.text(banglaStrings['ins.profit']!), findsOneWidget);
    expectNoOverflow(tester);
  });

  test(
    'new Insights strings have both languages and matching placeholders',
    () {
      final keys = englishStrings.keys
          .where((key) => key.startsWith('ins.'))
          .toSet();
      expect(
        banglaStrings.keys.where((key) => key.startsWith('ins.')).toSet(),
        keys,
      );
      final placeholders = RegExp(r'\{\w+\}');
      for (final key in keys) {
        expect(banglaStrings[key], isNotEmpty, reason: key);
        expect(
          placeholders.allMatches(banglaStrings[key]!).map((m) => m[0]).toSet(),
          placeholders
              .allMatches(englishStrings[key]!)
              .map((m) => m[0])
              .toSet(),
          reason: key,
        );
      }
    },
  );
}
