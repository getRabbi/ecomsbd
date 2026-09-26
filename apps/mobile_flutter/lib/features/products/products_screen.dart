import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/commerce/list_controllers.dart';
import '../../data/commerce/models.dart';
import '../../design/components/badges.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
import '../shared/responsive.dart';
import 'product_form_screen.dart';
import 'stock_history_screen.dart';
import '../../l10n/app_strings.dart';

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

    return Scaffold(
      backgroundColor: EcomsbdColors.background,
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _openForm,
        backgroundColor: EcomsbdColors.orange,
        foregroundColor: Colors.white,
        icon: const Icon(Icons.add_rounded),
        label: Text(context.tr('prod.add')),
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
                        tooltip: context.tr('common.back'),
                      ),
                      Expanded(
                        child: PageHeader(
                          eyebrow: context.tr('prod.eyebrow'),
                          title: context.tr('entity.products'),
                          description: context.tr('prod.description'),
                        ),
                      ),
                    ],
                  ),
                  if (state.isStale) ...<Widget>[
                    StaleDataNotice(
                      fetchedAt: state.fetchedAt,
                      onRetry: controller.refresh,
                    ),
                    const SizedBox(height: EcomsbdSpacing.sm),
                  ],
                  CommerceSearchField(
                    controller: _search,
                    hint: context.tr('prod.searchHint'),
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
                    emptyTitle: context.tr('prod.emptyTitle'),
                    emptyMessage: context.tr('prod.emptyBody'),
                    emptyActionLabel: context.tr('prod.emptyAction'),
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
                StatusChip(
                  label: context.tr('common.archived'),
                  tone: Tone.neutral,
                )
              else if (product.isLowStock)
                StatusChip(
                  label: context.tr('common.lowStock'),
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
              _Fact(
                label: context.tr('common.inStock'),
                value: '${product.stockOnHand}',
              ),
              _Fact(
                label: context.tr('imp.colCost'),
                value: product.cost.format(),
              ),
              _Fact(
                label: context.tr('common.price'),
                value: product.sellingPrice.format(),
              ),
              _Fact(
                label: context.tr('common.margin'),
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
                label: Text(context.tr('sh.title')),
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
          label: context.tr('prod.lowStockOnly'),
          selected: lowStockOnly,
          onChanged: onLowStock,
        ),
        FilterToggle(
          label: context.tr('prod.includeArchived'),
          selected: includeArchived,
          onChanged: onArchived,
        ),
      ],
    );
  }
}
