import 'dart:io';

import 'package:drift/drift.dart';
import 'package:drift/native.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:sqlite3_flutter_libs/sqlite3_flutter_libs.dart';

import 'tables.dart';

part 'database.g.dart';

/// The on-device database.
///
/// Holds the offline mutation outbox, cached read models and sync cursors.
/// It deliberately does **not** hold money totals as authoritative values: the
/// server owns the ledger, and a device that computed its own balance would
/// eventually disagree with it (master spec sections 37, 64).
@DriftDatabase(tables: <Type>[OutboxEntries, CachedDocuments, SyncCursors])
class EcomsbdDatabase extends _$EcomsbdDatabase {
  EcomsbdDatabase([QueryExecutor? executor])
    : super(executor ?? _openConnection());

  /// In-memory instance for tests.
  EcomsbdDatabase.memory() : super(NativeDatabase.memory());

  @override
  int get schemaVersion => 1;

  /// Store timestamps as ISO-8601 text rather than unix seconds.
  ///
  /// Drift's default integer storage returns a *local* `DateTime`, losing the
  /// UTC flag. Every timestamp in this system is UTC by contract (master spec
  /// section 69), and a value that silently comes back local is how an order
  /// created at 01:30 Dhaka ends up filed under the previous business day.
  @override
  DriftDatabaseOptions get options =>
      const DriftDatabaseOptions(storeDateTimeAsText: true);

  @override
  MigrationStrategy get migration => MigrationStrategy(
    onCreate: (m) async {
      await m.createAll();
    },
    beforeOpen: (details) async {
      // Foreign keys are off by default in SQLite.
      await customStatement('PRAGMA foreign_keys = ON');
    },
  );

  // --- outbox ---------------------------------------------------------------

  /// Entries that are due to be sent, oldest first.
  Future<List<OutboxEntry>> dueOutboxEntries({int limit = 20}) {
    final now = DateTime.now().toUtc();
    return (select(outboxEntries)
          ..where(
            (t) =>
                t.status.isIn(<String>[
                  LocalSyncState.localOnly.name,
                  LocalSyncState.syncing.name,
                ]) &
                t.availableAt.isSmallerOrEqualValue(now),
          )
          ..orderBy(<OrderClauseGenerator<$OutboxEntriesTable>>[
            (t) => OrderingTerm.asc(t.createdAt),
          ])
          ..limit(limit))
        .get();
  }

  /// Records the seller has created or edited but the server has not accepted.
  ///
  /// Drives the "N changes pending" count in the offline banner.
  Stream<int> watchPendingCount() {
    final count = outboxEntries.id.count();
    final query = selectOnly(outboxEntries)
      ..addColumns(<Expression<Object>>[count])
      ..where(
        outboxEntries.status.isIn(<String>[
          LocalSyncState.localOnly.name,
          LocalSyncState.syncing.name,
          LocalSyncState.conflict.name,
          LocalSyncState.failedValidation.name,
        ]),
      );
    return query.map((row) => row.read(count) ?? 0).watchSingle();
  }

  Future<void> enqueueMutation(OutboxEntriesCompanion entry) =>
      into(outboxEntries).insert(entry, mode: InsertMode.insertOrReplace);

  Future<void> markMutationStatus(
    String id,
    LocalSyncState status, {
    String? errorMessage,
    String? errorCode,
    DateTime? availableAt,
  }) {
    return (update(outboxEntries)..where((t) => t.id.equals(id))).write(
      OutboxEntriesCompanion(
        status: Value(status),
        lastError: Value(errorMessage),
        lastErrorCode: Value(errorCode),
        availableAt: availableAt == null
            ? const Value.absent()
            : Value(availableAt),
      ),
    );
  }

  Future<void> incrementAttempts(String id, {required DateTime nextAttemptAt}) {
    return customUpdate(
      'UPDATE outbox_entries SET attempts = attempts + 1, available_at = ? '
      'WHERE id = ?',
      variables: <Variable<Object>>[
        Variable<DateTime>(nextAttemptAt),
        Variable<String>(id),
      ],
      updates: <TableInfo<Table, dynamic>>{outboxEntries},
    );
  }

  Future<int> removeMutation(String id) =>
      (delete(outboxEntries)..where((t) => t.id.equals(id))).go();

  // --- cache ----------------------------------------------------------------

  Future<CachedDocument?> readCache(String key, String tenantId) {
    return (select(cachedDocuments)
          ..where((t) => t.key.equals(key) & t.tenantId.equals(tenantId)))
        .getSingleOrNull();
  }

  Future<void> writeCache(String key, String tenantId, String payload) {
    return into(cachedDocuments).insert(
      CachedDocumentsCompanion.insert(
        key: key,
        tenantId: tenantId,
        payload: payload,
        fetchedAt: DateTime.now().toUtc(),
      ),
      mode: InsertMode.insertOrReplace,
    );
  }

  /// Drop every cached document for a tenant.
  ///
  /// Called on sign-out and when switching shops, so one seller's figures can
  /// never appear under another's.
  Future<int> clearTenantCache(String tenantId) =>
      (delete(cachedDocuments)..where((t) => t.tenantId.equals(tenantId))).go();

  /// Wipe all local state on sign-out.
  Future<void> wipe() async {
    await batch((b) {
      b.deleteWhere(cachedDocuments, (_) => const Constant(true));
      b.deleteWhere(syncCursors, (_) => const Constant(true));
      // The outbox is deliberately NOT wiped here. Unsent seller work is the
      // one thing on this device that exists nowhere else; discarding it on
      // sign-out would silently destroy orders the seller typed. Callers that
      // truly want it gone must delete entries explicitly.
    });
  }
}

LazyDatabase _openConnection() {
  return LazyDatabase(() async {
    final directory = await getApplicationDocumentsDirectory();
    final file = File(p.join(directory.path, 'ecomsbd.sqlite'));

    // Some older Android builds ship an sqlite3 that Drift cannot use; this
    // loads the bundled library instead.
    await applyWorkaroundToOpenSqlite3OnOldAndroidVersions();
    sqlite3.tempDirectory = (await getTemporaryDirectory()).path;

    return NativeDatabase.createInBackground(file);
  });
}
