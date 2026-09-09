import 'dart:convert';

import 'package:drift/drift.dart' show Value;
import 'package:uuid/uuid.dart';

import '../local/database.dart';
import '../local/tables.dart';

/// Entity types the server accepts through `/v1/sync/mutations`.
///
/// Mirrors `SyncEntity` in `backend/app/sync/models.py`. The list is short on
/// purpose: master spec section 62.17 forbids queueing a courier booking, a
/// risk lookup or a payout as though it had succeeded, so those actions require
/// a connection and never reach the outbox.
class SyncEntityType {
  const SyncEntityType._();

  static const String order = 'ORDER';
  static const String product = 'PRODUCT';
  static const String customer = 'CUSTOMER';
  static const String stockAdjustment = 'STOCK_ADJUSTMENT';

  static const Set<String> all = <String>{
    order,
    product,
    customer,
    stockAdjustment,
  };
}

/// Writes seller changes to the local outbox so they survive a dead connection.
///
/// The id minted here is three things at once: the local record's id, the
/// mutation id the server deduplicates on, and the `client_id` that makes a
/// replayed create resolve to the same order. Generating a fresh one per
/// attempt is what turns one sale into two parcels.
class OutboxWriter {
  OutboxWriter({required this.db, Uuid? uuid}) : _uuid = uuid ?? const Uuid();

  final EcomsbdDatabase db;
  final Uuid _uuid;

  String newId() => _uuid.v4();

  /// Queue one mutation.
  ///
  /// Returns the entry id, which callers use as the local record's id.
  Future<String> enqueue({
    required String entityType,
    required String entityId,
    required MutationOperation operation,
    required Map<String, dynamic> payload,
    int? baseVersion,
    String? id,
  }) async {
    assert(
      SyncEntityType.all.contains(entityType),
      '$entityType cannot be synced offline. Only orders, products, customers '
      'and stock adjustments may be queued (master spec section 62.17).',
    );

    final now = DateTime.now().toUtc();
    final entryId = id ?? entityId;
    await db.enqueueMutation(
      OutboxEntriesCompanion.insert(
        id: entryId,
        entityType: entityType,
        entityId: entityId,
        operation: operation,
        payload: jsonEncode(<String, dynamic>{
          ...payload,
          if (baseVersion != null) '__base_version': baseVersion,
        }),
        clientTimestamp: now,
        availableAt: now,
        createdAt: now,
        status: const Value(LocalSyncState.localOnly),
      ),
    );
    return entryId;
  }
}

/// Decoded outbox payload plus the base version stored alongside it.
({Map<String, dynamic> body, int? baseVersion}) decodeOutboxPayload(
  String payload,
) {
  final decoded = jsonDecode(payload);
  if (decoded is! Map<String, dynamic>) {
    return (body: <String, dynamic>{}, baseVersion: null);
  }
  final body = Map<String, dynamic>.from(decoded);
  final baseVersion = body.remove('__base_version');
  return (body: body, baseVersion: baseVersion is int ? baseVersion : null);
}
