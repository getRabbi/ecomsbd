import 'dart:convert';

import 'package:drift/drift.dart' show Value;

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../local/database.dart';
import '../local/tables.dart';
import '../sync/outbox.dart';
import 'models.dart';
import 'repository_support.dart';

/// Customers and their history.
///
/// The mirror stores masked numbers only. The plaintext phone exists on this
/// device for exactly as long as one reveal call's response — it is never
/// written to the cache, because a lost phone would otherwise hand over the
/// seller's whole customer list (master spec sections 101, 133).
class CustomersRepository extends CachingRepository {
  CustomersRepository({
    required super.api,
    required super.db,
    required this.outbox,
    required this.tenantId,
  });

  final OutboxWriter outbox;
  bool moneyAvailable = false;

  @override
  final String? tenantId;

  // --- reads ----------------------------------------------------------------

  Future<Sourced<PagedResult<Customer>>> list({
    String? cursor,
    int limit = 30,
    String? search,
    bool repeatOnly = false,
    String? flag,
    String? segment,
    String? tagId,
  }) async {
    try {
      final json = await api.get(
        '/customers/crm',
        query: pageQuery(
          cursor: cursor,
          limit: limit,
          extra: <String, dynamic>{
            'search': search,
            'repeat_only': repeatOnly,
            'flag': flag,
            'segment': segment,
            'tag_id': tagId,
          },
        ),
      );
      final page = parsePage<Customer>(json, Customer.fromJson);
      moneyAvailable = json['money_locked'] == null;
      await _mirror(json['items'] as List<dynamic>? ?? const <dynamic>[]);
      return Sourced<PagedResult<Customer>>.live(page, DateTime.now().toUtc());
    } on ApiError catch (error) {
      if (!error.isOffline || segment != null || tagId != null) {
        rethrow;
      }
      return _fromMirror(
        error: error,
        search: search,
        repeatOnly: repeatOnly,
        flag: flag,
        limit: limit,
        cursor: cursor,
      );
    }
  }

  Future<Customer> get(String id) async {
    try {
      final json = await api.get('/customers/$id');
      await _mirror(<dynamic>[json]);
      return Customer.fromJson(json);
    } on ApiError catch (error) {
      if (!error.isOffline) {
        rethrow;
      }
      final tenant = tenantId;
      final row = tenant == null ? null : await db.localCustomer(tenant, id);
      if (row == null) {
        rethrow;
      }
      return _decode(row);
    }
  }

  /// Phone-first lookup for the order form.
  ///
  /// Returns `null` for an unknown number: at this point in the flow a new
  /// customer is the normal case, not an error.
  Future<Customer?> lookupByPhone(String phone) async {
    final json = await api.get(
      '/customers/lookup',
      query: <String, dynamic>{'phone': phone},
    );
    if (json.isEmpty || json['id'] == null) {
      return null;
    }
    await _mirror(<dynamic>[json]);
    return Customer.fromJson(json);
  }

  /// Reveal the full number, with a reason.
  ///
  /// Writes a `privacy.phone_revealed` audit entry on the server. The result is
  /// returned to the caller and deliberately not cached anywhere.
  Future<String> revealPhone(
    String customerId, {
    required String reason,
  }) async {
    final json = await api.post(
      '/customers/$customerId/reveal-phone',
      body: <String, dynamic>{'reason': reason},
    );
    return json['phone'] as String;
  }

  // --- writes ---------------------------------------------------------------

  Future<Customer> create({
    required String phone,
    String? name,
    String? address,
  }) async {
    final body = <String, dynamic>{
      'phone': phone,
      if (name != null && name.isNotEmpty) 'name': name,
      if (address != null && address.isNotEmpty) 'address': address,
    };

    try {
      final json = await api.post('/customers', body: body);
      await _mirror(<dynamic>[json]);
      return Customer.fromJson(json);
    } on ApiError catch (error) {
      if (!error.isOffline) {
        rethrow;
      }
      return _queueCreate(body, phone);
    }
  }

  Future<Customer> update(
    String id, {
    String? name,
    String? notes,
    String? flag,
    String? flagReason,
  }) async {
    final body = <String, dynamic>{
      if (name != null) 'name': name,
      if (notes != null) 'notes': notes,
      if (flag != null) 'flag': flag,
      if (flagReason != null) 'flag_reason': flagReason,
    };

    try {
      final json = await api.patch('/customers/$id', body: body);
      await _mirror(<dynamic>[json]);
      return Customer.fromJson(json);
    } on ApiError catch (error) {
      if (!error.isOffline) {
        rethrow;
      }
      await outbox.enqueue(
        entityType: SyncEntityType.customer,
        entityId: id,
        operation: MutationOperation.update,
        payload: body,
      );
      final merged = await _patchMirror(id, body);
      if (merged == null) {
        rethrow;
      }
      return merged;
    }
  }

  // --- mirror ---------------------------------------------------------------

  Future<void> _mirror(List<dynamic> items) async {
    final tenant = tenantId;
    if (tenant == null || items.isEmpty) {
      return;
    }
    await db.putCustomers(<CachedCustomersCompanion>[
      for (final item in items)
        _companion(tenant, item as Map<String, dynamic>, LocalSyncState.synced),
    ]);
  }

  CachedCustomersCompanion _companion(
    String tenant,
    Map<String, dynamic> json,
    LocalSyncState state,
  ) {
    // Belt and braces: the API never sends a plaintext number in a list or
    // detail response, and if a future change ever did, it stops here.
    final safe = Map<String, dynamic>.from(json)
      ..remove('phone')
      ..remove('value')
      ..['realized_revenue_paisa'] = null;
    // Offline data must not retain money after a role/plan change.
    if (safe['segments'] is List) {
      safe['segments'] = (safe['segments'] as List)
          .where((value) => value != 'HIGH_VALUE')
          .toList();
    }
    return CachedCustomersCompanion.insert(
      id: safe['id'] as String,
      tenantId: tenant,
      payload: jsonEncode(safe),
      updatedAt: DateTime.parse(
        (safe['updated_at'] ?? safe['created_at']) as String,
      ),
      phoneMasked: safe['phone_masked'] as String,
      phoneLast4: safe['phone_last4'] as String,
      createdAt: DateTime.parse(safe['created_at'] as String),
      syncState: Value(state),
      name: Value(safe['name'] as String?),
      flag: Value(safe['flag'] as String? ?? 'NONE'),
      orderCount: Value(safe['order_count'] as int? ?? 0),
      isRepeatBuyer: Value(safe['is_repeat_buyer'] as bool? ?? false),
    );
  }

  Future<Sourced<PagedResult<Customer>>> _fromMirror({
    required ApiError error,
    required String? search,
    required bool repeatOnly,
    required String? flag,
    required int limit,
    required String? cursor,
  }) async {
    final tenant = tenantId;
    if (tenant == null) {
      throw error;
    }
    final offset = int.tryParse(cursor ?? '0') ?? 0;
    final rows = await db.localCustomers(
      tenantId: tenant,
      search: search,
      repeatOnly: repeatOnly,
      flag: flag,
      limit: limit + 1,
      offset: offset,
    );
    final hasMore = rows.length > limit;
    final visible = rows.take(limit).toList();
    if (visible.isEmpty && offset == 0) {
      // Offline with nothing saved. Returning an empty page here would render
      // as "you have none of these yet", which is a different and wrong
      // statement — the truth is that we cannot reach the server. The screen
      // shows the offline state instead.
      throw error;
    }
    return Sourced<PagedResult<Customer>>(
      value: PagedResult<Customer>(
        items: <Customer>[for (final row in visible) _decode(row)],
        nextCursor: hasMore ? '${offset + limit}' : null,
        hasMore: hasMore,
      ),
      origin: DataOrigin.cache,
      fetchedAt: newestOf(visible.map((row) => row.updatedAt)),
      error: error,
    );
  }

  Customer _decode(CachedCustomer row) =>
      Customer.fromJson(jsonDecode(row.payload) as Map<String, dynamic>);

  Future<Customer> _queueCreate(Map<String, dynamic> body, String phone) async {
    final tenant = tenantId;
    final id = outbox.newId();
    await outbox.enqueue(
      entityType: SyncEntityType.customer,
      entityId: id,
      operation: MutationOperation.create,
      payload: body,
    );

    final now = DateTime.now().toUtc();
    final digits = normalizeDigits(phone).replaceAll(RegExp(r'\D'), '');
    final json = <String, dynamic>{
      'id': id,
      'name': body['name'],
      // Masked locally by the same rule the server uses, so the row reads the
      // same before and after it syncs.
      'phone_masked': maskPhone(phone),
      'phone_last4': digits.length >= 4
          ? digits.substring(digits.length - 4)
          : digits,
      'flag': 'NONE',
      'order_count': 0,
      'delivered_count': 0,
      'returned_count': 0,
      'cancelled_count': 0,
      'success_rate_basis_points': null,
      'realized_revenue_paisa': 0,
      'is_repeat_buyer': false,
      'created_at': now.toIso8601String(),
      'updated_at': now.toIso8601String(),
      'addresses': <dynamic>[],
    };
    if (tenant != null) {
      await db.putCustomers(<CachedCustomersCompanion>[
        _companion(tenant, json, LocalSyncState.localOnly),
      ]);
    }
    return Customer.fromJson(json);
  }

  Future<Customer?> _patchMirror(String id, Map<String, dynamic> body) async {
    final tenant = tenantId;
    if (tenant == null) {
      return null;
    }
    final row = await db.localCustomer(tenant, id);
    if (row == null) {
      return null;
    }
    final json = <String, dynamic>{
      ...jsonDecode(row.payload) as Map<String, dynamic>,
      ...body,
      'updated_at': DateTime.now().toUtc().toIso8601String(),
    };
    await db.putCustomers(<CachedCustomersCompanion>[
      _companion(tenant, json, LocalSyncState.localOnly),
    ]);
    return Customer.fromJson(json);
  }
}
