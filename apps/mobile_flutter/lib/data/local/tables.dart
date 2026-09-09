import 'package:drift/drift.dart';

/// Lifecycle of a locally created or edited record.
///
/// Master spec section 127 names these exact states, and the reason is in the
/// last one: a seller must be able to see which orders are not yet on the
/// server. An offline record that looks identical to a synced one is how money
/// gets lost.
enum LocalSyncState {
  /// Created on this device, never sent.
  localOnly,

  /// Sent, awaiting the server's answer.
  syncing,

  /// Accepted by the server.
  synced,

  /// The server has a different version of a field the seller edited.
  conflict,

  /// The server rejected it; the seller must fix something.
  failedValidation,
}

/// What an outbox entry does when it reaches the server.
enum MutationOperation { create, update, delete }

/// The offline mutation outbox (master spec section 37).
///
/// Only mutations that are *safe* to replay live here. Courier booking, risk
/// lookups, payout import and subscription checkout are never queued: master
/// spec section 62.17 forbids making an offline queued courier operation look
/// booked, so those actions require a connection and say so.
class OutboxEntries extends Table {
  /// Client-generated UUID, also the idempotency key when the entry is sent.
  TextColumn get id => text()();

  /// Domain entity, e.g. `order`, `product`, `customer`.
  TextColumn get entityType => text().withLength(min: 1, max: 64)();

  /// Client id of the affected record.
  TextColumn get entityId => text()();

  TextColumn get operation => textEnum<MutationOperation>()();

  /// JSON body to send.
  TextColumn get payload => text()();

  /// When the seller made the change, in UTC. Sent so the server can order
  /// edits made offline; it is never used for money or aging, which are
  /// computed server-side from the server clock (master spec section 69).
  DateTimeColumn get clientTimestamp => dateTime()();

  TextColumn get status =>
      textEnum<LocalSyncState>().withDefault(const Constant('localOnly'))();

  IntColumn get attempts => integer().withDefault(const Constant(0))();

  /// Next time this entry may be sent. Backoff after a failure.
  DateTimeColumn get availableAt => dateTime()();

  TextColumn get lastError => text().nullable()();

  /// Machine code of the last failure, so the UI can explain a rejection.
  TextColumn get lastErrorCode => text().nullable()();

  DateTimeColumn get createdAt => dateTime()();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{id};
}

/// Server data cached for offline reading.
///
/// Deliberately a generic blob store rather than mirrored domain tables. The
/// client is not the source of truth for anything financial (master spec
/// section 64), so this exists to render a last-known screen with a timestamp,
/// not to compute from.
class CachedDocuments extends Table {
  /// Cache key, e.g. `analytics.home` or `orders.page.1`.
  TextColumn get key => text()();

  /// Tenant the document belongs to. Cached data is scoped so switching shops
  /// never shows the previous shop's figures.
  TextColumn get tenantId => text()();

  TextColumn get payload => text()();

  /// When the server produced it. Shown to the seller alongside stale data
  /// (master spec section 126: cached data is labelled with its timestamp).
  DateTimeColumn get fetchedAt => dateTime()();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{key, tenantId};
}

/// Sync cursors per entity type (master spec section 38).
class SyncCursors extends Table {
  TextColumn get entityType => text()();
  TextColumn get tenantId => text()();
  TextColumn get cursor => text().nullable()();
  DateTimeColumn get lastSyncedAt => dateTime().nullable()();

  @override
  Set<Column<Object>> get primaryKey => <Column<Object>>{entityType, tenantId};
}
