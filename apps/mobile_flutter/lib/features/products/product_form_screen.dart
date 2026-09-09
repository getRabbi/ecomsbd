import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/models.dart';
import '../../design/components/badges.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
import 'stock_adjustment_sheet.dart';

/// Add or edit a product.
///
/// Cost is asked for on the same screen as price and is not optional-by-accident:
/// every profit figure this app will ever show depends on it, and a catalogue
/// entered without costs produces a profit report that is confidently wrong
/// (master spec section 135).
///
/// Stock is only editable here when the product is being created. After that it
/// changes by recording a movement, so there is always a reason and an audit
/// trail behind the number (section 10.4).
class ProductFormScreen extends ConsumerStatefulWidget {
  const ProductFormScreen({super.key, this.product});

  final Product? product;

  bool get isEditing => product != null;

  @override
  ConsumerState<ProductFormScreen> createState() => _ProductFormScreenState();
}

class _ProductFormScreenState extends ConsumerState<ProductFormScreen> {
  final GlobalKey<FormState> _form = GlobalKey<FormState>();

  late final TextEditingController _name = TextEditingController(
    text: widget.product?.name ?? '',
  );
  late final TextEditingController _sku = TextEditingController(
    text: widget.product?.sku ?? '',
  );
  late final TextEditingController _cost = TextEditingController(
    text: _takaText(widget.product?.cost),
  );
  late final TextEditingController _price = TextEditingController(
    text: _takaText(widget.product?.sellingPrice),
  );
  late final TextEditingController _opening = TextEditingController(text: '0');
  late final TextEditingController _threshold = TextEditingController(
    text: widget.product?.lowStockThreshold?.toString() ?? '',
  );

  bool _saving = false;
  ApiError? _error;
  bool _queuedOffline = false;

  @override
  void dispose() {
    _name.dispose();
    _sku.dispose();
    _cost.dispose();
    _price.dispose();
    _opening.dispose();
    _threshold.dispose();
    super.dispose();
  }

  static String _takaText(Money? amount) {
    if (amount == null || amount.paisa == 0) {
      return '';
    }
    return (amount.paisa / 100).toStringAsFixed(
      amount.paisa % 100 == 0 ? 0 : 2,
    );
  }

  /// Taka text to integer paisa.
  ///
  /// Parsed as a decimal string and scaled by hand rather than through a
  /// `double`: `12.35 * 100` is 1234.9999999999998 in binary floating point,
  /// and money is never allowed to arrive at the server one paisa short.
  int? _paisa(String raw) {
    final text = normalizeDigits(raw).replaceAll(',', '').trim();
    if (text.isEmpty) {
      return 0;
    }
    final match = RegExp(r'^(\d+)(?:\.(\d{1,2}))?$').firstMatch(text);
    if (match == null) {
      return null;
    }
    final taka = int.parse(match.group(1)!);
    final fraction = (match.group(2) ?? '').padRight(2, '0');
    return taka * 100 + int.parse(fraction);
  }

  int? _count(String raw) {
    final text = normalizeDigits(raw).trim();
    if (text.isEmpty) {
      return 0;
    }
    return int.tryParse(text);
  }

  Future<void> _save() async {
    if (!(_form.currentState?.validate() ?? false)) {
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });

    final repository = ref.read(productsRepositoryProvider);
    try {
      if (widget.isEditing) {
        await repository.update(
          widget.product!.id,
          name: _name.text.trim(),
          sku: _sku.text.trim(),
          costPaisa: _paisa(_cost.text),
          sellingPricePaisa: _paisa(_price.text),
          lowStockThreshold: _count(_threshold.text),
        );
      } else {
        await repository.create(
          name: _name.text.trim(),
          sku: _sku.text.trim(),
          costPaisa: _paisa(_cost.text) ?? 0,
          sellingPricePaisa: _paisa(_price.text) ?? 0,
          openingStock: _count(_opening.text) ?? 0,
          lowStockThreshold: _count(_threshold.text),
        );
      }
      if (mounted) {
        Navigator.of(context).pop(true);
      }
    } on ApiError catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _saving = false;
        _error = error;
        _queuedOffline = error.isOffline;
      });
    }
  }

  Future<void> _adjustStock() async {
    final changed = await StockAdjustmentSheet.show(
      context,
      product: widget.product!,
    );
    if ((changed ?? false) && mounted) {
      Navigator.of(context).pop(true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final product = widget.product;
    final margin = (_paisa(_price.text) ?? 0) - (_paisa(_cost.text) ?? 0);

    return DetailScaffold(
      title: widget.isEditing ? 'Edit product' : 'New product',
      subtitle: product?.sku,
      children: <Widget>[
        Form(
          key: _form,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              if (_error != null && !_queuedOffline) ...<Widget>[
                ErrorStateCard(error: _error!),
                const SizedBox(height: EcomsbdSpacing.sm),
              ],
              GlassCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    LabelledField(
                      label: 'Product name',
                      controller: _name,
                      hint: 'Cotton Abaya',
                      validator: (value) =>
                          (value == null || value.trim().isEmpty)
                          ? 'A name is required'
                          : null,
                    ),
                    const SizedBox(height: EcomsbdSpacing.md),
                    LabelledField(
                      label: 'SKU or code',
                      controller: _sku,
                      hint: 'Optional',
                    ),
                  ],
                ),
              ),
              const SizedBox(height: EcomsbdSpacing.sm),
              GlassCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    const Text('Money', style: EcomsbdType.bodyStrong),
                    const SizedBox(height: 3),
                    Text(
                      'Cost is what you pay. Without it, no profit figure this '
                      'app shows can be trusted.',
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                    ),
                    const SizedBox(height: EcomsbdSpacing.md),
                    Row(
                      children: <Widget>[
                        Expanded(
                          child: LabelledField(
                            label: 'Cost (৳)',
                            controller: _cost,
                            keyboardType: const TextInputType.numberWithOptions(
                              decimal: true,
                            ),
                            inputFormatters: _moneyFormatters,
                            onChanged: (_) => setState(() {}),
                            validator: (value) => _paisa(value ?? '') == null
                                ? 'Enter an amount like 350 or 350.50'
                                : null,
                          ),
                        ),
                        const SizedBox(width: EcomsbdSpacing.sm),
                        Expanded(
                          child: LabelledField(
                            label: 'Selling price (৳)',
                            controller: _price,
                            keyboardType: const TextInputType.numberWithOptions(
                              decimal: true,
                            ),
                            inputFormatters: _moneyFormatters,
                            onChanged: (_) => setState(() {}),
                            validator: (value) => _paisa(value ?? '') == null
                                ? 'Enter an amount like 350 or 350.50'
                                : null,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: EcomsbdSpacing.md),
                    Row(
                      children: <Widget>[
                        Expanded(
                          child: Text(
                            'Margin per unit',
                            style: EcomsbdType.caption.copyWith(
                              color: EcomsbdColors.muted,
                            ),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        const SizedBox(width: EcomsbdSpacing.xs),
                        MoneyText(
                          Money(margin),
                          style: EcomsbdType.bodyStrong,
                          colorBySign: true,
                        ),
                      ],
                    ),
                    if (margin < 0)
                      Padding(
                        padding: const EdgeInsets.only(top: 4),
                        child: Text(
                          'Selling below cost. Usually a swapped column — check '
                          'before saving.',
                          style: EcomsbdType.caption.copyWith(
                            color: EcomsbdColors.red,
                          ),
                        ),
                      ),
                  ],
                ),
              ),
              const SizedBox(height: EcomsbdSpacing.sm),
              GlassCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    const Text('Stock', style: EcomsbdType.bodyStrong),
                    const SizedBox(height: 3),
                    Text(
                      widget.isEditing
                          ? 'Stock changes by recording a movement, so there is '
                                'always a reason behind the number.'
                          : 'Your opening count. Every later change is recorded '
                                'as a movement.',
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                    ),
                    const SizedBox(height: EcomsbdSpacing.md),
                    if (widget.isEditing)
                      Row(
                        children: <Widget>[
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              mainAxisSize: MainAxisSize.min,
                              children: <Widget>[
                                Text(
                                  'IN STOCK',
                                  style: EcomsbdType.eyebrow.copyWith(
                                    color: EcomsbdColors.muted2,
                                  ),
                                ),
                                Text(
                                  '${product!.stockOnHand}',
                                  style: EcomsbdType.sectionTitle,
                                ),
                              ],
                            ),
                          ),
                          FilledButton.tonalIcon(
                            onPressed: _adjustStock,
                            icon: const Icon(Icons.tune_rounded, size: 18),
                            label: const Text('Adjust'),
                            style: FilledButton.styleFrom(
                              minimumSize: const Size(
                                0,
                                EcomsbdTouch.minTarget,
                              ),
                              shape: const StadiumBorder(),
                              textStyle: EcomsbdType.label,
                            ),
                          ),
                        ],
                      )
                    else
                      LabelledField(
                        label: 'Opening stock',
                        controller: _opening,
                        keyboardType: TextInputType.number,
                        inputFormatters: _countFormatters,
                        validator: (value) => _count(value ?? '') == null
                            ? 'Enter a whole number'
                            : null,
                      ),
                    const SizedBox(height: EcomsbdSpacing.md),
                    LabelledField(
                      label: 'Warn me below',
                      controller: _threshold,
                      hint: 'Optional',
                      keyboardType: TextInputType.number,
                      inputFormatters: _countFormatters,
                    ),
                  ],
                ),
              ),
              const SizedBox(height: EcomsbdSpacing.lg),
              FilledButton(
                onPressed: _saving ? null : _save,
                style: FilledButton.styleFrom(
                  backgroundColor: EcomsbdColors.orange,
                  minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
                  shape: const StadiumBorder(),
                  textStyle: EcomsbdType.label,
                ),
                child: Text(
                  _saving
                      ? 'Saving…'
                      : (widget.isEditing ? 'Save changes' : 'Add product'),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

final List<TextInputFormatter> _moneyFormatters = <TextInputFormatter>[
  FilteringTextInputFormatter.allow(RegExp(r'[0-9০-৯.]')),
];

final List<TextInputFormatter> _countFormatters = <TextInputFormatter>[
  FilteringTextInputFormatter.allow(RegExp(r'[0-9০-৯]')),
];
