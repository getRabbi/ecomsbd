import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/money.dart';
import '../../data/analytics/analytics_providers.dart';
import '../../data/analytics/insights_models.dart';
import '../../data/analytics/rto_models.dart';
import '../../data/commerce/repository_support.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../shared/data_state.dart';
import '../shared/responsive.dart';
import 'insight_screens.dart';
import 'insight_widgets.dart';
import 'profit_detail_screen.dart';
import 'rto_screen.dart';

/// The Insights tab — the business at a glance, then one area at a time.
///
/// Master spec section 120 sets the boundary: only insights the seller's own
/// data can support. Every figure here is the server's (`/analytics/insights`),
/// compared with the equivalent earlier period only when the earlier base is
/// large enough for a percentage to mean something. Thin samples say "limited
/// data"; money the member may not see says why instead of showing zero.
///
/// Kept short on purpose: the overview, what changed, one trend, and a way
/// into each area. The detail lives one tap away rather than in an endless
/// scroll.
class InsightsScreen extends ConsumerWidget {
  const InsightsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final overview = ref.watch(insightsOverviewProvider);
    final trend = ref.watch(insightsTrendProvider);
    final loaded = overview.valueOrNull?.value;

    return RefreshIndicator(
      edgeOffset: EcomsbdLayout.shellRefreshOffset(context),
      onRefresh: () async {
        ref
          ..invalidate(insightsOverviewProvider)
          ..invalidate(insightsTrendProvider)
          ..invalidate(rtoSummaryProvider);
        await ref.read(insightsOverviewProvider.future);
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
          const InsightRangePicker(),
          ...sourcedSection<InsightsOverview>(
            context,
            overview,
            onRetry: () => ref.invalidate(insightsOverviewProvider),
            data: (value) => <Widget>[
              _OverviewGrid(overview: value),
              if (value.moneyLocked != null) ...<Widget>[
                const SizedBox(height: EcomsbdSpacing.sm),
                MoneyLockedNotice(reason: value.moneyLocked!),
              ],
              const SizedBox(height: EcomsbdSpacing.md),
              WhatChangedCard(explanations: value.explanations),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          _TrendCard(value: trend),
          SectionHeader(
            title: context.tr('ins.explore'),
            subtitle: context.tr('ins.exploreSub'),
          ),
          _ExploreTiles(overview: loaded),
        ],
      ),
    );
  }
}

class _OverviewGrid extends StatelessWidget {
  const _OverviewGrid({required this.overview});

  final InsightsOverview overview;

  @override
  Widget build(BuildContext context) {
    final money = overview.money;
    final stock = overview.stock;
    final rto = overview.rto;
    return ResponsiveGrid(
      minTileWidth: 150,
      maxColumns: 3,
      spacing: EcomsbdSpacing.sm,
      children: <Widget>[
        if (money != null) ...<Widget>[
          MetricTile(
            label: context.tr('ins.revenue'),
            value: Money(money.revenue.current).formatCompact(),
            caption: changeCaption(context, money.revenue),
          ),
          MetricTile(
            label: context.tr('ins.profit'),
            value: Money(money.profit.current).formatCompact(),
            // Section 135: a profit with unknown costs is not exact, and
            // says so on the tile rather than in a footnote.
            caption: money.missingParcels > 0
                ? context.tr('ins.missingCost', <String, Object?>{
                    'count': money.missingParcels,
                  })
                : money.estimatedParcels > 0
                ? context.tr('ins.estimated')
                : changeCaption(context, money.profit),
            tone: money.profit.current < 0 ? Tone.bad : null,
          ),
        ],
        MetricTile(
          label: context.tr('ins.orders'),
          value: '${overview.orders.current}',
          caption: changeCaption(context, overview.orders),
        ),
        MetricTile(
          label: context.tr('ins.delivered'),
          value: '${overview.delivered.current}',
          caption: changeCaption(context, overview.delivered),
        ),
        MetricTile(
          label: context.tr('ins.rtoRate'),
          value: rto.completed == 0
              ? context.tr('ins.notEnough')
              : rto.sufficient
              ? rto.rateLabel
              : context.tr('ins.limited'),
          caption: context.tr('ins.rtoOf', <String, Object?>{
            'rto': rto.rto,
            'completed': rto.completed,
          }),
        ),
        if (money != null) ...<Widget>[
          MetricTile(
            label: context.tr('ins.received'),
            value: Money(money.received.current).formatCompact(),
            caption: changeCaption(context, money.received),
          ),
          MetricTile(
            label: context.tr('ins.receivable'),
            value: money.receivable.formatCompact(),
            caption: context.tr('ins.overdueCaption', <String, Object?>{
              'amount': money.overdue.formatCompact(),
            }),
            tone: money.overdue.paisa > 0 ? Tone.warning : null,
          ),
          MetricTile(
            label: context.tr('ins.discrepancies'),
            value: '${money.discrepancyCount}',
            caption: context.tr('ins.discrepancyCaption', <String, Object?>{
              'amount': money.discrepancy.formatCompact(),
            }),
            tone: money.discrepancyCount > 0 ? Tone.bad : null,
          ),
        ],
        if (stock != null)
          MetricTile(
            label: context.tr('ins.stock'),
            value: context.tr('ins.stockValue', <String, Object?>{
              'low': stock.lowStockItems,
              'out': stock.outOfStockItems,
            }),
            caption: context.tr('ins.slowCaption', <String, Object?>{
              'count': stock.slowMovingItems,
              'days': stock.slowMovingDays,
            }),
            tone: stock.outOfStockItems > 0 ? Tone.warning : null,
          ),
      ],
    );
  }
}

/// Profit per day (or week) when money is visible, otherwise orders.
class _TrendCard extends StatelessWidget {
  const _TrendCard({required this.value});

  final AsyncValue<Sourced<InsightsTrend>> value;

  @override
  Widget build(BuildContext context) {
    final trend = value.valueOrNull?.value;
    final money = trend != null && trend.moneyLocked == null;
    final title = context.tr(money ? 'ins.trendProfit' : 'ins.trendOrders');
    return PremiumChartCard(
      title: title,
      subtitle: context.tr(
        trend?.granularity == 'week' ? 'ins.trendSubWeek' : 'ins.trendSubDay',
      ),
      minHeight: 190,
      child: ChartData<InsightsTrend>(
        value: value,
        emptyMessage: context.tr('ins.trendEmpty'),
        isEmpty: (t) => t.isEmpty,
        builder: (t) => InsightBarChart(
          values: <int>[
            for (final b in t.buckets)
              money ? (b.profit?.paisa ?? 0) : b.orders,
          ],
          semanticsLabel: context.tr('ins.chartLabel', <String, Object?>{
            'title': title,
            'count': t.buckets.length,
          }),
          firstLabel: shortDate(t.buckets.first.start),
          lastLabel: shortDate(t.buckets.last.start),
        ),
      ),
    );
  }
}

/// One tile per area, in the order a seller works through the business.
/// A tile the member's role cannot open (as the server told us) is left out.
class _ExploreTiles extends ConsumerWidget {
  const _ExploreTiles({required this.overview});

  final InsightsOverview? overview;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final noMoney = overview?.moneyLocked == 'PERMISSION';
    final noStock = overview != null && overview!.stock == null;
    final rto = ref.watch(rtoSummaryProvider).valueOrNull?.value.counts;

    void open(Widget screen) => Navigator.of(
      context,
    ).push(MaterialPageRoute<void>(builder: (_) => screen));

    return ResponsiveGrid(
      minTileWidth: 280,
      maxColumns: 2,
      spacing: EcomsbdSpacing.sm,
      children: <Widget>[
        if (!noMoney)
          QuickActionTile(
            icon: Icons.trending_up_rounded,
            title: context.tr('ins.profitTile'),
            subtitle: context.tr('ins.profitTileSub'),
            onTap: () => open(const ProfitDetailScreen()),
          ),
        if (!noMoney)
          QuickActionTile(
            icon: Icons.account_balance_wallet_outlined,
            title: context.tr('ins.cashTile'),
            subtitle: context.tr('ins.cashTileSub'),
            onTap: () => open(const InsightsCashScreen()),
          ),
        QuickActionTile(
          icon: Icons.assignment_return_outlined,
          title: context.tr('rto.title'),
          subtitle: _rtoFact(context, rto),
          onTap: () => open(const RtoScreen()),
        ),
        if (!noStock)
          QuickActionTile(
            icon: Icons.inventory_2_outlined,
            title: context.tr('ins.productsTile'),
            subtitle: context.tr('ins.productsTileSub'),
            onTap: () => open(const InsightsProductsScreen()),
          ),
        QuickActionTile(
          icon: Icons.local_shipping_outlined,
          title: context.tr('ins.couriersTile'),
          subtitle: context.tr('ins.couriersTileSub'),
          onTap: () => open(const InsightsCouriersScreen()),
        ),
        if (!noStock)
          QuickActionTile(
            icon: Icons.warehouse_outlined,
            title: context.tr('ins.inventoryTile'),
            subtitle: context.tr('ins.inventoryTileSub'),
            onTap: () => open(const InsightsInventoryScreen()),
          ),
        QuickActionTile(
          icon: Icons.people_outline_rounded,
          title: context.tr('ins.customersTile'),
          subtitle: context.tr('ins.customersTileSub'),
          onTap: () => open(const InsightsCustomersScreen()),
        ),
      ],
    );
  }

  static String _rtoFact(BuildContext context, RtoCounts? counts) {
    if (counts == null || counts.completed == 0) {
      return context.tr('rto.tileSub');
    }
    return context.tr('rto.tileFact', <String, Object?>{
      'rate': counts.rateLabel,
      'rto': counts.rto,
      'completed': counts.completed,
    });
  }
}
