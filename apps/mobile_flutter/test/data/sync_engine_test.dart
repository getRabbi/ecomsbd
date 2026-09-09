import 'dart:convert';

import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/data/commerce/orders_repository.dart';
import 'package:ecomsbd/data/local/database.dart';
import 'package:ecomsbd/data/local/tables.dart';
import 'package:ecomsbd/data/sync/outbox.dart';
import 'package:ecomsbd/data/sync/sync_engine.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_api.dart';

/// The sync engine.
///
/// The outbox is the only place a seller's unsent work exists, so the tests
/// here are about not losing it and not duplicating it: a dead connection must
/// leave the queue intact, an accepted mutation must clear exactly once, and a
/// conflict must stop rather than overwrite.
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

  SyncEngine engine() => SyncEngine(api: api, db: db, tenantId: testTenantId);

  OrdersRepository orders() => OrdersRepository(
    api: api,
    db: db,
    outbox: outbox,
    tenantId: testTenantId,
  );

  Future<String> queueAnOfflineOrder() async {
    adapter.offline = true;
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
    adapter.offline = false;
    return saved.order.id;
  }

  group('push', () {
    test(
      'an accepted mutation clears the queue and marks the row synced',
      () async {
        final orderId = await queueAnOfflineOrder();

        adapter.on('POST', '/sync/mutations', (request) {
          final mutations = request.jsonBody['mutations'] as List<dynamic>;
          final mutation = mutations.single as Map<String, dynamic>;
          return FakeReply(<String, dynamic>{
            'results': <dynamic>[
              <String, dynamic>{
                'mutation_id': mutation['mutation_id'],
                'status': 'APPLIED',
                'entity_id': 'server-order-1',
                'server_version': 1,
              },
            ],
            'applied_count': 1,
          });
        });
        adapter.onJson('GET', '/sync/changes', <String, dynamic>{
          'changes': <dynamic>[],
          'has_more': false,
          'server_time': '2026-09-10T05:00:00Z',
        });

        final report = await engine().run();

        expect(report.applied, 1);
        expect(await db.dueOutboxEntries(), isEmpty);

        // Re-keyed to the server's id, so a later edit addresses the right row.
        expect(await db.localOrder(testTenantId, orderId), isNull);
        final promoted = await db.localOrder(testTenantId, 'server-order-1');
        expect(promoted!.syncState, LocalSyncState.synced);
      },
    );

    test('a mutation carries the id the device minted', () async {
      final orderId = await queueAnOfflineOrder();

      adapter.on('POST', '/sync/mutations', (request) {
        final mutation =
            (request.jsonBody['mutations'] as List<dynamic>).single
                as Map<String, dynamic>;
        // This is the whole idempotency contract: the server deduplicates on
        // it, which is what stops a retry becoming a second parcel.
        expect(mutation['mutation_id'], orderId);
        expect(mutation['entity_type'], 'ORDER');
        expect(mutation['operation'], 'CREATE');
        expect(
          (mutation['payload'] as Map<String, dynamic>)['client_id'],
          orderId,
        );
        return FakeReply(<String, dynamic>{
          'results': <dynamic>[
            <String, dynamic>{
              'mutation_id': orderId,
              'status': 'APPLIED',
              'entity_id': orderId,
            },
          ],
        });
      });
      adapter.onJson('GET', '/sync/changes', <String, dynamic>{
        'changes': <dynamic>[],
        'has_more': false,
        'server_time': '2026-09-10T05:00:00Z',
      });

      await engine().run();
    });

    test('DUPLICATE is treated as success, not as an error', () async {
      final orderId = await queueAnOfflineOrder();

      adapter.onJson('POST', '/sync/mutations', <String, dynamic>{
        'results': <dynamic>[
          <String, dynamic>{
            'mutation_id': orderId,
            // The server had it already — the device's retry landed twice.
            'status': 'DUPLICATE',
            'entity_id': 'server-order-1',
          },
        ],
      });
      adapter.onJson('GET', '/sync/changes', <String, dynamic>{
        'changes': <dynamic>[],
        'has_more': false,
        'server_time': '2026-09-10T05:00:00Z',
      });

      final report = await engine().run();

      expect(report.applied, 1);
      expect(report.rejected, 0);
      expect(await db.dueOutboxEntries(), isEmpty);
    });

    test(
      'losing the connection leaves the work queued and backs off',
      () async {
        await queueAnOfflineOrder();
        adapter.offline = true;

        final report = await engine().pushOutbox();

        expect(report.error!.isOffline, isTrue);
        expect(report.applied, 0);
        // Still queued — and not due immediately, so the app does not spin.
        expect(await db.dueOutboxEntries(), isEmpty);
        final all = await db.select(db.outboxEntries).get();
        expect(all.single.attempts, 1);
        expect(all.single.status, LocalSyncState.localOnly);
        expect(all.single.availableAt.isAfter(DateTime.now().toUtc()), isTrue);
      },
    );

    test(
      'a conflict is parked for the seller rather than overwritten',
      () async {
        final orderId = await queueAnOfflineOrder();

        adapter.onJson('POST', '/sync/mutations', <String, dynamic>{
          'results': <dynamic>[
            <String, dynamic>{
              'mutation_id': orderId,
              'status': 'CONFLICT',
              'entity_id': orderId,
              'server_version': 7,
              'server_state': <String, dynamic>{'cod_amount_paisa': 130000},
            },
          ],
        });
        adapter.onJson('GET', '/sync/changes', <String, dynamic>{
          'changes': <dynamic>[],
          'has_more': false,
          'server_time': '2026-09-10T05:00:00Z',
        });

        final report = await engine().run();

        expect(report.conflicts, 1);
        expect(report.needsAttention, isTrue);
        final entry = (await db.select(db.outboxEntries).get()).single;
        expect(entry.status, LocalSyncState.conflict);
        final row = await db.localOrder(testTenantId, orderId);
        expect(row!.syncState, LocalSyncState.conflict);
      },
    );

    test('a rejection is surfaced with its reason', () async {
      final orderId = await queueAnOfflineOrder();

      adapter.onJson('POST', '/sync/mutations', <String, dynamic>{
        'results': <dynamic>[
          <String, dynamic>{
            'mutation_id': orderId,
            'status': 'REJECTED',
            'error_code': 'VALIDATION_ERROR',
            'error_message': 'A phone number is required',
          },
        ],
      });
      adapter.onJson('GET', '/sync/changes', <String, dynamic>{
        'changes': <dynamic>[],
        'has_more': false,
        'server_time': '2026-09-10T05:00:00Z',
      });

      await engine().run();

      final entry = (await db.select(db.outboxEntries).get()).single;
      expect(entry.status, LocalSyncState.failedValidation);
      expect(entry.lastError, 'A phone number is required');
    });
  });

  group('pull', () {
    test('changes land in the mirror and the cursor advances', () async {
      adapter.on('GET', '/sync/changes', (request) {
        if (request.query['cursor'] == null) {
          return const FakeReply(<String, dynamic>{
            'changes': <dynamic>[
              <String, dynamic>{
                'entity_type': 'PRODUCT',
                'entity_id': 'p1',
                'deleted': false,
                'version': 1,
                'updated_at': '2026-09-10T05:00:00Z',
                'data': <String, dynamic>{
                  'id': 'p1',
                  'name': 'Cotton Abaya',
                  'stock_on_hand': 12,
                  'created_at': '2026-09-01T10:00:00Z',
                },
              },
            ],
            'next_cursor': 'cursor-2',
            'has_more': false,
            'server_time': '2026-09-10T05:00:00Z',
          });
        }
        return const FakeReply(<String, dynamic>{
          'changes': <dynamic>[],
          'has_more': false,
          'server_time': '2026-09-10T05:00:00Z',
        });
      });

      final report = await engine().pullChanges();

      expect(report.pulled, 1);
      final row = await db.localProduct(testTenantId, 'p1');
      expect(row!.name, 'Cotton Abaya');
      expect(row.stockOnHand, 12);

      final cursor = await db.select(db.syncCursors).getSingle();
      expect(cursor.cursor, 'cursor-2');
      expect(cursor.lastSyncedAt, isNotNull);
    });

    test('a tombstone removes the row instead of resurrecting it', () async {
      adapter.onJson('GET', '/orders', <String, dynamic>{
        'items': <dynamic>[
          <String, dynamic>{
            'id': 'o1',
            'order_number': 'CP-20260910-0001',
            'client_id': 'c1',
            'customer_name': 'Nusrat',
            'customer_phone_masked': '01712****78',
            'status': 'DRAFT',
            'channel': 'MANUAL',
            'business_date': '2026-09-10',
            'subtotal_paisa': 1,
            'discount_paisa': 0,
            'delivery_fee_paisa': 0,
            'cod_amount_paisa': 1,
            'version': 1,
            'created_at': '2026-09-10T04:00:00Z',
            'updated_at': '2026-09-10T04:00:00Z',
          },
        ],
      });
      await orders().list();
      expect(await db.localOrder(testTenantId, 'o1'), isNotNull);

      adapter.onJson('GET', '/sync/changes', <String, dynamic>{
        'changes': <dynamic>[
          <String, dynamic>{
            'entity_type': 'ORDER',
            'entity_id': 'o1',
            'deleted': true,
            'updated_at': '2026-09-10T06:00:00Z',
            'data': null,
          },
        ],
        'has_more': false,
        'server_time': '2026-09-10T06:00:00Z',
      });

      await engine().pullChanges();

      // Deleted elsewhere; a device that was offline drops it rather than
      // continuing to show a row the seller removed.
      expect(await db.localOrder(testTenantId, 'o1'), isNull);
    });

    test('a pulled customer never brings a plaintext phone with it', () async {
      adapter.onJson('GET', '/sync/changes', <String, dynamic>{
        'changes': <dynamic>[
          <String, dynamic>{
            'entity_type': 'CUSTOMER',
            'entity_id': 'c1',
            'deleted': false,
            'updated_at': '2026-09-10T05:00:00Z',
            'data': <String, dynamic>{
              'id': 'c1',
              'phone': '+8801712345678',
              'phone_masked': '01712****78',
              'phone_last4': '5678',
              'created_at': '2026-09-01T10:00:00Z',
            },
          },
        ],
        'has_more': false,
        'server_time': '2026-09-10T05:00:00Z',
      });

      await engine().pullChanges();

      final row = await db.localCustomer(testTenantId, 'c1');
      final payload = jsonDecode(row!.payload) as Map<String, dynamic>;
      expect(payload.containsKey('phone'), isFalse);
    });
  });

  group('ordering', () {
    test('queued work is pushed before changes are pulled', () async {
      await queueAnOfflineOrder();

      final calls = <String>[];
      adapter.on('POST', '/sync/mutations', (_) {
        calls.add('push');
        return const FakeReply(<String, dynamic>{'results': <dynamic>[]});
      });
      adapter.on('GET', '/sync/changes', (_) {
        calls.add('pull');
        return const FakeReply(<String, dynamic>{
          'changes': <dynamic>[],
          'has_more': false,
          'server_time': '2026-09-10T05:00:00Z',
        });
      });

      await engine().run();

      // Pulling first would overwrite the mirror with a server state that does
      // not contain the seller's queued order, and it would blink off screen.
      expect(calls, <String>['push', 'pull']);
    });
  });
}
