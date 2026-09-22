import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/commerce/list_controllers.dart';
import '../../data/commerce/models.dart';
import '../../design/components/badges.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
import '../../l10n/app_strings.dart';

/// A product's stock movements, newest first.
///
/// The answer to "where did my stock go?". Each row shows the change, the
/// reason, the order or reference behind it, who did it and the balance that
/// resulted, because a running balance the seller can follow is what makes the
/// ledger trustworthy rather than merely correct (master spec section 10.4).
class StockHistoryScreen extends ConsumerStatefulWidget {
  const StockHistoryScreen({required this.product, super.key});

  final Product product;

  @override
  ConsumerState<StockHistoryScreen> createState() => _StockHistoryScreenState();
}

/// Filter name -> label key. Order is the chip order.
const Map<String, String> _filterLabels = <String, String>{
  'all': 'inv.filterAll',
  'sales': 'inv.filterSales',
  'returns': 'inv.filterReturns',
  'restock': 'inv.filterRestock',
  'adjust': 'inv.filterAdjust',
};

class _StockHistoryScreenState extends ConsumerState<StockHistoryScreen> {
  String _filter = 'all';

  @override
  Widget build(BuildContext context) {
    final product = widget.product;
    final history = ref.watch(
      stockHistoryProvider((productId: product.id, filter: _filter)),
    );

    return DetailScaffold(
      title: context.tr('sh.title'),
      subtitle:
          '${product.name} · ${context.tr('inv.inStockCount', <String, Object?>{'count': product.stockOnHand})}',
      children: <Widget>[
        Wrap(
          spacing: EcomsbdSpacing.xs,
          runSpacing: EcomsbdSpacing.xs,
          children: <Widget>[
            for (final entry in _filterLabels.entries)
              FilterToggle(
                label: context.tr(entry.value),
                selected: _filter == entry.key,
                onChanged: (_) => setState(() => _filter = entry.key),
              ),
          ],
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        history.when(
          loading: () => Column(
            children: <Widget>[
              for (var i = 0; i < 4; i++)
                Padding(
                  padding: const EdgeInsets.only(bottom: EcomsbdSpacing.sm),
                  child: SkeletonLoader.card(height: 66),
                ),
            ],
          ),
          error: (error, _) => EmptyState(
            icon: Icons.error_outline,
            title: context.tr('sh.error'),
            message: '$error',
          ),
          data: (page) {
            if (page.value.items.isEmpty) {
              return EmptyState(
                icon: Icons.receipt_long_outlined,
                title: context.tr('sh.emptyTitle'),
                message: context.tr('sh.emptyBody'),
              );
            }
            return Column(
              children: <Widget>[
                if (page.isStale) ...<Widget>[
                  StaleDataNotice(fetchedAt: page.fetchedAt),
                  const SizedBox(height: EcomsbdSpacing.sm),
                ],
                for (final movement in page.value.items)
                  Padding(
                    padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
                    child: _MovementRow(movement: movement),
                  ),
              ],
            );
          },
        ),
      ],
    );
  }
}

class _MovementRow extends StatelessWidget {
  const _MovementRow({required this.movement});

  final StockMovement movement;

  @override
  Widget build(BuildContext context) {
    final tone = movement.isIncrease ? Tone.good : Tone.bad;
    final sign = movement.isIncrease ? '+' : '';
    final title = movement.variantName == null
        ? movement.reasonLabel
        : '${movement.reasonLabel} · ${movement.variantName}';
    final details = <String>[
      formatRelative(movement.occurredAt),
      if (movement.orderNumber != null)
        context.tr('inv.orderRef', <String, Object?>{
          'number': movement.orderNumber,
        }),
      if (movement.reference != null && movement.reference!.isNotEmpty)
        movement.reference!,
      if (movement.actorName != null && movement.actorName!.isNotEmpty)
        movement.actorName!,
      if (movement.note != null && movement.note!.isNotEmpty) movement.note!,
    ];

    return GlassCard(
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      borderRadius: EcomsbdRadii.cardMedium,
      child: Row(
        children: <Widget>[
          Container(
            width: 38,
            height: 38,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: tone.surface,
              borderRadius: EcomsbdRadii.cardSmall,
            ),
            child: Icon(
              movement.isIncrease
                  ? Icons.arrow_upward_rounded
                  : Icons.arrow_downward_rounded,
              size: 18,
              color: tone.ink,
            ),
          ),
          const SizedBox(width: EcomsbdSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Text(title, style: EcomsbdType.bodyStrong),
                const SizedBox(height: 2),
                Text(
                  details.join(' · '),
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ),
          ),
          const SizedBox(width: EcomsbdSpacing.xs),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Text(
                '$sign${movement.quantityDelta}',
                style: EcomsbdType.bodyStrong.copyWith(color: tone.ink),
              ),
              Text(
                context.tr('inv.left', <String, Object?>{
                  'count': movement.balanceAfter,
                }),
                style: EcomsbdType.caption.copyWith(
                  color: EcomsbdColors.muted2,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
