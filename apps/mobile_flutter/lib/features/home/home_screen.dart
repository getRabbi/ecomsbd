import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../demo/demo_dashboard.dart';
import '../../design/charts/bar_charts.dart';
import '../../design/charts/donut_chart.dart';
import '../../design/charts/line_chart.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../shared/demo_data_notice.dart';
import '../orders/order_compose_screen.dart';
import '../products/products_screen.dart';
import '../shared/responsive.dart';
import 'widgets/seller_hero.dart';

/// The Home dashboard — the seller's daily money control screen.
///
/// Master spec section 1.1: this is not a generic analytics dashboard. The
/// order of the page is the order of the seller's questions: how much money is
/// out there, what needs my attention, how is the business trending.
///
/// Reproduces the prototype's hierarchy:
/// navy hero → COD outstanding hero → quick actions → needs attention →
/// protected profit → profit trend → COD position → delivery funnel →
/// courier health → top products → return pressure → live activity.
class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key, this.onOpenMenu, this.onNavigate});

  final VoidCallback? onOpenMenu;

  /// Route name to push, used by the quick actions and section links.
  final ValueChanged<String>? onNavigate;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final topInset = MediaQuery.viewPaddingOf(context).top;
    final pending = ref.watch(pendingMutationCountProvider).valueOrNull ?? 0;
    final isOffline = ref.watch(isOfflineProvider);

    return CustomScrollView(
      slivers: <Widget>[
        SliverToBoxAdapter(
          child: SellerHero(
            shopName: DemoDashboard.shopName,
            subtitle: DemoDashboard.shopSubtitle,
            chips: DemoDashboard.heroChips,
            topInset: topInset + 56,
          ),
        ),
        SliverToBoxAdapter(
          child: Transform.translate(
            // The money card lifts out of the hero, as in the prototype.
            offset: const Offset(0, -31),
            child: Padding(
              padding: const EdgeInsets.symmetric(
                horizontal: EcomsbdSpacing.page,
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  if (isOffline) ...<Widget>[
                    OfflineBanner(pendingCount: pending),
                    const SizedBox(height: EcomsbdSpacing.sm),
                  ],
                  const DemoDataNotice(),
                  const SizedBox(height: EcomsbdSpacing.sm),
                  _CodOutstandingCard(onNavigate: onNavigate),
                  const SizedBox(height: EcomsbdSpacing.md),
                  _QuickActions(onNavigate: onNavigate),
                  const SizedBox(height: EcomsbdSpacing.md),
                  _NeedsAttention(onNavigate: onNavigate),
                  SectionHeader(
                    title: 'Business pulse',
                    subtitle: '7-day profit, COD and delivery quality',
                    actionLabel: 'Full analytics',
                    onAction: () => onNavigate?.call('insights'),
                  ),
                  const _PulseCharts(),
                  const SizedBox(height: EcomsbdSpacing.md),
                  const _PerformanceCharts(),
                  const SizedBox(height: EcomsbdSpacing.md),
                  const _ProductCharts(),
                  SectionHeader(
                    title: 'Live activity',
                    subtitle: 'Money and operational events only',
                    actionLabel: 'All activity',
                    onAction: () => onNavigate?.call('notifications'),
                  ),
                  const _ActivityFeed(),
                  const SizedBox(height: EcomsbdSpacing.bottomNavClearance),
                ],
              ),
            ),
          ),
        ),
      ],
    );
  }
}

class _CodOutstandingCard extends StatelessWidget {
  const _CodOutstandingCard({this.onNavigate});

  final ValueChanged<String>? onNavigate;

  @override
  Widget build(BuildContext context) {
    return HeroMoneyCard(
      eyebrow: 'COD outstanding · actual + verified estimates',
      amount: DemoDashboard.codOutstanding,
      subtitle: DemoDashboard.codOutstandingSubtitle,
      trailing: StatusChip(
        label: '${DemoDashboard.overdue.format()} overdue',
        tone: Tone.bad,
      ),
      kpis: DemoDashboard.homeKpis,
    );
  }
}

class _QuickActions extends StatelessWidget {
  const _QuickActions({this.onNavigate});

  final ValueChanged<String>? onNavigate;

  @override
  Widget build(BuildContext context) {
    return ResponsiveGrid(
      minTileWidth: 150,
      maxColumns: 4,
      spacing: EcomsbdSpacing.xs,
      children: <Widget>[
        QuickActionTile(
          icon: Icons.add_rounded,
          title: 'New order',
          subtitle: 'Paste or manual',
          onTap: () => Navigator.of(context).push(
            MaterialPageRoute<void>(builder: (_) => const OrderComposeScreen()),
          ),
        ),
        const QuickActionTile(
          icon: Icons.shield_outlined,
          title: 'Risk check',
          subtitle: 'Phone history',
          // Risk lookup needs a provider data path that is not verified yet;
          // showing it as available would promise something the app cannot do.
          enabled: false,
        ),
        QuickActionTile(
          icon: Icons.receipt_long_outlined,
          title: 'Add payout',
          subtitle: 'API · CSV · manual',
          onTap: () => onNavigate?.call('money'),
        ),
        QuickActionTile(
          icon: Icons.inventory_2_outlined,
          title: 'Products',
          subtitle: 'Cost · stock · margin',
          onTap: () => Navigator.of(context).push(
            MaterialPageRoute<void>(builder: (_) => const ProductsScreen()),
          ),
        ),
      ],
    );
  }
}

class _NeedsAttention extends StatelessWidget {
  const _NeedsAttention({this.onNavigate});

  final ValueChanged<String>? onNavigate;

  @override
  Widget build(BuildContext context) {
    return AttentionCard(
      items: DemoDashboard.attentionItems,
      actionLabel: 'Review all',
      onAction: () => onNavigate?.call('money'),
      side: DarkHighlightPanel(
        label: "Today's protected profit",
        value: DemoDashboard.protectedProfit.format(),
        description:
            '31 delivered orders. Profit excludes 2 orders with '
            'missing ad cost.',
        actionLabel: 'Open profit breakdown',
        onAction: () => onNavigate?.call('insights'),
      ),
    );
  }
}

class _PulseCharts extends StatelessWidget {
  const _PulseCharts();

  @override
  Widget build(BuildContext context) {
    return ResponsiveGrid(
      minTileWidth: 320,
      maxColumns: 2,
      spacing: EcomsbdSpacing.sm,
      children: <Widget>[
        PremiumChartCard(
          title: 'Contribution profit',
          subtitle: 'Last 7 days · actual/estimated inputs separated',
          trailing: Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Text(
                DemoDashboard.contributionProfit7d.formatCompact(),
                style: EcomsbdType.metricValue,
              ),
              Text(
                '↑ 12.8% vs prev 7d',
                style: EcomsbdType.chip.copyWith(color: EcomsbdColors.green),
              ),
            ],
          ),
          child: const ProfitTrendChart(points: DemoDashboard.profitTrend),
        ),
        PremiumChartCard(
          title: 'COD position',
          subtitle: 'Current collectible pipeline',
          trailing: const StatusChip(label: '61 parcels', tone: Tone.info),
          child: Center(
            child: CodDonutChart(
              slices: DemoDashboard.codComposition,
              centerValue: DemoDashboard.codOutstanding.formatCompact(),
            ),
          ),
        ),
      ],
    );
  }
}

class _PerformanceCharts extends StatelessWidget {
  const _PerformanceCharts();

  @override
  Widget build(BuildContext context) {
    return ResponsiveGrid(
      minTileWidth: 320,
      maxColumns: 2,
      spacing: EcomsbdSpacing.sm,
      children: <Widget>[
        const PremiumChartCard(
          title: 'Delivery funnel',
          subtitle: 'This month · 284 booked',
          trailing: StatusChip(label: '86.8% success', tone: Tone.good),
          child: DeliveryFunnelChart(stages: DemoDashboard.deliveryFunnel),
        ),
        PremiumChartCard(
          title: 'Courier health',
          subtitle: 'Own history only · sample-aware',
          child: Column(
            children: <Widget>[
              for (final courier in DemoDashboard.courierHealth)
                Padding(
                  padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
                  child: GlassListRow(
                    leading: RowIcon(label: courier.name.substring(0, 1)),
                    title: courier.name,
                    subtitle: courier.detail,
                    trailingTop: courier.value,
                    trailingBottom: courier.unit,
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }
}

class _ProductCharts extends StatelessWidget {
  const _ProductCharts();

  @override
  Widget build(BuildContext context) {
    return ResponsiveGrid(
      minTileWidth: 320,
      maxColumns: 2,
      spacing: EcomsbdSpacing.sm,
      children: <Widget>[
        const PremiumChartCard(
          title: 'Top products by profit',
          subtitle: 'Not revenue · contribution profit',
          child: HorizontalBarChart(data: DemoDashboard.topProductsByProfit),
        ),
        PremiumChartCard(
          title: 'Return pressure',
          subtitle: 'Actionable loss signals',
          trailing: StatusChip(
            label: DemoDashboard.returnLoss.format(),
            tone: Tone.bad,
          ),
          child: const HorizontalBarChart(
            data: DemoDashboard.returnPressure,
            accent: BarAccent.red,
          ),
        ),
      ],
    );
  }
}

class _ActivityFeed extends StatelessWidget {
  const _ActivityFeed();

  @override
  Widget build(BuildContext context) {
    return Column(
      children: <Widget>[
        for (final item in DemoDashboard.activity)
          Padding(
            padding: const EdgeInsets.only(bottom: EcomsbdSpacing.sm),
            child: SellerFeedCard(
              author: item.author,
              timestamp: item.timestamp,
              headline: item.headline,
              body: item.body,
              avatarLabel: item.avatarLabel,
              avatarColor: item.avatarColor,
              status: StatusChip(
                label: item.statusLabel,
                tone: item.statusTone,
              ),
            ),
          ),
      ],
    );
  }
}
