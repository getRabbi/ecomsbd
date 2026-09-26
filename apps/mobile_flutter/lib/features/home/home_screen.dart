import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../data/analytics/analytics_providers.dart';
import '../../data/analytics/models.dart';
import '../../data/channels/channel_models.dart';
import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/repository_support.dart';
import '../../design/charts/line_chart.dart';
import '../../design/components/cards.dart';
import '../../design/components/seller_blocks.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../expenses/expenses_screen.dart';
import '../orders/order_compose_screen.dart';
import '../shared/data_state.dart';
import 'widgets/seller_today.dart';

/// The Home dashboard — the seller's daily money control screen.
///
/// Master spec section 1.1: this is not a generic analytics dashboard. The
/// order of the page is the order of the seller's questions, as the final
/// prototype lays it out: how did today go (the dark summary), what needs me,
/// what should I do first, what would booking cost, the everyday actions, and
/// only then how the business is trending — one compact card, with the full
/// charts a tap away in Insights.
///
/// Every figure comes from `/v1/analytics/home`, `/v1/analytics/profit` and
/// the order queue. Nothing on this screen is a fixture: a dashboard that
/// falls back to plausible demo numbers when a call fails is worse than one
/// that says it could not load, because the seller cannot tell the difference.
class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key, this.onNavigate});

  /// Route name to push, used by the quick actions and section links.
  final ValueChanged<String>? onNavigate;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pending = ref.watch(pendingMutationCountProvider).valueOrNull ?? 0;
    final isOffline = ref.watch(isOfflineProvider);
    final home = ref.watch(homeMetricsProvider);
    final shopName = ref.watch(shopNameProvider);

    return RefreshIndicator(
      edgeOffset: EcomsbdLayout.shellRefreshOffset(context),
      onRefresh: () async {
        ref.invalidate(homeMetricsProvider);
        ref.invalidate(profitReportProvider);
        ref.invalidate(returnReportProvider);
        ref.invalidate(sellerQueueProvider);
        ref.invalidate(setupProgressProvider);
        ref.invalidate(salesChannelsProvider);
        await ref.read(homeMetricsProvider.future);
      },
      child: ListView(
        padding: EdgeInsets.fromLTRB(
          EcomsbdSpacing.page,
          EcomsbdLayout.shellTopPadding(context) - EcomsbdSpacing.xs,
          EcomsbdSpacing.page,
          EcomsbdSpacing.bottomNavClearance,
        ),
        children: <Widget>[
          if (isOffline) ...<Widget>[
            OfflineBanner(pendingCount: pending),
            const SizedBox(height: EcomsbdSpacing.sm),
          ],
          _HomeBody(
            home: home,
            shopName: shopName ?? context.tr('common.yourShop'),
            onNavigate: onNavigate,
          ),
        ],
      ),
    );
  }
}

class _HomeBody extends ConsumerWidget {
  const _HomeBody({
    required this.home,
    required this.shopName,
    this.onNavigate,
  });

  final AsyncValue<Sourced<HomeMetrics>> home;
  final String shopName;
  final ValueChanged<String>? onNavigate;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return home.when(
      loading: () => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          _HomeHero(shopName: shopName, onNavigate: onNavigate),
          const SizedBox(height: EcomsbdSpacing.md),
          const ContentLoader(minHeight: 220),
        ],
      ),
      error: (error, _) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          _HomeHero(shopName: shopName, onNavigate: onNavigate),
          const SizedBox(height: EcomsbdSpacing.md),
          ErrorStateCard(
            // Only ApiError reaches here: the repositories translate
            // everything they raise, and anything else is a bug worth seeing
            // as a crash rather than as a tidy "could not load" card.
            error: error is ApiError ? error : ApiError.unexpected(error),
            onRetry: () => ref.invalidate(homeMetricsProvider),
          ),
        ],
      ),
      data: (sourced) {
        final metrics = sourced.value;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            if (sourced.isStale) ...<Widget>[
              StaleDataNotice(
                fetchedAt: sourced.fetchedAt,
                onRetry: () => ref.invalidate(homeMetricsProvider),
              ),
              const SizedBox(height: EcomsbdSpacing.sm),
            ],
            _HomeHero(
              shopName: shopName,
              metrics: metrics,
              onNavigate: onNavigate,
            ),
            TodayAttention(metrics: metrics, onNavigate: onNavigate),
            TodaySuggestions(metrics: metrics, onNavigate: onNavigate),
            const CourierSavingPreview(),
            SectionHeader(
              title: context.tr('home.quickTitle'),
              subtitle: context.tr('home.quickSub'),
            ),
            _QuickActions(onNavigate: onNavigate),
            SectionHeader(
              title: context.tr('home.todayTitle'),
              subtitle: context.tr('home.todaySub'),
            ),
            _TodayProgress(metrics: metrics),
            const SalesChannelStrip(),
            _BusinessPulse(onNavigate: onNavigate),
          ],
        );
      },
    );
  }
}

/// `.hero` — today's summary on one dark card: the shop, sales, and the four
/// figures a seller checks first.
class _HomeHero extends StatelessWidget {
  const _HomeHero({required this.shopName, this.metrics, this.onNavigate});

  final String shopName;

  /// Null while today's figures load or when they failed; every figure then
  /// reads as a dash rather than a zero.
  final HomeMetrics? metrics;
  final ValueChanged<String>? onNavigate;

  @override
  Widget build(BuildContext context) {
    final m = metrics;
    const dash = '—';
    final profitNegative = m != null && m.contributionProfit.paisa < 0;
    final stats = <DarkMiniStat>[
      DarkMiniStat(
        value: m == null ? dash : '${m.ordersToday}',
        label: context.tr('home.stat.orders'),
      ),
      DarkMiniStat(
        value: m == null ? dash : '${m.deliveredToday}',
        label: context.tr('home.stat.delivered'),
      ),
      DarkMiniStat(
        value: m == null ? dash : m.codOutstanding.formatCompact(),
        label: context.tr('home.stat.codDue'),
      ),
      DarkMiniStat(
        value: m == null ? dash : m.contributionProfit.formatCompact(),
        label: context.tr('home.stat.profit'),
        valueColor: profitNegative ? const Color(0xFFFFB4B4) : Colors.white,
      ),
    ];

    return DarkPanel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            context.tr('home.heroEyebrow').toUpperCase(),
            style: trackedFor(
              context.tr('home.heroEyebrow'),
              EcomsbdType.eyebrowWide.copyWith(color: const Color(0xFFAEBDCB)),
            ),
          ),
          const SizedBox(height: 5),
          Text(
            shopName,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: EcomsbdType.heroTitle.copyWith(
              color: Colors.white,
              fontSize: 24,
            ),
          ),
          const SizedBox(height: 12),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: <Widget>[
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    FittedBox(
                      fit: BoxFit.scaleDown,
                      alignment: Alignment.centerLeft,
                      child: Text(
                        m == null ? dash : m.grossSales.format(),
                        style: EcomsbdType.heroMoney.copyWith(
                          color: Colors.white,
                          fontSize: 36,
                          fontWeight: FontWeight.w900,
                        ),
                      ),
                    ),
                    const SizedBox(height: 5),
                    Text(
                      m == null
                          ? context.tr('home.loadingToday')
                          : '${context.tr('home.salesToday')} · '
                                '${context.trPlural('home.heroSubtitle', m.ordersToday, <String, Object?>{'delivered': m.deliveredToday})}',
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.onDarkMuted,
                        fontSize: 12.5,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              TextButton(
                onPressed: onNavigate == null
                    ? null
                    : () => onNavigate!('money'),
                style: TextButton.styleFrom(
                  backgroundColor: const Color(0x21FFFFFF),
                  foregroundColor: Colors.white,
                  minimumSize: const Size(0, 40),
                  padding: const EdgeInsets.symmetric(horizontal: 13),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(14),
                  ),
                  textStyle: EcomsbdType.label.copyWith(
                    fontWeight: FontWeight.w800,
                  ),
                ),
                child: Text('${context.tr('nav.money')} →'),
              ),
            ],
          ),
          const SizedBox(height: 14),
          LayoutBuilder(
            builder: (context, constraints) {
              // The prototype's own breakpoint: four across, two on a very
              // narrow card.
              final columns = constraints.maxWidth >= 290 ? 4 : 2;
              const gap = 8.0;
              final width =
                  (constraints.maxWidth - gap * (columns - 1)) / columns;
              return Wrap(
                spacing: gap,
                runSpacing: gap,
                children: <Widget>[
                  for (final stat in stats) SizedBox(width: width, child: stat),
                ],
              );
            },
          ),
          if (m != null && m.hasEstimates) ...<Widget>[
            const SizedBox(height: 10),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                const Icon(
                  Icons.info_outline_rounded,
                  size: 15,
                  color: Color(0xFFFFD08A),
                ),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    m.profitCaveat,
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.onDarkMuted,
                    ),
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

/// `.quick-grid` — the four everyday actions, two by two.
class _QuickActions extends StatelessWidget {
  const _QuickActions({this.onNavigate});

  final ValueChanged<String>? onNavigate;

  @override
  Widget build(BuildContext context) {
    final tiles = <Widget>[
      QuickActionTile(
        icon: Icons.add_rounded,
        title: context.tr('home.quick.newOrder'),
        subtitle: context.tr('home.quick.newOrderSub'),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute<void>(builder: (_) => const OrderComposeScreen()),
        ),
      ),
      QuickActionTile(
        icon: Icons.forum_outlined,
        title: context.tr('home.quick.reply'),
        subtitle: context.tr('home.quick.replySub'),
        onTap: () => onNavigate?.call('inbox'),
      ),
      QuickActionTile(
        icon: Icons.local_shipping_outlined,
        title: context.tr('home.quick.book'),
        subtitle: context.tr('home.quick.bookSub'),
        onTap: () => onNavigate?.call('orders:confirmed'),
      ),
      QuickActionTile(
        icon: Icons.payments_outlined,
        title: context.tr('home.quick.expense'),
        subtitle: context.tr('home.quick.expenseSub'),
        onTap: () => ExpenseSheet.show(context),
      ),
    ];
    return Column(
      children: <Widget>[
        for (var row = 0; row < tiles.length; row += 2) ...<Widget>[
          if (row > 0) const SizedBox(height: 10),
          // Equal heights in a row, even when a Bangla title wraps.
          IntrinsicHeight(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                Expanded(child: tiles[row]),
                const SizedBox(width: 10),
                Expanded(child: tiles[row + 1]),
              ],
            ),
          ),
        ],
      ],
    );
  }
}

/// `.progress-card` — how today's finished parcels went, and where today's
/// money stands.
class _TodayProgress extends StatelessWidget {
  const _TodayProgress({required this.metrics});

  final HomeMetrics metrics;

  @override
  Widget build(BuildContext context) {
    final finished = metrics.deliveredToday + metrics.returnedToday;
    final fraction = finished == 0 ? 0.0 : metrics.deliveredToday / finished;

    return GlassCard(
      padding: const EdgeInsets.all(15),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  finished == 0
                      ? context.tr('home.progressNone')
                      : context.tr('home.progressTitle', <String, Object?>{
                          'delivered': metrics.deliveredToday,
                          'returned': metrics.returnedToday,
                        }),
                  style: EcomsbdType.bodyStrong,
                ),
              ),
              if (finished > 0)
                Text(
                  '${(fraction * 100).round()}%',
                  style: EcomsbdType.bodyStrong,
                ),
            ],
          ),
          const SizedBox(height: 11),
          ClipRRect(
            borderRadius: BorderRadius.circular(10),
            child: Container(
              height: 9,
              color: const Color(0xFFEEF1F4),
              alignment: Alignment.centerLeft,
              child: FractionallySizedBox(
                widthFactor: fraction,
                child: const DecoratedBox(
                  decoration: BoxDecoration(
                    gradient: LinearGradient(
                      colors: <Color>[EcomsbdColors.orange, Color(0xFFFF875C)],
                    ),
                  ),
                  child: SizedBox.expand(),
                ),
              ),
            ),
          ),
          const SizedBox(height: 9),
          Text(
            context.tr('home.progressMoney', <String, Object?>{
              'realized': metrics.realizedRevenue.format(),
              'cod': metrics.codOutstanding.format(),
            }),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: 2),
          Text(
            _codLine(context),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
        ],
      ),
    );
  }

  /// Says what is known about *when* the money lands, and admits when nothing
  /// is known. Master spec section 140: courier settlement timing is never
  /// invented, so unforecast money is called that rather than shown as due.
  String _codLine(BuildContext context) {
    if (metrics.codOutstanding.paisa == 0) {
      return context.tr('home.codNothing');
    }
    if (metrics.codExpectedToday.paisa > 0) {
      return context.tr('home.codExpectedToday', <String, Object?>{
        'amount': metrics.codExpectedToday.format(),
      });
    }
    if (metrics.codUnforecast.paisa > 0) {
      return context.tr('home.codNoDate');
    }
    return context.tr('home.codNothingToday');
  }
}

/// The 30-day trend in one compact card, last on the page. The full charts —
/// COD ageing, the delivery funnel, products, returns — live in Insights.
class _BusinessPulse extends ConsumerWidget {
  const _BusinessPulse({this.onNavigate});

  final ValueChanged<String>? onNavigate;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profit = ref.watch(profitReportProvider);
    final report = profit.valueOrNull?.value;
    final rate = _deliveredRate(report);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        SectionHeader(
          title: context.tr('home.businessPulse'),
          subtitle: context.tr('home.pulseSub'),
          actionLabel: context.tr('home.fullAnalytics'),
          onAction: () => onNavigate?.call('insights'),
        ),
        GlassCard(
          padding: const EdgeInsets.fromLTRB(15, 14, 15, 6),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              Row(
                children: <Widget>[
                  Expanded(
                    child: Text(
                      context.tr('chart.contributionProfit'),
                      style: EcomsbdType.bodyStrong,
                    ),
                  ),
                  if (report != null)
                    Text(
                      report.contributionProfit.formatCompact(),
                      style: EcomsbdType.metricValue,
                    ),
                ],
              ),
              const SizedBox(height: EcomsbdSpacing.sm),
              ChartData<ProfitReport>(
                value: profit,
                height: 120,
                emptyMessage: context.tr('chart.emptyNoSettled'),
                isEmpty: (report) => report.parcelCount == 0,
                builder: (report) => ProfitTrendChart(
                  height: 120,
                  points: _trendPoints(context, report.series),
                ),
              ),
              if (report != null && report.parcelCount > 0) ...<Widget>[
                const SizedBox(height: 4),
                KpiLine(
                  label: context.tr('home.pulseParcels'),
                  value: '${report.parcelCount}',
                  isLast: rate == null,
                ),
                if (rate != null)
                  KpiLine(
                    label: context.tr('home.pulseDelivered'),
                    value: '${rate.toStringAsFixed(1)}%',
                    isLast: true,
                  ),
              ],
            ],
          ),
        ),
      ],
    );
  }

  static double? _deliveredRate(ProfitReport? report) {
    if (report == null || report.funnel.isEmpty) return null;
    final dispatched = report.funnel.first.count;
    if (dispatched == 0) return null;
    final delivered = report.funnel
        .firstWhere(
          (stage) => stage.label == 'Delivered',
          orElse: () => const FunnelStage(label: 'Delivered', count: 0),
        )
        .count;
    return delivered * 100 / dispatched;
  }

  static List<TrendPoint> _trendPoints(
    BuildContext context,
    List<DayPoint> series,
  ) {
    // Last fourteen days: a month of daily points on a 360dp card is a
    // smear, and the seller reads this for direction rather than for
    // individual days.
    final window = series.length <= 14
        ? series
        : series.sublist(series.length - 14);
    return <TrendPoint>[
      for (final point in window)
        TrendPoint(
          label: _weekday(context, point.businessDate),
          value: point.contributionProfit.paisa,
        ),
    ];
  }

  static String _weekday(BuildContext context, DateTime date) =>
      switch (date.weekday) {
        DateTime.monday => context.tr('weekday.mon'),
        DateTime.tuesday => context.tr('weekday.tue'),
        DateTime.wednesday => context.tr('weekday.wed'),
        DateTime.thursday => context.tr('weekday.thu'),
        DateTime.friday => context.tr('weekday.fri'),
        DateTime.saturday => context.tr('weekday.sat'),
        _ => context.tr('weekday.sun'),
      };
}
