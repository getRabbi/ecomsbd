import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/commerce/list_controllers.dart';
import '../../data/commerce/models.dart';
import '../../design/components/badges.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart';

/// A product's stock movements, newest first.
///
/// The answer to "where did my stock go?". Each row shows the change, the
/// reason and the balance that resulted, because a running balance the seller
/// can follow is what makes the ledger trustworthy rather than merely correct
/// (master spec section 10.4).
class StockHistoryScreen extends ConsumerWidget {
  const StockHistoryScreen({required this.product, super.key});

  final Product product;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final history = ref.watch(stockMovementsProvider(product.id));

    return DetailScaffold(
      title: 'Stock history',
      subtitle: '${product.name} · ${product.stockOnHand} in stock',
      children: <Widget>[
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
            title: 'Could not load the history',
            message: '$error',
          ),
          data: (page) {
            if (page.value.items.isEmpty) {
              return const EmptyState(
                icon: Icons.receipt_long_outlined,
                title: 'No movements yet',
                message:
                    'Every change to this product’s stock will be listed '
                    'here with its reason.',
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
                Text(movement.reasonLabel, style: EcomsbdType.bodyStrong),
                const SizedBox(height: 2),
                Text(
                  <String>[
                    formatRelative(movement.occurredAt),
                    if (movement.note != null && movement.note!.isNotEmpty)
                      movement.note!,
                  ].join(' · '),
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
                '${movement.balanceAfter} left',
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
