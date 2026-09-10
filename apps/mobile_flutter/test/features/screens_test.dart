import 'package:ecomsbd/design/charts/bar_charts.dart';
import 'package:ecomsbd/design/charts/donut_chart.dart';
import 'package:ecomsbd/design/charts/line_chart.dart';
import 'package:ecomsbd/design/components/navigation.dart';
import 'package:ecomsbd/features/home/home_screen.dart';
import 'package:ecomsbd/features/insights/insights_screen.dart';
import 'package:ecomsbd/features/shared/demo_data_notice.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../helpers.dart';

/// Screen-level tests.
///
/// Every one runs at 360dp — the reference low-end Android width from master
/// spec section 53 — and asserts no overflow, because that is the width where
/// a four-column grid or an unwrapped money figure actually breaks.
void main() {
  group('Home dashboard', () {
    testWidgets('renders the prototype hierarchy at 360dp', (tester) async {
      await pumpAtSize(tester, const Scaffold(body: HomeScreen()));
      await tester.pump();

      // Hero: shop identity and the COD outstanding statement.
      expect(find.text('Noor Fashion'), findsOneWidget);
      // Also shown in the donut centre, so more than one is correct.
      expect(find.text('৳87,450'), findsWidgets);

      // Quick actions.
      expect(find.text('New order'), findsOneWidget);
      expect(find.text('Risk check'), findsOneWidget);

      // Needs attention.
      expect(find.text('Needs attention'), findsOneWidget);
      expect(find.text('3 delivered orders still unpaid'), findsOneWidget);

      expect(tester.takeException(), isNull);
    });

    testWidgets('renders every chart from the prototype', (tester) async {
      await pumpAtSize(tester, const Scaffold(body: HomeScreen()));
      await tester.pump();

      expect(find.byType(ProfitTrendChart), findsOneWidget);
      expect(find.byType(CodDonutChart), findsOneWidget);

      // The funnel and both horizontal bar sets are further down the page.
      await tester.scrollUntilVisible(
        find.byType(DeliveryFunnelChart),
        320,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.byType(DeliveryFunnelChart), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('marks itself as demo data', (tester) async {
      // A dashboard screenshot must never be mistakable for real seller money.
      await pumpAtSize(tester, const Scaffold(body: HomeScreen()));
      expect(find.byType(DemoDataNotice), findsOneWidget);
    });

    testWidgets('does not overflow on a larger phone', (tester) async {
      await pumpAtSize(
        tester,
        const Scaffold(body: HomeScreen()),
        size: largePhone,
      );
      await tester.pump();
      expect(tester.takeException(), isNull);
    });
  });

  // The Orders screen moved to real data in Phase B; its tests live in
  // commerce_screens_test.dart, where a fake server supplies the rows.

  // The Money screen moved to real data in Phase D; its tests live in
  // money_screens_test.dart, where a fake server supplies the figures.

  group('Insights', () {
    testWidgets('renders the profit bridge and scorecard', (tester) async {
      await pumpAtSize(tester, const Scaffold(body: InsightsScreen()));
      await tester.pump();

      expect(find.text('Profit & business intelligence'), findsOneWidget);
      expect(tester.takeException(), isNull);

      // `.first` on a not-yet-built finder throws inside scrollUntilVisible,
      // so scroll to the card's title and assert on the chart afterwards.
      await tester.scrollUntilVisible(
        find.text('Revenue → profit bridge'),
        320,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.byType(HorizontalBarChart), findsWidgets);
    });

    testWidgets('withholds a ranking when the sample is too small', (
      tester,
    ) async {
      // Master spec section 24: no ranking before the sample supports it.
      await pumpAtSize(tester, const Scaffold(body: InsightsScreen()));
      await tester.pump();
      await tester.scrollUntilVisible(
        find.text('Courier scorecard'),
        300,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.text('No rank yet'), findsOneWidget);
    });
  });

  group('navigation labels', () {
    test('the five destinations are locked and in order', () {
      expect(MainDestination.values.map((d) => d.label).toList(), <String>[
        'Home',
        'Orders',
        'Money',
        'Insights',
        'Menu',
      ]);
    });
  });
}
