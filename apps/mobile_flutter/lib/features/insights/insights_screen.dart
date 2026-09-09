import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../demo/demo_dashboard.dart';
import '../../design/charts/bar_charts.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../shared/demo_data_notice.dart';
import '../shared/responsive.dart';

/// The Insights tab — profit and business intelligence.
///
/// Master spec section 120 sets the boundary this screen respects: only
/// insights the seller's *own* data can support. No fabricated category
/// benchmarks, and no ranking shown before there is enough sample to justify
/// one — the courier card says "no rank" rather than inventing a position.
class InsightsScreen extends ConsumerWidget {
  const InsightsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final topInset = MediaQuery.viewPaddingOf(context).top;

    return ListView(
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
        const DemoDataNotice(
          detail: 'Demo data — profit snapshots arrive in Phase E.',
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        HeroMoneyCard(
          eyebrow: 'Contribution profit · this month',
          amount: DemoDashboard.contributionProfit7d,
          subtitle:
              'Actual and estimated inputs separated · 82% of delivered '
              'orders have ad cost',
          quality: DataQuality.estimated,
          trailing: const StatusChip(label: '28.4% margin', tone: Tone.good),
          kpis: <HeroKpi>[
            const HeroKpi(
              label: 'Delivered sales',
              value: '৳2.16L',
              caption: 'realized',
            ),
            HeroKpi(
              label: 'Return loss',
              value: DemoDashboard.returnLoss.formatCompact(),
              caption: '27 returns',
              tone: Tone.bad,
            ),
            const HeroKpi(
              label: 'Delivery success',
              value: '86.8%',
              caption: '214 terminal attempts',
            ),
          ],
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        const _ProfitQualityNote(),
        const SizedBox(height: EcomsbdSpacing.md),
        const ResponsiveGrid(
          minTileWidth: 320,
          maxColumns: 2,
          spacing: EcomsbdSpacing.sm,
          children: <Widget>[
            PremiumChartCard(
              title: 'Revenue → profit bridge',
              subtitle: 'Where the money goes',
              trailing: StatusChip(label: 'This month', tone: Tone.info),
              child: HorizontalBarChart(data: DemoDashboard.profitBridge),
            ),
            PremiumChartCard(
              title: 'Profit by product',
              subtitle: 'Contribution profit · ranked',
              child: HorizontalBarChart(
                data: DemoDashboard.topProductsByProfit,
              ),
            ),
          ],
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        const ResponsiveGrid(
          minTileWidth: 320,
          maxColumns: 2,
          spacing: EcomsbdSpacing.sm,
          children: <Widget>[
            PremiumChartCard(
              title: 'Courier scorecard',
              subtitle: 'Own history · visible sample size',
              child: _CourierScorecard(),
            ),
            PremiumChartCard(
              title: 'Return loss map',
              subtitle: 'By product / area / reason',
              child: HorizontalBarChart(
                data: DemoDashboard.returnPressure,
                accent: BarAccent.red,
              ),
            ),
          ],
        ),
      ],
    );
  }
}

class _CourierScorecard extends StatelessWidget {
  const _CourierScorecard();

  @override
  Widget build(BuildContext context) {
    return Column(
      children: <Widget>[
        for (final courier in DemoDashboard.courierHealth)
          Padding(
            padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
            child: GlassListRow(
              leading: RowIcon(label: courier.name.substring(0, 1)),
              title: courier.name,
              subtitle: courier.detail,
              trailing: courier.value == '—'
                  // Master spec section 24: a ranking is not shown before the
                  // sample supports it, and the reason is stated.
                  ? const StatusChip(label: 'No rank yet', tone: Tone.neutral)
                  : Text(courier.value, style: EcomsbdType.money),
            ),
          ),
      ],
    );
  }
}

class _ProfitQualityNote extends StatelessWidget {
  const _ProfitQualityNote();

  @override
  Widget build(BuildContext context) {
    // Stacked rather than side by side: the badge carries a long label and a
    // Row would size it to its intrinsic width, overflowing a 360dp screen.
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          const Align(
            alignment: Alignment.centerLeft,
            child: DataQualityBadge(
              quality: DataQuality.estimated,
              detail: 'ad cost missing on 2 orders',
            ),
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          Text(
            'Estimated inputs are marked rather than blended in. '
            'A profit figure the app is not sure about never prints as exact.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
        ],
      ),
    );
  }
}
