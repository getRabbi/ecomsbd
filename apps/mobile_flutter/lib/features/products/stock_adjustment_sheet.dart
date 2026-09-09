import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/models.dart';
import '../../design/components/badges.dart';
import '../../design/tokens.dart';
import '../shared/inputs.dart';

/// Reasons a seller may choose by hand.
///
/// Deliberately short. The other reasons in the ledger — `BOOKED_RESERVE`,
/// `RETURN_RESTORE`, `CANCEL_RESTORE` — are written by the system when an order
/// moves, and offering them here would let a seller record a return that never
/// happened (master spec section 10.4).
const List<({String code, String label, String help})> sellerStockReasons =
    <({String code, String label, String help})>[
      (
        code: 'MANUAL_ADJUSTMENT',
        label: 'Correction',
        help: 'The count on the shelf does not match the app.',
      ),
      (
        code: 'OPENING',
        label: 'New stock',
        help: 'You received more of this product.',
      ),
      (
        code: 'DAMAGED_WRITE_OFF',
        label: 'Damaged',
        help: 'Written off and cannot be sold.',
      ),
    ];

/// Record a stock movement.
///
/// The seller enters *how much changed*, never a new total. That is what keeps
/// the ledger and the balance in agreement, and it is why the sheet asks for a
/// direction and a reason rather than a number to overwrite.
class StockAdjustmentSheet extends ConsumerStatefulWidget {
  const StockAdjustmentSheet({required this.product, super.key});

  final Product product;

  static Future<bool?> show(BuildContext context, {required Product product}) {
    return showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => StockAdjustmentSheet(product: product),
    );
  }

  @override
  ConsumerState<StockAdjustmentSheet> createState() =>
      _StockAdjustmentSheetState();
}

class _StockAdjustmentSheetState extends ConsumerState<StockAdjustmentSheet> {
  final TextEditingController _quantity = TextEditingController();
  final TextEditingController _note = TextEditingController();

  bool _isIncrease = true;
  String _reason = 'MANUAL_ADJUSTMENT';
  bool _saving = false;
  ApiError? _error;

  /// Set when the server refused because the change would go below zero.
  bool _wouldOversell = false;

  @override
  void dispose() {
    _quantity.dispose();
    _note.dispose();
    super.dispose();
  }

  int get _amount => int.tryParse(normalizeDigits(_quantity.text).trim()) ?? 0;
  int get _delta => _isIncrease ? _amount : -_amount;
  int get _projected => widget.product.stockOnHand + _delta;

  Future<void> _save({bool allowNegative = false}) async {
    if (_amount <= 0) {
      setState(() => _error = null);
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });

    try {
      await ref
          .read(productsRepositoryProvider)
          .adjustStock(
            widget.product.id,
            quantityDelta: _delta,
            reason: _reason,
            note: _note.text.trim(),
            allowNegative: allowNegative,
          );
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
        _wouldOversell = error.code == ApiErrorCode.conflict;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final insets = MediaQuery.viewInsetsOf(context).bottom;

    return Padding(
      padding: EdgeInsets.only(bottom: insets),
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
              const Text('Adjust stock', style: EcomsbdType.sectionTitle),
              const SizedBox(height: 2),
              Text(
                '${widget.product.name} · ${widget.product.stockOnHand} in stock',
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              ),
              const SizedBox(height: EcomsbdSpacing.md),
              Row(
                children: <Widget>[
                  Expanded(
                    child: _DirectionButton(
                      label: 'Add',
                      icon: Icons.add_rounded,
                      selected: _isIncrease,
                      tone: Tone.good,
                      onTap: () => setState(() => _isIncrease = true),
                    ),
                  ),
                  const SizedBox(width: EcomsbdSpacing.sm),
                  Expanded(
                    child: _DirectionButton(
                      label: 'Remove',
                      icon: Icons.remove_rounded,
                      selected: !_isIncrease,
                      tone: Tone.bad,
                      onTap: () => setState(() => _isIncrease = false),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: EcomsbdSpacing.md),
              LabelledField(
                label: 'How many',
                controller: _quantity,
                keyboardType: TextInputType.number,
                inputFormatters: <TextInputFormatter>[
                  FilteringTextInputFormatter.allow(RegExp(r'[0-9০-৯]')),
                ],
                onChanged: (_) => setState(() {}),
              ),
              const SizedBox(height: EcomsbdSpacing.md),
              Text(
                'REASON',
                style: EcomsbdType.eyebrow.copyWith(
                  color: EcomsbdColors.muted2,
                ),
              ),
              const SizedBox(height: 6),
              Wrap(
                spacing: EcomsbdSpacing.xs,
                runSpacing: EcomsbdSpacing.xs,
                children: <Widget>[
                  for (final reason in sellerStockReasons)
                    FilterToggle(
                      label: reason.label,
                      selected: _reason == reason.code,
                      onChanged: (_) => setState(() => _reason = reason.code),
                    ),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                sellerStockReasons
                    .firstWhere((reason) => reason.code == _reason)
                    .help,
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              ),
              const SizedBox(height: EcomsbdSpacing.md),
              LabelledField(
                label: 'Note',
                controller: _note,
                hint: 'Optional — what happened',
              ),
              const SizedBox(height: EcomsbdSpacing.md),
              if (_amount > 0)
                Row(
                  children: <Widget>[
                    Expanded(
                      child: Text(
                        'After this change',
                        style: EcomsbdType.caption.copyWith(
                          color: EcomsbdColors.muted,
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    const SizedBox(width: EcomsbdSpacing.xs),
                    Text(
                      '$_projected in stock',
                      style: EcomsbdType.bodyStrong.copyWith(
                        color: _projected < 0 ? EcomsbdColors.red : null,
                      ),
                    ),
                  ],
                ),
              if (_error != null) ...<Widget>[
                const SizedBox(height: EcomsbdSpacing.sm),
                Text(
                  _error!.displayMessage,
                  style: EcomsbdType.caption.copyWith(color: EcomsbdColors.red),
                ),
              ],
              const SizedBox(height: EcomsbdSpacing.md),
              if (_wouldOversell)
                // Going negative is possible but never silent: the seller has
                // to say they mean it (master spec section 76).
                OutlinedButton(
                  onPressed: _saving ? null : () => _save(allowNegative: true),
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
                    foregroundColor: EcomsbdColors.red,
                    side: BorderSide(
                      color: EcomsbdColors.red.withValues(alpha: 0.4),
                    ),
                    shape: const StadiumBorder(),
                    textStyle: EcomsbdType.label,
                  ),
                  child: const Text('Record it anyway, stock goes negative'),
                )
              else
                FilledButton(
                  onPressed: _saving || _amount <= 0 ? null : () => _save(),
                  style: FilledButton.styleFrom(
                    backgroundColor: EcomsbdColors.orange,
                    minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
                    shape: const StadiumBorder(),
                    textStyle: EcomsbdType.label,
                  ),
                  child: Text(_saving ? 'Recording…' : 'Record movement'),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _DirectionButton extends StatelessWidget {
  const _DirectionButton({
    required this.label,
    required this.icon,
    required this.selected,
    required this.tone,
    required this.onTap,
  });

  final String label;
  final IconData icon;
  final bool selected;
  final Tone tone;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      selected: selected,
      child: Material(
        color: selected ? tone.surface : Colors.white,
        borderRadius: EcomsbdRadii.cardSmall,
        child: InkWell(
          onTap: onTap,
          borderRadius: EcomsbdRadii.cardSmall,
          child: Container(
            height: 48,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              borderRadius: EcomsbdRadii.cardSmall,
              border: Border.all(
                color: selected ? tone.ink : EcomsbdColors.stroke,
              ),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Icon(
                  icon,
                  size: 18,
                  color: selected ? tone.ink : EcomsbdColors.muted,
                ),
                const SizedBox(width: 6),
                Text(
                  label,
                  style: EcomsbdType.label.copyWith(
                    color: selected ? tone.ink : EcomsbdColors.ink,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
