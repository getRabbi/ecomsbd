import 'dart:convert';

import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/core/api/api_error.dart';
import 'package:ecomsbd/data/commerce/customers_repository.dart';
import 'package:ecomsbd/data/commerce/orders_repository.dart';
import 'package:ecomsbd/data/commerce/products_repository.dart';
import 'package:ecomsbd/data/commerce/repository_support.dart';
import 'package:ecomsbd/data/local/database.dart';
import 'package:ecomsbd/data/local/tables.dart';
import 'package:ecomsbd/data/sync/outbox.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_api.dart';

/// The commerce repositories.
///
/// Two properties are load-bearing and are tested directly here:
///
///  * a production repository never invents data. When the server cannot be
///    reached it serves the local mirror **labelled as cached**; when there is
///    no mirror it fails rather than showing an empty list, and it never
///    reaches for a demo fixture;
///  * an offline write is queued with a stable id, so a retry over a bad
///    connection cannot turn one sale into two orders.
void main() {
  late EcomsbdDatabase db;
  late ApiClient api;
  late FakeApiAdapter adapter;
  late OutboxWriter outbox;

  setUp(() {
    db = EcomsbdDatabase.memory();
    final fake = buildFakeApi();
    api = fake.client;
    adapter = fake.adapter;
    outbox = OutboxWriter(db: db);
  });

  tearDown(() => db.close());

  ProductsRepository products() => ProductsRepository(
    api: api,
    db: db,
    outbox: outbox,
    tenantId: testTenantId,
  );

  CustomersRepository customers() => CustomersRepository(
    api: api,
    db: db,
    outbox: outbox,
    tenantId: testTenantId,
  );

  OrdersRepository orders() => OrdersRepository(
    api: api,
    db: db,
    outbox: outbox,
    tenantId: testTenantId,
  );

  // --- products -------------------------------------------------------------

  group('products', () {
    test('a page is fetched, parsed and mirrored', () async {
      adapter.onJson('GET', '/products', <String, dynamic>{
        'items': <dynamic>[_productJson(id: 'p1', name: 'Cotton Abaya')],
        'next_cursor': 'cursor-2',
        'has_more': true,
      });

      final page = await products().list();

      expect(page.origin, DataOrigin.network);
      expect(page.value.items.single.name, 'Cotton Abaya');
      expect(page.value.hasMore, isTrue);
      expect(page.value.nextCursor, 'cursor-2');

      final mirrored = await db.localProducts(tenantId: testTenantId);
      expect(mirrored.single.name, 'Cotton Abaya');
      expect(mirrored.single.syncState, LocalSyncState.synced);
    });

    test('offline reads fall back to the mirror and say so', () async {
      adapter.onJson('GET', '/products', <String, dynamic>{
        'items': <dynamic>[_productJson(id: 'p1', name: 'Cotton Abaya')],
      });
      await products().list();

      adapter.offline = true;
      final page = await products().list();

      expect(page.origin, DataOrigin.cache);
      expect(page.isStale, isTrue);
      expect(page.error?.isOffline, isTrue);
      expect(page.value.items.single.name, 'Cotton Abaya');
    });

    test(
      'offline with nothing cached is not reported as an empty shop',
      () async {
        // The important half of the previous test. An empty list here would read
        // as "you have no products", which is a different statement from "we
        // cannot reach the server" — and the one that makes a seller re-enter a
        // catalogue they already have.
        adapter.offline = true;
        await expectLater(
          products().list(),
          throwsA(
            isA<ApiError>().having((e) => e.isOffline, 'isOffline', true),
          ),
        );
      },
    );

    test('a server error is not answered from the cache', () async {
      adapter.onJson('GET', '/products', <String, dynamic>{
        'items': <dynamic>[_productJson(id: 'p1', name: 'Cotton Abaya')],
      });
      await products().list();

      adapter.on(
        'GET',
        '/products',
        (_) => const FakeReply(<String, dynamic>{
          'code': 'INTERNAL_ERROR',
          'message_bn': '',
          'message_en': 'boom',
          'retryable': true,
        }, statusCode: 500),
      );

      // Serving yesterday's list in response to a bug would hide the bug and
      // mislead the seller; only "no connection" earns the cache.
      await expectLater(products().list(), throwsA(isA<ApiError>()));
    });

    test('creating offline queues the mutation and shows the row', () async {
      adapter.offline = true;

      final product = await products().create(
        name: 'Silk Hijab',
        costPaisa: 30000,
        sellingPricePaisa: 55000,
        openingStock: 12,
      );

      expect(product.name, 'Silk Hijab');
      expect(product.stockOnHand, 12);

      final queued = await db.dueOutboxEntries();
      expect(queued.single.entityType, SyncEntityType.product);
      expect(queued.single.operation, MutationOperation.create);
      expect(queued.single.entityId, product.id);

      final mirrored = await db.localProducts(tenantId: testTenantId);
      expect(mirrored.single.syncState, LocalSyncState.localOnly);
    });

    test('a stock adjustment appends a movement, never a total', () async {
      adapter
          .onJson('POST', '/products/p1/stock-adjustments', <String, dynamic>{
            'id': 'm1',
            'product_id': 'p1',
            'quantity_delta': -3,
            'balance_after': 9,
            'reason': 'MANUAL_ADJUSTMENT',
            'source': 'SELLER',
            'note': 'damaged',
            'order_id': null,
            'consignment_id': null,
            'occurred_at': '2026-09-10T04:00:00Z',
            'created_at': '2026-09-10T04:00:00Z',
          });
      adapter.onJson(
        'GET',
        '/products/p1',
        _productJson(id: 'p1', name: 'Cotton Abaya', stock: 9),
      );

      final movement = await products().adjustStock(
        'p1',
        quantityDelta: -3,
        note: 'damaged',
      );

      expect(movement!.quantityDelta, -3);
      expect(movement.balanceAfter, 9);
      final body = adapter.to('POST', '/products/p1/stock-adjustments').single;
      expect(body.jsonBody['quantity_delta'], -3);
      expect(body.jsonBody.containsKey('stock_on_hand'), isFalse);
      // Overselling has to be asked for.
      expect(body.jsonBody['allow_negative'], isFalse);
    });

    test(
      'an offline stock adjustment queues and returns no movement',
      () async {
        adapter.onJson('GET', '/products', <String, dynamic>{
          'items': <dynamic>[_productJson(id: 'p1', name: 'Abaya', stock: 10)],
        });
        await products().list();

        adapter.offline = true;
        final movement = await products().adjustStock('p1', quantityDelta: -2);

        // No ledger entry exists yet: writing one is the server's job.
        expect(movement, isNull);
        final queued = await db.dueOutboxEntries();
        expect(queued.single.entityType, SyncEntityType.stockAdjustment);

        final mirrored = await db.localProduct(testTenantId, 'p1');
        expect(mirrored!.stockOnHand, 8);
        expect(mirrored.syncState, LocalSyncState.localOnly);
      },
    );
  });

  // --- customers ------------------------------------------------------------

  group('customers', () {
    test('the mirror never stores a plaintext phone', () async {
      adapter.onJson('GET', '/customers', <String, dynamic>{
        'items': <dynamic>[
          <String, dynamic>{
            ..._customerJson(id: 'c1'),
            // Even if a future server change leaked one, it stops at the cache.
            'phone': '+8801712345678',
          },
        ],
      });

      await customers().list();

      final row = await db.localCustomer(testTenantId, 'c1');
      final payload = jsonDecode(row!.payload) as Map<String, dynamic>;
      expect(payload.containsKey('phone'), isFalse);
      expect(row.phoneMasked, '01712****78');
    });

    test('revealing a phone states a reason and caches nothing', () async {
      adapter.onJson('POST', '/customers/c1/reveal-phone', <String, dynamic>{
        'phone': '+8801712345678',
      });

      final phone = await customers().revealPhone(
        'c1',
        reason: 'Calling about a failed delivery',
      );

      expect(phone, '+8801712345678');
      final request = adapter.to('POST', '/customers/c1/reveal-phone').single;
      expect(request.jsonBody['reason'], 'Calling about a failed delivery');
      expect(await db.localCustomer(testTenantId, 'c1'), isNull);
    });

    test('an unknown number is not an error', () async {
      adapter.onJson('GET', '/customers/lookup', <String, dynamic>{});
      expect(await customers().lookupByPhone('01712345678'), isNull);
    });

    test('a customer created offline is masked the same way', () async {
      adapter.offline = true;

      final customer = await customers().create(
        phone: '01712345678',
        name: 'Nusrat',
      );

      expect(customer.phoneMasked, '01712****78');
      expect(customer.phoneLast4, '5678');
      // No history yet, so no success rate — not 0%.
      expect(customer.successRateBasisPoints, isNull);
      expect(customer.successRateLabel, 'No history yet');
    });
  });

  // --- orders ---------------------------------------------------------------

  group('orders', () {
    test('creating an order sends one client id and books nothing', () async {
      adapter.onJson('POST', '/orders', <String, dynamic>{
        'order': _orderJson(id: 'o1'),
        'duplicate_check': null,
      });

      final saved = await orders().create(
        phone: '01712345678',
        items: <Map<String, dynamic>>[
          <String, dynamic>{
            'name': 'Abaya',
            'quantity': 1,
            'unit_price_paisa': 125000,
          },
        ],
        codAmountPaisa: 125000,
      );

      expect(saved.isQueued, isFalse);
      expect(saved.order.orderNumber, 'CP-20260910-0001');
      // Placeholders, not invented courier or profit values.
      expect(saved.order.fulfillmentState, 'NOT_BOOKED');
      expect(saved.order.riskState, 'NOT_CHECKED');
      expect(saved.order.profitState, 'PENDING_CALCULATION');

      final request = adapter.to('POST', '/orders').single;
      expect(request.jsonBody['client_id'], isNotNull);
      // Nothing in the request asks a courier for anything.
      expect(request.jsonBody.keys.any((k) => k.contains('courier')), isFalse);
    });

    test(
      'a duplicate warning is surfaced, and the order is still created',
      () async {
        adapter.onJson('POST', '/orders', <String, dynamic>{
          'order': _orderJson(id: 'o2'),
          'duplicate_check': <String, dynamic>{
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
                'matching_item_names': <String>['Abaya'],
                'is_strong': true,
              },
            ],
          },
        });

        final saved = await orders().create(
          phone: '01712345678',
          items: <Map<String, dynamic>>[
            <String, dynamic>{'name': 'Abaya', 'quantity': 1},
          ],
        );

        // Section 9: a warning, never a block.
        expect(saved.order.id, 'o2');
        expect(saved.duplicateCheck!.possibleDuplicate, isTrue);
        expect(
          saved.duplicateCheck!.candidates.single.whenLabel,
          '3 hours ago',
        );
      },
    );

    test('an order created offline keeps its id when it is retried', () async {
      adapter.offline = true;

      final first = await orders().create(
        phone: '01712345678',
        items: <Map<String, dynamic>>[
          <String, dynamic>{
            'name': 'Abaya',
            'quantity': 2,
            'unit_price_paisa': 60000,
          },
        ],
      );

      expect(first.isQueued, isTrue);
      // No number has been allocated; the card must not imply one has.
      expect(first.order.orderNumber, 'PENDING');
      expect(first.order.subtotal.paisa, 120000);
      expect(first.order.customerPhoneMasked, '01712****78');

      final queued = await db.dueOutboxEntries();
      expect(queued.single.entityId, first.order.id);

      // Retrying the same intent reuses the id, so the server deduplicates.
      final retry = await orders().create(
        phone: '01712345678',
        items: <Map<String, dynamic>>[
          <String, dynamic>{
            'name': 'Abaya',
            'quantity': 2,
            'unit_price_paisa': 60000,
          },
        ],
        clientId: first.order.id,
      );
      expect(retry.order.id, first.order.id);
      expect(await db.dueOutboxEntries(), hasLength(1));
    });

    test('an offline edit carries the version the seller saw', () async {
      adapter.onJson('GET', '/orders', <String, dynamic>{
        'items': <dynamic>[_orderJson(id: 'o1', version: 4)],
      });
      await orders().list();

      adapter.offline = true;
      await orders().update(
        'o1',
        note: 'Call before delivery',
        expectedVersion: 4,
      );

      final queued = await db.dueOutboxEntries();
      final decoded = decodeOutboxPayload(queued.single.payload);
      expect(decoded.baseVersion, 4);
      expect(decoded.body['note'], 'Call before delivery');
      expect(decoded.body.containsKey('__base_version'), isFalse);
    });

    test('parsed text is returned for confirmation, not saved', () async {
      adapter.onJson('POST', '/orders/parse', <String, dynamic>{
        'customer_name': 'Nusrat',
        'phones': <String>['01712345678', '01898765432'],
        'selected_phone': null,
        'address': 'House 4, Road 2, Dhanmondi',
        'items': <dynamic>[
          <String, dynamic>{'name': 'Abaya', 'quantity': 1, 'size': 'XL'},
        ],
        'cod_amount_paisa': 125000,
        'confidence': <String, dynamic>{'phone': 1.0, 'address': 0.5},
        'warnings': <String>['Two numbers found'],
        'needs_phone_selection': true,
        'is_low_confidence': false,
        'source_text': 'raw pasted text',
      });

      final parsed = await orders().parse('raw pasted text');

      expect(parsed.needsPhoneSelection, isTrue);
      expect(parsed.selectedPhone, isNull);
      expect(parsed.phones, hasLength(2));
      // The address scored low, so the form marks it for checking.
      expect(parsed.isUncertain('address'), isTrue);
      expect(parsed.isUncertain('phone'), isFalse);
      // The original text survives a bad parse.
      expect(parsed.sourceText, 'raw pasted text');
      // Parsing creates nothing.
      expect(adapter.to('POST', '/orders'), isEmpty);
    });
  });
}

Map<String, dynamic> _productJson({
  required String id,
  required String name,
  int stock = 10,
}) => <String, dynamic>{
  'id': id,
  'name': name,
  'sku': 'SKU-$id',
  'description': null,
  'cost_paisa': 40000,
  'default_selling_price_paisa': 125000,
  'stock_tracking_enabled': true,
  'stock_on_hand': stock,
  'low_stock_threshold': 3,
  'is_low_stock': stock <= 3,
  'is_active': true,
  'is_archived': false,
  'created_at': '2026-09-01T10:00:00Z',
  'updated_at': '2026-09-09T10:00:00Z',
};

Map<String, dynamic> _customerJson({required String id}) => <String, dynamic>{
  'id': id,
  'name': 'Nusrat',
  'phone_masked': '01712****78',
  'phone_last4': '5678',
  'flag': 'NONE',
  'is_repeat_buyer': false,
  'order_count': 1,
  'delivered_count': 0,
  'returned_count': 0,
  'cancelled_count': 0,
  'success_rate_basis_points': null,
  'realized_revenue_paisa': 0,
  'first_order_at': null,
  'last_order_at': null,
  'created_at': '2026-09-01T10:00:00Z',
  'notes': null,
  'flag_reason': null,
  'addresses': <dynamic>[],
};

Map<String, dynamic> _orderJson({required String id, int version = 1}) =>
    <String, dynamic>{
      'id': id,
      'order_number': 'CP-20260910-0001',
      'client_id': 'client-$id',
      'customer_id': 'c1',
      'customer_name': 'Nusrat',
      'customer_phone_masked': '01712****78',
      'delivery_address_raw': 'House 4, Road 2, Dhanmondi',
      'delivery_district': 'Dhaka',
      'delivery_area': 'Dhanmondi',
      'status': 'DRAFT',
      'channel': 'MANUAL',
      'business_date': '2026-09-10',
      'subtotal_paisa': 125000,
      'discount_paisa': 0,
      'delivery_fee_paisa': 0,
      'cod_amount_paisa': 125000,
      'note': null,
      'version': version,
      'created_at': '2026-09-10T04:00:00Z',
      'updated_at': '2026-09-10T04:00:00Z',
      'fulfillment_state': 'NOT_BOOKED',
      'risk_state': 'NOT_CHECKED',
      'profit_state': 'PENDING_CALCULATION',
      'items': <dynamic>[
        <String, dynamic>{
          'id': 'i1',
          'product_id': 'p1',
          'product_name': 'Abaya',
          'sku': 'SKU-p1',
          'variant_label': null,
          'quantity': 1,
          'unit_price_paisa': 125000,
          'unit_cost_snapshot_paisa': 40000,
          'discount_paisa': 0,
          'line_total_paisa': 125000,
          'note': null,
        },
      ],
      'source_text': null,
      'estimated_item_cost_paisa': 40000,
    };
