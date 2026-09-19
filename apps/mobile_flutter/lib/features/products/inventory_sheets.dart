import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/models.dart';
import '../../data/money/money_providers.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../shared/inputs.dart';
import 'stock_adjustment_sheet.dart' show VariantPicker;

/// Inventory V2 sheets: restock, add a variant, receive a returned parcel.
///
/// Each one appends to the stock ledger through the server; none sets a total.

final List<TextInputFormatter> _digits = <TextInputFormatter>[
  FilteringTextInputFormatter.allow(RegExp(r'[0-9০-৯]')),
];

int? _count(String raw) {
  final text = normalizeDigits(raw).trim();
  return text.isEmpty ? null : int.tryParse(text);
}

/// Taka text to paisa without a `double` (see ProductFormScreen._paisa).
int? _paisa(String raw) {
  final text = normalizeDigits(raw).replaceAll(',', '').trim();
  final match = RegExp(r'^(\d+)(?:\.(\d{1,2}))?$').firstMatch(text);
  if (match == null) {
    return null;
  }
  final fraction = (match.group(2) ?? '').padRight(2, '0');
  return int.parse(match.group(1)!) * 100 + int.parse(fraction);
}

Future<T?> _showSheet<T>(BuildContext context, Widget sheet) {
  return showModalBottomSheet<T>(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.transparent,
    builder: (_) => sheet,
  );
}

class _SheetFrame extends StatelessWidget {
  const _SheetFrame({
    required this.title,
    required this.children,
    this.subtitle,
  });

  final String title;
  final String? subtitle;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
      child: Container(
        decoration: const BoxDecoration(
          color: EcomsbdColors.backgroundLight,
          borderRadius: BorderRadius.vertical(
            top: Radius.circular(EcomsbdRadii.lg),
          ),
        ),
        padding: const EdgeInsets.fromLTRB(
          EcomsbdSpacing.lg,
          EcomsbdSpacing.md,
          EcomsbdSpacing.lg,
          EcomsbdSpacing.xl,
        ),
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Center(
                child: Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(
                    color: EcomsbdColors.trackLight,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              const SizedBox(height: EcomsbdSpacing.md),
              Text(title, style: EcomsbdType.sectionTitle),
              if (subtitle != null) ...<Widget>[
                const SizedBox(height: 2),
                Text(
                  subtitle!,
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ],
              const SizedBox(height: EcomsbdSpacing.md),
              ...children,
            ],
          ),
        ),
      ),
    );
  }
}

class _PrimaryButton extends StatelessWidget {
  const _PrimaryButton({required this.label, required this.onPressed});

  final String label;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return FilledButton(
      onPressed: onPressed,
      style: FilledButton.styleFrom(
        backgroundColor: EcomsbdColors.orange,
        minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
        shape: const StadiumBorder(),
        textStyle: EcomsbdType.label,
      ),
      child: Text(label),
    );
  }
}

Widget _errorText(ApiError? error) => error == null
    ? const SizedBox.shrink()
    : Padding(
        padding: const EdgeInsets.only(bottom: EcomsbdSpacing.sm),
        child: Text(
          error.displayMessage,
          style: EcomsbdType.caption.copyWith(color: EcomsbdColors.red),
        ),
      );

// --------------------------------------------------------------------------- //
// Restock
// --------------------------------------------------------------------------- //

/// New goods arrived: quantity, optional unit cost and reference.
///
/// Not a purchase order or a supplier bill — only a positive, explained
/// movement in the stock history.
class RestockSheet extends ConsumerStatefulWidget {
  const RestockSheet({required this.product, super.key});

  final Product product;

  static Future<bool?> show(BuildContext context, {required Product product}) =>
      _showSheet<bool>(context, RestockSheet(product: product));

  @override
  ConsumerState<RestockSheet> createState() => _RestockSheetState();
}

class _RestockSheetState extends ConsumerState<RestockSheet> {
  final TextEditingController _quantity = TextEditingController();
  final TextEditingController _cost = TextEditingController();
  final TextEditingController _reference = TextEditingController();
  ProductVariant? _variant;
  bool _updateCost = false;
  bool _saving = false;
  ApiError? _error;

  @override
  void dispose() {
    _quantity.dispose();
    _cost.dispose();
    _reference.dispose();
    super.dispose();
  }

  bool get _ready =>
      (_count(_quantity.text) ?? 0) > 0 &&
      (!widget.product.hasVariants || _variant != null) &&
      (_cost.text.trim().isEmpty || _paisa(_cost.text) != null);

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await ref
          .read(productsRepositoryProvider)
          .restock(
            widget.product.id,
            quantity: _count(_quantity.text)!,
            variantId: _variant?.id,
            unitCostPaisa: _cost.text.trim().isEmpty
                ? null
                : _paisa(_cost.text),
            updateCost: _updateCost,
            reference: _reference.text.trim(),
          );
      if (mounted) {
        Navigator.of(context).pop(true);
      }
    } on ApiError catch (error) {
      if (mounted) {
        setState(() {
          _saving = false;
          _error = error;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return _SheetFrame(
      title: context.tr('inv.restockTitle'),
      subtitle: context.tr('inv.restockSub'),
      children: <Widget>[
        if (widget.product.hasVariants) ...<Widget>[
          VariantPicker(
            variants: widget.product.activeVariants,
            selected: _variant,
            onChanged: (variant) => setState(() => _variant = variant),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
        ],
        LabelledField(
          label: context.tr('inv.quantity'),
          controller: _quantity,
          keyboardType: TextInputType.number,
          inputFormatters: _digits,
          onChanged: (_) => setState(() {}),
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        LabelledField(
          label: context.tr('inv.unitCost'),
          controller: _cost,
          hint: context.tr('common.optional'),
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          inputFormatters: <TextInputFormatter>[
            FilteringTextInputFormatter.allow(RegExp(r'[0-9০-৯.]')),
          ],
          onChanged: (_) => setState(() {}),
        ),
        if (_cost.text.trim().isNotEmpty)
          CheckboxListTile(
            contentPadding: EdgeInsets.zero,
            value: _updateCost,
            onChanged: (value) => setState(() => _updateCost = value ?? false),
            title: Text(
              context.tr('inv.updateCost'),
              style: EcomsbdType.caption,
            ),
            controlAffinity: ListTileControlAffinity.leading,
          ),
        const SizedBox(height: EcomsbdSpacing.md),
        LabelledField(
          label: context.tr('inv.reference'),
          controller: _reference,
          hint: context.tr('common.optional'),
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        _errorText(_error),
        _PrimaryButton(
          label: _saving
              ? context.tr('common.recording')
              : context.tr('inv.recordRestock'),
          onPressed: _saving || !_ready ? null : _save,
        ),
      ],
    );
  }
}

// --------------------------------------------------------------------------- //
// Add variant
// --------------------------------------------------------------------------- //

/// Add "Black / M" with its own opening stock and low-stock level.
class AddVariantSheet extends ConsumerStatefulWidget {
  const AddVariantSheet({required this.product, super.key});

  final Product product;

  static Future<bool?> show(BuildContext context, {required Product product}) =>
      _showSheet<bool>(context, AddVariantSheet(product: product));

  @override
  ConsumerState<AddVariantSheet> createState() => _AddVariantSheetState();
}

class _AddVariantSheetState extends ConsumerState<AddVariantSheet> {
  final TextEditingController _name = TextEditingController();
  final TextEditingController _sku = TextEditingController();
  final TextEditingController _opening = TextEditingController();
  final TextEditingController _threshold = TextEditingController();
  bool _saving = false;
  ApiError? _error;

  @override
  void dispose() {
    _name.dispose();
    _sku.dispose();
    _opening.dispose();
    _threshold.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await ref
          .read(productsRepositoryProvider)
          .addVariant(
            widget.product.id,
            name: _name.text.trim(),
            sku: _sku.text.trim(),
            openingStock: _count(_opening.text) ?? 0,
            lowStockThreshold: _count(_threshold.text),
          );
      if (mounted) {
        Navigator.of(context).pop(true);
      }
    } on ApiError catch (error) {
      if (mounted) {
        setState(() {
          _saving = false;
          _error = error;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final product = widget.product;
    final splitting = !product.hasVariants && product.stockOnHand != 0;
    return _SheetFrame(
      title: context.tr('inv.addVariant'),
      subtitle: splitting
          ? context.tr('inv.splitNote', <String, Object?>{
              'count': product.stockOnHand,
            })
          : context.tr('inv.variantsSub'),
      children: <Widget>[
        LabelledField(
          label: context.tr('inv.variantName'),
          controller: _name,
          hint: context.tr('inv.variantNameHint'),
          onChanged: (_) => setState(() {}),
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        LabelledField(
          label: context.tr('pf.sku'),
          controller: _sku,
          hint: context.tr('common.optional'),
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        Row(
          children: <Widget>[
            Expanded(
              child: LabelledField(
                label: context.tr('imp.colOpeningStock'),
                controller: _opening,
                keyboardType: TextInputType.number,
                inputFormatters: _digits,
              ),
            ),
            const SizedBox(width: EcomsbdSpacing.sm),
            Expanded(
              child: LabelledField(
                label: context.tr('pf.warnBelow'),
                controller: _threshold,
                hint: context.tr('common.optional'),
                keyboardType: TextInputType.number,
                inputFormatters: _digits,
              ),
            ),
          ],
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        _errorText(_error),
        _PrimaryButton(
          label: _saving
              ? context.tr('common.saving')
              : context.tr('inv.addVariant'),
          onPressed: _saving || _name.text.trim().isEmpty ? null : _save,
        ),
      ],
    );
  }
}

// --------------------------------------------------------------------------- //
// Receive a returned parcel
// --------------------------------------------------------------------------- //

/// What physically came back: back to stock, damaged, or some of each.
///
/// A courier marking a parcel "returned" never restocks by itself. This is the
/// only place a return puts units back on the shelf.
class ReceiveReturnSheet extends ConsumerStatefulWidget {
  const ReceiveReturnSheet({
    required this.consignmentId,
    required this.items,
    super.key,
  });

  final String consignmentId;

  /// The order's lines, for names; quantities come from the parcel.
  final List<OrderItem> items;

  static Future<bool?> show(
    BuildContext context, {
    required String consignmentId,
    required List<OrderItem> items,
  }) => _showSheet<bool>(
    context,
    ReceiveReturnSheet(consignmentId: consignmentId, items: items),
  );

  @override
  ConsumerState<ReceiveReturnSheet> createState() => _ReceiveReturnSheetState();
}

class _ReturnLine {
  _ReturnLine({required this.id, required this.name, required this.pending})
    : restock = pending;

  final String id;
  final String name;
  final int pending;
  int restock;
}

class _ReceiveReturnSheetState extends ConsumerState<ReceiveReturnSheet> {
  String _decision = 'RESTOCK_ALL';
  List<_ReturnLine>? _lines;
  bool _saving = false;
  ApiError? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final parcel = await ref
          .read(moneyRepositoryProvider)
          .consignment(widget.consignmentId);
      final names = <String, String>{
        for (final item in widget.items)
          item.id: item.variantLabel == null
              ? item.productName
              : '${item.productName} (${item.variantLabel})',
      };
      if (!mounted) {
        return;
      }
      setState(() {
        _lines = <_ReturnLine>[
          for (final raw in parcel['items'] as List<dynamic>? ?? <dynamic>[])
            if (((raw as Map<String, dynamic>)['qty_return_pending'] as int? ??
                    0) >
                0)
              _ReturnLine(
                id: raw['id'] as String,
                name: names[raw['order_item_id']] ?? '',
                pending: raw['qty_return_pending'] as int,
              ),
        ];
      });
    } on ApiError catch (error) {
      if (mounted) {
        setState(() => _error = error);
      }
    }
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await ref
          .read(moneyRepositoryProvider)
          .receiveReturn(
            widget.consignmentId,
            decision: _decision,
            items: _decision == 'PARTIAL'
                ? <Map<String, dynamic>>[
                    for (final line in _lines ?? <_ReturnLine>[])
                      <String, dynamic>{
                        'consignment_item_id': line.id,
                        'qty_restocked': line.restock,
                        'qty_not_restocked': line.pending - line.restock,
                      },
                  ]
                : const <Map<String, dynamic>>[],
          );
      if (mounted) {
        Navigator.of(context).pop(true);
      }
    } on ApiError catch (error) {
      if (mounted) {
        setState(() {
          _saving = false;
          _error = error;
        });
      }
    }
  }

  Widget _option(String code, String titleKey, String subKey) {
    final selected = _decision == code;
    return Padding(
      padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
      child: Material(
        color: selected ? EcomsbdColors.orange.withValues(alpha: 0.08) : null,
        borderRadius: EcomsbdRadii.cardSmall,
        child: Semantics(
          selected: selected,
          child: ListTile(
            onTap: () => setState(() => _decision = code),
            leading: Icon(
              selected
                  ? Icons.radio_button_checked_rounded
                  : Icons.radio_button_unchecked_rounded,
              color: selected ? EcomsbdColors.orange : EcomsbdColors.muted,
            ),
            title: Text(context.tr(titleKey), style: EcomsbdType.bodyStrong),
            subtitle: Text(context.tr(subKey), style: EcomsbdType.caption),
            contentPadding: const EdgeInsets.symmetric(horizontal: 4),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final lines = _lines;
    return _SheetFrame(
      title: context.tr('rtn.receive'),
      subtitle: context.tr('rtn.why'),
      children: <Widget>[
        _option('RESTOCK_ALL', 'rtn.restockAll', 'rtn.restockAllSub'),
        _option('RESTOCK_NONE', 'rtn.damaged', 'rtn.damagedSub'),
        _option('PARTIAL', 'rtn.partial', 'rtn.partialSub'),
        if (_decision == 'PARTIAL' && lines != null)
          for (final line in lines)
            Padding(
              padding: const EdgeInsets.only(top: EcomsbdSpacing.xs),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(line.name, style: EcomsbdType.bodyStrong),
                        Text(
                          context.tr('rtn.restockQty', <String, Object?>{
                            'restock': line.restock,
                            'damaged': line.pending - line.restock,
                          }),
                          style: EcomsbdType.caption.copyWith(
                            color: EcomsbdColors.muted,
                          ),
                        ),
                      ],
                    ),
                  ),
                  IconButton(
                    tooltip: context.tr('common.remove'),
                    onPressed: line.restock > 0
                        ? () => setState(() => line.restock--)
                        : null,
                    icon: const Icon(Icons.remove_circle_outline),
                  ),
                  Text('${line.restock}', style: EcomsbdType.bodyStrong),
                  IconButton(
                    tooltip: context.tr('common.add'),
                    onPressed: line.restock < line.pending
                        ? () => setState(() => line.restock++)
                        : null,
                    icon: const Icon(Icons.add_circle_outline),
                  ),
                ],
              ),
            ),
        const SizedBox(height: EcomsbdSpacing.md),
        _errorText(_error),
        _PrimaryButton(
          label: _saving
              ? context.tr('common.recording')
              : context.tr('rtn.confirm'),
          onPressed: _saving || lines == null ? null : _save,
        ),
      ],
    );
  }
}
