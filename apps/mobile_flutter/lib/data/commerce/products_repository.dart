import 'dart:convert';

import 'package:drift/drift.dart' show Value;

import '../../core/api/api_error.dart';
import '../local/database.dart';
import '../local/tables.dart';
import '../sync/outbox.dart';
import 'models.dart';
import 'repository_support.dart';

/// Products and the stock ledger.
///
/// Reads go through the local mirror so the catalogue survives a dead
/// connection; writes go to the server when there is one and to the outbox when
/// there is not. Nothing here computes a stock total: stock changes by
/// recording a movement, and the balance is whatever the server says it is
/// (master spec section 10.4).
class ProductsRepository extends CachingRepository {
  ProductsRepository({
    required super.api,
    required super.db,
    required this.outbox,
    required this.tenantId,
  });

  final OutboxWriter outbox;

  @override
  final String? tenantId;

  // --- reads ----------------------------------------------------------------

  Future<Sourced<PagedResult<Product>>> list({
    String? cursor,
    int limit = 30,
    String? search,
    bool includeArchived = false,
    bool lowStockOnly = false,
  }) async {
    try {
      final json = await api.get(
        '/products',
        query: pageQuery(
          cursor: cursor,
          limit: limit,
          extra: <String, dynamic>{
            'search': search,
            'include_archived': includeArchived,
            'low_stock_only': lowStockOnly,
          },
        ),
      );
      final page = parsePage<Product>(json, Product.fromJson);
      await _mirror(json['items'] as List<dynamic>? ?? const <dynamic>[]);
      return Sourced<PagedResult<Product>>.live(page, DateTime.now().toUtc());
    } on ApiError catch (error) {
      if (!error.isOffline) {
        rethrow;
      }
      return _fromMirror(
        error: error,
        search: search,
        includeArchived: includeArchived,
        lowStockOnly: lowStockOnly,
        limit: limit,
        cursor: cursor,
      );
    }
  }

  Future<Product> get(String id) async {
    try {
      final json = await api.get('/products/$id');
      await _mirror(<dynamic>[json]);
      return Product.fromJson(json);
    } on ApiError catch (error) {
      if (!error.isOffline) {
        rethrow;
      }
      final local = await _localById(id);
      if (local == null) {
        rethrow;
      }
      return local;
    }
  }

  /// The audit trail behind the stock number.
  ///
  /// Cached read-through, so a seller can still answer "where did my stock go?"
  /// offline — with the page labelled as of its fetch time.
  Future<Sourced<PagedResult<StockMovement>>> stockMovements(
    String productId, {
    String? cursor,
    int limit = 30,
    List<String>? reasons,
    String? variantId,
  }) async {
    final suffix = <String>[
      if (reasons != null && reasons.isNotEmpty) reasons.join(','),
      if (variantId != null) variantId,
      if (cursor != null) cursor,
    ].map((part) => '.$part').join();
    final sourced = await readThrough(
      'products.$productId.movements$suffix',
      () => api.get(
        '/products/$productId/stock-movements',
        query: pageQuery(
          cursor: cursor,
          limit: limit,
          extra: <String, dynamic>{
            // Repeated `reason=` parameters; the server ORs them.
            'reason': reasons,
            'variant_id': variantId,
          },
        ),
      ),
    );
    return sourced.map(
      (json) => parsePage<StockMovement>(json, StockMovement.fromJson),
    );
  }

  // --- writes ---------------------------------------------------------------

  Future<Product> create({
    required String name,
    String? sku,
    String? description,
    int costPaisa = 0,
    int sellingPricePaisa = 0,
    int openingStock = 0,
    int? lowStockThreshold,
  }) async {
    final body = <String, dynamic>{
      'name': name,
      if (sku != null && sku.isNotEmpty) 'sku': sku,
      if (description != null && description.isNotEmpty)
        'description': description,
      'cost_paisa': costPaisa,
      'default_selling_price_paisa': sellingPricePaisa,
      'opening_stock': openingStock,
      if (lowStockThreshold != null) 'low_stock_threshold': lowStockThreshold,
    };

    try {
      final json = await api.post('/products', body: body);
      await _mirror(<dynamic>[json]);
      return Product.fromJson(json);
    } on ApiError catch (error) {
      if (!error.isOffline) {
        rethrow;
      }
      return _queueCreate(body);
    }
  }

  Future<Product> update(
    String id, {
    String? name,
    String? sku,
    String? description,
    int? costPaisa,
    int? sellingPricePaisa,
    int? lowStockThreshold,
    bool? isActive,
    bool? archived,
  }) async {
    final body = <String, dynamic>{
      if (name != null) 'name': name,
      if (sku != null) 'sku': sku,
      if (description != null) 'description': description,
      if (costPaisa != null) 'cost_paisa': costPaisa,
      if (sellingPricePaisa != null)
        'default_selling_price_paisa': sellingPricePaisa,
      if (lowStockThreshold != null) 'low_stock_threshold': lowStockThreshold,
      if (isActive != null) 'is_active': isActive,
      if (archived != null) 'archived': archived,
    };

    try {
      final json = await api.patch('/products/$id', body: body);
      await _mirror(<dynamic>[json]);
      return Product.fromJson(json);
    } on ApiError catch (error) {
      if (!error.isOffline) {
        rethrow;
      }
      await outbox.enqueue(
        entityType: SyncEntityType.product,
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

  /// Record a manual stock correction.
  ///
  /// Appends a movement; it never sets a total. `allowNegative` has to be asked
  /// for explicitly, so a seller cannot drift into negative stock by accident
  /// (master spec section 76).
  ///
  /// Returns `null` when the adjustment was queued offline — there is no
  /// movement yet, because the ledger entry is the server's to write.
  Future<StockMovement?> adjustStock(
    String productId, {
    required int quantityDelta,
    String reason = 'MANUAL_ADJUSTMENT',
    String? note,
    bool allowNegative = false,
    String? variantId,
  }) async {
    final body = <String, dynamic>{
      'quantity_delta': quantityDelta,
      'reason': reason,
      if (note != null && note.isNotEmpty) 'note': note,
      'allow_negative': allowNegative,
      if (variantId != null) 'variant_id': variantId,
    };

    try {
      final json = await api.post(
        '/products/$productId/stock-adjustments',
        body: body,
        // A retried request records one movement, not two.
        idempotencyKey: api.newIdempotencyKey(),
      );
      // The cached balance is now stale; take the server's new one rather than
      // adding the delta locally.
      await get(productId);
      return StockMovement.fromJson(json);
    } on ApiError catch (error) {
      if (!error.isOffline) {
        rethrow;
      }
      final mutationId = outbox.newId();
      await outbox.enqueue(
        entityType: SyncEntityType.stockAdjustment,
        entityId: mutationId,
        operation: MutationOperation.create,
        payload: <String, dynamic>{'product_id': productId, ...body},
      );
      // Show the seller's own count immediately. The row is badged as not
      // synced, and the server's balance replaces it on the next sync — this is
      // a display value, never an input to a money calculation.
      await _applyLocalDelta(productId, quantityDelta);
      return null;
    }
  }

  /// Record new goods arriving. Online only: a restock is a ledger entry the
  /// server writes, and the unit cost may update the product's cost.
  Future<StockMovement> restock(
    String productId, {
    required int quantity,
    String? variantId,
    int? unitCostPaisa,
    bool updateCost = false,
    String? reference,
    String? note,
  }) async {
    final json = await api.post(
      '/products/$productId/restocks',
      body: <String, dynamic>{
        'quantity': quantity,
        if (variantId != null) 'variant_id': variantId,
        if (unitCostPaisa != null) 'unit_cost_paisa': unitCostPaisa,
        'update_cost': updateCost && unitCostPaisa != null,
        if (reference != null && reference.isNotEmpty) 'reference': reference,
        if (note != null && note.isNotEmpty) 'note': note,
      },
      idempotencyKey: api.newIdempotencyKey(),
    );
    await get(productId);
    return StockMovement.fromJson(json);
  }

  /// Add a variant ("Black / M"). Returns the product with all its variants.
  Future<Product> addVariant(
    String productId, {
    required String name,
    String? sku,
    int openingStock = 0,
    int? lowStockThreshold,
  }) async {
    final json = await api.post(
      '/products/$productId/variants',
      body: <String, dynamic>{
        'name': name,
        if (sku != null && sku.isNotEmpty) 'sku': sku,
        'opening_stock': openingStock,
        if (lowStockThreshold != null) 'low_stock_threshold': lowStockThreshold,
      },
    );
    await _mirror(<dynamic>[json]);
    return Product.fromJson(json);
  }

  // --- mirror ---------------------------------------------------------------

  Future<void> _mirror(List<dynamic> items) async {
    final tenant = tenantId;
    if (tenant == null || items.isEmpty) {
      return;
    }
    await db.putProducts(<CachedProductsCompanion>[
      for (final item in items)
        _companion(tenant, item as Map<String, dynamic>, LocalSyncState.synced),
    ]);
  }

  CachedProductsCompanion _companion(
    String tenant,
    Map<String, dynamic> json,
    LocalSyncState state,
  ) {
    return CachedProductsCompanion.insert(
      id: json['id'] as String,
      tenantId: tenant,
      payload: jsonEncode(json),
      updatedAt: DateTime.parse(
        (json['updated_at'] ?? json['created_at']) as String,
      ),
      name: json['name'] as String,
      createdAt: DateTime.parse(json['created_at'] as String),
      syncState: Value(state),
      sku: Value(json['sku'] as String?),
      stockOnHand: Value(json['stock_on_hand'] as int? ?? 0),
      isLowStock: Value(json['is_low_stock'] as bool? ?? false),
      isArchived: Value(json['is_archived'] as bool? ?? false),
    );
  }

  Future<Sourced<PagedResult<Product>>> _fromMirror({
    required ApiError error,
    required String? search,
    required bool includeArchived,
    required bool lowStockOnly,
    required int limit,
    required String? cursor,
  }) async {
    final tenant = tenantId;
    if (tenant == null) {
      throw error;
    }
    // Offline paging walks the mirror by offset; the cursor carries it.
    final offset = int.tryParse(cursor ?? '0') ?? 0;
    final rows = await db.localProducts(
      tenantId: tenant,
      search: search,
      includeArchived: includeArchived,
      lowStockOnly: lowStockOnly,
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
    return Sourced<PagedResult<Product>>(
      value: PagedResult<Product>(
        items: <Product>[for (final row in visible) _decode(row)],
        nextCursor: hasMore ? '${offset + limit}' : null,
        hasMore: hasMore,
      ),
      origin: DataOrigin.cache,
      fetchedAt: newestOf(visible.map((row) => row.updatedAt)),
      error: error,
    );
  }

  Product _decode(CachedProduct row) =>
      Product.fromJson(jsonDecode(row.payload) as Map<String, dynamic>);

  Future<Product?> _localById(String id) async {
    final tenant = tenantId;
    if (tenant == null) {
      return null;
    }
    final row = await db.localProduct(tenant, id);
    return row == null ? null : _decode(row);
  }

  Future<Product> _queueCreate(Map<String, dynamic> body) async {
    final tenant = tenantId;
    final id = outbox.newId();
    await outbox.enqueue(
      entityType: SyncEntityType.product,
      entityId: id,
      operation: MutationOperation.create,
      payload: body,
    );

    final now = DateTime.now().toUtc();
    final json = <String, dynamic>{
      ...body,
      'id': id,
      'stock_on_hand': body['opening_stock'] ?? 0,
      'stock_tracking_enabled': true,
      'is_low_stock': false,
      'is_active': true,
      'is_archived': false,
      'created_at': now.toIso8601String(),
      'updated_at': now.toIso8601String(),
    };
    if (tenant != null) {
      await db.putProducts(<CachedProductsCompanion>[
        _companion(tenant, json, LocalSyncState.localOnly),
      ]);
    }
    return Product.fromJson(json);
  }

  Future<Product?> _patchMirror(String id, Map<String, dynamic> body) async {
    final tenant = tenantId;
    if (tenant == null) {
      return null;
    }
    final row = await db.localProduct(tenant, id);
    if (row == null) {
      return null;
    }
    final json = <String, dynamic>{
      ...jsonDecode(row.payload) as Map<String, dynamic>,
      ...body,
      'updated_at': DateTime.now().toUtc().toIso8601String(),
    };
    if (body['archived'] == true) {
      json['is_archived'] = true;
    }
    await db.putProducts(<CachedProductsCompanion>[
      _companion(tenant, json, LocalSyncState.localOnly),
    ]);
    return Product.fromJson(json);
  }

  Future<void> _applyLocalDelta(String productId, int delta) async {
    final tenant = tenantId;
    if (tenant == null) {
      return;
    }
    final row = await db.localProduct(tenant, productId);
    if (row == null) {
      return;
    }
    final json = jsonDecode(row.payload) as Map<String, dynamic>;
    json['stock_on_hand'] = (json['stock_on_hand'] as int? ?? 0) + delta;
    await db.putProducts(<CachedProductsCompanion>[
      _companion(tenant, json, LocalSyncState.localOnly),
    ]);
  }
}
