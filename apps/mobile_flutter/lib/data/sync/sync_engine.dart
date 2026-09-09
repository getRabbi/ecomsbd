import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;

import 'package:drift/drift.dart';
import 'package:meta/meta.dart';

import '../../core/api/api_client.dart';
import '../../core/api/api_error.dart';
import '../local/database.dart';
import '../local/tables.dart';
import 'outbox.dart';

/// What a sync run did.
@immutable
class SyncReport {
  const SyncReport({
    this.pushed = 0,
    this.applied = 0,
    this.conflicts = 0,
    this.rejected = 0,
    this.pulled = 0,
    this.error,
  });

  final int pushed;
  final int applied;
  final int conflicts;
  final int rejected;
  final int pulled;

  /// The failure that stopped the run, if any. Offline is not a failure worth
  /// surfacing — it is the expected case on the connections this app targets.
  final ApiError? error;

  bool get didWork => applied > 0 || pulled > 0;
  bool get needsAttention => conflicts > 0 || rejected > 0;
}

/// Drains the outbox and pulls server changes.
///
/// Push before pull, always. Pulling first would overwrite the local mirror
/// with a server state that does not yet contain the seller's queued work, and
/// their edits would briefly vanish from the screen.
///
/// Every mutation carries the id the device minted, and the server deduplicates
/// on it. That is what makes a retry over a bad connection safe: resending the
/// batch returns each mutation's original outcome instead of creating a second
/// order (master spec sections 37, 38).
class SyncEngine {
  SyncEngine({required this.api, required this.db, required this.tenantId});

  final ApiClient api;
  final EcomsbdDatabase db;
  final String? tenantId;

  /// How many mutations go in one request. The server caps a batch at 100.
  static const int batchSize = 50;

  /// Give up on an entry after this many attempts and park it for the seller.
  ///
  /// A mutation that has failed eight times is not going to succeed on the
  /// ninth; leaving it in the queue would silently retry forever while the
  /// seller believes their order is on its way.
  static const int maxAttempts = 8;

  bool _running = false;

  /// Push queued mutations, then pull changes.
  Future<SyncReport> run() async {
    if (_running) {
      // A second concurrent run would send the same entries twice. Harmless on
      // the server thanks to mutation ids, but it doubles traffic on a
      // connection that is already the constraint.
      return const SyncReport();
    }
    _running = true;
    try {
      final push = await pushOutbox();
      if (push.error != null && push.error!.isOffline) {
        return push;
      }
      final pull = await pullChanges();
      return SyncReport(
        pushed: push.pushed,
        applied: push.applied,
        conflicts: push.conflicts,
        rejected: push.rejected,
        pulled: pull.pulled,
        error: pull.error,
      );
    } finally {
      _running = false;
    }
  }

  // --- push -----------------------------------------------------------------

  Future<SyncReport> pushOutbox() async {
    final entries = await db.dueOutboxEntries(limit: batchSize);
    if (entries.isEmpty) {
      return const SyncReport();
    }

    final mutations = <Map<String, dynamic>>[];
    for (final entry in entries) {
      final decoded = decodeOutboxPayload(entry.payload);
      mutations.add(<String, dynamic>{
        'mutation_id': entry.id,
        'entity_type': entry.entityType,
        'entity_id': entry.entityId,
        'operation': entry.operation.name.toUpperCase(),
        'payload': decoded.body,
        if (decoded.baseVersion != null) 'base_version': decoded.baseVersion,
        'client_timestamp': entry.clientTimestamp.toIso8601String(),
      });
      await db.markMutationStatus(entry.id, LocalSyncState.syncing);
    }

    final Map<String, dynamic> response;
    try {
      response = await api.post(
        '/sync/mutations',
        body: <String, dynamic>{'mutations': mutations},
      );
    } on ApiError catch (error) {
      // Nothing was accepted, so every entry goes back to queued with a
      // backoff. Marking them failed would hide real seller work behind an
      // error that was only ever about the network.
      for (final entry in entries) {
        await _requeue(entry, error);
      }
      return SyncReport(pushed: 0, error: error);
    }

    final results = response['results'] as List<dynamic>? ?? const <dynamic>[];
    var applied = 0;
    var conflicts = 0;
    var rejected = 0;

    for (final raw in results) {
      final result = raw as Map<String, dynamic>;
      final mutationId = result['mutation_id'] as String;
      final status = result['status'] as String;
      final entityId = result['entity_id'] as String?;

      switch (status) {
        case 'APPLIED':
        case 'DUPLICATE':
          // DUPLICATE means the server already had it — the same success from
          // the device's point of view.
          applied++;
          await _promoteMirror(mutationId, entityId);
          await db.removeMutation(mutationId);
        case 'CONFLICT':
          conflicts++;
          await db.markMutationStatus(
            mutationId,
            LocalSyncState.conflict,
            errorCode: result['error_code'] as String?,
            errorMessage:
                'This record changed elsewhere. Choose which version to keep.',
          );
          await _markMirrorState(mutationId, LocalSyncState.conflict);
        case 'REJECTED':
        default:
          rejected++;
          await db.markMutationStatus(
            mutationId,
            LocalSyncState.failedValidation,
            errorCode: result['error_code'] as String?,
            errorMessage: result['error_message'] as String?,
          );
          await _markMirrorState(mutationId, LocalSyncState.failedValidation);
      }
    }

    return SyncReport(
      pushed: entries.length,
      applied: applied,
      conflicts: conflicts,
      rejected: rejected,
    );
  }

  Future<void> _requeue(OutboxEntry entry, ApiError error) async {
    final attempts = entry.attempts + 1;
    if (attempts >= maxAttempts) {
      await db.markMutationStatus(
        entry.id,
        LocalSyncState.failedValidation,
        errorCode: error.code,
        errorMessage:
            'Could not send after $attempts tries. ${error.displayMessage}',
      );
      await _markMirrorState(entry.id, LocalSyncState.failedValidation);
      return;
    }

    // Exponential backoff, capped. Ten seconds to five minutes.
    final seconds = math.min(300, 10 * math.pow(2, attempts).toInt());
    await db.incrementAttempts(
      entry.id,
      nextAttemptAt: DateTime.now().toUtc().add(Duration(seconds: seconds)),
    );
    await db.markMutationStatus(
      entry.id,
      LocalSyncState.localOnly,
      errorCode: error.code,
      errorMessage: error.displayMessage,
    );
  }

  // --- pull -----------------------------------------------------------------

  Future<SyncReport> pullChanges({int limit = 100}) async {
    final tenant = tenantId;
    if (tenant == null) {
      return const SyncReport();
    }

    var pulled = 0;
    var cursor = await _cursorFor(tenant);

    // Bounded: a device that has been offline for a month should not block the
    // UI draining an unbounded feed in one go. The rest arrives next run.
    for (var page = 0; page < 20; page++) {
      final Map<String, dynamic> response;
      try {
        response = await api.get(
          '/sync/changes',
          query: <String, dynamic>{
            'limit': limit,
            if (cursor != null) 'cursor': cursor,
          },
        );
      } on ApiError catch (error) {
        return SyncReport(pulled: pulled, error: error);
      }

      final changes =
          response['changes'] as List<dynamic>? ?? const <dynamic>[];
      for (final raw in changes) {
        await _applyChange(tenant, raw as Map<String, dynamic>);
        pulled++;
      }

      cursor = response['next_cursor'] as String?;
      await _saveCursor(tenant, cursor);
      if (response['has_more'] != true || cursor == null) {
        break;
      }
    }

    return SyncReport(pulled: pulled);
  }

  Future<void> _applyChange(String tenant, Map<String, dynamic> change) async {
    final entityType = change['entity_type'] as String;
    final entityId = change['entity_id'] as String;
    final deleted = change['deleted'] as bool? ?? false;
    final data = change['data'] as Map<String, dynamic>?;

    if (deleted) {
      // A tombstone. Drop the row rather than leaving something the seller
      // deleted on another device sitting on this screen.
      await _deleteMirrorRow(tenant, entityType, entityId);
      return;
    }
    if (data == null) {
      return;
    }

    switch (entityType) {
      case 'ORDER':
        await db.putOrders(<CachedOrdersCompanion>[
          CachedOrdersCompanion.insert(
            id: entityId,
            tenantId: tenant,
            payload: jsonEncode(data),
            updatedAt: _timestamp(change['updated_at']),
            orderNumber: data['order_number'] as String? ?? 'PENDING',
            status: data['status'] as String? ?? 'DRAFT',
            createdAt: _timestamp(data['created_at'] ?? change['updated_at']),
            customerName: Value(data['customer_name'] as String?),
            phoneMasked: Value(data['customer_phone_masked'] as String?),
            codAmountPaisa: Value(data['cod_amount_paisa'] as int? ?? 0),
            version: Value(data['version'] as int? ?? 1),
          ),
        ]);
      case 'PRODUCT':
        await db.putProducts(<CachedProductsCompanion>[
          CachedProductsCompanion.insert(
            id: entityId,
            tenantId: tenant,
            payload: jsonEncode(data),
            updatedAt: _timestamp(change['updated_at']),
            name: data['name'] as String? ?? '',
            createdAt: _timestamp(data['created_at'] ?? change['updated_at']),
            sku: Value(data['sku'] as String?),
            stockOnHand: Value(data['stock_on_hand'] as int? ?? 0),
            isLowStock: Value(data['is_low_stock'] as bool? ?? false),
            isArchived: Value(data['is_archived'] as bool? ?? false),
          ),
        ]);
      case 'CUSTOMER':
        final safe = Map<String, dynamic>.from(data)..remove('phone');
        await db.putCustomers(<CachedCustomersCompanion>[
          CachedCustomersCompanion.insert(
            id: entityId,
            tenantId: tenant,
            payload: jsonEncode(safe),
            updatedAt: _timestamp(change['updated_at']),
            phoneMasked: safe['phone_masked'] as String? ?? '',
            phoneLast4: safe['phone_last4'] as String? ?? '',
            createdAt: _timestamp(safe['created_at'] ?? change['updated_at']),
            name: Value(safe['name'] as String?),
            flag: Value(safe['flag'] as String? ?? 'NONE'),
            orderCount: Value(safe['order_count'] as int? ?? 0),
            isRepeatBuyer: Value(safe['is_repeat_buyer'] as bool? ?? false),
          ),
        ]);
    }
  }

  Future<void> _deleteMirrorRow(
    String tenant,
    String entityType,
    String entityId,
  ) async {
    switch (entityType) {
      case 'ORDER':
        await (db.delete(db.cachedOrders)
              ..where((t) => t.tenantId.equals(tenant) & t.id.equals(entityId)))
            .go();
      case 'PRODUCT':
        await (db.delete(db.cachedProducts)
              ..where((t) => t.tenantId.equals(tenant) & t.id.equals(entityId)))
            .go();
      case 'CUSTOMER':
        await (db.delete(db.cachedCustomers)
              ..where((t) => t.tenantId.equals(tenant) & t.id.equals(entityId)))
            .go();
    }
  }

  DateTime _timestamp(Object? value) {
    if (value is String) {
      return DateTime.parse(value).toUtc();
    }
    return DateTime.now().toUtc();
  }

  Future<String?> _cursorFor(String tenant) async {
    final row =
        await (db.select(db.syncCursors)..where(
              (t) => t.tenantId.equals(tenant) & t.entityType.equals('all'),
            ))
            .getSingleOrNull();
    return row?.cursor;
  }

  Future<void> _saveCursor(String tenant, String? cursor) async {
    await db
        .into(db.syncCursors)
        .insertOnConflictUpdate(
          SyncCursorsCompanion.insert(
            entityType: 'all',
            tenantId: tenant,
            cursor: Value(cursor),
            lastSyncedAt: Value(DateTime.now().toUtc()),
          ),
        );
  }

  // --- mirror bookkeeping ---------------------------------------------------

  /// Mark a mirrored row as synced once the server has it.
  ///
  /// The server may have given the record a different id than the device
  /// minted (a create resolved through `client_id`), so the local row is
  /// re-keyed rather than left behind as a duplicate.
  Future<void> _promoteMirror(String mutationId, String? serverId) async {
    final tenant = tenantId;
    if (tenant == null) {
      return;
    }

    final order = await db.localOrder(tenant, mutationId);
    if (order != null) {
      await _rekeyOrder(tenant, order, serverId);
      return;
    }
    final product = await db.localProduct(tenant, mutationId);
    if (product != null) {
      await _rekeyProduct(tenant, product, serverId);
      return;
    }
    final customer = await db.localCustomer(tenant, mutationId);
    if (customer != null) {
      await _rekeyCustomer(tenant, customer, serverId);
    }
  }

  Future<void> _rekeyOrder(
    String tenant,
    CachedOrder row,
    String? serverId,
  ) async {
    final id = serverId ?? row.id;
    final payload = jsonDecode(row.payload) as Map<String, dynamic>;
    payload['id'] = id;
    await db.putOrders(<CachedOrdersCompanion>[
      CachedOrdersCompanion.insert(
        id: id,
        tenantId: tenant,
        payload: jsonEncode(payload),
        updatedAt: DateTime.now().toUtc(),
        orderNumber: row.orderNumber,
        status: row.status,
        createdAt: row.createdAt,
        syncState: const Value(LocalSyncState.synced),
        customerName: Value(row.customerName),
        phoneMasked: Value(row.phoneMasked),
        codAmountPaisa: Value(row.codAmountPaisa),
        version: Value(row.version),
      ),
    ]);
    if (id != row.id) {
      await (db.delete(
        db.cachedOrders,
      )..where((t) => t.tenantId.equals(tenant) & t.id.equals(row.id))).go();
    }
  }

  Future<void> _rekeyProduct(
    String tenant,
    CachedProduct row,
    String? serverId,
  ) async {
    final id = serverId ?? row.id;
    final payload = jsonDecode(row.payload) as Map<String, dynamic>;
    payload['id'] = id;
    await db.putProducts(<CachedProductsCompanion>[
      CachedProductsCompanion.insert(
        id: id,
        tenantId: tenant,
        payload: jsonEncode(payload),
        updatedAt: DateTime.now().toUtc(),
        name: row.name,
        createdAt: row.createdAt,
        syncState: const Value(LocalSyncState.synced),
        sku: Value(row.sku),
        stockOnHand: Value(row.stockOnHand),
        isLowStock: Value(row.isLowStock),
        isArchived: Value(row.isArchived),
      ),
    ]);
    if (id != row.id) {
      await (db.delete(
        db.cachedProducts,
      )..where((t) => t.tenantId.equals(tenant) & t.id.equals(row.id))).go();
    }
  }

  Future<void> _rekeyCustomer(
    String tenant,
    CachedCustomer row,
    String? serverId,
  ) async {
    final id = serverId ?? row.id;
    final payload = jsonDecode(row.payload) as Map<String, dynamic>;
    payload['id'] = id;
    await db.putCustomers(<CachedCustomersCompanion>[
      CachedCustomersCompanion.insert(
        id: id,
        tenantId: tenant,
        payload: jsonEncode(payload),
        updatedAt: DateTime.now().toUtc(),
        phoneMasked: row.phoneMasked,
        phoneLast4: row.phoneLast4,
        createdAt: row.createdAt,
        syncState: const Value(LocalSyncState.synced),
        name: Value(row.name),
        flag: Value(row.flag),
        orderCount: Value(row.orderCount),
        isRepeatBuyer: Value(row.isRepeatBuyer),
      ),
    ]);
    if (id != row.id) {
      await (db.delete(
        db.cachedCustomers,
      )..where((t) => t.tenantId.equals(tenant) & t.id.equals(row.id))).go();
    }
  }

  Future<void> _markMirrorState(String id, LocalSyncState state) async {
    final tenant = tenantId;
    if (tenant == null) {
      return;
    }
    await (db.update(db.cachedOrders)
          ..where((t) => t.tenantId.equals(tenant) & t.id.equals(id)))
        .write(CachedOrdersCompanion(syncState: Value(state)));
    await (db.update(db.cachedProducts)
          ..where((t) => t.tenantId.equals(tenant) & t.id.equals(id)))
        .write(CachedProductsCompanion(syncState: Value(state)));
    await (db.update(db.cachedCustomers)
          ..where((t) => t.tenantId.equals(tenant) & t.id.equals(id)))
        .write(CachedCustomersCompanion(syncState: Value(state)));
  }
}
