import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../data/commerce/list_controllers.dart';
import '../../data/commerce/models.dart';
import '../../design/components/badges.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
import '../shared/responsive.dart';
import 'product_form_screen.dart';
import 'stock_history_screen.dart';

/// Products and stock.
///
/// Every figure here comes from the server. The margin shown on a row is
/// `price − cost` and is labelled as a margin, not as profit: profit needs a
/// settled delivery, a courier fee and a return outcome, none of which exist
/// yet for an unshipped product (master spec sections 85, 135).
class ProductsScreen extends ConsumerStatefulWidget {
  const ProductsScreen({super.key});

  static const String routeName = '/products';

  @override
  ConsumerState<ProductsScreen> createState() => _ProductsScreenState();
}

class _ProductsScreenState extends ConsumerState<ProductsScreen> {
  final TextEditingController _search = TextEditingController();

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _openForm({Product? product}) async {
    final saved = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => ProductFormScreen(product: product),
      ),
    );
    if (saved ?? false) {
      await ref.read(productListProvider.notifier).refresh();
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(productListProvider);
    final controller = ref.read(productListProvider.notifier);
    final isOffline = ref.watch(isOfflineProvider);

    return Scaffold(
      backgroundColor: EcomsbdColors.background,
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _openForm,
        backgroundColor: EcomsbdColors.orange,
        foregroundColor: Colors.white,
        icon: const Icon(Icons.add_rounded),
        label: const Text('Add product'),
      ),
      body: EcomsbdBackground(
        child: SafeArea(
          child: ContentWidthLimit(
            child: RefreshIndicator(
              edgeOffset: EcomsbdLayout.pushedRefreshOffset,
              onRefresh: controller.refresh,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(
                  EcomsbdSpacing.page,
                  EcomsbdLayout.pushedTopPadding,
                  EcomsbdSpacing.page,
                  EcomsbdSpacing.bottomNavClearance,
                ),
                children: <Widget>[
                  Row(
                    children: <Widget>[
                      IconButton(
                        onPressed: () => Navigator.of(context).maybePop(),
                        icon: const Icon(Icons.arrow_back_rounded),
                        tooltip: 'Back',
                      ),
                      const Expanded(
                        child: PageHeader(
                          eyebrow: 'Cost · margin · inventory',
                          title: 'Products',
                          description:
                              'Stock changes by recording a movement, never by '
                              'typing over a total.',
                        ),
                      ),
                    ],
                  ),
                  if (isOffline) ...<Widget>[
                    const OfflineBanner(),
                    const SizedBox(height: EcomsbdSpacing.sm),
                  ],
                  if (state.isStale) ...<Widget>[
                    StaleDataNotice(
                      fetchedAt: state.fetchedAt,
                      onRetry: controller.refresh,
                    ),
                    const SizedBox(height: EcomsbdSpacing.sm),
                  ],
                  CommerceSearchField(
                    controller: _search,
                    hint: 'Search by name or SKU',
                    onChanged: controller.setSearch,
                  ),
                  const SizedBox(height: EcomsbdSpacing.sm),
                  _Filters(
                    lowStockOnly: controller.lowStockOnly,
                    includeArchived: controller.includeArchived,
                    onLowStock: controller.setLowStockOnly,
                    onArchived: controller.setIncludeArchived,
                  ),
                  const SizedBox(height: EcomsbdSpacing.sm),
                  PagedListBody<Product>(
                    state: state,
                    onRetry: controller.refresh,
                    onLoadMore: controller.loadMore,
                    emptyIcon: Icons.inventory_2_outlined,
                    emptyTitle: 'No products yet',
                    emptyMessage:
                        'Add what you sell, with its cost, so profit can be '
                        'worked out later from real numbers.',
                    emptyActionLabel: 'Add your first product',
                    onEmptyAction: _openForm,
                    itemBuilder: (context, product) => ProductRow(
                      product: product,
                      onTap: () => _openForm(product: product),
                      onHistory: () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) => StockHistoryScreen(product: product),
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// One product row.
class ProductRow extends StatelessWidget {
  const ProductRow({
    required this.product,
    super.key,
    this.onTap,
    this.onHistory,
  });

  final Product product;
  final VoidCallback? onTap;
  final VoidCallback? onHistory;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      onTap: onTap,
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    Text(
                      product.name,
                      style: EcomsbdType.bodyStrong,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    if (product.sku != null) ...<Widget>[
                      const SizedBox(height: 2),
                      Text(
                        product.sku!,
                        style: EcomsbdType.caption.copyWith(
                          color: EcomsbdColors.muted2,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(width: EcomsbdSpacing.xs),
              if (product.isArchived)
                const StatusChip(label: 'Archived', tone: Tone.neutral)
              else if (product.isLowStock)
                const StatusChip(
                  label: 'Low stock',
                  tone: Tone.warning,
                  icon: Icons.trending_down_rounded,
                ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          ResponsiveGrid(
            minTileWidth: 96,
            maxColumns: 4,
            spacing: EcomsbdSpacing.xs,
            children: <Widget>[
              _Fact(label: 'In stock', value: '${product.stockOnHand}'),
              _Fact(label: 'Cost', value: product.cost.format()),
              _Fact(label: 'Price', value: product.sellingPrice.format()),
              _Fact(
                label: 'Margin',
                value: product.margin.format(),
                tone: product.margin.isNegative ? Tone.bad : null,
              ),
            ],
          ),
          if (onHistory != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xs),
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton.icon(
                onPressed: onHistory,
                icon: const Icon(Icons.receipt_long_outlined, size: 16),
                label: const Text('Stock history'),
                style: TextButton.styleFrom(
                  foregroundColor: EcomsbdColors.orange,
                  minimumSize: const Size(0, EcomsbdTouch.minTarget),
                  padding: const EdgeInsets.symmetric(horizontal: 6),
                  textStyle: EcomsbdType.chip,
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _Fact extends StatelessWidget {
  const _Fact({required this.label, required this.value, this.tone});

  final String label;
  final String value;
  final Tone? tone;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(9),
      decoration: BoxDecoration(
        color: EcomsbdColors.miniTile,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Text(
            label.toUpperCase(),
            style: EcomsbdType.eyebrow.copyWith(color: EcomsbdColors.muted2),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          const SizedBox(height: 3),
          FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.centerLeft,
            child: Text(
              value,
              style: EcomsbdType.bodyStrong.copyWith(color: tone?.ink),
            ),
          ),
        ],
      ),
    );
  }
}

class _Filters extends StatelessWidget {
  const _Filters({
    required this.lowStockOnly,
    required this.includeArchived,
    required this.onLowStock,
    required this.onArchived,
  });

  final bool lowStockOnly;
  final bool includeArchived;
  final ValueChanged<bool> onLowStock;
  final ValueChanged<bool> onArchived;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: EcomsbdSpacing.xs,
      runSpacing: EcomsbdSpacing.xs,
      children: <Widget>[
        FilterToggle(
          label: 'Low stock only',
          selected: lowStockOnly,
          onChanged: onLowStock,
        ),
        FilterToggle(
          label: 'Include archived',
          selected: includeArchived,
          onChanged: onArchived,
        ),
      ],
    );
  }
}
