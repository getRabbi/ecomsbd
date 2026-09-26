import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/money.dart';
import '../../data/analytics/analytics_providers.dart';
import '../../data/money/models.dart';
import '../../data/money/money_providers.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/navigation.dart';
import '../../design/components/seller_blocks.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../couriers/courier_compare_screen.dart';
import '../settings/connections_screen.dart';
import 'cases_screen.dart';
import 'receivables_screen.dart';

/// Payout differences per courier, from reconciliation's own parcel lines.
///
/// Expected and received are the server's expected and actual net per parcel;
/// the phone only adds them up per courier. Nothing is settled here — the
/// seller reviews each difference in Reconciliation.
class PayoutMismatchSection extends ConsumerWidget {
  const PayoutMismatchSection({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final items = ref
        .watch(reconciliationItemListProvider)
        .items
        .where((item) => item.isDiscrepancy && item.difference != null)
        .toList();
    if (items.isEmpty) return const SizedBox.shrink();

    final byCourier = <String, List<ReconciliationItem>>{};
    for (final item in items) {
      byCourier
          .putIfAbsent(item.provider, () => <ReconciliationItem>[])
          .add(item);
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        SectionHeader(
          title: context.tr('mm.title'),
          subtitle: context.tr('mm.subtitle'),
        ),
        for (final entry in byCourier.entries)
          Padding(
            padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
            child: _MismatchRow(
              mismatch: CourierMismatch.of(entry.key, entry.value),
            ),
          ),
      ],
    );
  }
}

/// One courier's payout difference.
@immutable
class CourierMismatch {
  const CourierMismatch({
    required this.provider,
    required this.expected,
    required this.received,
    required this.difference,
    required this.parcels,
  });

  factory CourierMismatch.of(String provider, List<ReconciliationItem> items) {
    var expected = 0;
    var received = 0;
    var difference = 0;
    for (final item in items) {
      received += item.actualNet.paisa;
      expected += item.expectedNet?.paisa ?? item.actualNet.paisa;
      difference += item.difference?.paisa ?? 0;
    }
    return CourierMismatch(
      provider: provider,
      expected: Money(expected),
      received: Money(received),
      difference: Money(difference),
      parcels: items,
    );
  }

  final String provider;
  final Money expected;
  final Money received;

  /// Received minus expected: negative when the courier paid less.
  final Money difference;
  final List<ReconciliationItem> parcels;

  String get courierName => courierDisplayName(provider);
}

class _MismatchRow extends StatelessWidget {
  const _MismatchRow({required this.mismatch});

  final CourierMismatch mismatch;

  @override
  Widget build(BuildContext context) {
    final short = mismatch.difference.isNegative;
    return AlertStrip(
      icon: Icons.rule_rounded,
      tone: short ? Tone.bad : Tone.warning,
      title: context.tr(short ? 'mm.short' : 'mm.differs', <String, Object?>{
        'courier': mismatch.courierName,
        'amount': Money(mismatch.difference.paisa.abs()).format(),
      }),
      detail: context.trPlural('mm.parcels', mismatch.parcels.length),
      actionLabel: context.tr('mm.review'),
      onAction: () => unawaited(PayoutMismatchSheet.show(context, mismatch)),
    );
  }
}

class PayoutMismatchSheet extends StatelessWidget {
  const PayoutMismatchSheet({required this.mismatch, super.key});

  final CourierMismatch mismatch;

  static Future<void> show(BuildContext context, CourierMismatch mismatch) =>
      GlassBottomSheet.show<void>(
        context: context,
        title: context.tr('mm.sheetTitle', <String, Object?>{
          'courier': mismatch.courierName,
        }),
        description: context.tr('mm.sheetSub'),
        child: PayoutMismatchSheet(mismatch: mismatch),
      );

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        _Line(label: context.tr('mm.expected'), value: mismatch.expected),
        _Line(label: context.tr('mm.received'), value: mismatch.received),
        _Line(
          label: context.tr('mm.difference'),
          value: mismatch.difference,
          tone: mismatch.difference.isNegative ? Tone.bad : Tone.warning,
        ),
        SectionHeader(title: context.tr('mm.affected')),
        for (final item in mismatch.parcels)
          GlassListRow(
            title: item.reference ?? item.trackingCode ?? item.id,
            subtitle: item.statusLabel,
            trailingTop: item.difference?.format(signed: true),
          ),
        const SizedBox(height: EcomsbdSpacing.md),
        FilledButton(
          onPressed: () {
            Navigator.of(context).pop();
            unawaited(
              Navigator.of(context).push(
                MaterialPageRoute<void>(builder: (_) => const CasesScreen()),
              ),
            );
          },
          style: FilledButton.styleFrom(
            backgroundColor: EcomsbdColors.orange,
            minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
            shape: const StadiumBorder(),
            textStyle: EcomsbdType.label,
          ),
          child: Text(context.tr('mm.reconcile')),
        ),
        const SizedBox(height: EcomsbdSpacing.xxs),
        Text(
          context.tr('mm.note'),
          textAlign: TextAlign.center,
          style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
        ),
      ],
    );
  }
}

/// What each courier holds for the shop right now.
class CourierReceivableSection extends ConsumerWidget {
  const CourierReceivableSection({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final balances = ref.watch(courierBalancesProvider).valueOrNull?.value;
    if (balances == null || balances.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        SectionHeader(
          title: context.tr('mm.byCourier'),
          subtitle: context.tr('mm.byCourierSub'),
        ),
        for (final balance in balances)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: SpendRow(
              provider: balance.provider,
              name: courierDisplayName(balance.provider),
              detail: context.tr('mm.byCourierLine', <String, Object?>{
                'parcels': balance.parcelCount,
                'overdue': balance.overdueCount,
              }),
              amount: balance.outstanding.format(),
              onTap: () => unawaited(
                Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => const ReceivablesScreen(),
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }
}

/// "Courier cost — last 30 days".
///
/// Shop-wide charges come from the profit report; per-courier shipments and
/// return rates from the RTO report's last-30-day counts.
/// TODO(courier-spend): per-courier delivery spend needs the server to sum
/// consignment charges by courier; until then only the shop total is shown.
class CourierSpendSection extends ConsumerWidget {
  const CourierSpendSection({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profit = ref.watch(profitReportProvider).valueOrNull?.value;
    final rto = ref.watch(rtoCouriersProvider).valueOrNull?.value;
    if (profit == null && rto == null) return const SizedBox.shrink();
    final lines = rto?.items.where((line) => line.recent.completed > 0);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        SectionHeader(
          title: context.tr('spend.title'),
          subtitle: context.tr('spend.subtitle'),
          actionLabel: context.tr('spend.compare'),
          onAction: () => unawaited(CourierCompareScreen.open(context)),
        ),
        if (profit != null)
          GlassCard(
            padding: const EdgeInsets.fromLTRB(15, 4, 15, 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                KpiLine(
                  label: context.tr('spend.delivery'),
                  value: profit.deliveryCharge.format(),
                ),
                KpiLine(
                  label: context.tr('spend.codFees'),
                  value: profit.codFee.format(),
                ),
                KpiLine(
                  label: context.tr('spend.returns'),
                  value: profit.returnCharge.format(),
                  isLast: profit.parcelCount == 0,
                ),
                if (profit.parcelCount > 0)
                  KpiLine(
                    label: context.tr('spend.average'),
                    value: Money(
                      profit.deliveryCharge.paisa ~/ profit.parcelCount,
                    ).format(),
                    isLast: true,
                  ),
                const SizedBox(height: 6),
                Text(
                  context.tr('spend.note'),
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted2,
                  ),
                ),
              ],
            ),
          ),
        if (lines != null && lines.isNotEmpty) ...<Widget>[
          const SizedBox(height: 10),
          for (final line in lines)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: SpendRow(
                provider: line.provider,
                name: courierDisplayName(line.provider),
                detail: context.tr('spend.courierLine', <String, Object?>{
                  'shipments': line.recent.completed,
                  'rto': line.recent.sufficient
                      ? line.recent.rateLabel
                      : context.tr('common.notEnoughData'),
                }),
              ),
            ),
        ],
        if (profit == null && (lines == null || lines.isEmpty))
          GlassCard(
            child: Text(
              context.tr('spend.empty'),
              style: EcomsbdType.body.copyWith(color: EcomsbdColors.muted),
            ),
          ),
      ],
    );
  }
}

class _Line extends StatelessWidget {
  const _Line({required this.label, required this.value, this.tone});

  final String label;
  final Money value;
  final Tone? tone;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xxs),
      child: Row(
        children: <Widget>[
          Expanded(child: Text(label, style: EcomsbdType.body)),
          Text(
            value.format(),
            style: EcomsbdType.bodyStrong.copyWith(color: tone?.ink),
          ),
        ],
      ),
    );
  }
}
