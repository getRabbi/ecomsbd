import 'dart:convert';

import 'package:ecomsbd/data/local/tables.dart';
import 'package:ecomsbd/features/imports/imports_screen.dart';
import 'package:ecomsbd/features/orders/order_compose_screen.dart';
import 'package:ecomsbd/features/products/products_screen.dart';
import 'package:ecomsbd/features/products/stock_adjustment_sheet.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'commerce_harness.dart';

/// End-to-end seller flows, against a fake server.
///
/// These cover the moments where getting it wrong costs money: confirming a
/// parsed order, saving one with no connection, adjusting stock, and committing
/// an import.
void main() {
  group('Paste an order', () {
    testWidgets('the parsed fields come back for confirmation, not saved', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('POST', '/orders/parse', <String, dynamic>{
          'customer_name': 'Nusrat Jahan',
          'phones': <String>['01712345678'],
          'selected_phone': '01712345678',
          'address': 'House 4, Road 2, Dhanmondi',
          'items': <dynamic>[
            <String, dynamic>{
              'name': 'Cotton Abaya',
              'quantity': 1,
              'size': 'XL',
            },
          ],
          'cod_amount_paisa': 125000,
          'confidence': <String, dynamic>{
            'name': 1.0,
            'phone': 1.0,
            'address': 0.8,
            'amount': 1.0,
          },
          'warnings': <String>[],
          'needs_phone_selection': false,
          'is_low_confidence': false,
          'source_text': 'Nusrat Jahan 01712345678 …',
        });

      await pumpCommerceScreen(
        tester,
        const OrderComposeScreen(startWithPaste: true),
        harness: harness,
      );

      await tester.enterText(
        find.byType(TextFormField).first,
        'Nusrat Jahan 01712345678 House 4, Road 2, Dhanmondi 1 Cotton Abaya XL COD 1250',
      );
      await tester.tap(find.text('Read the message'));
      await settle(tester);

      // The review form, pre-filled — and nothing created.
      // `findsWidgets`: a filled TextField renders its value in both an
      // EditableText and the Text beneath it.
      expect(find.text('Check before saving'), findsOneWidget);
      expect(find.text('Nusrat Jahan'), findsWidgets);
      expect(find.text('01712345678'), findsWidgets);
      expect(find.text('Cotton Abaya XL'), findsWidgets);
      expect(harness.adapter.to('POST', '/orders'), isEmpty);
      expectNoOverflow(tester);
    });

    testWidgets('two numbers are put to the seller, never picked', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('POST', '/orders/parse', <String, dynamic>{
          'phones': <String>['01712345678', '01898765432'],
          'selected_phone': null,
          'items': <dynamic>[],
          'confidence': <String, dynamic>{'phone': 1.0},
          'warnings': <String>['Two numbers found in the message'],
          'needs_phone_selection': true,
          'is_low_confidence': false,
          'source_text': 'raw',
        });

      await pumpCommerceScreen(
        tester,
        const OrderComposeScreen(startWithPaste: true),
        harness: harness,
      );

      await tester.enterText(find.byType(TextFormField).first, 'raw');
      await tester.tap(find.text('Read the message'));
      await settle(tester);

      // Picking the wrong number sends the parcel to the wrong person, so the
      // parser refuses to choose (master spec section 8).
      expect(find.text('Which number is the customer?'), findsOneWidget);
      expect(find.text('01712345678'), findsOneWidget);
      expect(find.text('01898765432'), findsOneWidget);
      expect(find.text('Two numbers found in the message'), findsOneWidget);
    });

    testWidgets('a field the parser doubted is marked for checking', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('POST', '/orders/parse', <String, dynamic>{
          'customer_name': 'Nusrat',
          'phones': <String>['01712345678'],
          'selected_phone': '01712345678',
          'address': 'Dhanmondi',
          'items': <dynamic>[],
          'cod_amount_paisa': null,
          // The address scored low and no amount was found at all.
          'confidence': <String, dynamic>{
            'name': 1.0,
            'phone': 1.0,
            'address': 0.25,
            'amount': 0.0,
          },
          'warnings': <String>['No COD amount found'],
          'needs_phone_selection': false,
          'is_low_confidence': false,
          'source_text': 'raw',
        });

      await pumpCommerceScreen(
        tester,
        const OrderComposeScreen(startWithPaste: true),
        harness: harness,
      );

      await tester.enterText(find.byType(TextFormField).first, 'raw');
      await tester.tap(find.text('Read the message'));
      await settle(tester);

      expect(find.text('Check this'), findsNWidgets(2));
      expect(find.text('No COD amount found'), findsOneWidget);
    });
  });

  group('Save an order', () {
    testWidgets('a duplicate warning does not block the save', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson('POST', '/orders/check-duplicates', <String, dynamic>{
          'possible_duplicate': true,
          'message': 'A similar order was placed 2 hours ago.',
          'window_hours': 24,
          'candidates': <dynamic>[
            <String, dynamic>{
              'order_id': 'o1',
              'order_number': 'CP-20260910-0001',
              'status': 'CONFIRMED',
              'cod_amount_paisa': 125000,
              'hours_ago': 2.0,
              'reasons': <String>['SAME_PHONE', 'IDENTICAL_AMOUNT'],
              'matching_item_names': <String>['Cotton Abaya'],
              'is_strong': true,
            },
          ],
        })
        ..adapter.onJson('POST', '/orders', <String, dynamic>{
          'order': orderJson(),
          'duplicate_check': null,
        });

      await pumpCommerceScreen(
        tester,
        const OrderComposeScreen(),
        harness: harness,
      );

      await _fillOrderForm(tester);
      await tapAfterScroll(tester, find.text('Save order'));

      expect(find.text('Possible repeat order'), findsOneWidget);
      // Nothing has been created while the seller decides.
      expect(harness.adapter.to('POST', '/orders'), isEmpty);

      await tester.tap(find.text('Keep this order'));
      await settle(tester);

      expect(harness.adapter.to('POST', '/orders'), hasLength(1));
    });

    testWidgets('offline, the order is queued with the id it will keep', (
      tester,
    ) async {
      final harness = CommerceHarness()..offline = true;
      await pumpCommerceScreen(
        tester,
        const OrderComposeScreen(),
        harness: harness,
      );

      await _fillOrderForm(tester);
      await tapAfterScroll(tester, find.text('Save order'));

      final queued = await harness.db.dueOutboxEntries();
      expect(queued, hasLength(1));
      expect(queued.single.entityType, 'ORDER');
      final payload = jsonDecode(queued.single.payload) as Map<String, dynamic>;
      // The id the server will deduplicate a replay on.
      expect(payload['client_id'], queued.single.entityId);

      final mirrored = await harness.db.localOrders(tenantId: testTenantId);
      expect(mirrored.single.syncState, LocalSyncState.localOnly);
      expect(mirrored.single.orderNumber, 'PENDING');
    });
  });

  group('Adjust stock', () {
    testWidgets('sends a signed movement, never a new total', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/products',
          page(<Map<String, dynamic>>[productJson(stock: 12)]),
        )
        ..adapter.onJson('GET', '/products/p1', productJson(stock: 9))
        ..adapter
            .onJson('POST', '/products/p1/stock-adjustments', <String, dynamic>{
              'id': 'm1',
              'product_id': 'p1',
              'quantity_delta': -3,
              'balance_after': 9,
              'reason': 'DAMAGED_WRITE_OFF',
              'source': 'SELLER',
              'note': null,
              'order_id': null,
              'consignment_id': null,
              'occurred_at': '2026-09-10T04:00:00Z',
              'created_at': '2026-09-10T04:00:00Z',
            });

      await pumpCommerceScreen(
        tester,
        const ProductsScreen(),
        harness: harness,
      );
      await tester.tap(find.text('Cotton Abaya'));
      await settle(tester);
      await tapAfterScroll(tester, find.text('Adjust'));

      expect(find.text('Adjust stock'), findsOneWidget);
      await tester.tap(find.text('Remove'));
      // Scoped to the sheet: the form behind the modal has fields of its own,
      // and `.first` would find one of those.
      await tester.enterText(
        find
            .descendant(
              of: find.byType(StockAdjustmentSheet),
              matching: find.byType(TextFormField),
            )
            .first,
        '3',
      );
      await tester.tap(find.text('Damaged'));
      await settle(tester);

      // The projection is shown before anything is sent.
      expect(find.text('9 in stock'), findsOneWidget);

      await tapAfterScroll(tester, find.text('Record movement'));

      final sent = harness.adapter
          .to('POST', '/products/p1/stock-adjustments')
          .single;
      expect(sent.jsonBody['quantity_delta'], -3);
      expect(sent.jsonBody['reason'], 'DAMAGED_WRITE_OFF');
      expect(sent.jsonBody['allow_negative'], isFalse);
      expect(sent.jsonBody.containsKey('stock_on_hand'), isFalse);
    });
  });

  group('Import a file', () {
    testWidgets('the dry run runs before anything is created', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson('POST', '/imports', _importJson())
        ..adapter.onJson(
          'POST',
          '/imports/imp1/dry-run',
          _importJson(
            status: 'VALIDATED',
            ready: 8,
            warning: 1,
            invalid: 1,
            canCommit: true,
          ),
        )
        ..adapter.on(
          'GET',
          '/imports/imp1/rows',
          (_) => const FakeReply(<dynamic>[
            <String, dynamic>{
              'row_number': 4,
              'status': 'INVALID',
              'raw': <String, dynamic>{'Phone': '0171'},
              'parsed': <String, dynamic>{},
              'errors': <dynamic>[
                <String, dynamic>{
                  'field': 'phone',
                  'message': "'0171' is not a valid Bangladeshi mobile number",
                },
              ],
              'warnings': <dynamic>[],
              'created_entity_id': null,
            },
          ]),
        )
        ..adapter.onJson('POST', '/imports/imp1/commit', <String, dynamic>{
          'import_batch': _importJson(
            status: 'COMMITTED',
            ready: 8,
            warning: 1,
            invalid: 1,
            created: 9,
          ),
          'created_count': 9,
          'skipped_count': 1,
        });

      await pumpCommerceScreen(
        tester,
        ImportsScreen(
          pickFile: () async => const PickedImportFile(
            name: 'products.csv',
            bytes: <int>[110, 97, 109, 101],
          ),
        ),
        harness: harness,
      );

      await tester.tap(find.text('Choose a file'));
      await settle(tester);

      // Step 2: the guessed mapping, correctable.
      expect(find.text('products.csv'), findsOneWidget);
      expect(find.text('Product name'), findsOneWidget);
      expect(harness.adapter.to('POST', '/imports/imp1/commit'), isEmpty);

      await tapAfterScroll(tester, find.text('Check every row'));

      // Step 3: the counts, and the bad row with the value the file held.
      expect(find.text('Row 4'), findsOneWidget);
      expect(
        find.textContaining('is not a valid Bangladeshi mobile number'),
        findsOneWidget,
      );
      expect(find.text('Cannot import'), findsWidgets);
      expect(harness.adapter.to('POST', '/imports/imp1/commit'), isEmpty);

      await tapAfterScroll(tester, find.textContaining('Create 9 record'));

      expect(find.text('9 records created'), findsOneWidget);
      expect(find.textContaining('1 row skipped'), findsOneWidget);
      expectNoOverflow(tester);
    });

    testWidgets('a file with nothing importable cannot be committed', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('POST', '/imports', _importJson())
        ..adapter.onJson(
          'POST',
          '/imports/imp1/dry-run',
          _importJson(status: 'VALIDATED', invalid: 10),
        )
        ..adapter.on(
          'GET',
          '/imports/imp1/rows',
          (_) => const FakeReply(<dynamic>[]),
        );

      await pumpCommerceScreen(
        tester,
        ImportsScreen(
          pickFile: () async =>
              const PickedImportFile(name: 'orders.csv', bytes: <int>[1]),
        ),
        harness: harness,
      );

      await tester.tap(find.text('Choose a file'));
      await settle(tester);
      await tapAfterScroll(tester, find.text('Check every row'));

      expect(find.text('Nothing to create'), findsOneWidget);
      final button = tester.widget<FilledButton>(
        find.ancestor(
          of: find.text('Nothing to create'),
          matching: find.byType(FilledButton),
        ),
      );
      expect(button.onPressed, isNull);
    });
  });
}

/// Fill the minimum an order needs: a phone, an item and a price.
///
/// Field order on the review step is phone, name, address, district, area,
/// then item name, quantity, unit price.
Future<void> _fillOrderForm(WidgetTester tester) async {
  await tester.enterText(find.byType(TextFormField).at(0), '01712345678');
  await tester.enterText(find.byType(TextFormField).at(5), 'Cotton Abaya');
  await tester.enterText(find.byType(TextFormField).at(7), '1250');
  await settle(tester, frames: 2);
}

/// Scroll a control into view, then tap it.
Future<void> tapAfterScroll(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await settle(tester, frames: 2);
  await tester.tap(finder);
  await settle(tester);
}

Map<String, dynamic> _importJson({
  String status = 'DETECTED',
  int ready = 0,
  int warning = 0,
  int duplicate = 0,
  int invalid = 0,
  int created = 0,
  bool canCommit = false,
}) => <String, dynamic>{
  'id': 'imp1',
  'template': 'PRODUCTS',
  'status': status,
  'original_filename': 'products.csv',
  'source_sha256': 'abc123',
  'detected_headers': <String>['Name', 'SKU', 'Cost', 'Price', 'Stock'],
  'column_mapping': <String, String>{
    'name': 'Name',
    'sku': 'SKU',
    'cost': 'Cost',
    'price': 'Price',
    'stock': 'Stock',
  },
  'row_count': 10,
  'ready_count': ready,
  'warning_count': warning,
  'duplicate_count': duplicate,
  'invalid_count': invalid,
  'created_count': created,
  'can_commit': canCommit || ready + warning > 0,
  'dry_run_at': null,
  'committed_at': null,
  'failure_reason': null,
  'created_at': '2026-09-10T04:00:00Z',
};
