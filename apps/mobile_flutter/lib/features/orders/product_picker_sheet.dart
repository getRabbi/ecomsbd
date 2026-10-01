import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/models.dart';
import '../../design/components/navigation.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../products/stock_adjustment_sheet.dart' show VariantPicker;
import '../shared/inputs.dart';

/// A product (and its variant) chosen from the shop's catalogue.
typedef PickedProduct = ({Product product, ProductVariant? variant});

/// Choose which catalogue product an order line is, and for a product with
/// sizes or colours, which one.
///
/// [suggestions] are the products the chat parser could not choose between;
/// they are offered first, never picked for the seller.
class ProductPickerSheet extends ConsumerStatefulWidget {
  const ProductPickerSheet({
    super.key,
    this.suggestions = const <({String id, String name})>[],
    this.initialQuery = '',
  });

  final List<({String id, String name})> suggestions;
  final String initialQuery;

  static Future<PickedProduct?> show(
    BuildContext context, {
    List<({String id, String name})> suggestions =
        const <({String id, String name})>[],
    String initialQuery = '',
  }) => GlassBottomSheet.show<PickedProduct>(
    context: context,
    title: context.tr('cho.pickProduct'),
    child: ProductPickerSheet(
      suggestions: suggestions,
      initialQuery: initialQuery,
    ),
  );

  @override
  ConsumerState<ProductPickerSheet> createState() => _ProductPickerState();
}

class _ProductPickerState extends ConsumerState<ProductPickerSheet> {
  late final TextEditingController _query = TextEditingController(
    text: widget.initialQuery,
  );
  Timer? _debounce;
  List<Product>? _results;
  Product? _chosen;
  bool _loading = false;
  ApiError? _error;

  @override
  void initState() {
    super.initState();
    if (widget.suggestions.isEmpty) {
      Future<void>.microtask(_search);
    }
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _query.dispose();
    super.dispose();
  }

  Future<void> _search() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final page = await ref
          .read(productsRepositoryProvider)
          .list(search: _query.text.trim(), limit: 20);
      if (mounted) setState(() => _results = page.value.items);
    } on ApiError catch (error) {
      if (mounted) setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _choose(String id) async {
    setState(() => _loading = true);
    try {
      final product = await ref.read(productsRepositoryProvider).get(id);
      _pick(product);
    } on ApiError catch (error) {
      if (mounted) setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  void _pick(Product product) {
    if (product.hasVariants && product.activeVariants.length > 1) {
      setState(() => _chosen = product);
      return;
    }
    final only = product.activeVariants.isEmpty
        ? null
        : product.activeVariants.first;
    Navigator.of(context).pop<PickedProduct>((product: product, variant: only));
  }

  @override
  Widget build(BuildContext context) {
    final chosen = _chosen;
    if (chosen != null) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Text(chosen.name, style: EcomsbdType.bodyStrong),
          const SizedBox(height: EcomsbdSpacing.sm),
          VariantPicker(
            variants: chosen.activeVariants,
            selected: null,
            onChanged: (variant) => Navigator.of(
              context,
            ).pop<PickedProduct>((product: chosen, variant: variant)),
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          TextButton(
            onPressed: () => setState(() => _chosen = null),
            child: Text(context.tr('common.back')),
          ),
        ],
      );
    }
    final results = _results;
    // The frosted sheet paints its own background; list rows need a Material
    // of their own for their ink to show.
    return Material(
      type: MaterialType.transparency,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          if (widget.suggestions.isNotEmpty) ...<Widget>[
            Text(
              context.tr('cho.suggested'),
              style: EcomsbdType.eyebrow.copyWith(color: EcomsbdColors.muted2),
            ),
            const SizedBox(height: 6),
            for (final suggestion in widget.suggestions)
              ListTile(
                key: ValueKey('pick-suggestion-${suggestion.id}'),
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.inventory_2_outlined),
                title: Text(suggestion.name),
                onTap: _loading
                    ? null
                    : () => unawaited(_choose(suggestion.id)),
              ),
            const Divider(),
          ],
          LabelledField(
            label: context.tr('cho.searchCatalogue'),
            controller: _query,
            onChanged: (_) {
              _debounce?.cancel();
              _debounce = Timer(const Duration(milliseconds: 350), _search);
            },
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          if (_loading) const LinearProgressIndicator(),
          if (_error != null)
            Text(
              _error!.displayMessage,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.red),
            ),
          if (results != null && results.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: EcomsbdSpacing.sm),
              child: Text(
                context.tr('cho.noProducts'),
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              ),
            ),
          for (final product in results ?? const <Product>[])
            ListTile(
              key: ValueKey('pick-product-${product.id}'),
              contentPadding: EdgeInsets.zero,
              title: Text(product.name),
              subtitle: product.hasVariants
                  ? Text(
                      context.tr('cho.variantCount', <String, Object?>{
                        'count': product.activeVariants.length,
                      }),
                    )
                  : Text(product.sellingPrice.format()),
              onTap: () => _pick(product),
            ),
        ],
      ),
    );
  }
}
