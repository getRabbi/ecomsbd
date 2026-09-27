import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/analytics/analytics_providers.dart';
import '../../data/analytics/rto_models.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../shared/data_state.dart';
import '../shared/responsive.dart';

/// Insights → Returns & RTO.
///
/// Facts from the shop's own parcels, in the words of the counts behind them:
/// "3 of 12 completed parcels came back", never a score. RTO is returned plus
/// cancelled at the courier; the rate is over completed parcels only, so a
/// parcel still moving or an order cancelled before dispatch never moves it.
/// A figure below the minimum sample is labelled limited data instead of
/// being compared, and district analysis stays off unless the data is there.
///
/// Nothing on this screen is calculated on the device: it reads the same
/// endpoints as the web.
class RtoScreen extends ConsumerStatefulWidget {
  const RtoScreen({super.key});

  @override
  ConsumerState<RtoScreen> createState() => _RtoScreenState();
}

class _RtoScreenState extends ConsumerState<RtoScreen> {
  static const int _pageSize = 20;

  final List<ProductRto> _products = <ProductRto>[];
  bool _hasMore = false;
  bool _loadingProducts = true;
  Object? _productsError;

  @override
  void initState() {
    super.initState();
    _loadProducts(reset: true);
  }

  Future<void> _loadProducts({required bool reset}) async {
    // The first load starts in the loading state already, so initState never
    // calls setState.
    if (!_loadingProducts) {
      setState(() {
        _loadingProducts = true;
        _productsError = null;
      });
    }
    try {
      final page = await ref
          .read(analyticsRepositoryProvider)
          .rtoProducts(offset: reset ? 0 : _products.length, limit: _pageSize);
      if (!mounted) return;
      setState(() {
        if (reset) _products.clear();
        _products.addAll(page.items);
        _hasMore = page.hasMore;
      });
    } on Object catch (error) {
      if (mounted) setState(() => _productsError = error);
    } finally {
      if (mounted) setState(() => _loadingProducts = false);
    }
  }

  Future<void> _refresh() async {
    ref
      ..invalidate(rtoSummaryProvider)
      ..invalidate(rtoTrendProvider)
      ..invalidate(rtoCouriersProvider)
      ..invalidate(rtoAreasProvider)
      ..invalidate(rtoPatternsProvider);
    await Future.wait(<Future<Object?>>[
      ref.read(rtoSummaryProvider.future),
      _loadProducts(reset: true),
    ]);
  }

  @override
  Widget build(BuildContext context) {
    final summary = ref.watch(rtoSummaryProvider);
    final trend = ref.watch(rtoTrendProvider);
    final couriers = ref.watch(rtoCouriersProvider);
    final areas = ref.watch(rtoAreasProvider);
    final patterns = ref.watch(rtoPatternsProvider);

    return Scaffold(
      backgroundColor: EcomsbdColors.background,
      body: EcomsbdBackground(
        child: SafeArea(
          child: ContentWidthLimit(
            child: RefreshIndicator(
              edgeOffset: EcomsbdLayout.pushedRefreshOffset,
              onRefresh: _refresh,
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
                          eyebrow: context.tr('rto.eyebrow'),
                          title: context.tr('rto.title'),
                          description: context.tr('rto.description'),
                        ),
                      ),
                    ],
                  ),
                  ...summary.when(
                    loading: () => <Widget>[SkeletonLoader.card(height: 200)],
                    error: (error, _) => <Widget>[
                      if (error is ApiError)
                        ErrorStateCard(
                          error: error,
                          onRetry: () => ref.invalidate(rtoSummaryProvider),
                        )
                      else
                        EmptyState(
                          icon: Icons.error_outline,
                          title: context.tr('rto.couldNotLoad'),
                          message: context.tr('common.somethingWentWrong'),
                        ),
                    ],
                    data: (sourced) => <Widget>[
                      if (sourced.isStale) ...<Widget>[
                        StaleDataNotice(
                          fetchedAt: sourced.fetchedAt,
                          onRetry: () => ref.invalidate(rtoSummaryProvider),
                        ),
                        const SizedBox(height: EcomsbdSpacing.sm),
                      ],
                      RtoSummaryCard(summary: sourced.value),
                      const SizedBox(height: EcomsbdSpacing.sm),
                      RtoWindowsRow(summary: sourced.value),
                    ],
                  ),
                  ...trend.maybeWhen(
                    data: (sourced) => <Widget>[
                      SectionHeader(
                        title: context.tr('rto.trendTitle'),
                        subtitle: context.tr('rto.trendSub'),
                      ),
                      RtoTrendCard(weeks: sourced.value),
                    ],
                    orElse: () => const <Widget>[],
                  ),
                  ...couriers.maybeWhen(
                    data: (sourced) => <Widget>[
                      SectionHeader(
                        title: context.tr('rto.couriersTitle'),
                        subtitle: context.tr('rto.couriersSub'),
                      ),
                      if (sourced.value.items.isEmpty)
                        _Muted(context.tr('rto.noCompleted'))
                      else
                        for (final row in sourced.value.items)
                          _CourierRow(row: row),
                      if (sourced.value.excluded.isNotEmpty)
                        _Muted(
                          context.tr('rto.excluded', <String, Object?>{
                            'providers': sourced.value.excluded
                                .map(courierName)
                                .join(', '),
                          }),
                        ),
                    ],
                    orElse: () => const <Widget>[],
                  ),
                  SectionHeader(
                    title: context.tr('rto.productsTitle'),
                    subtitle: context.tr('rto.productsSub'),
                  ),
                  if (_products.isEmpty && !_loadingProducts)
                    _Muted(
                      _productsError == null
                          ? context.tr('rto.noCompleted')
                          : context.tr('rto.couldNotLoad'),
                    ),
                  for (final row in _products) _ProductRow(row: row),
                  if (_loadingProducts)
                    const Padding(
                      padding: EdgeInsets.all(EcomsbdSpacing.md),
                      child: Center(child: CircularProgressIndicator()),
                    )
                  else if (_hasMore)
                    TextButton(
                      onPressed: () => _loadProducts(reset: false),
                      child: Text(context.tr('common.loadMore')),
                    ),
                  ...areas.maybeWhen(
                    data: (sourced) => <Widget>[
                      SectionHeader(
                        title: context.tr('rto.areasTitle'),
                        subtitle: context.tr('rto.areasSub'),
                      ),
                      if (!sourced.value.reliable)
                        GlassCard(
                          child: Text(
                            context.tr(
                              'rto.areasNotReliable',
                              <String, Object?>{
                                'coverage': sourced.value.coverageLabel,
                              },
                            ),
                            style: EcomsbdType.caption.copyWith(
                              color: EcomsbdColors.muted,
                            ),
                          ),
                        )
                      else
                        for (final row in sourced.value.items)
                          _RateRow(
                            title: row.label,
                            counts: row.counts,
                            subtitle: _countsLine(context, row.counts),
                          ),
                    ],
                    orElse: () => const <Widget>[],
                  ),
                  // Needs the risk permission; a role without it simply
                  // does not get this section rather than an error.
                  ...patterns.maybeWhen(
                    data: (sourced) => <Widget>[
                      SectionHeader(
                        title: context.tr('rto.patternsTitle'),
                        subtitle: context.tr('rto.patternsSub'),
                      ),
                      if (sourced.value.isEmpty)
                        _Muted(context.tr('rto.noPatterns'))
                      else
                        for (final row in sourced.value) _PatternRow(row: row),
                    ],
                    orElse: () => const <Widget>[],
                  ),
                  const SizedBox(height: EcomsbdSpacing.md),
                  _Muted(context.tr('rto.ownShopOnly')),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// `Steadfast`, `Pathao`, or the seller's own manual label.
String courierName(String provider) => switch (provider) {
  'steadfast' => 'Steadfast',
  'pathao' => 'Pathao',
  'redx' => 'RedX',
  'manual' => 'Manual',
  _ => provider,
};

String _countsLine(BuildContext context, RtoCounts counts) => context.tr(
  'rto.rowCounts',
  <String, Object?>{'completed': counts.completed, 'rto': counts.rto},
);

/// The headline: rate, the counts it came from, and what the rate excludes.
class RtoSummaryCard extends StatelessWidget {
  const RtoSummaryCard({required this.summary, super.key});

  final RtoSummary summary;

  @override
  Widget build(BuildContext context) {
    final counts = summary.counts;
    return StrongGlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  context.tr('rto.rateWindow', <String, Object?>{
                    'days': summary.days,
                  }),
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ),
              if (!counts.sufficient && counts.completed > 0)
                StatusChip(
                  label: context.tr('rto.limited'),
                  tone: Tone.neutral,
                ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          Text(counts.rateLabel, style: EcomsbdType.sectionTitle),
          const SizedBox(height: EcomsbdSpacing.xs),
          Text(
            counts.completed == 0
                ? context.tr('rto.noCompleted')
                : context.tr('rto.rateOf', <String, Object?>{
                    'rto': counts.rto,
                    'completed': counts.completed,
                  }),
            style: EcomsbdType.body,
          ),
          if (!counts.sufficient && counts.completed > 0) ...<Widget>[
            const SizedBox(height: 2),
            Text(
              context.tr('rto.limitedHint', <String, Object?>{
                'min': summary.minSample,
              }),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
            ),
          ],
          const SizedBox(height: EcomsbdSpacing.sm),
          _Stat(label: context.tr('rto.delivered'), value: counts.delivered),
          if (counts.partial > 0)
            _Stat(label: context.tr('rto.partial'), value: counts.partial),
          _Stat(label: context.tr('rto.returned'), value: counts.returned),
          _Stat(
            label: context.tr('rto.courierCancelled'),
            value: counts.courierCancelled,
          ),
          if (counts.lost > 0)
            _Stat(label: context.tr('rto.lost'), value: counts.lost),
          _Stat(label: context.tr('rto.openNow'), value: summary.openNow),
          _Stat(
            label: context.tr('rto.cancelledBefore'),
            value: summary.cancelledBeforeDispatch,
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          Text(
            context.tr('rto.definition'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
        ],
      ),
    );
  }
}

/// 7 / 30 / 90 days side by side, each with its own counts.
class RtoWindowsRow extends StatelessWidget {
  const RtoWindowsRow({required this.summary, super.key});

  final RtoSummary summary;

  @override
  Widget build(BuildContext context) {
    return ResponsiveGrid(
      minTileWidth: 96,
      maxColumns: 3,
      spacing: EcomsbdSpacing.xs,
      children: <Widget>[
        for (final window in summary.windows)
          MetricTile(
            label: context.tr('rto.windowDays', <String, Object?>{
              'days': window.days,
            }),
            value: window.counts.rateLabel,
            caption: window.counts.completed == 0
                ? context.tr('rto.none')
                : context.tr('rto.windowCounts', <String, Object?>{
                    'rto': window.counts.rto,
                    'completed': window.counts.completed,
                  }),
            tone: window.counts.sufficient ? null : Tone.neutral,
          ),
      ],
    );
  }
}

/// Twelve weeks of completed parcels, bar height = RTO rate.
///
/// A week below the minimum sample is drawn muted, and a week with nothing
/// completed has no bar at all rather than a zero that reads as "no returns".
class RtoTrendCard extends StatelessWidget {
  const RtoTrendCard({required this.weeks, super.key});

  final List<RtoWeek> weeks;

  @override
  Widget build(BuildContext context) {
    final latest = weeks.isEmpty ? null : weeks.last.counts;
    final maxBps = weeks.fold<int>(
      0,
      (max, w) => (w.counts.rtoRateBps ?? 0) > max ? w.counts.rtoRateBps! : max,
    );
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          SizedBox(
            height: 88,
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: <Widget>[
                for (final week in weeks)
                  Expanded(
                    child: Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 2),
                      child: week.counts.completed == 0
                          ? const SizedBox.shrink()
                          : FractionallySizedBox(
                              heightFactor: maxBps == 0
                                  ? 0.04
                                  : ((week.counts.rtoRateBps ?? 0) / maxBps)
                                        .clamp(0.04, 1.0),
                              child: DecoratedBox(
                                decoration: BoxDecoration(
                                  color: week.counts.sufficient
                                      ? EcomsbdColors.navy
                                      : EcomsbdColors.muted2.withValues(
                                          alpha: 0.5,
                                        ),
                                  borderRadius: BorderRadius.circular(4),
                                ),
                              ),
                            ),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          Text(
            latest == null || latest.completed == 0
                ? context.tr('rto.trendNoLatest')
                : context.tr('rto.trendLatest', <String, Object?>{
                    'rto': latest.rto,
                    'completed': latest.completed,
                  }),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
        ],
      ),
    );
  }
}

class _CourierRow extends StatelessWidget {
  const _CourierRow({required this.row});

  final CourierRto row;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
      child: GlassListRow(
        leading: RowIcon(label: courierName(row.provider).substring(0, 1)),
        title: courierName(row.provider),
        subtitle:
            '${context.tr('rto.courierCounts', <String, Object?>{'completed': row.counts.completed, 'rto': row.counts.rto, 'open': row.inTransitNow})}\n'
            '${context.tr('rto.trend.${row.trend}', <String, Object?>{'recent': row.recent.rateLabel, 'previous': row.previous.rateLabel})}',
        trailing: _RateTrailing(counts: row.counts),
      ),
    );
  }
}

class _ProductRow extends StatelessWidget {
  const _ProductRow({required this.row});

  final ProductRto row;

  @override
  Widget build(BuildContext context) {
    final value = row.rtoValue.paisa > 0
        ? '\n${context.tr('rto.productValue', <String, Object?>{'amount': row.rtoValue.formatCompact()})}'
        : '';
    return _RateRow(
      title: row.productName,
      counts: row.counts,
      subtitle: '${_countsLine(context, row.counts)}$value',
    );
  }
}

class _RateRow extends StatelessWidget {
  const _RateRow({
    required this.title,
    required this.counts,
    required this.subtitle,
  });

  final String title;
  final RtoCounts counts;
  final String subtitle;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
      child: GlassListRow(
        leading: RowIcon(
          label: title.isEmpty ? '?' : title.substring(0, 1).toUpperCase(),
        ),
        title: title,
        subtitle: subtitle,
        trailing: _RateTrailing(counts: counts),
      ),
    );
  }
}

/// The rate when the sample supports it, otherwise "limited data".
class _RateTrailing extends StatelessWidget {
  const _RateTrailing({required this.counts});

  final RtoCounts counts;

  @override
  Widget build(BuildContext context) {
    if (counts.completed == 0) {
      return const SizedBox.shrink();
    }
    if (!counts.sufficient) {
      return StatusChip(label: context.tr('rto.limited'), tone: Tone.neutral);
    }
    return Text(counts.rateLabel, style: EcomsbdType.money);
  }
}

class _PatternRow extends StatelessWidget {
  const _PatternRow({required this.row});

  final CustomerPattern row;

  @override
  Widget build(BuildContext context) {
    final name = row.name?.isNotEmpty ?? false
        ? row.name!
        : (row.phoneMasked ?? context.tr('common.customer'));
    final lines = <String>[
      if ((row.name?.isNotEmpty ?? false) && row.phoneMasked != null)
        row.phoneMasked!,
      for (final obs in row.observations)
        if (obs.labelKey != null) context.tr(obs.labelKey!, obs.vars),
    ];
    return Padding(
      padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
      child: GlassListRow(
        leading: const RowIcon(label: '↺'),
        title: name,
        subtitle: lines.join('\n'),
        trailing: const Icon(Icons.chevron_right_rounded),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => RtoCustomerScreen(customerId: row.customerId),
          ),
        ),
      ),
    );
  }
}

/// One customer's parcels with this shop: counts, patterns, recent outcomes.
class RtoCustomerScreen extends ConsumerWidget {
  const RtoCustomerScreen({required this.customerId, super.key});

  final String customerId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final history = ref.watch(rtoCustomerProvider(customerId));
    return DetailScaffold(
      title: context.tr('rto.customerTitle'),
      subtitle: context.tr('rto.ownShopOnly'),
      children: history.when(
        loading: () => <Widget>[SkeletonLoader.card(height: 200)],
        error: (error, _) => <Widget>[
          if (error is ApiError)
            ErrorStateCard(
              error: error,
              onRetry: () => ref.invalidate(rtoCustomerProvider(customerId)),
            )
          else
            _Muted(context.tr('rto.couldNotLoad')),
        ],
        data: (h) => <Widget>[
          RtoHistoryCard(
            title: h.name?.isNotEmpty ?? false
                ? h.name!
                : (h.phoneMasked ?? context.tr('common.customer')),
            subtitle: h.phoneMasked,
            counts: h.counts,
            orderCount: h.orderCount,
            inTransit: h.inTransit,
            cancelledBeforeDispatch: h.cancelledBeforeDispatch,
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          RtoObservationsCard(observations: h.observations),
          const SizedBox(height: EcomsbdSpacing.sm),
          RtoRecentCard(recent: h.recent),
        ],
      ),
    );
  }
}

/// Counts for one customer. Shared with the Risk Check screen.
class RtoHistoryCard extends StatelessWidget {
  const RtoHistoryCard({
    required this.title,
    required this.counts,
    required this.orderCount,
    required this.inTransit,
    required this.cancelledBeforeDispatch,
    super.key,
    this.subtitle,
  });

  final String title;
  final String? subtitle;
  final RtoCounts counts;
  final int orderCount;
  final int inTransit;
  final int cancelledBeforeDispatch;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(title, style: EcomsbdType.bodyStrong),
          if (subtitle != null)
            Text(
              subtitle!,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          const SizedBox(height: EcomsbdSpacing.xs),
          Text(
            counts.completed == 0
                ? context.tr('rto.noCompleted')
                : context.tr('rto.rateOf', <String, Object?>{
                    'rto': counts.rto,
                    'completed': counts.completed,
                  }),
            style: EcomsbdType.body,
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          _Stat(label: context.tr('rc.totalOrders'), value: orderCount),
          _Stat(
            label: context.tr('rto.delivered'),
            value: counts.delivered + counts.partial,
          ),
          _Stat(label: context.tr('rto.returned'), value: counts.returned),
          _Stat(
            label: context.tr('rto.courierCancelled'),
            value: counts.courierCancelled,
          ),
          _Stat(label: context.tr('rto.onTheWay'), value: inTransit),
          _Stat(
            label: context.tr('rto.cancelledBefore'),
            value: cancelledBeforeDispatch,
          ),
        ],
      ),
    );
  }
}

class RtoObservationsCard extends StatelessWidget {
  const RtoObservationsCard({required this.observations, super.key});

  final List<RtoObservation> observations;

  @override
  Widget build(BuildContext context) {
    final known = observations.where((o) => o.labelKey != null).toList();
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            context.tr('rto.observationsTitle'),
            style: EcomsbdType.bodyStrong,
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          if (known.isEmpty)
            _Muted(context.tr('rto.noObservations'))
          else
            for (final obs in known)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Text(
                  '• ${context.tr(obs.labelKey!, obs.vars)}',
                  style: EcomsbdType.caption,
                ),
              ),
          const SizedBox(height: EcomsbdSpacing.xs),
          _Muted(context.tr('rto.observationsNote')),
        ],
      ),
    );
  }
}

class RtoRecentCard extends StatelessWidget {
  const RtoRecentCard({required this.recent, super.key});

  final List<ParcelEvent> recent;

  @override
  Widget build(BuildContext context) {
    if (recent.isEmpty) {
      return const SizedBox.shrink();
    }
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(context.tr('rto.recentTitle'), style: EcomsbdType.bodyStrong),
          const SizedBox(height: EcomsbdSpacing.xs),
          for (final event in recent)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: Text(
                      '${event.orderNumber} · ${courierName(event.provider)}',
                      style: EcomsbdType.caption,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  StatusChip(
                    label: context.tr('rto.outcome.${event.outcome}'),
                    tone: switch (event.outcome) {
                      'DELIVERED' || 'PARTIAL' => Tone.good,
                      'RTO' || 'LOST' => Tone.bad,
                      _ => Tone.info,
                    },
                  ),
                  const SizedBox(width: EcomsbdSpacing.xs),
                  Text(
                    formatRelative(event.at),
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.muted2,
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.value});

  final String label;
  final int value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Text(
              label,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ),
          Text('$value', style: EcomsbdType.bodyStrong),
        ],
      ),
    );
  }
}

class _Muted extends StatelessWidget {
  const _Muted(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: EcomsbdSpacing.xs),
      child: Text(
        text,
        style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
      ),
    );
  }
}
