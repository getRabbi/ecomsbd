import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/models.dart';
import '../../data/commerce/repository_support.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../customers/customer_detail_screen.dart';
import '../customers/customers_screen.dart';
import '../orders/order_detail_screen.dart';
import '../orders/orders_screen.dart';
import '../products/product_form_screen.dart';
import '../products/products_screen.dart';
import '../products/stock_history_screen.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';

/// One search box for the whole shop: orders, customers and products at once.
///
/// A seller on the phone with a buyer has a name, a phone number or an order
/// number and needs the record, not a guess about which list it lives in. Each
/// section asks the same endpoint its own list screen uses, so a match here is
/// a match there, and each falls back to what is stored on this phone when the
/// connection is gone.
class GlobalSearchScreen extends ConsumerStatefulWidget {
  const GlobalSearchScreen({super.key});

  /// Results shown per section. Search is for finding one record; the full,
  /// filterable list is on its own screen.
  static const int perSection = 5;

  /// Shorter queries match most of the shop and are not worth a round trip.
  static const int minQueryLength = 2;

  /// Same pause the list screens use, so typing does not fire three requests
  /// per keystroke.
  static const Duration debounce = Duration(milliseconds: 300);

  @override
  ConsumerState<GlobalSearchScreen> createState() => _GlobalSearchScreenState();
}

class _GlobalSearchScreenState extends ConsumerState<GlobalSearchScreen> {
  final TextEditingController _search = TextEditingController();
  Timer? _debounce;

  /// Bumped on every new query, so a slow answer to an old query cannot
  /// replace the results of the one the seller is looking at.
  int _generation = 0;
  String _query = '';
  bool _loading = false;
  _Results? _results;

  @override
  void dispose() {
    _debounce?.cancel();
    _search.dispose();
    super.dispose();
  }

  void _onChanged(String value) {
    _debounce?.cancel();
    final query = value.trim();
    if (query.length < GlobalSearchScreen.minQueryLength) {
      _generation++;
      setState(() {
        _query = query;
        _loading = false;
        _results = null;
      });
      return;
    }
    setState(() => _loading = true);
    _debounce = Timer(GlobalSearchScreen.debounce, () => _run(query));
  }

  Future<void> _run(String query) async {
    final generation = ++_generation;
    const limit = GlobalSearchScreen.perSection;
    // Started together and awaited together: one wait for three sections, and
    // a failure in one section does not hide the other two.
    final orders = AsyncValue.guard(
      () =>
          ref.read(ordersRepositoryProvider).list(search: query, limit: limit),
    );
    final customers = AsyncValue.guard(
      () => ref
          .read(customersRepositoryProvider)
          .list(search: query, limit: limit),
    );
    final products = AsyncValue.guard(
      () => ref
          .read(productsRepositoryProvider)
          .list(search: query, limit: limit),
    );
    final results = _Results(
      orders: await orders,
      customers: await customers,
      products: await products,
    );
    if (!mounted || generation != _generation) {
      return;
    }
    setState(() {
      _query = query;
      _loading = false;
      _results = results;
    });
  }

  void _open(Widget screen) {
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => screen));
  }

  Future<void> _openProduct(Product product) async {
    final saved = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => ProductFormScreen(product: product),
      ),
    );
    if ((saved ?? false) && mounted) {
      await _run(_query);
    }
  }

  @override
  Widget build(BuildContext context) {
    return DetailScaffold(
      title: 'Search',
      children: <Widget>[
        CommerceSearchField(
          controller: _search,
          hint: 'Order number, name, phone or product',
          onChanged: _onChanged,
          autofocus: true,
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        ..._body(),
      ],
    );
  }

  List<Widget> _body() {
    final results = _results;
    if (results == null) {
      if (_loading) {
        return <Widget>[
          for (var i = 0; i < 3; i++) ...<Widget>[
            SkeletonLoader.card(height: 96),
            const SizedBox(height: EcomsbdSpacing.sm),
          ],
        ];
      }
      return const <Widget>[
        EmptyState(
          icon: Icons.search_rounded,
          title: 'Search your shop',
          message:
              'Find an order by its number, a customer by name or phone — '
              'the last 4 digits are enough — or a product by name or SKU.',
        ),
      ];
    }

    final orders =
        results.orders.valueOrNull?.value.items ?? const <SellerOrder>[];
    final customers =
        results.customers.valueOrNull?.value.items ?? const <Customer>[];
    final products =
        results.products.valueOrNull?.value.items ?? const <Product>[];
    final failed = <String>[
      if (results.orders.hasError) 'orders',
      if (results.customers.hasError) 'customers',
      if (results.products.hasError) 'products',
    ];
    final stale = <Sourced<dynamic>?>[
      results.orders.valueOrNull,
      results.customers.valueOrNull,
      results.products.valueOrNull,
    ].whereType<Sourced<dynamic>>().where((sourced) => sourced.isStale);

    if (orders.isEmpty &&
        customers.isEmpty &&
        products.isEmpty &&
        failed.isEmpty) {
      return <Widget>[
        if (_loading) const _Working(),
        EmptyState(
          icon: Icons.search_off_rounded,
          title: 'Nothing matches “$_query”',
          message:
              'Check the spelling, or try the last 4 digits of the phone '
              'number.',
        ),
      ];
    }

    return <Widget>[
      if (_loading) const _Working(),
      if (stale.isNotEmpty) ...<Widget>[
        StaleDataNotice(
          fetchedAt: stale.first.fetchedAt,
          onRetry: () => _run(_query),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
      ],
      if (failed.isNotEmpty) ...<Widget>[
        Text(
          'Could not search ${failed.join(' or ')} just now.',
          style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
      ],
      if (orders.isNotEmpty) ...<Widget>[
        const SectionHeader(title: 'Orders'),
        const SizedBox(height: EcomsbdSpacing.xs),
        for (final order in orders) ...<Widget>[
          SellerOrderCard(
            order: order,
            onTap: () => _open(OrderDetailScreen(orderId: order.id)),
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
        ],
      ],
      if (customers.isNotEmpty) ...<Widget>[
        const SectionHeader(title: 'Customers'),
        const SizedBox(height: EcomsbdSpacing.xs),
        for (final customer in customers) ...<Widget>[
          CustomerRow(
            customer: customer,
            onTap: () => _open(CustomerDetailScreen(customerId: customer.id)),
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
        ],
      ],
      if (products.isNotEmpty) ...<Widget>[
        const SectionHeader(title: 'Products'),
        const SizedBox(height: EcomsbdSpacing.xs),
        for (final product in products) ...<Widget>[
          ProductRow(
            product: product,
            onTap: () => _openProduct(product),
            onHistory: () => _open(StockHistoryScreen(product: product)),
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
        ],
      ],
    ];
  }
}

class _Results {
  const _Results({
    required this.orders,
    required this.customers,
    required this.products,
  });

  final AsyncValue<Sourced<PagedResult<SellerOrder>>> orders;
  final AsyncValue<Sourced<PagedResult<Customer>>> customers;
  final AsyncValue<Sourced<PagedResult<Product>>> products;
}

/// A thin bar while a newer query runs, so the results already on screen stay
/// readable instead of flashing back to skeletons on every keystroke.
class _Working extends StatelessWidget {
  const _Working();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.only(bottom: EcomsbdSpacing.sm),
      child: LinearProgressIndicator(minHeight: 2),
    );
  }
}
