import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/analytics/analytics_providers.dart';
import '../../data/analytics/models.dart';
import '../../data/analytics/rto_models.dart';
import '../../design/charts/bar_charts.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../expenses/expenses_screen.dart';
import '../shared/data_state.dart';
import '../shared/responsive.dart';
import 'rto_screen.dart';

/// The Insights tab — profit and business intelligence.
///
/// Master spec section 120 sets the boundary this screen respects: only
/// insights the seller's *own* data can support. No fabricated category
/// benchmarks, and no ranking shown before there is enough sample to justify
/// one — a courier or product below the threshold says "no rank yet" rather
/// than inventing a position.
///
/// Section 135 sets the other one: the profit headline carries how much of it
/// was measured. A ৳40,000 profit built from twelve estimated parcels and one
/// built from twelve settled ones are different claims.
class InsightsScreen extends ConsumerWidget {
  const InsightsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profit = ref.watch(profitReportProvider);
    final returns = ref.watch(returnReportProvider);
    final products = ref.watch(productProfitProvider);

    return RefreshIndicator(
      edgeOffset: EcomsbdLayout.shellRefreshOffset(context),
      onRefresh: () async {
        ref.invalidate(profitReportProvider);
        ref.invalidate(returnReportProvider);
        ref.invalidate(productProfitProvider);
        ref.invalidate(rtoSummaryProvider);
        await ref.read(profitReportProvider.future);
      },
      child: ListView(
        padding: EdgeInsets.fromLTRB(
          EcomsbdSpacing.page,
          EcomsbdLayout.shellTopPadding(context),
          EcomsbdSpacing.page,
          EcomsbdSpacing.bottomNavClearance,
        ),
        children: <Widget>[
          PageHeader(
            eyebrow: context.tr('insights.eyebrow'),
            title: context.tr('insights.title'),
            description: context.tr('insights.description'),
          ),
          profit.when(
            loading: () => SkeletonLoader.card(height: 180),
            error: (error, _) => error is ApiError && error.isPlanLimited
                ? const GlassCard(child: PlanLockedNotice())
                : Text(
                    context.tr('insights.couldNotLoadProfit'),
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.muted,
                    ),
                  ),
            data: (sourced) => Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                if (sourced.isStale) ...<Widget>[
                  StaleDataNotice(
                    fetchedAt: sourced.fetchedAt,
                    onRetry: () => ref.invalidate(profitReportProvider),
                  ),
                  const SizedBox(height: EcomsbdSpacing.sm),
                ],
                _ProfitHero(
                  report: sourced.value,
                  returns: returns.valueOrNull?.value,
                ),
                const SizedBox(height: EcomsbdSpacing.sm),
                _ProfitQualityNote(report: sourced.value),
              ],
            ),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          SectionHeader(
            title: context.tr('insights.ownCosts'),
            subtitle: context.tr('insights.ownCostsSub'),
            actionLabel: context.tr('insights.expenses'),
            onAction: () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const ExpensesScreen()),
            ),
          ),
          ResponsiveGrid(
            minTileWidth: 320,
            maxColumns: 2,
            spacing: EcomsbdSpacing.sm,
            children: <Widget>[
              PremiumChartCard(
                title: context.tr('insights.bridge'),
                subtitle: context.tr('insights.bridgeSub'),
                trailing: StatusChip(
                  label: context.tr('chart.last30'),
                  tone: Tone.info,
                ),
                child: ChartData<ProfitReport>(
                  value: profit,
                  emptyMessage: context.tr('chart.emptyNoSettled'),
                  isEmpty: (report) => report.parcelCount == 0,
                  builder: (report) =>
                      HorizontalBarChart(data: _bridge(context, report)),
                ),
              ),
              PremiumChartCard(
                title: context.tr('insights.profitByProduct'),
                subtitle: context.tr('insights.profitByProductSub'),
                child: ChartData<List<ProductLine>>(
                  value: products,
                  emptyMessage: context.tr('chart.emptyNoDelivered'),
                  isEmpty: (rows) => rows.isEmpty,
                  builder: (rows) => HorizontalBarChart(
                    data: <HorizontalBarDatum>[
                      for (final row in rows.take(6))
                        HorizontalBarDatum(
                          label: row.productName,
                          value: row.profit.paisa.abs(),
                          displayValue: row.hasEnoughSample
                              ? row.profit.formatCompact()
                              : '${row.profit.formatCompact()} · '
                                    '${context.tr('insights.thin')}',
                        ),
                    ],
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          _RtoEntry(value: ref.watch(rtoSummaryProvider).valueOrNull?.value),
          const SizedBox(height: EcomsbdSpacing.md),
          ResponsiveGrid(
            minTileWidth: 320,
            maxColumns: 2,
            spacing: EcomsbdSpacing.sm,
            children: <Widget>[
              PremiumChartCard(
                title: context.tr('insights.courierScorecard'),
                subtitle: context.tr('insights.courierScorecardSub'),
                child: ChartData<ReturnReport>(
                  value: returns,
                  emptyMessage: context.tr('chart.emptyNoCourierSample'),
                  isEmpty: (report) => report.byCourier.isEmpty,
                  builder: (report) => _RateRows(
                    lines: report.byCourier,
                    unit: context.tr('insights.finishedUnit'),
                  ),
                ),
              ),
              PremiumChartCard(
                title: context.tr('insights.returnLossMap'),
                subtitle: context.tr('insights.returnLossMapSub'),
                trailing: returns.valueOrNull == null
                    ? null
                    : StatusChip(
                        label: returns.valueOrNull!.value.directLoss.format(),
                        tone: returns.valueOrNull!.value.directLoss.paisa > 0
                            ? Tone.bad
                            : Tone.neutral,
                      ),
                child: ChartData<ReturnReport>(
                  value: returns,
                  emptyMessage: context.tr('chart.emptyNoReturns'),
                  isEmpty: (report) => report.returnCount == 0,
                  builder: (report) => HorizontalBarChart(
                    accent: BarAccent.red,
                    data: <HorizontalBarDatum>[
                      for (final line in [
                        ...report.byProduct
                            .where((l) => l.returnCount > 0)
                            .take(3),
                        ...report.byArea
                            .where((l) => l.returnCount > 0)
                            .take(3),
                      ])
                        HorizontalBarDatum(
                          label: line.label,
                          value: line.loss.paisa == 0
                              ? line.returnCount
                              : line.loss.paisa,
                          displayValue: line.loss.paisa == 0
                              ? context.tr(
                                  'courier.backRate',
                                  <String, Object?>{'rate': line.returnCount},
                                )
                              : line.loss.formatCompact(),
                        ),
                    ],
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          _ReturnReasons(value: returns),
        ],
      ),
    );
  }

  /// Revenue down to profit, one bar per deduction.
  ///
  /// Item cost first because it is almost always the largest, and a seller
  /// looking at this wants to know whether the courier or the supplier is
  /// eating the margin.
  static List<HorizontalBarDatum> _bridge(
    BuildContext context,
    ProfitReport report,
  ) {
    final parts = <(String, Money)>[
      (context.tr('bridge.revenue'), report.realizedRevenue),
      (context.tr('bridge.goods'), report.itemCost),
      (context.tr('bridge.delivery'), report.deliveryCharge),
      (context.tr('bridge.codFee'), report.codFee),
      (context.tr('bridge.returns'), report.returnCharge),
      (context.tr('bridge.packaging'), report.packaging),
      (context.tr('bridge.ads'), report.adCost),
      (context.tr('bridge.writeOffs'), report.writeOffCost),
      (context.tr('bridge.profit'), report.contributionProfit),
    ];
    return <HorizontalBarDatum>[
      for (final (label, amount) in parts)
        if (amount.paisa != 0)
          HorizontalBarDatum(
            label: label,
            value: amount.paisa.abs(),
            displayValue: amount.formatCompact(),
          ),
    ];
  }
}

class _ProfitHero extends StatelessWidget {
  const _ProfitHero({required this.report, this.returns});

  final ProfitReport report;
  final ReturnReport? returns;

  @override
  Widget build(BuildContext context) {
    final measured = report.actualCount;
    final total = report.parcelCount;

    return HeroMoneyCard(
      eyebrow: context.tr('insights.profitEyebrow'),
      amount: report.contributionProfit,
      subtitle: total == 0
          ? context.tr('insights.noParcelsWindow')
          : context.trPlural('insights.settledOf', total, <String, Object?>{
              'measured': measured,
            }),
      quality: _quality(report),
      trailing: report.marginLabel == null
          ? null
          : StatusChip(
              label: report.marginLabel!,
              tone: report.contributionProfit.paisa < 0 ? Tone.bad : Tone.good,
            ),
      kpis: <HeroKpi>[
        HeroKpi(
          label: context.tr('insights.deliveredSales'),
          value: report.realizedRevenue.formatCompact(),
          caption: context.tr('insights.realized'),
        ),
        HeroKpi(
          label: context.tr('insights.returnLoss'),
          value: (returns?.directLoss ?? const Money(0)).formatCompact(),
          caption: context.tr('insights.returnsCaption', <String, Object?>{
            'count': returns?.returnCount ?? 0,
          }),
          tone: (returns?.directLoss.paisa ?? 0) > 0 ? Tone.bad : null,
        ),
        HeroKpi(
          label: context.tr('insights.afterFixed'),
          value: report.operatingProfit.formatCompact(),
          caption: context.tr('insights.operating'),
          tone: report.operatingProfit.paisa < 0 ? Tone.bad : null,
        ),
      ],
    );
  }

  static DataQuality _quality(ProfitReport report) {
    if (report.missingCount > 0) return DataQuality.missing;
    if (report.estimatedCount > 0) return DataQuality.estimated;
    return DataQuality.actual;
  }
}

class _ProfitQualityNote extends StatelessWidget {
  const _ProfitQualityNote({required this.report});

  final ProfitReport report;

  @override
  Widget build(BuildContext context) {
    // Stacked rather than side by side: the badge carries a long label and a
    // Row would size it to its intrinsic width, overflowing a 360dp screen.
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Align(
            alignment: Alignment.centerLeft,
            child: DataQualityBadge(
              quality: _ProfitHero._quality(report),
              detail: _detailFor(context),
            ),
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          Text(
            context.tr('insights.qualityNote'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          if (report.unallocatedAdSpend.paisa > 0) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            Text(
              context.tr('insights.unallocatedAds', <String, Object?>{
                'amount': report.unallocatedAdSpend.format(),
              }),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
        ],
      ),
    );
  }

  String _detailFor(BuildContext context) {
    if (report.missingCount > 0) {
      return context.trPlural('insights.detailMissing', report.missingCount);
    }
    if (report.estimatedCount > 0) {
      return context.trPlural(
        'insights.detailEstimated',
        report.estimatedCount,
      );
    }
    return context.tr('insights.detailAllSettled');
  }
}

/// Return rate per courier or area, with the sample-size rule visible.
class _RateRows extends StatelessWidget {
  const _RateRows({required this.lines, required this.unit});

  final List<RateLine> lines;
  final String unit;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: <Widget>[
        for (final line in lines.take(5))
          Padding(
            padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
            child: GlassListRow(
              leading: RowIcon(label: line.label.substring(0, 1).toUpperCase()),
              title: line.label,
              subtitle: context.tr('insights.rateRowSub', <String, Object?>{
                'count': line.parcelCount,
                'unit': unit,
                'returned': line.returnCount,
              }),
              trailing: line.hasEnoughSample
                  ? Text(
                      context.tr('courier.backRate', <String, Object?>{
                        'rate': line.rateLabel,
                      }),
                      style: EcomsbdType.money,
                    )
                  // Master spec section 24: a ranking is not shown before the
                  // sample supports it, and the reason is stated.
                  : StatusChip(
                      label: context.tr('common.noRankYet'),
                      tone: Tone.neutral,
                    ),
            ),
          ),
      ],
    );
  }
}

/// Why parcels came back (master spec section 19).
///
/// The count with no reason recorded is shown alongside the reasons, because
/// how much of the picture is missing is itself something the seller should
/// know before drawing a conclusion from it.
class _ReturnReasons extends StatelessWidget {
  const _ReturnReasons({required this.value});

  final AsyncValue<dynamic> value;

  @override
  Widget build(BuildContext context) {
    final report = value.valueOrNull?.value as ReturnReport?;
    if (report == null || report.returnCount == 0) {
      return const SizedBox.shrink();
    }

    final entries = report.byReason.entries.toList()
      ..sort((a, b) => b.value.compareTo(a.value));

    return PremiumChartCard(
      title: context.tr('insights.whyBack'),
      subtitle: context.tr('insights.returnsInWindow', <String, Object?>{
        'count': report.returnCount,
      }),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          for (final entry in entries)
            Padding(
              padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: Text(
                      ReturnReason.tryParse(entry.key)?.label ?? entry.key,
                      style: EcomsbdType.body,
                    ),
                  ),
                  Text('${entry.value}', style: EcomsbdType.money),
                ],
              ),
            ),
          if (report.unknownReasonCount > 0)
            Padding(
              padding: const EdgeInsets.only(top: EcomsbdSpacing.xs),
              child: Text(
                context.trPlural(
                  'insights.noReasonRecorded',
                  report.unknownReasonCount,
                ),
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              ),
            ),
        ],
      ),
    );
  }
}

/// The way into Returns & RTO, carrying the headline fact when it is known.
class _RtoEntry extends StatelessWidget {
  const _RtoEntry({required this.value});

  final RtoSummary? value;

  @override
  Widget build(BuildContext context) {
    final counts = value?.counts;
    return QuickActionTile(
      icon: Icons.assignment_return_outlined,
      title: context.tr('rto.title'),
      subtitle: counts == null || counts.completed == 0
          ? context.tr('rto.tileSub')
          : context.tr('rto.tileFact', <String, Object?>{
              'rate': counts.rateLabel,
              'rto': counts.rto,
              'completed': counts.completed,
            }),
      onTap: () => Navigator.of(context).push(
        MaterialPageRoute<void>(builder: (_) => const RtoScreen()),
      ),
    );
  }
}
