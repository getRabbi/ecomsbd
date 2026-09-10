import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/money.dart';
import '../../data/analytics/analytics_providers.dart';
import '../../data/analytics/models.dart';
import '../../design/charts/bar_charts.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../expenses/expenses_screen.dart';
import '../shared/data_state.dart';
import '../shared/responsive.dart';

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
    final topInset = MediaQuery.viewPaddingOf(context).top;
    final profit = ref.watch(profitReportProvider);
    final returns = ref.watch(returnReportProvider);
    final products = ref.watch(productProfitProvider);

    return RefreshIndicator(
      onRefresh: () async {
        ref.invalidate(profitReportProvider);
        ref.invalidate(returnReportProvider);
        ref.invalidate(productProfitProvider);
        await ref.read(profitReportProvider.future);
      },
      child: ListView(
        padding: EdgeInsets.fromLTRB(
          EcomsbdSpacing.page,
          topInset + EcomsbdTouch.minTarget + EcomsbdSpacing.lg,
          EcomsbdSpacing.page,
          EcomsbdSpacing.bottomNavClearance,
        ),
        children: <Widget>[
          const PageHeader(
            eyebrow: 'Accumulated-history advantage',
            title: 'Profit & business intelligence',
            description: 'Only insights that your own data can support.',
          ),
          profit.when(
            loading: () => SkeletonLoader.card(height: 180),
            error: (error, _) => Text(
              'Could not load your profit figures.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
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
            title: 'Costs you enter yourself',
            subtitle: 'Ad spend, packaging, rent',
            actionLabel: 'Expenses',
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
                title: 'Revenue → profit bridge',
                subtitle: 'Where the money goes',
                trailing: const StatusChip(
                  label: 'Last 30 days',
                  tone: Tone.info,
                ),
                child: ChartData<ProfitReport>(
                  value: profit,
                  emptyMessage: 'No settled parcels in the last 30 days yet.',
                  isEmpty: (report) => report.parcelCount == 0,
                  builder: (report) =>
                      HorizontalBarChart(data: _bridge(report)),
                ),
              ),
              PremiumChartCard(
                title: 'Profit by product',
                subtitle: 'Contribution profit · ranked',
                child: ChartData<List<ProductLine>>(
                  value: products,
                  emptyMessage: 'No delivered products in the last 30 days.',
                  isEmpty: (rows) => rows.isEmpty,
                  builder: (rows) => HorizontalBarChart(
                    data: <HorizontalBarDatum>[
                      for (final row in rows.take(6))
                        HorizontalBarDatum(
                          label: row.productName,
                          value: row.profit.paisa.abs(),
                          displayValue: row.hasEnoughSample
                              ? row.profit.formatCompact()
                              : '${row.profit.formatCompact()} · thin',
                        ),
                    ],
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          ResponsiveGrid(
            minTileWidth: 320,
            maxColumns: 2,
            spacing: EcomsbdSpacing.sm,
            children: <Widget>[
              PremiumChartCard(
                title: 'Courier scorecard',
                subtitle: 'Own history · visible sample size',
                child: ChartData<ReturnReport>(
                  value: returns,
                  emptyMessage:
                      'No finished parcels to judge a courier on yet.',
                  isEmpty: (report) => report.byCourier.isEmpty,
                  builder: (report) =>
                      _RateRows(lines: report.byCourier, unit: 'finished'),
                ),
              ),
              PremiumChartCard(
                title: 'Return loss map',
                subtitle: 'By product and area',
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
                  emptyMessage: 'No returns in the last 30 days.',
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
                              ? '${line.returnCount} back'
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
  static List<HorizontalBarDatum> _bridge(ProfitReport report) {
    final parts = <(String, Money)>[
      ('Revenue', report.realizedRevenue),
      ('Goods', report.itemCost),
      ('Delivery', report.deliveryCharge),
      ('COD fee', report.codFee),
      ('Returns', report.returnCharge),
      ('Packaging', report.packaging),
      ('Ads', report.adCost),
      ('Write-offs', report.writeOffCost),
      ('Profit', report.contributionProfit),
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
      eyebrow: 'Contribution profit · last 30 days',
      amount: report.contributionProfit,
      subtitle: total == 0
          ? 'No parcels have finished in this window yet.'
          : '$measured of $total parcel${total == 1 ? '' : 's'} settled. '
                'Estimated inputs are marked, never blended in.',
      quality: _quality(report),
      trailing: report.marginLabel == null
          ? null
          : StatusChip(
              label: report.marginLabel!,
              tone: report.contributionProfit.paisa < 0 ? Tone.bad : Tone.good,
            ),
      kpis: <HeroKpi>[
        HeroKpi(
          label: 'Delivered sales',
          value: report.realizedRevenue.formatCompact(),
          caption: 'realized',
        ),
        HeroKpi(
          label: 'Return loss',
          value: (returns?.directLoss ?? const Money(0)).formatCompact(),
          caption: '${returns?.returnCount ?? 0} returns',
          tone: (returns?.directLoss.paisa ?? 0) > 0 ? Tone.bad : null,
        ),
        HeroKpi(
          label: 'After fixed costs',
          value: report.operatingProfit.formatCompact(),
          caption: 'operating',
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
              detail: _detail,
            ),
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          Text(
            'Estimated inputs are marked rather than blended in. '
            'A profit figure the app is not sure about never prints as exact.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          if (report.unallocatedAdSpend.paisa > 0) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            Text(
              '${report.unallocatedAdSpend.format()} of ad spend has not been '
              'allocated to any parcel, so it sits below contribution profit '
              'rather than being spread across unrelated orders.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
        ],
      ),
    );
  }

  String get _detail {
    if (report.missingCount > 0) {
      return 'a cost is missing on ${report.missingCount} '
          'parcel${report.missingCount == 1 ? '' : 's'}';
    }
    if (report.estimatedCount > 0) {
      return '${report.estimatedCount} '
          'parcel${report.estimatedCount == 1 ? '' : 's'} not settled yet';
    }
    return 'every input settled';
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
              subtitle: '${line.parcelCount} $unit · ${line.returnCount} back',
              trailing: line.hasEnoughSample
                  ? Text('${line.rateLabel} back', style: EcomsbdType.money)
                  // Master spec section 24: a ranking is not shown before the
                  // sample supports it, and the reason is stated.
                  : const StatusChip(label: 'No rank yet', tone: Tone.neutral),
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
      title: 'Why parcels came back',
      subtitle: '${report.returnCount} returns in the last 30 days',
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
                '${report.unknownReasonCount} return'
                '${report.unknownReasonCount == 1 ? '' : 's'} with no reason '
                'recorded. Adding one when you log a return makes this list '
                'worth acting on.',
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              ),
            ),
        ],
      ),
    );
  }
}
