import 'package:ecomsbd/features/search/search_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'commerce_harness.dart';

/// Global search, against a fake server.
///
/// What matters is that one query reaches every section the seller could mean,
/// that a stray character does not hit the server, and that "nothing found" is
/// said rather than shown as a blank page.
void main() {
  group('Search', () {
    testWidgets('one query searches orders, customers and products', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/orders',
          page(<Map<String, dynamic>>[orderJson()]),
        )
        ..adapter.onJson(
          'GET',
          '/customers/crm',
          page(<Map<String, dynamic>>[customerJson()]),
        )
        ..adapter.onJson(
          'GET',
          '/products',
          page(<Map<String, dynamic>>[productJson()]),
        );
      // Tall enough that all three sections are built without scrolling.
      await pumpCommerceScreen(
        tester,
        const GlobalSearchScreen(),
        harness: harness,
        size: const Size(400, 2000),
      );

      expect(find.text('Search your shop'), findsOneWidget);

      await tester.enterText(find.byType(TextField), 'abaya');
      await settle(tester, frames: 10, step: const Duration(milliseconds: 100));

      final searched = harness.adapter.requests
          .where((request) => request.query['search'] == 'abaya')
          .map((request) => request.path)
          .toSet();
      expect(
        searched,
        containsAll(<String>['/orders', '/customers/crm', '/products']),
      );
      expect(find.text('Orders'), findsOneWidget);
      expect(find.text('Customers'), findsOneWidget);
      expect(find.text('Products'), findsOneWidget);
      expect(find.textContaining('CP-20260910-0042'), findsWidgets);
      expect(find.text('Cotton Abaya'), findsWidgets);
      expectNoOverflow(tester);
    });

    testWidgets('a single character does not reach the server', (tester) async {
      final harness = CommerceHarness();
      await pumpCommerceScreen(
        tester,
        const GlobalSearchScreen(),
        harness: harness,
      );

      await tester.enterText(find.byType(TextField), 'a');
      await settle(tester, frames: 10, step: const Duration(milliseconds: 100));

      expect(
        harness.adapter.requests.where(
          (request) => request.query['search'] != null,
        ),
        isEmpty,
      );
      expect(find.text('Search your shop'), findsOneWidget);
    });

    testWidgets('no match says so instead of showing a blank page', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/orders', page(<Map<String, dynamic>>[]))
        ..adapter.onJson(
          'GET',
          '/customers/crm',
          page(<Map<String, dynamic>>[]),
        )
        ..adapter.onJson('GET', '/products', page(<Map<String, dynamic>>[]));
      await pumpCommerceScreen(
        tester,
        const GlobalSearchScreen(),
        harness: harness,
      );

      await tester.enterText(find.byType(TextField), 'zzzz');
      await settle(tester, frames: 10, step: const Duration(milliseconds: 100));

      expect(find.textContaining('Nothing matches'), findsOneWidget);
    });
  });
}
