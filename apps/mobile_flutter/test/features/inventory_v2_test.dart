/// Inventory V2 on the phone: variants, restock, filtered stock history and
/// receiving a returned parcel.
///
/// The rules under test are the server's, seen from the app: stock changes are
/// movements (never a typed-in total), a variant product always names the
/// variant, and a courier's "returned" waits for the seller to say what
/// actually came back.
library;

import 'package:ecomsbd/data/commerce/models.dart';
import 'package:ecomsbd/features/orders/order_detail_screen.dart';
import 'package:ecomsbd/features/products/inventory_sheets.dart';
import 'package:ecomsbd/features/products/product_form_screen.dart';
import 'package:ecomsbd/features/products/stock_history_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'commerce_harness.dart';

Map<String, dynamic> _variantProduct() => <String, dynamic>{
  ...productJson(stock: 5),
  'has_variants': true,
  'is_low_stock': true,
  'variants': <dynamic>[
    <String, dynamic>{
      'id': 'v1',
      'product_id': 'p1',
      'name': 'Black / M',
      'sku': 'TS-BM',
      'stock_on_hand': 1,
      'low_stock_threshold': 2,
      'is_low_stock': true,
      'is_active': true,
    },
    <String, dynamic>{
      'id': 'v2',
      'product_id': 'p1',
      'name': 'Black / L',
      'sku': null,
      'stock_on_hand': 4,
      'low_stock_threshold': 2,
      'is_low_stock': false,
      'is_active': true,
    },
  ],
};

Map<String, dynamic> _movement() => <String, dynamic>{
  'id': 'm1',
  'product_id': 'p1',
  'variant_id': 'v1',
  'variant_name': 'Black / M',
  'quantity_delta': 6,
  'balance_after': 7,
  'reason': 'RESTOCK',
  'source': 'SELLER',
  'note': null,
  'reference': 'Invoice 42',
  'order_id': null,
  'order_number': null,
  'consignment_id': null,
  'actor_name': 'Rafi',
  'occurred_at': '2026-09-10T04:00:00Z',
  'created_at': '2026-09-10T04:00:00Z',
};

void main() {
  testWidgets('a variant product lists its variants and restocks one of them', (
    tester,
  ) async {
    final harness = CommerceHarness()
      ..adapter.onJson('GET', '/products/p1', _variantProduct())
      ..adapter.onJson('POST', '/products/p1/restocks', _movement());
    final product = Product.fromJson(_variantProduct());

    await pumpCommerceScreen(
      tester,
      ProductFormScreen(product: product),
      harness: harness,
    );
    expect(find.text('Black / M · TS-BM'), findsOneWidget);
    expect(find.text('Low stock'), findsOneWidget);

    await tapAfterScroll(tester, find.text('Restock').first);
    await settle(tester);
    expect(find.byType(RestockSheet), findsOneWidget);

    await tester.enterText(
      find
          .descendant(
            of: find.byType(RestockSheet),
            matching: find.byType(TextFormField),
          )
          .first,
      '6',
    );
    await settle(tester);
    // No variant chosen yet: nothing can be recorded against "the product".
    await tapAfterScroll(tester, find.text('Record restock'));
    expect(harness.adapter.to('POST', '/products/p1/restocks'), isEmpty);

    await tester.tap(find.text('Black / M (1)'));
    await settle(tester);
    await tapAfterScroll(tester, find.text('Record restock'));

    final sent = harness.adapter.to('POST', '/products/p1/restocks').single;
    expect(sent.jsonBody['variant_id'], 'v1');
    expect(sent.jsonBody['quantity'], 6);
    expect(sent.jsonBody.containsKey('stock_on_hand'), isFalse);
    expectNoOverflow(tester);
  });

  testWidgets('stock history filters by movement type on the server', (
    tester,
  ) async {
    final harness = CommerceHarness()
      ..adapter.onJson(
        'GET',
        '/products/p1/stock-movements',
        page(<Map<String, dynamic>>[_movement()]),
      );

    await pumpCommerceScreen(
      tester,
      StockHistoryScreen(product: Product.fromJson(_variantProduct())),
      harness: harness,
    );
    expect(find.text('Restock · Black / M'), findsOneWidget);
    expect(find.textContaining('Invoice 42'), findsOneWidget);
    expect(find.textContaining('Rafi'), findsOneWidget);

    await tester.tap(find.text('Returns'));
    await settle(tester);
    final filtered = harness.adapter
        .to('GET', '/products/p1/stock-movements')
        .last;
    expect(filtered.query['reason'], <String>[
      'RETURN_RESTORE',
      'PARTIAL_RETURN_RESTORE',
      'CANCEL_RESTORE',
    ]);
  });

  testWidgets('a returned parcel waits for the seller to receive it', (
    tester,
  ) async {
    final harness = CommerceHarness()
      ..adapter.onJson('GET', '/orders/o1', <String, dynamic>{
        ...orderJson(status: 'FULFILLMENT_STARTED'),
        'consignment_id': 'c1',
        'consignment_status': 'RETURNED',
        'return_pending_units': 1,
      })
      ..adapter.onJson('GET', '/orders', page(<Map<String, dynamic>>[]))
      ..adapter.onJson('GET', '/consignments/c1', <String, dynamic>{
        'id': 'c1',
        'items': <dynamic>[
          <String, dynamic>{
            'id': 'ci1',
            'order_item_id': 'i1',
            'qty_returned': 1,
            'qty_return_pending': 1,
          },
        ],
      })
      ..adapter.onJson(
        'POST',
        '/consignments/c1/return-receipt',
        <String, dynamic>{'id': 'c1', 'items': <dynamic>[]},
      );

    await pumpCommerceScreen(
      tester,
      const OrderDetailScreen(orderId: 'o1'),
      harness: harness,
    );
    expect(find.text('Returned parcel'), findsOneWidget);
    expect(
      find.text('1 returned item(s) not back in stock yet'),
      findsOneWidget,
    );

    await tapAfterScroll(tester, find.text('Receive return'));
    await settle(tester);
    expect(find.byType(ReceiveReturnSheet), findsOneWidget);

    await tester.tap(find.text('Damaged / do not restock'));
    await settle(tester);
    await tapAfterScroll(tester, find.text('Confirm'));

    final sent = harness.adapter
        .to('POST', '/consignments/c1/return-receipt')
        .single;
    expect(sent.jsonBody['decision'], 'RESTOCK_NONE');
    expectNoOverflow(tester);
  });
}

/// Scroll a control into view, then tap it (as in commerce_flows_test).
Future<void> tapAfterScroll(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await settle(tester, frames: 2);
  await tester.tap(finder);
  await settle(tester);
}
