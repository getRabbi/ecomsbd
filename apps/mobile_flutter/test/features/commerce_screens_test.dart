import 'dart:math' as math;

import 'package:ecomsbd/data/commerce/models.dart';
import 'package:ecomsbd/data/local/database.dart';
import 'package:ecomsbd/data/local/tables.dart';
import 'package:ecomsbd/features/customers/customers_screen.dart';
import 'package:ecomsbd/features/orders/duplicate_warning_sheet.dart';
import 'package:ecomsbd/features/orders/order_detail_screen.dart';
import 'package:ecomsbd/features/orders/orders_screen.dart';
import 'package:ecomsbd/features/products/products_screen.dart';
import 'package:ecomsbd/features/shared/data_state.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'commerce_harness.dart';

/// The Phase B screens, against a fake server.
///
/// The assertions concentrate on the things that would mislead a seller if they
/// were wrong: a fabricated courier or profit value, an offline record that
/// looks synced, a phone that is not masked, an empty list that is really a
/// dead connection.
void main() {
  group('Products', () {
    testWidgets('lists what the server returned', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/products',
          page(<Map<String, dynamic>>[
            productJson(name: 'Cotton Abaya', stock: 12),
            productJson(id: 'p2', name: 'Silk Hijab', stock: 2),
          ]),
        );
      await pumpCommerceScreen(
        tester,
        const ProductsScreen(),
        harness: harness,
      );

      expect(find.text('Cotton Abaya'), findsOneWidget);
      expect(find.text('Silk Hijab'), findsOneWidget);
      expectNoOverflow(tester);
    });

    testWidgets('flags low stock and shows the margin as a margin', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/products',
          page(<Map<String, dynamic>>[
            productJson(
              name: 'Silk Hijab',
              stock: 2,
              cost: 30000,
              price: 55000,
            ),
          ]),
        );
      await pumpCommerceScreen(
        tester,
        const ProductsScreen(),
        harness: harness,
      );

      expect(find.text('Low stock'), findsOneWidget);
      // ৳250 of margin, labelled MARGIN — never "profit", which needs a
      // settled delivery behind it.
      expect(find.text('MARGIN'), findsOneWidget);
      expect(find.text('৳250'), findsOneWidget);
      expect(find.textContaining('PROFIT'), findsNothing);
    });

    testWidgets('an empty catalogue explains the next action', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/products', page(<Map<String, dynamic>>[]));
      await pumpCommerceScreen(
        tester,
        const ProductsScreen(),
        harness: harness,
      );

      expect(find.text('No products yet'), findsOneWidget);
      expect(find.text('Add your first product'), findsOneWidget);
    });

    testWidgets('offline with nothing cached is not an empty catalogue', (
      tester,
    ) async {
      final harness = CommerceHarness()..offline = true;
      await pumpCommerceScreen(
        tester,
        const ProductsScreen(),
        harness: harness,
      );

      // The distinction that matters: "we cannot reach the server" is not the
      // same statement as "you have no products".
      expect(find.text('No internet connection'), findsOneWidget);
      expect(find.text('No products yet'), findsNothing);
    });

    testWidgets('cached results are labelled with when they were true', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/products',
          page(<Map<String, dynamic>>[productJson(name: 'Cotton Abaya')]),
        );
      await pumpCommerceScreen(
        tester,
        const ProductsScreen(),
        harness: harness,
      );
      expect(find.byType(StaleDataNotice), findsNothing);

      harness.offline = true;
      await tester.drag(find.byType(ListView), const Offset(0, 300));
      await settle(tester);

      expect(find.byType(StaleDataNotice), findsOneWidget);
      expect(find.textContaining('Saved data from'), findsOneWidget);
      expect(find.text('Cotton Abaya'), findsOneWidget);
    });
  });

  group('Customers', () {
    testWidgets('shows masked numbers and never a full one', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/customers/crm',
          page(<Map<String, dynamic>>[customerJson()]),
        );
      await pumpCommerceScreen(
        tester,
        const CustomersScreen(),
        harness: harness,
      );

      expect(find.text('Nusrat Jahan'), findsOneWidget);
      expect(find.textContaining('01712****78'), findsWidgets);
      expect(find.textContaining('01712345678'), findsNothing);
    });

    testWidgets('a customer with no history is not shown as 0% delivered', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/customers/crm',
          page(<Map<String, dynamic>>[
            customerJson(
              orders: 1,
              delivered: 0,
              returned: 0,
              successBasisPoints: null,
              repeat: false,
            ),
          ]),
        );
      await pumpCommerceScreen(
        tester,
        const CustomersScreen(),
        harness: harness,
      );

      // 0% would read as a judgement about the customer that the data does not
      // support (master spec section 130).
      expect(find.text('No history yet'), findsOneWidget);
      expect(find.textContaining('0.0%'), findsNothing);
    });

    testWidgets('a repeat buyer is marked', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/customers/crm',
          page(<Map<String, dynamic>>[customerJson()]),
        );
      await pumpCommerceScreen(
        tester,
        const CustomersScreen(),
        harness: harness,
      );

      expect(find.text('Repeat'), findsOneWidget);
      expect(find.text('66.7% delivered'), findsOneWidget);
    });
  });

  group('Orders', () {
    testWidgets('a compact card keeps courier and risk separate', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/orders',
          page(<Map<String, dynamic>>[orderJson()]),
        );
      await pumpCommerceScreen(tester, const OrdersScreen(), harness: harness);

      expect(find.text('CP-20260910-0042'), findsOneWidget);
      expect(find.text('৳1,250'), findsWidgets);
      // COD, courier and risk as separate facts; the two not yet computed say
      // so plainly. Profit lives on the order detail, not the feed card.
      expect(find.text('Not booked'), findsOneWidget);
      expect(find.text('Not checked'), findsOneWidget);
      expect(find.text('Pending'), findsNothing);
      expectNoOverflow(tester);
    });

    testWidgets('a delivered parcel does not imply its money arrived', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/orders',
          page(<Map<String, dynamic>>[
            orderJson(status: 'COMPLETED', fulfillment: 'DELIVERED'),
          ]),
        );
      await pumpCommerceScreen(tester, const OrdersScreen(), harness: harness);

      // Master spec section 1.4: courier state and money state are separate,
      // and merging them is how unpaid money gets hidden. The card states the
      // courier's "Delivered" and claims nothing about the money.
      expect(
        find.descendant(
          of: find.byType(SellerOrderCard),
          matching: find.text('Delivered'),
        ),
        findsOneWidget,
      );
      expect(find.textContaining('Paid'), findsNothing);
    });

    testWidgets('queued work is visible and offers a sync', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/orders',
          page(<Map<String, dynamic>>[orderJson()]),
        );
      await pumpCommerceScreen(tester, const OrdersScreen(), harness: harness);

      await harness.db.enqueueMutation(
        OutboxEntriesCompanion.insert(
          id: 'm1',
          entityType: 'ORDER',
          entityId: 'o9',
          operation: MutationOperation.create,
          payload: '{}',
          clientTimestamp: DateTime.now().toUtc(),
          availableAt: DateTime.now().toUtc(),
          createdAt: DateTime.now().toUtc(),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      expect(find.text('1 change waiting'), findsOneWidget);
      expect(find.text('Sync now'), findsOneWidget);
    });

    testWidgets('an offline list says so rather than showing nothing', (
      tester,
    ) async {
      final harness = CommerceHarness()..offline = true;
      await pumpCommerceScreen(tester, const OrdersScreen(), harness: harness);

      expect(find.text('No internet connection'), findsOneWidget);
      expect(find.text('No orders yet'), findsNothing);
    });

    testWidgets('the empty state points at both ways to add an order', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/orders', page(<Map<String, dynamic>>[]));
      await pumpCommerceScreen(tester, const OrdersScreen(), harness: harness);

      expect(find.text('No orders yet'), findsOneWidget);
      expect(find.textContaining('paste a message'), findsOneWidget);
    });

    testWidgets('the last card scrolls clear of the floating add buttons', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/orders',
          page(<Map<String, dynamic>>[
            for (var i = 1; i <= 8; i++)
              orderJson(id: 'o$i', number: 'CP-20260910-000$i'),
          ]),
        )
        ..adapter.onJson('GET', '/orders/o8', orderJson(id: 'o8'));
      await pumpCommerceScreen(tester, const OrdersScreen(), harness: harness);

      // Scrolled as far as the feed goes.
      await tester.drag(find.byType(ListView).first, const Offset(0, -6000));
      await settle(tester);

      final controls = find.byType(FloatingActionButton);
      expect(controls, findsNWidgets(2));
      final controlsTop = math.min(
        tester.getRect(controls.at(0)).top,
        tester.getRect(controls.at(1)).top,
      );
      final lastCard = find.byType(SellerOrderCard).last;
      expect(tester.getRect(lastCard).bottom, lessThanOrEqualTo(controlsTop));

      // Its "•••" is the button that answers, not the paste or add button.
      await tester.tap(
        find.descendant(of: lastCard, matching: find.text('•••')),
      );
      await settle(tester);
      expect(find.byType(OrderDetailScreen), findsOneWidget);
    });
  });

  group('Order detail', () {
    testWidgets('shows items, the money breakdown and the three states', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/orders/o1', orderJson())
        ..adapter.onJson('GET', '/orders', page(<Map<String, dynamic>>[]));
      await pumpCommerceScreen(
        tester,
        const OrderDetailScreen(orderId: 'o1'),
        harness: harness,
      );

      expect(find.text('Cotton Abaya'), findsOneWidget);
      expect(find.text('COD to collect'), findsOneWidget);
      expect(find.text('Courier'), findsOneWidget);
      expect(find.text('Delivery risk'), findsOneWidget);
      expect(find.text('Profit'), findsOneWidget);
      expectNoOverflow(tester);
    });

    testWidgets('offers only the transitions the server allows', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/orders/o1', orderJson(status: 'CONFIRMED'))
        ..adapter.onJson('GET', '/orders', page(<Map<String, dynamic>>[]));
      await pumpCommerceScreen(
        tester,
        const OrderDetailScreen(orderId: 'o1'),
        harness: harness,
      );

      await scrollTo(tester, find.text('Mark packed'));
      expect(find.text('Mark packed'), findsOneWidget);
      expect(find.text('Cancel order'), findsOneWidget);
      // CONFIRMED cannot jump straight to the courier.
      expect(find.text('Hand to courier'), findsNothing);
      await scrollTo(tester, find.text('None of these contacts a courier.'));
      expect(find.text('None of these contacts a courier.'), findsOneWidget);
    });

    testWidgets('a completed order offers no transitions', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/orders/o1', orderJson(status: 'COMPLETED'))
        ..adapter.onJson('GET', '/orders', page(<Map<String, dynamic>>[]));
      await pumpCommerceScreen(
        tester,
        const OrderDetailScreen(orderId: 'o1'),
        harness: harness,
      );

      expect(find.text('Move this order on'), findsNothing);
    });
  });

  group('Duplicate warning', () {
    testWidgets('warns without blocking, and lets the seller continue', (
      tester,
    ) async {
      bool? kept;
      await pumpCommerceScreen(
        tester,
        Builder(
          builder: (context) => TextButton(
            onPressed: () async {
              kept = await DuplicateWarningSheet.show(
                context,
                DuplicateCheckFixture.check,
              );
            },
            child: const Text('open'),
          ),
        ),
      );

      await tester.tap(find.text('open'));
      await settle(tester);

      expect(find.text('Possible repeat order'), findsOneWidget);
      expect(find.text('CP-20260910-0001'), findsOneWidget);
      expect(find.text('3 hours ago · CONFIRMED'), findsOneWidget);
      expect(find.text('Same number'), findsOneWidget);

      // Both doors are open: master spec section 9 makes this advisory.
      expect(find.text('Let me check'), findsOneWidget);
      await tester.tap(find.text('Keep this order'));
      await settle(tester);
      expect(kept, isTrue);
    });
  });

  group('Layout at 360dp', () {
    testWidgets('every commerce list fits the reference device', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/orders',
          page(<Map<String, dynamic>>[
            orderJson(),
            orderJson(id: 'o2', number: 'CP-20260910-0043', cod: 2845000),
          ]),
        );
      await pumpCommerceScreen(tester, const OrdersScreen(), harness: harness);
      expectNoOverflow(tester);
    });
  });
}

/// A duplicate check shaped like `/orders/check-duplicates`.
class DuplicateCheckFixture {
  const DuplicateCheckFixture._();

  static final DuplicateCheck check = DuplicateCheck.fromJson(
    const <String, dynamic>{
      'possible_duplicate': true,
      'message': 'A similar order was placed 3 hours ago.',
      'window_hours': 24,
      'candidates': <dynamic>[
        <String, dynamic>{
          'order_id': 'o1',
          'order_number': 'CP-20260910-0001',
          'status': 'CONFIRMED',
          'cod_amount_paisa': 125000,
          'hours_ago': 3.0,
          'reasons': <String>['SAME_PHONE', 'IDENTICAL_AMOUNT'],
          'matching_item_names': <String>['Cotton Abaya'],
          'is_strong': true,
        },
      ],
    },
  );
}
