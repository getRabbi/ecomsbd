// `show` is required: drift exports isNull/isNotNull as SQL helpers, which
// collide with the matchers of the same name.
import 'package:drift/drift.dart' show Value;
import 'package:ecomsbd/data/local/database.dart';
import 'package:ecomsbd/data/local/tables.dart';
import 'package:flutter_test/flutter_test.dart';

/// The offline outbox is the only place a seller's unsent work exists, so its
/// behaviour is tested directly rather than through a screen.
void main() {
  late EcomsbdDatabase db;

  setUp(() => db = EcomsbdDatabase.memory());
  tearDown(() => db.close());

  OutboxEntriesCompanion entry(
    String id, {
    DateTime? availableAt,
    LocalSyncState status = LocalSyncState.localOnly,
  }) {
    final now = DateTime.now().toUtc();
    return OutboxEntriesCompanion.insert(
      id: id,
      entityType: 'order',
      entityId: 'order-$id',
      operation: MutationOperation.create,
      payload: '{"cod_amount_paisa":125000}',
      clientTimestamp: now,
      availableAt: availableAt ?? now.subtract(const Duration(seconds: 1)),
      createdAt: now,
      status: Value(status),
    );
  }

  group('initialisation', () {
    test('creates its schema', () async {
      // Proves the generated Drift schema actually opens; a broken migration
      // would otherwise surface on a seller's device, not in CI.
      expect(await db.dueOutboxEntries(), isEmpty);
      expect(db.schemaVersion, 2);
    });
  });

  group('outbox', () {
    test('queued mutations become due', () async {
      await db.enqueueMutation(entry('a'));
      final due = await db.dueOutboxEntries();
      expect(due, hasLength(1));
      expect(due.single.status, LocalSyncState.localOnly);
      expect(due.single.attempts, 0);
    });

    test('an entry scheduled for later is not due yet', () async {
      await db.enqueueMutation(
        entry(
          'b',
          availableAt: DateTime.now().toUtc().add(const Duration(minutes: 5)),
        ),
      );
      expect(await db.dueOutboxEntries(), isEmpty);
    });

    test('a synced entry is no longer picked up', () async {
      await db.enqueueMutation(entry('c'));
      await db.markMutationStatus('c', LocalSyncState.synced);
      expect(await db.dueOutboxEntries(), isEmpty);
    });

    test('a failure records its code and backs off', () async {
      await db.enqueueMutation(entry('d'));
      final later = DateTime.now().toUtc().add(const Duration(minutes: 2));
      await db.markMutationStatus(
        'd',
        LocalSyncState.failedValidation,
        errorMessage: 'Address rejected',
        errorCode: 'ADDRESS_REJECTED',
        availableAt: later,
      );

      final stored = await (db.select(
        db.outboxEntries,
      )..where((t) => t.id.equals('d'))).getSingle();
      expect(stored.status, LocalSyncState.failedValidation);
      expect(stored.lastErrorCode, 'ADDRESS_REJECTED');
    });

    test('attempts increment', () async {
      await db.enqueueMutation(entry('e'));
      await db.incrementAttempts(
        'e',
        nextAttemptAt: DateTime.now().toUtc().add(const Duration(seconds: 30)),
      );
      final stored = await (db.select(
        db.outboxEntries,
      )..where((t) => t.id.equals('e'))).getSingle();
      expect(stored.attempts, 1);
    });

    test('pending count covers every unfinished state', () async {
      // The offline banner's number: anything not yet accepted by the server.
      await db.enqueueMutation(entry('f1'));
      await db.enqueueMutation(entry('f2', status: LocalSyncState.conflict));
      await db.enqueueMutation(entry('f3', status: LocalSyncState.synced));

      expect(await db.watchPendingCount().first, 2);
    });

    test('removing an entry drops it', () async {
      await db.enqueueMutation(entry('g'));
      expect(await db.removeMutation('g'), 1);
      expect(await db.dueOutboxEntries(), isEmpty);
    });
  });

  group('cache', () {
    test('stores and reads a document with its fetch time', () async {
      await db.writeCache('analytics.home', 'tenant-1', '{"cod":8745000}');
      final cached = await db.readCache('analytics.home', 'tenant-1');
      expect(cached, isNotNull);
      expect(cached!.payload, contains('8745000'));
      expect(cached.fetchedAt.isUtc, isTrue);
    });

    test('is scoped per tenant', () async {
      // Switching shops must never show the previous shop's figures.
      await db.writeCache('analytics.home', 'tenant-1', '{"a":1}');
      await db.writeCache('analytics.home', 'tenant-2', '{"a":2}');

      expect(
        (await db.readCache('analytics.home', 'tenant-1'))!.payload,
        '{"a":1}',
      );
      expect(
        (await db.readCache('analytics.home', 'tenant-2'))!.payload,
        '{"a":2}',
      );
    });

    test('clearing one tenant leaves the other intact', () async {
      await db.writeCache('k', 'tenant-1', 'a');
      await db.writeCache('k', 'tenant-2', 'b');
      await db.clearTenantCache('tenant-1');

      expect(await db.readCache('k', 'tenant-1'), isNull);
      expect(await db.readCache('k', 'tenant-2'), isNotNull);
    });
  });

  group('wipe', () {
    test('clears cached reads but keeps unsent seller work', () async {
      // Unsent orders exist nowhere else. Discarding them on sign-out would
      // destroy work the seller typed and never got to send.
      await db.writeCache('analytics.home', 'tenant-1', '{}');
      await db.enqueueMutation(entry('keep-me'));

      await db.wipe();

      expect(await db.readCache('analytics.home', 'tenant-1'), isNull);
      expect(await db.dueOutboxEntries(), hasLength(1));
    });
  });
}
