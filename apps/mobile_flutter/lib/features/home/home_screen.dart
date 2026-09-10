import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../data/analytics/analytics_providers.dart';
import '../../data/analytics/models.dart';
import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/repository_support.dart';
import '../../data/money/money_providers.dart';
import '../../data/money/models.dart' show AgingBand;
import '../../design/charts/bar_charts.dart' as charts;
import '../../design/charts/donut_chart.dart';
import '../../design/charts/line_chart.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../notifications/notification_centre_screen.dart';
import '../orders/order_compose_screen.dart';
import '../products/products_screen.dart';
import '../shared/data_state.dart';
import '../shared/responsive.dart';
import 'widgets/seller_hero.dart';

/// The Home dashboard — the seller's daily money control screen.
///
/// Master spec section 1.1: this is not a generic analytics dashboard. The
/// order of the page is the order of the seller's questions: how much money is
/// out there, what needs my attention, how is the business trending.
///
/// Every figure comes from `/v1/analytics/home`, `/v1/analytics/profit` and
/// the money core. Nothing on this screen is a fixture: a dashboard that falls
/// back to plausible demo numbers when a call fails is worse than one that
/// says it could not load, because the seller cannot tell the difference.
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
    final home = ref.watch(homeMetricsProvider);
    final shopName = ref.watch(shopNameProvider);

    return RefreshIndicator(
      onRefresh: () async {
        ref.invalidate(homeMetricsProvider);
        ref.invalidate(profitReportProvider);
        ref.invalidate(moneySummaryProvider);
        ref.invalidate(productProfitProvider);
        ref.invalidate(returnReportProvider);
        await ref.read(homeMetricsProvider.future);
      },
      child: CustomScrollView(
        slivers: <Widget>[
          SliverToBoxAdapter(
            child: SellerHero(
              shopName: shopName ?? 'Your shop',
              subtitle: _heroSubtitle(home.valueOrNull?.value),
              chips: _heroChips(home.valueOrNull?.value),
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
                    _HomeBody(home: home, onNavigate: onNavigate),
                    const SizedBox(height: EcomsbdSpacing.bottomNavClearance),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  static String _heroSubtitle(HomeMetrics? metrics) {
    if (metrics == null) return 'Loading today';
    return '${metrics.ordersToday} order${metrics.ordersToday == 1 ? '' : 's'} '
        'today · ${metrics.deliveredToday} delivered';
  }

  static List<String> _heroChips(HomeMetrics? metrics) {
    if (metrics == null) return const <String>[];
    return <String>[
      if (metrics.codOverdue.paisa > 0)
        '${metrics.codOverdue.format()} overdue',
      if (metrics.mismatchCount > 0)
        '${metrics.mismatchCount} mismatch'
            '${metrics.mismatchCount == 1 ? '' : 'es'}',
      if (metrics.returnedToday > 0) '${metrics.returnedToday} returned today',
    ];
  }
}

class _HomeBody extends ConsumerWidget {
  const _HomeBody({required this.home, this.onNavigate});

  final AsyncValue<Sourced<HomeMetrics>> home;
  final ValueChanged<String>? onNavigate;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return home.when(
      loading: () => const Padding(
        padding: EdgeInsets.symmetric(vertical: EcomsbdSpacing.xl),
        child: Center(child: CircularProgressIndicator()),
      ),
      error: (error, _) => ErrorStateCard(
        // Only ApiError reaches here: the repositories translate everything
        // they raise, and anything else is a bug worth seeing as a crash
        // rather than as a tidy "could not load" card.
        error: error is ApiError ? error : ApiError.unexpected(error),
        onRetry: () => ref.invalidate(homeMetricsProvider),
      ),
      data: (sourced) {
        final metrics = sourced.value;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            if (sourced.isStale) ...<Widget>[
              StaleDataNotice(
                fetchedAt: sourced.fetchedAt,
                onRetry: () => ref.invalidate(homeMetricsProvider),
              ),
              const SizedBox(height: EcomsbdSpacing.sm),
            ],
            _CodOutstandingCard(metrics: metrics),
            const SizedBox(height: EcomsbdSpacing.md),
            _QuickActions(onNavigate: onNavigate),
            const SizedBox(height: EcomsbdSpacing.md),
            _NeedsAttention(metrics: metrics, onNavigate: onNavigate),
            SectionHeader(
              title: 'Business pulse',
              subtitle: '30-day profit, COD and delivery quality',
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
              onAction: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => const NotificationCentreScreen(),
                ),
              ),
            ),
            const _ActivityFeed(),
          ],
        );
      },
    );
  }
}

class _CodOutstandingCard extends StatelessWidget {
  const _CodOutstandingCard({required this.metrics});

  final HomeMetrics metrics;

  @override
  Widget build(BuildContext context) {
    return HeroMoneyCard(
      eyebrow: 'COD outstanding · with couriers right now',
      amount: metrics.codOutstanding,
      subtitle: _subtitle,
      quality: metrics.hasEstimates
          ? DataQuality.estimated
          : DataQuality.actual,
      trailing: metrics.codOverdue.paisa > 0
          ? StatusChip(
              label: '${metrics.codOverdue.format()} overdue',
              tone: Tone.bad,
            )
          : null,
      kpis: <HeroKpi>[
        HeroKpi(
          label: 'Realized today',
          value: metrics.realizedRevenue.formatCompact(),
          caption: '${metrics.deliveredToday} delivered',
        ),
        HeroKpi(
          label: 'Profit today',
          value: metrics.contributionProfit.formatCompact(),
          caption: 'contribution',
          tone: metrics.contributionProfit.paisa < 0 ? Tone.bad : null,
        ),
        HeroKpi(
          label: 'Mismatch',
          value: metrics.mismatch.formatCompact(),
          caption: '${metrics.mismatchCount} open',
          tone: metrics.mismatchCount > 0 ? Tone.bad : null,
        ),
      ],
    );
  }

  /// Says what is known about *when* the money lands, and admits when nothing
  /// is known. Master spec section 140: courier settlement timing is never
  /// invented, so unforecast money is called that rather than shown as due.
  String get _subtitle {
    if (metrics.codOutstanding.paisa == 0) {
      return 'Nothing with couriers.';
    }
    if (metrics.codExpectedToday.paisa > 0) {
      return '${metrics.codExpectedToday.format()} expected today, '
          'from your own settlement history.';
    }
    if (metrics.codUnforecast.paisa > 0) {
      return 'No arrival date yet — not enough settlement history with these '
          'couriers to say when it lands.';
    }
    return 'Nothing expected to land today.';
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
  const _NeedsAttention({required this.metrics, this.onNavigate});

  final HomeMetrics metrics;
  final ValueChanged<String>? onNavigate;

  @override
  Widget build(BuildContext context) {
    return AttentionCard(
      items: <AttentionItem>[
        for (final alert in metrics.alerts)
          AttentionItem(
            title: alert.title,
            detail: alert.detail,
            tone: _tone(alert.severity),
            onTap: () => onNavigate?.call('money'),
          ),
      ],
      actionLabel: metrics.alerts.isEmpty ? null : 'Review all',
      onAction: metrics.alerts.isEmpty ? null : () => onNavigate?.call('money'),
      side: DarkHighlightPanel(
        label: "Today's contribution profit",
        value: metrics.contributionProfit.format(),
        description: metrics.profitCaveat,
        actionLabel: 'Open profit breakdown',
        onAction: () => onNavigate?.call('insights'),
      ),
    );
  }

  static Tone _tone(String severity) => switch (severity) {
    'CRITICAL' => Tone.bad,
    'WARNING' => Tone.warning,
    'ACTION' => Tone.info,
    _ => Tone.neutral,
  };
}

class _PulseCharts extends ConsumerWidget {
  const _PulseCharts();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profit = ref.watch(profitReportProvider);
    final money = ref.watch(moneySummaryProvider);

    return ResponsiveGrid(
      minTileWidth: 320,
      maxColumns: 2,
      spacing: EcomsbdSpacing.sm,
      children: <Widget>[
        PremiumChartCard(
          title: 'Contribution profit',
          subtitle: 'Last 30 days · estimated inputs marked, not blended',
          trailing: profit.valueOrNull == null
              ? null
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    Text(
                      profit.valueOrNull!.value.contributionProfit
                          .formatCompact(),
                      style: EcomsbdType.metricValue,
                    ),
                    if (profit.valueOrNull!.value.marginLabel != null)
                      Text(
                        profit.valueOrNull!.value.marginLabel!,
                        style: EcomsbdType.chip.copyWith(
                          color: EcomsbdColors.muted,
                        ),
                      ),
                  ],
                ),
          child: ChartData<ProfitReport>(
            value: profit,
            emptyMessage: 'No settled parcels in the last 30 days yet.',
            isEmpty: (report) => report.parcelCount == 0,
            builder: (report) =>
                ProfitTrendChart(points: _trendPoints(report.series)),
          ),
        ),
        PremiumChartCard(
          title: 'COD position',
          subtitle: 'Current collectible pipeline, by age',
          child: ChartData<List<AgingBand>>(
            value: money.whenData(
              (sourced) => sourced.map((summary) => summary.aging),
            ),
            emptyMessage: 'No money with couriers right now.',
            isEmpty: (bands) =>
                bands.every((band) => band.outstanding.paisa == 0),
            builder: (bands) => Center(
              child: CodDonutChart(
                slices: _agingSlices(bands),
                centerValue: money.valueOrNull!.value.outstanding
                    .formatCompact(),
              ),
            ),
          ),
        ),
      ],
    );
  }

  static List<TrendPoint> _trendPoints(List<DayPoint> series) {
    // Last fourteen days: a month of daily points on a 360dp card is a
    // smear, and the seller reads this for direction rather than for
    // individual days.
    final window = series.length <= 14
        ? series
        : series.sublist(series.length - 14);
    return <TrendPoint>[
      for (final point in window)
        TrendPoint(
          label: _weekday(point.businessDate),
          value: point.contributionProfit.paisa,
        ),
    ];
  }

  static List<DonutSlice> _agingSlices(List<AgingBand> bands) {
    const palette = <Color>[
      EcomsbdColors.green,
      EcomsbdColors.blue,
      EcomsbdColors.orange,
      EcomsbdColors.red,
    ];
    final slices = <DonutSlice>[];
    for (var index = 0; index < bands.length; index++) {
      final band = bands[index];
      if (band.outstanding.paisa == 0) continue;
      slices.add(
        DonutSlice(
          label: band.label,
          value: band.outstanding.paisa,
          color: palette[index % palette.length],
        ),
      );
    }
    return slices;
  }

  static String _weekday(DateTime date) => switch (date.weekday) {
    DateTime.monday => 'Mon',
    DateTime.tuesday => 'Tue',
    DateTime.wednesday => 'Wed',
    DateTime.thursday => 'Thu',
    DateTime.friday => 'Fri',
    DateTime.saturday => 'Sat',
    _ => 'Sun',
  };
}

class _PerformanceCharts extends ConsumerWidget {
  const _PerformanceCharts();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profit = ref.watch(profitReportProvider);
    final returns = ref.watch(returnReportProvider);

    return ResponsiveGrid(
      minTileWidth: 320,
      maxColumns: 2,
      spacing: EcomsbdSpacing.sm,
      children: <Widget>[
        PremiumChartCard(
          title: 'Delivery funnel',
          subtitle: 'Last 30 days',
          trailing: _successChip(profit.valueOrNull?.value),
          child: ChartData<ProfitReport>(
            value: profit,
            emptyMessage: 'No parcels dispatched in the last 30 days.',
            isEmpty: (report) =>
                report.funnel.isEmpty || report.funnel.first.count == 0,
            builder: (report) => charts.DeliveryFunnelChart(
              stages: <charts.FunnelStage>[
                for (final stage in report.funnel)
                  charts.FunnelStage(label: stage.label, count: stage.count),
              ],
            ),
          ),
        ),
        PremiumChartCard(
          title: 'Courier health',
          subtitle: 'Your own history only · sample-aware',
          child: ChartData<ReturnReport>(
            value: returns,
            emptyMessage: 'No finished parcels to judge a courier on yet.',
            isEmpty: (report) => report.byCourier.isEmpty,
            builder: (report) => _CourierRows(lines: report.byCourier),
          ),
        ),
      ],
    );
  }

  static Widget? _successChip(ProfitReport? report) {
    if (report == null || report.funnel.isEmpty) return null;
    final dispatched = report.funnel.first.count;
    if (dispatched == 0) return null;
    final delivered = report.funnel
        .firstWhere(
          (stage) => stage.label == 'Delivered',
          orElse: () => const FunnelStage(label: 'Delivered', count: 0),
        )
        .count;
    final rate = delivered * 100 / dispatched;
    return StatusChip(
      label: '${rate.toStringAsFixed(1)}% delivered',
      tone: rate >= 85 ? Tone.good : Tone.warning,
    );
  }
}

/// Return rate per courier, with the sample-size rule visible.
///
/// Master spec section 24: a ranking is not shown before the sample supports
/// it. A courier with three parcels gets "Not enough data" rather than a
/// percentage the seller might act on.
class _CourierRows extends StatelessWidget {
  const _CourierRows({required this.lines});

  final List<RateLine> lines;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: <Widget>[
        for (final line in lines.take(4))
          Padding(
            padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
            child: GlassListRow(
              leading: RowIcon(label: line.label.substring(0, 1).toUpperCase()),
              title: line.label,
              subtitle:
                  '${line.parcelCount} finished · '
                  '${line.returnCount} returned',
              trailing: line.hasEnoughSample
                  ? Text('${line.rateLabel} back', style: EcomsbdType.money)
                  : const StatusChip(
                      label: 'Not enough data',
                      tone: Tone.neutral,
                    ),
            ),
          ),
      ],
    );
  }
}

class _ProductCharts extends ConsumerWidget {
  const _ProductCharts();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final products = ref.watch(productProfitProvider);
    final returns = ref.watch(returnReportProvider);

    return ResponsiveGrid(
      minTileWidth: 320,
      maxColumns: 2,
      spacing: EcomsbdSpacing.sm,
      children: <Widget>[
        PremiumChartCard(
          title: 'Top products by profit',
          subtitle: 'Not revenue · contribution profit',
          child: ChartData<List<ProductLine>>(
            value: products,
            emptyMessage: 'No delivered products in the last 30 days.',
            isEmpty: (rows) => rows.isEmpty,
            builder: (rows) => charts.HorizontalBarChart(
              data: <charts.HorizontalBarDatum>[
                for (final row in rows.take(5))
                  charts.HorizontalBarDatum(
                    label: row.productName,
                    value: row.profit.paisa.abs(),
                    displayValue: row.profit.formatCompact(),
                  ),
              ],
            ),
          ),
        ),
        PremiumChartCard(
          title: 'Return pressure',
          subtitle: 'Where returns actually cost money',
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
            builder: (report) => charts.HorizontalBarChart(
              accent: charts.BarAccent.red,
              data: <charts.HorizontalBarDatum>[
                for (final line
                    in report.byProduct
                        .where((line) => line.returnCount > 0)
                        .take(5))
                  charts.HorizontalBarDatum(
                    label: line.label,
                    value: line.returnCount,
                    displayValue: line.hasEnoughSample
                        ? line.rateLabel
                        : '${line.returnCount} of ${line.parcelCount}',
                  ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

/// The most recent notifications, which is what "live activity" actually is.
class _ActivityFeed extends ConsumerWidget {
  const _ActivityFeed();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final notifications = ref.watch(notificationListProvider);
    final items = notifications.items.take(3).toList();

    if (notifications.isLoading && items.isEmpty) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: EcomsbdSpacing.lg),
        child: Center(child: CircularProgressIndicator()),
      );
    }
    if (items.isEmpty) {
      return const EmptyState(
        icon: Icons.notifications_none_rounded,
        title: 'Nothing needs you right now',
        message:
            'Alerts appear here when money is at risk — delivered parcels '
            'that were never paid for, payments that came up short, parcels '
            'stuck with a courier.',
      );
    }

    return Column(
      children: <Widget>[
        for (final item in items)
          Padding(
            padding: const EdgeInsets.only(bottom: EcomsbdSpacing.sm),
            child: SellerFeedCard(
              author: 'ecomsbd',
              timestamp: formatRelative(item.createdAt),
              headline: item.title,
              body: item.body,
              avatarLabel: _avatarFor(item.severity),
              status: StatusChip(
                label: _severityLabel(item.severity),
                tone: _severityTone(item.severity),
              ),
            ),
          ),
      ],
    );
  }

  static String _avatarFor(NotificationSeverity severity) => switch (severity) {
    NotificationSeverity.critical => '!',
    NotificationSeverity.warning => '⚠',
    NotificationSeverity.action => '→',
    NotificationSeverity.info => 'i',
  };

  static String _severityLabel(NotificationSeverity severity) =>
      switch (severity) {
        NotificationSeverity.critical => 'Losing money',
        NotificationSeverity.warning => 'At risk',
        NotificationSeverity.action => 'Needs you',
        NotificationSeverity.info => 'For info',
      };

  static Tone _severityTone(NotificationSeverity severity) =>
      switch (severity) {
        NotificationSeverity.critical => Tone.bad,
        NotificationSeverity.warning => Tone.warning,
        NotificationSeverity.action => Tone.info,
        NotificationSeverity.info => Tone.neutral,
      };
}
