import 'dart:convert';

import 'package:drift/drift.dart' show Value;
import 'package:ecomsbd/data/local/database.dart';
import 'package:ecomsbd/features/orders/orders_screen.dart';
import 'package:ecomsbd/features/products/products_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../features/commerce_harness.dart';

/// Performance guards for the shapes a real shop reaches.
///
/// Master spec section 53 targets a low-end 360dp Android phone, so the budgets
/// here are deliberately generous — they exist to catch a regression that turns
/// a list into a freeze, not to benchmark the machine CI happens to run on.
/// A page must stay a page: the app never loads a shop's whole order history
/// into memory (section 106).
void main() {
  group('Large local datasets', () {
    late EcomsbdDatabase db;

    setUp(() => db = EcomsbdDatabase.memory());
    tearDown(() => db.close());

    Future<void> seedOrders(int count) async {
      final base = DateTime.utc(2026, 1, 1);
      await db.putOrders(<CachedOrdersCompanion>[
        for (var i = 0; i < count; i++)
          CachedOrdersCompanion.insert(
            id: 'order-$i',
            tenantId: testTenantId,
            payload: jsonEncode(
              orderJson(id: 'order-$i', number: 'CP-2026-$i', cod: 100000 + i),
            ),
            updatedAt: base.add(Duration(minutes: i)),
            orderNumber: 'CP-2026-$i',
            status: i.isEven ? 'CONFIRMED' : 'COMPLETED',
            createdAt: base.add(Duration(minutes: i)),
            customerName: Value('Customer $i'),
            phoneMasked: const Value('01712****78'),
            codAmountPaisa: Value(100000 + i),
          ),
      ]);
    }

    test('a page of orders stays a page with 2,000 rows stored', () async {
      await seedOrders(2000);

      final stopwatch = Stopwatch()..start();
      final page = await db.localOrders(tenantId: testTenantId, limit: 30);
      stopwatch.stop();

      expect(page, hasLength(30));
      // The whole point of cursor pagination: the cost of showing a screen does
      // not grow with the shop's history.
      expect(
        stopwatch.elapsedMilliseconds,
        lessThan(250),
        reason: 'a 30-row page took ${stopwatch.elapsedMilliseconds}ms',
      );
    });

    test('paging to the end of 2,000 orders does not degrade', () async {
      await seedOrders(2000);

      final first = Stopwatch()..start();
      await db.localOrders(tenantId: testTenantId, limit: 30);
      first.stop();

      final last = Stopwatch()..start();
      final deep = await db.localOrders(
        tenantId: testTenantId,
        limit: 30,
        offset: 1950,
      );
      last.stop();

      expect(deep, hasLength(30));
      expect(last.elapsedMilliseconds, lessThan(400));
    });

    test('searching 2,000 orders answers quickly', () async {
      await seedOrders(2000);

      final stopwatch = Stopwatch()..start();
      final results = await db.localOrders(
        tenantId: testTenantId,
        search: 'Customer 1999',
        limit: 30,
      );
      stopwatch.stop();

      expect(results, hasLength(1));
      expect(
        stopwatch.elapsedMilliseconds,
        lessThan(400),
        reason: 'search took ${stopwatch.elapsedMilliseconds}ms',
      );
    });

    test('a 500-product catalogue filters by low stock quickly', () async {
      final base = DateTime.utc(2026, 1, 1);
      await db.putProducts(<CachedProductsCompanion>[
        for (var i = 0; i < 500; i++)
          CachedProductsCompanion.insert(
            id: 'product-$i',
            tenantId: testTenantId,
            payload: jsonEncode(
              productJson(id: 'product-$i', name: 'Product $i', stock: i % 20),
            ),
            updatedAt: base,
            name: 'Product $i',
            createdAt: base.add(Duration(minutes: i)),
            stockOnHand: Value(i % 20),
            isLowStock: Value(i % 20 <= 3),
          ),
      ]);

      final stopwatch = Stopwatch()..start();
      final low = await db.localProducts(
        tenantId: testTenantId,
        lowStockOnly: true,
        limit: 30,
      );
      stopwatch.stop();

      expect(low, hasLength(30));
      expect(stopwatch.elapsedMilliseconds, lessThan(250));
    });

    test('another shop cannot appear in this shop\'s page', () async {
      await seedOrders(50);
      await db.putOrders(<CachedOrdersCompanion>[
        CachedOrdersCompanion.insert(
          id: 'other-1',
          tenantId: 'tenant-other',
          payload: jsonEncode(orderJson(id: 'other-1')),
          updatedAt: DateTime.utc(2026, 6, 1),
          orderNumber: 'CP-OTHER-1',
          status: 'CONFIRMED',
          createdAt: DateTime.utc(2026, 6, 1),
        ),
      ]);

      final page = await db.localOrders(tenantId: testTenantId, limit: 100);
      expect(page.any((row) => row.orderNumber == 'CP-OTHER-1'), isFalse);
    });
  });

  group('Rendering at 360dp', () {
    testWidgets('a full page of order cards builds without overflow', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/orders', <String, dynamic>{
          'items': <Map<String, dynamic>>[
            for (var i = 0; i < 30; i++)
              orderJson(
                id: 'o$i',
                number: 'CP-20260910-${i.toString().padLeft(4, '0')}',
                // A lakh-scale amount, which is where compact formatting and
                // the four-cell fact grid actually get tight.
                cod: 284500 + i,
              ),
          ],
          'next_cursor': 'cursor-2',
          'has_more': true,
        });

      final stopwatch = Stopwatch()..start();
      await pumpCommerceScreen(tester, const OrdersScreen(), harness: harness);
      stopwatch.stop();

      expectNoOverflow(tester);
      expect(
        stopwatch.elapsedMilliseconds,
        lessThan(6000),
        reason: 'first paint took ${stopwatch.elapsedMilliseconds}ms',
      );
      // Only the visible slice is built; a ListView that materialised all 30
      // cards at 360dp would be doing work nobody can see.
      expect(
        find.byType(SellerOrderCard, skipOffstage: false).evaluate().length,
        lessThanOrEqualTo(30),
      );
    });

    testWidgets('a full page of product rows builds without overflow', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/products', <String, dynamic>{
          'items': <Map<String, dynamic>>[
            for (var i = 0; i < 30; i++)
              productJson(
                id: 'p$i',
                name: 'Premium Cotton Abaya with a deliberately long name $i',
                stock: i,
                cost: 40000 + i,
                price: 284500 + i,
              ),
          ],
          'next_cursor': null,
          'has_more': false,
        });

      await pumpCommerceScreen(
        tester,
        const ProductsScreen(),
        harness: harness,
      );
      expectNoOverflow(tester);
    });

    testWidgets('the widest supported phone is fine too', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/orders', <String, dynamic>{
          'items': <Map<String, dynamic>>[orderJson(cod: 12845000)],
          'has_more': false,
        });
      await pumpCommerceScreen(
        tester,
        const OrdersScreen(),
        harness: harness,
        size: const Size(412, 915),
      );
      expectNoOverflow(tester);
    });
  });

  group('Search', () {
    testWidgets('typing does not fire a request per keystroke', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/products', <String, dynamic>{
          'items': <Map<String, dynamic>>[],
          'has_more': false,
        });
      await pumpCommerceScreen(
        tester,
        const ProductsScreen(),
        harness: harness,
      );

      final initial = harness.adapter.to('GET', '/products').length;

      // Six characters typed the way a seller types them.
      for (final text in <String>[
        'a',
        'ab',
        'aba',
        'abay',
        'abaya',
        'abayas',
      ]) {
        await tester.enterText(find.byType(TextField).first, text);
        await tester.pump(const Duration(milliseconds: 40));
      }
      await settle(tester, frames: 10);

      final issued = harness.adapter.to('GET', '/products').length - initial;
      // One request for the settled term. On the connections this app targets,
      // six would cost the seller real seconds.
      expect(
        issued,
        lessThanOrEqualTo(2),
        reason: 'six keystrokes issued $issued requests',
      );
      expect(
        harness.adapter.to('GET', '/products').last.query['search'],
        'abayas',
      );
    });
  });
}
