import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/money.dart';
import '../../data/analytics/analytics_providers.dart';
import '../../data/commerce/list_controllers.dart';
import '../../data/commerce/models.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/navigation.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../customers/customer_detail_screen.dart';
import '../orders/order_status.dart';
import '../shared/data_state.dart';

/// What the seller chose on the risk review. Decision support only: nothing
/// here changes the order by itself.
enum RiskDecision { callToConfirm, askAdvance, continueManually, cancelOrder }

/// Return-risk review for one order, from the customer's own history with
/// this shop. Opened before a courier is booked.
class RiskReviewSheet extends ConsumerWidget {
  const RiskReviewSheet({required this.order, super.key, this.canCancel});

  final SellerOrder order;

  /// Whether "Cancel order" is offered. The caller runs its own cancel flow,
  /// with its reason prompt, when the seller picks it.
  final bool? canCancel;

  /// Show the review and carry out the non-destructive choices here; returns
  /// true when the seller asked to cancel, for the caller to confirm.
  static Future<bool> review(
    BuildContext context, {
    required SellerOrder order,
    bool canCancel = false,
  }) async {
    final decision = await GlassBottomSheet.show<RiskDecision>(
      context: context,
      title: context.tr('rrv.title'),
      description: <String?>[
        order.customerName,
        order.customerPhoneMasked,
      ].whereType<String>().join(' · '),
      child: RiskReviewSheet(order: order, canCancel: canCancel),
    );
    if (!context.mounted) return false;
    switch (decision) {
      case RiskDecision.callToConfirm:
        final customerId = order.customerId;
        if (customerId != null) {
          // The full number is revealed there, with the reason logged.
          unawaited(
            Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => CustomerDetailScreen(customerId: customerId),
              ),
            ),
          );
        }
      case RiskDecision.askAdvance:
        await Clipboard.setData(
          ClipboardData(
            text: context.tr('rrv.advanceMessage', <String, Object?>{
              'order': order.orderNumber,
            }),
          ),
        );
        if (context.mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(context.tr('rrv.advanceCopied'))),
          );
        }
      case RiskDecision.cancelOrder:
        return true;
      case RiskDecision.continueManually:
      case null:
        break;
    }
    return false;
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final customerId = order.customerId;
    final customer = customerId == null
        ? null
        : ref.watch(customerProvider(customerId));
    final history = customerId == null
        ? null
        : ref.watch(customerOrdersProvider(customerId));
    final returns = ref.watch(returnReportProvider).valueOrNull?.value;
    final averageLoss = returns == null || returns.returnCount == 0
        ? null
        : Money(returns.directLoss.paisa ~/ returns.returnCount);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Align(
          alignment: Alignment.centerLeft,
          child: StatusChip(
            label: context.tr('rrv.riskState', <String, Object?>{
              'state': riskLabel(order.riskState),
            }),
            tone: switch (order.riskState) {
              'HIGH' => Tone.bad,
              'MEDIUM' => Tone.warning,
              'LOW' => Tone.good,
              _ => Tone.neutral,
            },
          ),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        if (customer == null)
          Text(
            context.tr('rrv.noCustomer'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          )
        else
          customer.when(
            loading: () => const ContentLoader(minHeight: 110),
            error: (_, __) => Text(
              context.tr('rrv.historyUnavailable'),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
            data: (record) => _HistorySummary(customer: record),
          ),
        if (history != null)
          ...history.maybeWhen(
            data: (orders) {
              final others = orders.where((o) => o.id != order.id).take(4);
              if (others.isEmpty) return const <Widget>[];
              return <Widget>[
                SectionHeader(title: context.tr('rrv.recent')),
                for (final other in others)
                  GlassListRow(
                    title: other.orderNumber,
                    subtitle:
                        '${other.statusLabel} · ${fulfillmentLabel(other.fulfillmentState)}',
                    trailingTop: other.codAmount.format(),
                  ),
              ];
            },
            orElse: () => const <Widget>[],
          ),
        const SizedBox(height: EcomsbdSpacing.sm),
        GlassCard(
          padding: const EdgeInsets.all(EcomsbdSpacing.sm),
          child: Column(
            children: <Widget>[
              _Line(
                label: context.tr('rrv.thisCod'),
                value: order.codAmount.format(),
              ),
              _Line(
                label: context.tr('rrv.lossIfReturned'),
                value:
                    averageLoss?.format() ?? context.tr('rrv.lossUnknownShort'),
                tone: averageLoss == null ? null : Tone.bad,
              ),
              Text(
                averageLoss == null
                    ? context.tr('rrv.lossUnknown')
                    : context.tr('rrv.lossBasis'),
                style: EcomsbdType.caption.copyWith(
                  color: EcomsbdColors.muted2,
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        FilledButton.icon(
          onPressed: customerId == null
              ? null
              : () => Navigator.of(context).pop(RiskDecision.callToConfirm),
          icon: const Icon(Icons.call_outlined, size: 18),
          label: Text(context.tr('rrv.call')),
          style: FilledButton.styleFrom(
            backgroundColor: EcomsbdColors.orange,
            minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
            shape: const StadiumBorder(),
            textStyle: EcomsbdType.label,
          ),
        ),
        const SizedBox(height: EcomsbdSpacing.xs),
        OutlinedButton.icon(
          onPressed: () => Navigator.of(context).pop(RiskDecision.askAdvance),
          icon: const Icon(Icons.copy_rounded, size: 18),
          label: Text(context.tr('rrv.askAdvance')),
          style: OutlinedButton.styleFrom(
            minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
            shape: const StadiumBorder(),
            textStyle: EcomsbdType.label,
          ),
        ),
        Row(
          children: <Widget>[
            Expanded(
              child: TextButton(
                onPressed: () =>
                    Navigator.of(context).pop(RiskDecision.continueManually),
                child: Text(context.tr('rrv.continue')),
              ),
            ),
            if (canCancel ?? false)
              Expanded(
                child: TextButton(
                  onPressed: () =>
                      Navigator.of(context).pop(RiskDecision.cancelOrder),
                  style: TextButton.styleFrom(
                    foregroundColor: EcomsbdColors.red,
                  ),
                  child: Text(context.tr('rrv.cancel')),
                ),
              ),
          ],
        ),
        Text(
          context.tr('rrv.disclaimer'),
          textAlign: TextAlign.center,
          style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
        ),
      ],
    );
  }
}

class _HistorySummary extends StatelessWidget {
  const _HistorySummary({required this.customer});

  final Customer customer;

  @override
  Widget build(BuildContext context) {
    final returned = customer.returnedCount;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        ProviderHealthBanner(
          provider: returned > 0
              ? context.trPlural('rrv.returnsFound', returned)
              : context.tr('rrv.noReturns'),
          detail: context.tr('rrv.historyLine', <String, Object?>{
            'orders': customer.orderCount,
            'delivered': customer.deliveredCount,
            'returned': returned,
          }),
          tone: returned == 0
              ? Tone.good
              : (returned >= 2 ? Tone.bad : Tone.warning),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        Row(
          children: <Widget>[
            _Stat(
              label: context.tr('rrv.parcels'),
              value: '${customer.orderCount}',
            ),
            _Stat(
              label: context.tr('rrv.delivered'),
              value: '${customer.deliveredCount}',
            ),
            _Stat(label: context.tr('rrv.returns'), value: '$returned'),
            _Stat(
              label: context.tr('rrv.success'),
              // "No history yet" is a sentence, not a figure.
              value: customer.successRateBasisPoints == null
                  ? '—'
                  : customer.successRateLabel,
            ),
          ],
        ),
        if (customer.lastOrderAt != null) ...<Widget>[
          const SizedBox(height: EcomsbdSpacing.xs),
          Text(
            context.tr('rrv.lastOrder', <String, Object?>{
              'when': formatRelative(customer.lastOrderAt),
            }),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
        ],
      ],
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Column(
        children: <Widget>[
          FittedBox(
            fit: BoxFit.scaleDown,
            child: Text(value, style: EcomsbdType.metricValue),
          ),
          Text(
            label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
        ],
      ),
    );
  }
}

class _Line extends StatelessWidget {
  const _Line({required this.label, required this.value, this.tone});

  final String label;
  final String value;
  final Tone? tone;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xxs),
      child: Row(
        children: <Widget>[
          Expanded(child: Text(label, style: EcomsbdType.body)),
          Text(value, style: EcomsbdType.bodyStrong.copyWith(color: tone?.ink)),
        ],
      ),
    );
  }
}
