import 'dart:convert';

import 'package:drift/drift.dart' show Value;

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../local/database.dart';
import '../local/tables.dart';
import '../sync/outbox.dart';
import 'models.dart';
import 'repository_support.dart';

/// The result of saving an order, including whether it is on the server yet.
class SavedOrder {
  const SavedOrder({
    required this.order,
    required this.isQueued,
    this.duplicateCheck,
  });

  final SellerOrder order;

  /// True when the order lives only in the outbox so far. The UI says so
  /// rather than implying the server has it (master spec section 127).
  final bool isQueued;

  final DuplicateCheck? duplicateCheck;
}

/// Orders, order items and the paste parser.
///
/// Saving an order **never books a courier**. Booking is a separate explicit
/// action that does not exist yet, and making it a side effect of saving would
/// make an irreversible external call on the seller's behalf (master spec
/// section 62.17).
class OrdersRepository extends CachingRepository {
  OrdersRepository({
    required super.api,
    required super.db,
    required this.outbox,
    required this.tenantId,
  });

  final OutboxWriter outbox;

  @override
  final String? tenantId;

  // --- reads ----------------------------------------------------------------

  Future<Sourced<PagedResult<SellerOrder>>> list({
    String? cursor,
    int limit = 30,
    String? status,
    String? search,
    String? customerId,
  }) async {
    try {
      final json = await api.get(
        '/orders',
        query: pageQuery(
          cursor: cursor,
          limit: limit,
          extra: <String, dynamic>{
            'status': status,
            'search': search,
            'customer_id': customerId,
          },
        ),
      );
      final page = parsePage<SellerOrder>(json, SellerOrder.fromJson);
      await _mirror(json['items'] as List<dynamic>? ?? const <dynamic>[]);
      return Sourced<PagedResult<SellerOrder>>.live(
        page,
        DateTime.now().toUtc(),
      );
    } on ApiError catch (error) {
      if (!error.isOffline) {
        rethrow;
      }
      return _fromMirror(
        error: error,
        search: search,
        status: status,
        limit: limit,
        cursor: cursor,
      );
    }
  }

  Future<SellerOrder> get(String id) async {
    try {
      final json = await api.get('/orders/$id');
      await _mirror(<dynamic>[json]);
      return SellerOrder.fromJson(json);
    } on ApiError catch (error) {
      if (!error.isOffline) {
        rethrow;
      }
      final tenant = tenantId;
      final row = tenant == null ? null : await db.localOrder(tenant, id);
      if (row == null) {
        rethrow;
      }
      return _decode(row);
    }
  }

  /// Whether this order is still waiting to reach the server.
  Future<LocalSyncState> syncStateOf(String id) async {
    final tenant = tenantId;
    if (tenant == null) {
      return LocalSyncState.synced;
    }
    final row = await db.localOrder(tenant, id);
    return row?.syncState ?? LocalSyncState.synced;
  }

  // --- parsing --------------------------------------------------------------

  /// Extract order fields from pasted Messenger or WhatsApp text.
  ///
  /// Nothing is saved. Every field comes back for the seller to confirm, and a
  /// field the parser was unsure about arrives empty rather than guessed
  /// (master spec section 7.1). Needs a connection: the parser is deterministic
  /// but it runs on the server, so there is one implementation to test.
  Future<ParsedOrder> parse(String text) async {
    final json = await api.post(
      '/orders/parse',
      body: <String, dynamic>{'text': text},
    );
    return ParsedOrder.fromJson(json);
  }

  /// Look for a similar recent order before the seller commits.
  ///
  /// Advisory. A warning here never blocks the save (master spec section 9).
  Future<DuplicateCheck> checkDuplicates({
    required String phone,
    required int codAmountPaisa,
    List<String> itemNames = const <String>[],
    String? excludeOrderId,
  }) async {
    final json = await api.post(
      '/orders/check-duplicates',
      body: <String, dynamic>{
        'phone': phone,
        'cod_amount_paisa': codAmountPaisa,
        'item_names': itemNames,
        if (excludeOrderId != null) 'exclude_order_id': excludeOrderId,
      },
    );
    return DuplicateCheck.fromJson(json);
  }

  // --- writes ---------------------------------------------------------------

  Future<SavedOrder> create({
    required String phone,
    required List<Map<String, dynamic>> items,
    String? customerName,
    String? address,
    String? district,
    String? area,
    int? codAmountPaisa,
    int discountPaisa = 0,
    int deliveryFeePaisa = 0,
    String? note,
    String? sourceText,
    String channel = 'MANUAL',
    String? clientId,
  }) async {
    // Minted before the attempt so a retry — online or offline — carries the
    // same id. This is what stops one sale becoming two orders.
    final id = clientId ?? outbox.newId();
    final body = <String, dynamic>{
      'phone': phone,
      'items': items,
      if (customerName != null && customerName.isNotEmpty)
        'customer_name': customerName,
      if (address != null && address.isNotEmpty) 'address': address,
      if (district != null && district.isNotEmpty) 'district': district,
      if (area != null && area.isNotEmpty) 'area': area,
      if (codAmountPaisa != null) 'cod_amount_paisa': codAmountPaisa,
      'discount_paisa': discountPaisa,
      'delivery_fee_paisa': deliveryFeePaisa,
      if (note != null && note.isNotEmpty) 'note': note,
      if (sourceText != null && sourceText.isNotEmpty)
        'source_text': sourceText,
      'channel': channel,
      'client_id': id,
    };

    try {
      final json = await api.post('/orders', body: body);
      final result = OrderCreateResult.fromJson(json);
      await _mirror(<dynamic>[json['order']]);
      return SavedOrder(
        order: result.order,
        isQueued: false,
        duplicateCheck: result.duplicateCheck,
      );
    } on ApiError catch (error) {
      if (!error.isOffline) {
        rethrow;
      }
      return SavedOrder(order: await _queueCreate(id, body), isQueued: true);
    }
  }

  Future<SavedOrder> update(
    String id, {
    List<Map<String, dynamic>>? items,
    int? codAmountPaisa,
    int? discountPaisa,
    int? deliveryFeePaisa,
    String? note,
    String? address,
    String? district,
    String? area,
    String? status,
    String? cancellationReason,
    int? expectedVersion,
  }) async {
    final body = <String, dynamic>{
      if (items != null) 'items': items,
      if (codAmountPaisa != null) 'cod_amount_paisa': codAmountPaisa,
      if (discountPaisa != null) 'discount_paisa': discountPaisa,
      if (deliveryFeePaisa != null) 'delivery_fee_paisa': deliveryFeePaisa,
      if (note != null) 'note': note,
      if (address != null) 'address': address,
      if (district != null) 'district': district,
      if (area != null) 'area': area,
      if (status != null) 'status': status,
      if (cancellationReason != null) 'cancellation_reason': cancellationReason,
      if (expectedVersion != null) 'expected_version': expectedVersion,
    };

    try {
      final json = await api.patch('/orders/$id', body: body);
      await _mirror(<dynamic>[json]);
      return SavedOrder(order: SellerOrder.fromJson(json), isQueued: false);
    } on ApiError catch (error) {
      if (!error.isOffline) {
        rethrow;
      }
      await outbox.enqueue(
        entityType: SyncEntityType.order,
        entityId: id,
        operation: MutationOperation.update,
        payload: body,
        // The server compares this and reports a conflict rather than
        // overwriting a change made elsewhere (master spec section 128).
        baseVersion: expectedVersion,
      );
      final merged = await _patchMirror(id, body);
      if (merged == null) {
        rethrow;
      }
      return SavedOrder(order: merged, isQueued: true);
    }
  }

  // --- mirror ---------------------------------------------------------------

  Future<void> _mirror(List<dynamic> items) async {
    final tenant = tenantId;
    if (tenant == null || items.isEmpty) {
      return;
    }
    await db.putOrders(<CachedOrdersCompanion>[
      for (final item in items)
        _companion(tenant, item as Map<String, dynamic>, LocalSyncState.synced),
    ]);
  }

  CachedOrdersCompanion _companion(
    String tenant,
    Map<String, dynamic> json,
    LocalSyncState state,
  ) {
    return CachedOrdersCompanion.insert(
      id: json['id'] as String,
      tenantId: tenant,
      payload: jsonEncode(json),
      updatedAt: DateTime.parse(
        (json['updated_at'] ?? json['created_at']) as String,
      ),
      orderNumber: json['order_number'] as String,
      status: json['status'] as String,
      createdAt: DateTime.parse(json['created_at'] as String),
      syncState: Value(state),
      customerName: Value(json['customer_name'] as String?),
      phoneMasked: Value(json['customer_phone_masked'] as String?),
      codAmountPaisa: Value(json['cod_amount_paisa'] as int? ?? 0),
      version: Value(json['version'] as int? ?? 1),
    );
  }

  Future<Sourced<PagedResult<SellerOrder>>> _fromMirror({
    required ApiError error,
    required String? search,
    required String? status,
    required int limit,
    required String? cursor,
  }) async {
    final tenant = tenantId;
    if (tenant == null) {
      throw error;
    }
    final offset = int.tryParse(cursor ?? '0') ?? 0;
    final rows = await db.localOrders(
      tenantId: tenant,
      search: search,
      status: status,
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
    return Sourced<PagedResult<SellerOrder>>(
      value: PagedResult<SellerOrder>(
        items: <SellerOrder>[for (final row in visible) _decode(row)],
        nextCursor: hasMore ? '${offset + limit}' : null,
        hasMore: hasMore,
      ),
      origin: DataOrigin.cache,
      fetchedAt: newestOf(visible.map((row) => row.updatedAt)),
      error: error,
    );
  }

  SellerOrder _decode(CachedOrder row) =>
      SellerOrder.fromJson(jsonDecode(row.payload) as Map<String, dynamic>);

  Future<SellerOrder> _queueCreate(String id, Map<String, dynamic> body) async {
    await outbox.enqueue(
      entityType: SyncEntityType.order,
      entityId: id,
      operation: MutationOperation.create,
      payload: body,
    );

    final now = DateTime.now().toUtc();
    final items = <Map<String, dynamic>>[
      for (final item in body['items'] as List<dynamic>)
        item as Map<String, dynamic>,
    ];
    var subtotal = 0;
    for (final item in items) {
      final unit = item['unit_price_paisa'] as int? ?? 0;
      final quantity = item['quantity'] as int? ?? 1;
      final discount = item['discount_paisa'] as int? ?? 0;
      subtotal += unit * quantity - discount;
    }

    final json = <String, dynamic>{
      'id': id,
      // The server allocates the real number. Until then the card shows this
      // placeholder, which reads as "not yet numbered" rather than pretending
      // to be a reference the seller could quote to a courier.
      'order_number': 'PENDING',
      'client_id': id,
      'customer_id': null,
      'customer_name': body['customer_name'],
      'customer_phone_masked': maskPhone(body['phone'] as String),
      'delivery_address_raw': body['address'],
      'delivery_district': body['district'],
      'delivery_area': body['area'],
      'status': 'DRAFT',
      'channel': body['channel'] ?? 'MANUAL',
      'business_date': _businessDate(now).toIso8601String().substring(0, 10),
      'subtotal_paisa': subtotal,
      'discount_paisa': body['discount_paisa'] ?? 0,
      'delivery_fee_paisa': body['delivery_fee_paisa'] ?? 0,
      'cod_amount_paisa': body['cod_amount_paisa'] ?? subtotal,
      'note': body['note'],
      'source_text': body['source_text'],
      'version': 1,
      'created_at': now.toIso8601String(),
      'updated_at': now.toIso8601String(),
      'fulfillment_state': 'NOT_BOOKED',
      'risk_state': 'NOT_CHECKED',
      'profit_state': 'PENDING_CALCULATION',
      'items': <Map<String, dynamic>>[
        for (final item in items)
          <String, dynamic>{
            'id': outbox.newId(),
            'product_id': item['product_id'],
            'product_name': item['name'] ?? 'Item',
            'sku': null,
            'variant_label': item['variant_label'],
            'quantity': item['quantity'] ?? 1,
            'unit_price_paisa': item['unit_price_paisa'] ?? 0,
            // Unknown until the server snapshots the product's cost. Zero here
            // is a placeholder, and the order carries PENDING_CALCULATION so
            // no profit figure is derived from it.
            'unit_cost_snapshot_paisa': 0,
            'discount_paisa': item['discount_paisa'] ?? 0,
            'line_total_paisa':
                ((item['unit_price_paisa'] as int? ?? 0) *
                    (item['quantity'] as int? ?? 1)) -
                (item['discount_paisa'] as int? ?? 0),
            'note': item['note'],
          },
      ],
    };

    final tenant = tenantId;
    if (tenant != null) {
      await db.putOrders(<CachedOrdersCompanion>[
        _companion(tenant, json, LocalSyncState.localOnly),
      ]);
    }
    return SellerOrder.fromJson(json);
  }

  Future<SellerOrder?> _patchMirror(
    String id,
    Map<String, dynamic> body,
  ) async {
    final tenant = tenantId;
    if (tenant == null) {
      return null;
    }
    final row = await db.localOrder(tenant, id);
    if (row == null) {
      return null;
    }
    final json = <String, dynamic>{
      ...jsonDecode(row.payload) as Map<String, dynamic>,
      ...body,
      'updated_at': DateTime.now().toUtc().toIso8601String(),
    };
    // `items` on the wire is a create/update payload, not the response shape;
    // leave the mirrored items alone until the server answers.
    json['items'] = (jsonDecode(row.payload) as Map<String, dynamic>)['items'];
    await db.putOrders(<CachedOrdersCompanion>[
      _companion(tenant, json, LocalSyncState.localOnly),
    ]);
    return SellerOrder.fromJson(json);
  }

  /// The Dhaka business date for a UTC instant.
  ///
  /// Mirrors `business_date()` on the server: an order taken at 01:30 Dhaka
  /// belongs to that day, not to the previous UTC one (master spec section 69).
  DateTime _businessDate(DateTime utc) {
    final dhaka = utc.add(const Duration(hours: 6));
    return DateTime.utc(dhaka.year, dhaka.month, dhaka.day);
  }
}
