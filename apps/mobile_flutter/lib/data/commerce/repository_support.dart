import 'dart:convert';

import 'package:meta/meta.dart';

import '../../core/api/api_client.dart';
import '../../core/api/api_error.dart';
import '../local/database.dart';
import 'models.dart';

/// Where a value came from.
///
/// The UI needs this to keep master spec section 126 honest: cached data is
/// labelled with its timestamp rather than presented as current. A repository
/// that quietly returned stale rows would make a seller act on yesterday's
/// stock.
enum DataOrigin {
  /// Straight from the server.
  network,

  /// From the on-device cache because the network was unreachable.
  cache,
}

/// A value plus where it came from and when it was true.
@immutable
class Sourced<T> {
  const Sourced({
    required this.value,
    required this.origin,
    required this.fetchedAt,
    this.error,
  });

  const Sourced.live(T value, DateTime at)
    : this(value: value, origin: DataOrigin.network, fetchedAt: at);

  final T value;
  final DataOrigin origin;

  /// When the *server* produced this data, not when it was read from disk.
  final DateTime fetchedAt;

  /// The failure that forced the fallback to cache, when there was one.
  final ApiError? error;

  bool get isStale => origin == DataOrigin.cache;

  Sourced<R> map<R>(R Function(T) transform) => Sourced<R>(
    value: transform(value),
    origin: origin,
    fetchedAt: fetchedAt,
    error: error,
  );
}

/// Base class for the commerce repositories.
///
/// Holds the read-through cache policy, which is the same everywhere: try the
/// network, cache what comes back, and fall back to the cache **only** when the
/// device is offline. Any other failure — a validation error, a revoked
/// session, a server fault — is rethrown, because showing yesterday's list in
/// response to a bug is how a seller stops trusting the numbers.
///
/// There is no fixture fallback anywhere in this layer. `lib/demo` exists for
/// the Phase A layout preview and is never reachable from here (master spec
/// section 20).
abstract class CachingRepository {
  CachingRepository({required this.api, required this.db});

  final ApiClient api;
  final EcomsbdDatabase db;

  /// The shop whose data is being read. Cache entries are keyed by it so
  /// switching shops can never surface the previous shop's figures.
  String? get tenantId;

  /// Fetch [key] from the network, caching the result; fall back to the cache
  /// when — and only when — the device is offline.
  Future<Sourced<Map<String, dynamic>>> readThrough(
    String key,
    Future<Map<String, dynamic>> Function() fetch, {
    bool useCache = true,
  }) async {
    try {
      final json = await fetch();
      if (useCache) {
        await _writeCache(key, json);
      }
      return Sourced<Map<String, dynamic>>.live(json, DateTime.now().toUtc());
    } on ApiError catch (error) {
      if (!error.isOffline || !useCache) {
        rethrow;
      }
      final cached = await _readCache(key);
      if (cached == null) {
        rethrow;
      }
      return Sourced<Map<String, dynamic>>(
        value: cached.$1,
        origin: DataOrigin.cache,
        fetchedAt: cached.$2,
        error: error,
      );
    }
  }

  Future<void> _writeCache(String key, Map<String, dynamic> json) async {
    final tenant = tenantId;
    if (tenant == null) {
      return;
    }
    await db.writeCache(key, tenant, jsonEncode(json));
  }

  Future<(Map<String, dynamic>, DateTime)?> _readCache(String key) async {
    final tenant = tenantId;
    if (tenant == null) {
      return null;
    }
    final row = await db.readCache(key, tenant);
    if (row == null) {
      return null;
    }
    try {
      final decoded = jsonDecode(row.payload);
      if (decoded is! Map<String, dynamic>) {
        return null;
      }
      return (decoded, row.fetchedAt);
    } on FormatException {
      // A corrupt cache entry is dropped rather than shown. Losing a cached
      // page costs a refresh; rendering garbage costs trust.
      return null;
    }
  }
}

/// Query string for a cursor-paginated list, omitting empty values so the
/// cache key for "first page, no filters" stays stable.
Map<String, dynamic> pageQuery({
  String? cursor,
  int limit = 30,
  Map<String, dynamic> extra = const <String, dynamic>{},
}) {
  return <String, dynamic>{
    'limit': limit,
    if (cursor != null) 'cursor': cursor,
    for (final entry in extra.entries)
      if (entry.value != null && entry.value != '' && entry.value != false)
        entry.key: entry.value,
  };
}

/// Parse a `Page[T]` response body.
PagedResult<T> parsePage<T>(
  Map<String, dynamic> json,
  T Function(Map<String, dynamic>) fromJson,
) => PagedResult.parse<T>(json, fromJson);

/// The most recent of a set of timestamps, or "now" if there are none.
///
/// Used to label a page served from the mirror with the moment its newest row
/// was true, rather than with the moment it was read off disk.
DateTime newestOf(Iterable<DateTime> timestamps) {
  DateTime? newest;
  for (final timestamp in timestamps) {
    if (newest == null || timestamp.isAfter(newest)) {
      newest = timestamp;
    }
  }
  return newest ?? DateTime.now().toUtc();
}
