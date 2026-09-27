import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'commerce_providers.dart';
import 'customers_repository.dart';
import 'models.dart';
import 'orders_repository.dart';
import 'paged_list_controller.dart';
import 'products_repository.dart';
import 'repository_support.dart';

/// The list controllers behind the Products, Customers and Orders screens.
///
/// Each owns its filters and re-queries the repository when they change. The
/// filters live here rather than in the widget so switching tabs does not throw
/// away what the seller was looking at.

// --------------------------------------------------------------------------- //
// Products
// --------------------------------------------------------------------------- //

class ProductListController extends PagedListController<Product> {
  ProductListController(this._repository) {
    refresh();
  }

  final ProductsRepository _repository;

  String _search = '';
  bool _lowStockOnly = false;
  bool _includeArchived = false;

  String get search => _search;
  bool get lowStockOnly => _lowStockOnly;
  bool get includeArchived => _includeArchived;

  @override
  Future<Sourced<PagedResult<Product>>> fetchPage({String? cursor}) {
    return _repository.list(
      cursor: cursor,
      search: _search.isEmpty ? null : _search,
      lowStockOnly: _lowStockOnly,
      includeArchived: _includeArchived,
    );
  }

  void setSearch(String value) {
    _search = value.trim();
    refreshDebounced();
  }

  void setLowStockOnly(bool value) {
    _lowStockOnly = value;
    refresh();
  }

  void setIncludeArchived(bool value) {
    _includeArchived = value;
    refresh();
  }
}

final productListProvider =
    StateNotifierProvider<ProductListController, PagedListState<Product>>((
      ref,
    ) {
      return ProductListController(ref.watch(productsRepositoryProvider));
    });

/// One product, for the detail and edit screens.
final productProvider = FutureProvider.family<Product, String>((ref, id) {
  return ref.watch(productsRepositoryProvider).get(id);
});

/// A product's stock movement history.
final stockMovementsProvider =
    FutureProvider.family<Sourced<PagedResult<StockMovement>>, String>((
      ref,
      productId,
    ) {
      return ref.watch(productsRepositoryProvider).stockMovements(productId);
    });

/// Movement-type filters on the stock history screen, and the ledger reasons
/// each one covers.
const Map<String, List<String>> stockHistoryFilters = <String, List<String>>{
  'all': <String>[],
  'sales': <String>['BOOKED_DECREMENT', 'BOOKED_RESERVE'],
  'returns': <String>[
    'RETURN_RESTORE',
    'PARTIAL_RETURN_RESTORE',
    'CANCEL_RESTORE',
  ],
  'restock': <String>['RESTOCK', 'OPENING'],
  'adjust': <String>[
    'MANUAL_ADJUSTMENT',
    'DAMAGED_WRITE_OFF',
    'IMPORT_ADJUSTMENT',
    'EXTERNAL_SYNC',
  ],
};

/// A product's stock history under one filter. Keyed by the filter's name so
/// the family argument compares by value.
final stockHistoryProvider =
    FutureProvider.family<
      Sourced<PagedResult<StockMovement>>,
      ({String productId, String filter})
    >((ref, query) {
      final reasons = stockHistoryFilters[query.filter] ?? const <String>[];
      return ref
          .watch(productsRepositoryProvider)
          .stockMovements(
            query.productId,
            reasons: reasons.isEmpty ? null : reasons,
          );
    });

// --------------------------------------------------------------------------- //
// Customers
// --------------------------------------------------------------------------- //

class CustomerListController extends PagedListController<Customer> {
  CustomerListController(this._repository) {
    refresh();
  }

  final CustomersRepository _repository;

  String _search = '';
  bool _repeatOnly = false;
  String? _flag;
  String? _segment;
  String? _tagId;
  String? get segment => _segment;
  String? get tagId => _tagId;

  void setSegment(String? value) {
    _segment = value;
    refresh();
  }

  void setTag(String? value) {
    _tagId = value;
    refresh();
  }

  String get search => _search;
  bool get repeatOnly => _repeatOnly;
  String? get flag => _flag;

  @override
  Future<Sourced<PagedResult<Customer>>> fetchPage({String? cursor}) {
    return _repository.list(
      cursor: cursor,
      search: _search.isEmpty ? null : _search,
      repeatOnly: _repeatOnly,
      flag: _flag,
      segment: _segment,
      tagId: _tagId,
    );
  }

  void setSearch(String value) {
    _search = value.trim();
    refreshDebounced();
  }

  void setRepeatOnly(bool value) {
    _repeatOnly = value;
    refresh();
  }

  void setFlag(String? value) {
    _flag = value;
    refresh();
  }
}

final customerListProvider =
    StateNotifierProvider<CustomerListController, PagedListState<Customer>>((
      ref,
    ) {
      return CustomerListController(ref.watch(customersRepositoryProvider));
    });

final customerProvider = FutureProvider.family<Customer, String>((ref, id) {
  return ref.watch(customersRepositoryProvider).get(id);
});

// --------------------------------------------------------------------------- //
// Orders
// --------------------------------------------------------------------------- //

/// The seller-facing order filters, over the server's own statuses.
///
/// The status values are the API's and are not changed; a group only decides
/// which of them a chip shows. Groups may overlap: "Action needed" is every
/// order the seller still has to move before it reaches a courier.
enum OrderFilterGroup {
  all(<String>{}),
  actionNeeded(<String>{'DRAFT', 'CONFIRMED', 'PACKED'}),
  confirmed(<String>{'CONFIRMED', 'PACKED'}),
  courier(<String>{'FULFILLMENT_STARTED'}),
  delivered(<String>{'COMPLETED'});

  const OrderFilterGroup(this.statuses);

  final Set<String> statuses;
}

class OrderListController extends PagedListController<SellerOrder> {
  OrderListController(this._repository) {
    refresh();
  }

  final OrdersRepository _repository;

  String _search = '';
  String? _status;
  OrderFilterGroup _group = OrderFilterGroup.all;
  String? _customerId;

  String get search => _search;
  String? get status => _status;
  OrderFilterGroup get group => _group;

  @override
  Future<Sourced<PagedResult<SellerOrder>>> fetchPage({String? cursor}) async {
    final statuses = _status != null ? <String>{_status!} : _group.statuses;
    if (statuses.length <= 1) {
      return _repository.list(
        cursor: cursor,
        search: _search.isEmpty ? null : _search,
        status: statuses.isEmpty ? null : statuses.first,
        customerId: _customerId,
      );
    }
    // The server filters on one status at a time, so a group of several is
    // filtered here — reading on a few pages when one has nothing to show.
    var next = cursor;
    for (var round = 0; ; round++) {
      final page = await _repository.list(
        cursor: next,
        search: _search.isEmpty ? null : _search,
        customerId: _customerId,
      );
      final kept = page.value.items
          .where((order) => statuses.contains(order.status))
          .toList();
      final more = page.value.hasMore && page.value.nextCursor != null;
      if (kept.isNotEmpty || !more || round >= 4) {
        return page.map(
          (result) => PagedResult<SellerOrder>(
            items: kept,
            nextCursor: result.nextCursor,
            hasMore: result.hasMore,
          ),
        );
      }
      next = page.value.nextCursor;
    }
  }

  void setSearch(String value) {
    _search = value.trim();
    refreshDebounced();
  }

  void setStatus(String? value) {
    _status = value;
    refresh();
  }

  void setGroup(OrderFilterGroup value) {
    _status = null;
    _group = value;
    refresh();
  }

  void setCustomer(String? customerId) {
    _customerId = customerId;
    refresh();
  }
}

final orderListProvider =
    StateNotifierProvider<OrderListController, PagedListState<SellerOrder>>((
      ref,
    ) {
      return OrderListController(ref.watch(ordersRepositoryProvider));
    });

final orderProvider = FutureProvider.family<SellerOrder, String>((ref, id) {
  return ref.watch(ordersRepositoryProvider).get(id);
});

/// A customer's recent orders, for the history preview on the order screen.
final customerOrdersProvider = FutureProvider.family<List<SellerOrder>, String>(
  (ref, customerId) async {
    final page = await ref
        .watch(ordersRepositoryProvider)
        .list(customerId: customerId, limit: 5);
    return page.value.items;
  },
);
