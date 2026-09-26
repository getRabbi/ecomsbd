import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/money/models.dart';
import '../../data/money/money_providers.dart';
import '../../design/components/badges.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../shared/data_state.dart';
import '../shared/responsive.dart';

/// Money → Cashflow.
///
/// Three questions: what arrived, what couriers hold now, and when it may
/// arrive. The first two are facts from the ledger and the receivables and are
/// marked as such. The third is an estimate — marked as one, split out from
/// the facts, and built only from money that is already receivable: it never
/// adds a paisa, it only says when. Where the date cannot be estimated the
/// screen says so instead of guessing.
///
/// Nothing on this screen is calculated on the device.
class CashflowScreen extends ConsumerWidget {
  const CashflowScreen({super.key});

  Future<void> _refresh(WidgetRef ref) async {
    ref
      ..invalidate(cashflowProvider)
      ..invalidate(courierBalancesProvider);
    await ref.read(cashflowProvider.future);
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final flow = ref.watch(cashflowProvider);
    final couriers = ref.watch(courierBalancesProvider);

    return Scaffold(
      backgroundColor: EcomsbdColors.background,
      body: EcomsbdBackground(
        child: SafeArea(
          child: ContentWidthLimit(
            child: RefreshIndicator(
              edgeOffset: EcomsbdLayout.pushedRefreshOffset,
              onRefresh: () => _refresh(ref),
              child: ListView(
                padding: const EdgeInsets.fromLTRB(
                  EcomsbdSpacing.page,
                  EcomsbdLayout.pushedTopPadding,
                  EcomsbdSpacing.page,
                  EcomsbdSpacing.xxl,
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
                          eyebrow: context.tr('rcv.eyebrow'),
                          title: context.tr('rcv.title'),
                          description: context.tr('rcv.description'),
                        ),
                      ),
                    ],
                  ),
                  ...flow.when(
                    loading: () => const <Widget>[
                      Padding(
                        padding: EdgeInsets.all(EcomsbdSpacing.xl),
                        child: Center(child: CircularProgressIndicator()),
                      ),
                    ],
                    error: (error, _) => <Widget>[
                      if (error is ApiError)
                        ErrorStateCard(
                          error: error,
                          onRetry: () => ref.invalidate(cashflowProvider),
                        )
                      else
                        EmptyState(
                          icon: Icons.error_outline,
                          title: context.tr('money.couldNotLoadTitle'),
                          message: context.tr('common.somethingWentWrong'),
                        ),
                    ],
                    data: (sourced) => <Widget>[
                      if (sourced.isStale) ...<Widget>[
                        StaleDataNotice(
                          fetchedAt: sourced.fetchedAt,
                          onRetry: () => ref.invalidate(cashflowProvider),
                        ),
                        const SizedBox(height: EcomsbdSpacing.sm),
                      ],
                      CashflowFactsCard(flow: sourced.value),
                      const SizedBox(height: EcomsbdSpacing.sm),
                      CashflowForecastCard(flow: sourced.value),
                    ],
                  ),
                  ...couriers.maybeWhen(
                    data: (sourced) => sourced.value.isEmpty
                        ? const <Widget>[]
                        : <Widget>[
                            SectionHeader(
                              title: context.tr('rcv.couriersTitle'),
                              subtitle: context.tr('rcv.couriersSub'),
                            ),
                            for (final row in sourced.value) ...<Widget>[
                              CourierBalanceCard(balance: row),
                              const SizedBox(height: EcomsbdSpacing.sm),
                            ],
                          ],
                    orElse: () => const <Widget>[],
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

/// Received, receivable, overdue, in transit. Facts.
class CashflowFactsCard extends StatelessWidget {
  const CashflowFactsCard({required this.flow, super.key});

  final CashflowView flow;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  context.tr('rcv.factsTitle'),
                  style: EcomsbdType.bodyStrong,
                ),
              ),
              const DataQualityBadge(quality: DataQuality.actual),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          _FigureRow(
            label: context.tr('rcv.received'),
            caption: context.tr('rcv.last30'),
            amount: flow.received,
          ),
          _FigureRow(
            label: context.tr('rcv.receivable'),
            caption: context.tr('rcv.receivableNote'),
            amount: flow.receivable,
          ),
          _FigureRow(
            label: context.tr('rcv.overdue'),
            caption: context.tr('rcv.overdueNote', <String, Object?>{
              'days': flow.overdueAfterDays,
            }),
            amount: flow.overdue,
            tone: flow.overdue.isZero ? null : EcomsbdColors.red,
          ),
          _FigureRow(
            label: context.tr('rcv.inTransit'),
            caption: context.trPlural('rcv.inTransitNote', flow.inTransitCount),
            amount: flow.inTransit,
          ),
        ],
      ),
    );
  }
}

/// When the receivable may arrive. An estimate, and labelled one.
class CashflowForecastCard extends StatelessWidget {
  const CashflowForecastCard({required this.flow, super.key});

  final CashflowView flow;

  @override
  Widget build(BuildContext context) {
    final delay = flow.overallDelay;
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  context.tr('rcv.forecastTitle'),
                  style: EcomsbdType.bodyStrong,
                ),
              ),
              const DataQualityBadge(quality: DataQuality.estimated),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          if (flow.receivable.isZero)
            Text(context.tr('rcv.nothingExpected'), style: EcomsbdType.body)
          else
            for (final window in flow.forecast)
              if (!window.amount.isZero)
                _FigureRow(
                  label: window.label,
                  caption: context.trPlural('rcv.parcels', window.parcelCount),
                  amount: window.amount,
                  // A dated slice is an estimate; an undated one is simply
                  // money whose timing is unknown.
                  estimated: !window.isUndated,
                  tone: window.isUndated ? EcomsbdColors.amber : null,
                ),
          const SizedBox(height: EcomsbdSpacing.xs),
          Text(
            delay == null
                ? context.tr('rcv.noHistoryNote')
                : context.tr('rcv.usualDelay', <String, Object?>{
                    'days': delay.medianDays,
                    'count': delay.samples,
                  }),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.xxs),
          Text(
            context.tr('rcv.forecastNote'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
          ),
        ],
      ),
    );
  }
}

/// One courier: what it holds, how late, and how it usually pays.
class CourierBalanceCard extends StatelessWidget {
  const CourierBalanceCard({required this.balance, super.key});

  final CourierBalance balance;

  @override
  Widget build(BuildContext context) {
    final delay = balance.delay;
    final agedBands = <AgingBand>[
      for (final band in balance.aging)
        if (!band.outstanding.isZero) band,
    ];
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  balance.name,
                  style: EcomsbdType.bodyStrong,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              MoneyText(balance.outstanding, style: EcomsbdType.bodyStrong),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          if (balance.deliveredUnpaidCount > 0)
            _Line(
              context.trPlural(
                'rcv.deliveredUnpaid',
                balance.deliveredUnpaidCount,
              ),
            ),
          if (!balance.overdue.isZero)
            _Line(
              context.tr('rcv.courierOverdue', <String, Object?>{
                'amount': balance.overdue.format(),
                'count': balance.overdueCount,
              }),
              color: EcomsbdColors.red,
            ),
          if (balance.oldestAgeDays != null && !balance.outstanding.isZero)
            _Line(
              context.tr('rcv.oldest', <String, Object?>{
                'days': balance.oldestAgeDays,
              }),
            ),
          if (!balance.inTransit.isZero)
            _Line(
              context.tr('rcv.courierInTransit', <String, Object?>{
                'amount': balance.inTransit.format(),
                'count': balance.inTransitCount,
              }),
            ),
          _Line(
            balance.lastPaymentOn == null
                ? context.tr('rcv.neverPaid')
                : context.tr('rcv.lastPaid', <String, Object?>{
                    'when': formatRelative(balance.lastPaymentOn),
                  }),
          ),
          _Line(
            delay == null || !delay.reliable
                ? context.tr('rcv.noDelay')
                : context.tr('rcv.usualDelay', <String, Object?>{
                    'days': delay.medianDays,
                    'count': delay.samples,
                  }),
            color: EcomsbdColors.muted,
          ),
          if (agedBands.isNotEmpty) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xs),
            Wrap(
              spacing: EcomsbdSpacing.xs,
              runSpacing: EcomsbdSpacing.xs,
              children: <Widget>[
                for (final band in agedBands)
                  StatusChip(
                    label:
                        '${band.displayLabel} · ${band.outstanding.format()}',
                    tone: band.isOverdue ? Tone.bad : Tone.neutral,
                    showIcon: false,
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _Line extends StatelessWidget {
  const _Line(this.text, {this.color});

  final String text;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 2),
      child: Text(
        text,
        style: EcomsbdType.caption.copyWith(color: color ?? EcomsbdColors.ink),
      ),
    );
  }
}

class _FigureRow extends StatelessWidget {
  const _FigureRow({
    required this.label,
    required this.amount,
    this.caption,
    this.tone,
    this.estimated = false,
  });

  final String label;
  final String? caption;
  final Money amount;
  final Color? tone;
  final bool estimated;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: EcomsbdSpacing.sm),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(label, style: EcomsbdType.body),
                if (caption != null)
                  Text(
                    caption!,
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.muted2,
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(width: EcomsbdSpacing.sm),
          MoneyText(
            amount,
            estimated: estimated,
            style: EcomsbdType.bodyStrong.copyWith(color: tone),
          ),
        ],
      ),
    );
  }
}
