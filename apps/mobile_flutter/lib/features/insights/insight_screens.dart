import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/money.dart';
import '../../data/analytics/analytics_providers.dart';
import '../../data/analytics/insights_models.dart';
import '../../data/analytics/rto_models.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../money/cases_screen.dart';
import '../shared/responsive.dart';
import 'insight_widgets.dart';
import 'rto_screen.dart';
import '../customers/customers_screen.dart';

/// Insights drill-downs. Each reads one `/analytics/insights` section for the
/// range picked on the overview, and pages long lists on the server.

String _rate(BuildContext context, RtoCounts counts) {
  if (counts.completed == 0) return context.tr('ins.notEnough');
  return counts.sufficient ? counts.rateLabel : context.tr('ins.limited');
}

// --------------------------------------------------------------------------- //
// Cash & COD
// --------------------------------------------------------------------------- //

class InsightsCashScreen extends ConsumerWidget {
  const InsightsCashScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final cash = ref.watch(insightsCashProvider);
    return InsightDetailScaffold(
      title: context.tr('ins.cashTile'),
      description: context.tr('ins.cashDescription'),
      onRefresh: () async {
        ref.invalidate(insightsCashProvider);
        await ref.read(insightsCashProvider.future);
      },
      children: sourcedSection<CashInsights>(
        context,
        cash,
        onRetry: () => ref.invalidate(insightsCashProvider),
        data: (value) => <Widget>[
          ResponsiveGrid(
            minTileWidth: 150,
            maxColumns: 2,
            spacing: EcomsbdSpacing.sm,
            children: <Widget>[
              MetricTile(
                label: context.tr('ins.received'),
                value: Money(value.received.current).formatCompact(),
                caption: changeCaption(context, value.received),
              ),
              MetricTile(
                label: context.tr('ins.receivable'),
                value: value.receivable.formatCompact(),
              ),
              MetricTile(
                label: context.tr('ins.overdue'),
                value: value.overdue.formatCompact(),
                tone: value.overdue.paisa > 0 ? Tone.warning : null,
              ),
              // Not receivable until delivered, so kept on its own tile.
              MetricTile(
                label: context.tr('ins.onRoad'),
                value: value.inTransit.formatCompact(),
                caption: context.trPlural(
                  'ins.parcelCount',
                  value.inTransitCount,
                ),
              ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          WhatChangedCard(explanations: value.explanations),
          SectionHeader(title: context.tr('ins.aging')),
          TextButton(
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const CasesScreen()),
            ),
            child: Text(context.tr('menu.reconciliation')),
          ),
          GlassCard(
            child: value.receivable.paisa == 0
                ? InsightNote(context.tr('ins.nothingOwed'))
                : Column(
                    children: <Widget>[
                      for (final row in value.aging)
                        FactRow(
                          label: row.maxDays == null
                              ? context.tr('ins.daysOpen', <String, Object?>{
                                  'min': row.minDays,
                                })
                              : context.tr('ins.daysRange', <String, Object?>{
                                  'min': row.minDays,
                                  'max': row.maxDays,
                                }),
                          value:
                              '${row.amount.formatCompact()} · '
                              '${context.trPlural('ins.parcelCount', row.count)}',
                        ),
                    ],
                  ),
          ),
          if (value.couriers.isNotEmpty) ...<Widget>[
            SectionHeader(title: context.tr('ins.byCourier')),
            GlassCard(
              child: Column(
                children: <Widget>[
                  for (final (provider, outstanding, overdue) in value.couriers)
                    FactRow(
                      label: courierName(provider),
                      value:
                          '${outstanding.formatCompact()} · '
                          '${context.tr('ins.overdueCaption', <String, Object?>{'amount': overdue.formatCompact()})}',
                      tone: overdue.paisa > 0 ? Tone.warning : null,
                    ),
                ],
              ),
            ),
          ],
          SectionHeader(
            title: context.tr('ins.forecast'),
            subtitle: context.tr('ins.forecastSub'),
          ),
          GlassCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                StatusChip(label: context.tr('ins.estimate'), tone: Tone.info),
                const SizedBox(height: EcomsbdSpacing.xs),
                for (final (key, amount) in value.forecast)
                  if (amount.paisa > 0)
                    FactRow(
                      label: context.tr('ins.fc.$key'),
                      value: amount.formatCompact(),
                    ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

// --------------------------------------------------------------------------- //
// Products
// --------------------------------------------------------------------------- //

const List<String> _productCategories = <String>[
  'all',
  'top_revenue',
  'top_profit',
  'high_rto',
  'low_stock',
  'slow_moving',
  'fast_moving',
];

class InsightsProductsScreen extends ConsumerStatefulWidget {
  const InsightsProductsScreen({super.key});

  @override
  ConsumerState<InsightsProductsScreen> createState() =>
      _InsightsProductsScreenState();
}

class _InsightsProductsScreenState
    extends ConsumerState<InsightsProductsScreen> {
  static const int _pageSize = 20;

  String _category = 'all';
  final List<ProductInsight> _items = <ProductInsight>[];
  ProductInsightPage? _last;
  bool _loading = true;
  Object? _error;

  @override
  void initState() {
    super.initState();
    _load(reset: true);
  }

  Future<void> _load({required bool reset}) async {
    if (!_loading) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final page = await ref
          .read(analyticsRepositoryProvider)
          .insightsProducts(
            days: ref.read(insightsDaysProvider),
            category: _category,
            offset: reset ? 0 : _items.length,
            limit: _pageSize,
          );
      if (!mounted) return;
      setState(() {
        if (reset) _items.clear();
        _items.addAll(page.items);
        _last = page;
      });
    } on Object catch (error) {
      if (mounted) setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    // A new range reloads the list from the first page.
    ref.listen<int>(insightsDaysProvider, (_, _) => _load(reset: true));
    final page = _last;
    final categories = <String>[
      for (final c in _productCategories)
        if (page == null || page.counts.containsKey(c)) c,
    ];
    return InsightDetailScaffold(
      title: context.tr('ins.productsTile'),
      description: context.tr('ins.productsDescription'),
      onRefresh: () => _load(reset: true),
      children: <Widget>[
        Wrap(
          spacing: EcomsbdSpacing.xs,
          runSpacing: EcomsbdSpacing.xs,
          children: <Widget>[
            for (final c in categories)
              ChoiceChip(
                label: Text(
                  page?.counts[c] == null
                      ? context.tr('ins.cat.$c')
                      : '${context.tr('ins.cat.$c')} · ${page!.counts[c]}',
                ),
                selected: c == _category,
                onSelected: (_) {
                  setState(() => _category = c);
                  _load(reset: true);
                },
              ),
          ],
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        if (page?.moneyLocked != null)
          MoneyLockedNotice(reason: page!.moneyLocked!),
        if (_category == 'high_rto')
          InsightNote(
            context.tr('ins.highRtoNote', <String, Object?>{'min': 10}),
          ),
        if (_items.isEmpty && !_loading)
          InsightNote(
            _error == null
                ? context.tr('ins.noProducts')
                : context.tr('ins.couldNotLoad'),
          ),
        for (final row in _items)
          Padding(
            padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
            child: _ProductRow(row: row, slowDays: page?.slowMovingDays ?? 30),
          ),
        if (_loading)
          const Padding(
            padding: EdgeInsets.all(EcomsbdSpacing.md),
            child: Center(child: CircularProgressIndicator()),
          )
        else if (page?.hasMore ?? false)
          TextButton(
            onPressed: () => _load(reset: false),
            child: Text(context.tr('ins.loadMore')),
          ),
      ],
    );
  }
}

class _ProductRow extends StatelessWidget {
  const _ProductRow({required this.row, required this.slowDays});

  final ProductInsight row;
  final int slowDays;

  @override
  Widget build(BuildContext context) {
    final stock = row.stockOnHand;
    final String subtitle;
    if (row.slowMoving && stock != null) {
      subtitle = context.tr('ins.slowRow', <String, Object?>{
        'days': slowDays,
        'count': stock,
      });
    } else {
      subtitle = context.tr('ins.productRow', <String, Object?>{
        'parcels': row.parcels,
        'rto': _rate(context, row.rto),
      });
    }
    final revenue = row.revenue;
    final top = revenue?.formatCompact();
    final String bottom;
    if (revenue != null && row.profit == null && row.parcels > 0) {
      bottom = context.tr('ins.noProfit');
    } else if (row.profit != null) {
      bottom = row.marginBps == null
          ? row.profit!.formatCompact()
          : '${row.profit!.formatCompact()} · ${bpsLabel(row.marginBps!)}';
    } else if (stock != null) {
      bottom = context.tr('ins.stockLeft', <String, Object?>{'count': stock});
    } else {
      bottom = '';
    }
    return GlassListRow(
      leading: RowIcon(
        label: row.name.isEmpty ? '?' : row.name.substring(0, 1).toUpperCase(),
      ),
      title: row.name,
      subtitle: subtitle,
      trailing: top != null || bottom.isNotEmpty
          ? Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                if (top != null) Text(top, style: EcomsbdType.money),
                if (bottom.isNotEmpty) Text(bottom, style: EcomsbdType.caption),
                if (row.profitQuality == 'ESTIMATED')
                  Text(context.tr('ins.estimated'), style: EcomsbdType.caption),
                if (row.stockStatus == 'OUT' || row.stockStatus == 'LOW')
                  StatusChip(
                    label: context.tr('ins.status.${row.stockStatus}'),
                    tone: row.stockStatus == 'OUT' ? Tone.bad : Tone.warning,
                  ),
              ],
            )
          : null,
    );
  }
}

// --------------------------------------------------------------------------- //
// Couriers
// --------------------------------------------------------------------------- //

class InsightsCouriersScreen extends ConsumerWidget {
  const InsightsCouriersScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final couriers = ref.watch(insightsCouriersProvider);
    return InsightDetailScaffold(
      title: context.tr('ins.couriersTitle'),
      description: context.tr('ins.couriersDescription'),
      onRefresh: () async {
        ref.invalidate(insightsCouriersProvider);
        await ref.read(insightsCouriersProvider.future);
      },
      children: sourcedSection<CourierInsights>(
        context,
        couriers,
        onRetry: () => ref.invalidate(insightsCouriersProvider),
        data: (value) => <Widget>[
          if (value.moneyLocked != null) ...<Widget>[
            MoneyLockedNotice(reason: value.moneyLocked!),
            const SizedBox(height: EcomsbdSpacing.sm),
          ],
          if (value.items.isEmpty) InsightNote(context.tr('ins.noCouriers')),
          ResponsiveGrid(
            minTileWidth: 300,
            maxColumns: 2,
            spacing: EcomsbdSpacing.sm,
            children: <Widget>[
              for (final card in value.items) _CourierCard(card: card),
            ],
          ),
          if (value.excluded.isNotEmpty)
            InsightNote(
              context.tr('ins.excluded', <String, Object?>{
                'providers': value.excluded.map(courierName).join(', '),
              }),
            ),
        ],
      ),
    );
  }
}

class _CourierCard extends StatelessWidget {
  const _CourierCard({required this.card});

  final CourierScorecard card;

  @override
  Widget build(BuildContext context) {
    final counts = card.counts;
    final success = counts.completed == 0
        ? context.tr('ins.notEnough')
        : counts.sufficient && counts.successRateBps != null
        ? bpsLabel(counts.successRateBps!)
        : context.tr('ins.limited');
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Text(courierName(card.provider), style: EcomsbdType.sectionTitle),
          const SizedBox(height: EcomsbdSpacing.xs),
          FactRow(
            label: context.tr('ins.fact.completed'),
            value: '${counts.completed}',
          ),
          FactRow(label: context.tr('ins.fact.success'), value: success),
          FactRow(
            label: context.tr('ins.fact.rto'),
            value:
                '${_rate(context, counts)} · ${counts.rto}/${counts.completed}',
          ),
          FactRow(
            label: context.tr('ins.fact.inTransit'),
            value: '${card.inTransitNow}',
          ),
          FactRow(
            label: context.tr('ins.fact.stuck'),
            value: '${card.stuckNow}',
            tone: card.stuckNow > 0 ? Tone.warning : null,
          ),
          if (card.outstanding != null)
            FactRow(
              label: context.tr('ins.fact.outstanding'),
              value: card.outstanding!.formatCompact(),
            ),
          if (card.overdue != null)
            FactRow(
              label: context.tr('ins.fact.overdue'),
              value: card.overdue!.formatCompact(),
              tone: card.overdue!.paisa > 0 ? Tone.warning : null,
            ),
          if (card.outstanding != null)
            FactRow(
              label: context.tr('ins.fact.delay'),
              value: card.payoutDelayDays == null
                  ? context.tr('ins.notEnough')
                  : card.payoutDelayReliable
                  ? context.tr('ins.fact.delayValue', <String, Object?>{
                      'days': card.payoutDelayDays,
                    })
                  : context.tr('ins.limited'),
            ),
          if (card.discrepancyCount != null)
            FactRow(
              label: context.tr('ins.fact.discrepancies'),
              value: card.discrepancyCount == 0
                  ? '0'
                  : '${card.discrepancyCount} · ${card.discrepancy!.formatCompact()}',
              tone: (card.discrepancyCount ?? 0) > 0 ? Tone.bad : null,
            ),
        ],
      ),
    );
  }
}

// --------------------------------------------------------------------------- //
// Inventory
// --------------------------------------------------------------------------- //

const List<String> _stockFilters = <String>[
  'all',
  'low_stock',
  'out_of_stock',
  'slow_moving',
  'fast_moving',
];

class InsightsInventoryScreen extends ConsumerStatefulWidget {
  const InsightsInventoryScreen({super.key});

  @override
  ConsumerState<InsightsInventoryScreen> createState() =>
      _InsightsInventoryScreenState();
}

class _InsightsInventoryScreenState
    extends ConsumerState<InsightsInventoryScreen> {
  static const int _pageSize = 20;

  String _filter = 'all';
  final List<StockItemInsight> _items = <StockItemInsight>[];
  InventoryInsights? _last;
  bool _loading = true;
  Object? _error;

  @override
  void initState() {
    super.initState();
    _load(reset: true);
  }

  Future<void> _load({required bool reset}) async {
    if (!_loading) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final page = await ref
          .read(analyticsRepositoryProvider)
          .insightsInventory(
            days: ref.read(insightsDaysProvider),
            filter: _filter,
            offset: reset ? 0 : _items.length,
            limit: _pageSize,
          );
      if (!mounted) return;
      setState(() {
        if (reset) _items.clear();
        _items.addAll(page.items);
        _last = page;
      });
    } on Object catch (error) {
      if (mounted) setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    ref.listen<int>(insightsDaysProvider, (_, _) => _load(reset: true));
    final page = _last;
    return InsightDetailScaffold(
      title: context.tr('ins.inventoryTile'),
      description: context.tr('ins.inventoryDescription', <String, Object?>{
        'days': page?.slowMovingDays ?? 30,
      }),
      onRefresh: () => _load(reset: true),
      children: <Widget>[
        if (page != null) ...<Widget>[
          ResponsiveGrid(
            minTileWidth: 140,
            maxColumns: 4,
            spacing: EcomsbdSpacing.sm,
            children: <Widget>[
              MetricTile(
                label: context.tr('ins.units'),
                value: '${page.totalUnits}',
              ),
              MetricTile(
                label: context.tr('ins.lowItems'),
                value: '${page.lowStockItems}',
                tone: page.lowStockItems > 0 ? Tone.warning : null,
              ),
              MetricTile(
                label: context.tr('ins.outItems'),
                value: '${page.outOfStockItems}',
                tone: page.outOfStockItems > 0 ? Tone.bad : null,
              ),
              MetricTile(
                label: context.tr('ins.slowItems'),
                value: '${page.counts['slow_moving'] ?? 0}',
              ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          WhatChangedCard(explanations: page.explanations),
          const SizedBox(height: EcomsbdSpacing.md),
        ],
        Wrap(
          spacing: EcomsbdSpacing.xs,
          runSpacing: EcomsbdSpacing.xs,
          children: <Widget>[
            for (final f in _stockFilters)
              ChoiceChip(
                label: Text(context.tr('ins.cat.$f')),
                selected: f == _filter,
                onSelected: (_) {
                  setState(() => _filter = f);
                  _load(reset: true);
                },
              ),
          ],
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        if (_items.isEmpty && !_loading)
          InsightNote(
            _error == null
                ? context.tr('ins.noProducts')
                : context.tr('ins.couldNotLoad'),
          ),
        for (final item in _items)
          Padding(
            padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
            child: GlassListRow(
              title: item.label,
              subtitle: item.slowMoving
                  ? context.tr('ins.slowRow', <String, Object?>{
                      'days': page?.slowMovingDays ?? 30,
                      'count': item.stockOnHand,
                    })
                  : context.tr('ins.itemRow', <String, Object?>{
                      'count': item.stockOnHand,
                      'units': item.unitsBooked,
                    }),
              trailing: item.status == 'OK'
                  ? null
                  : StatusChip(
                      label: context.tr('ins.status.${item.status}'),
                      tone: item.status == 'OUT' ? Tone.bad : Tone.warning,
                    ),
            ),
          ),
        if (_loading)
          const Padding(
            padding: EdgeInsets.all(EcomsbdSpacing.md),
            child: Center(child: CircularProgressIndicator()),
          )
        else if (page?.hasMore ?? false)
          TextButton(
            onPressed: () => _load(reset: false),
            child: Text(context.tr('ins.loadMore')),
          ),
      ],
    );
  }
}

// --------------------------------------------------------------------------- //
// Customers
// --------------------------------------------------------------------------- //

class InsightsCustomersScreen extends ConsumerWidget {
  const InsightsCustomersScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final customers = ref.watch(insightsCustomersProvider);
    return InsightDetailScaffold(
      title: context.tr('ins.customersTile'),
      description: context.tr('ins.customersDescription'),
      onRefresh: () async {
        ref.invalidate(insightsCustomersProvider);
        await ref.read(insightsCustomersProvider.future);
      },
      children: sourcedSection<CustomerInsights>(
        context,
        customers,
        onRetry: () => ref.invalidate(insightsCustomersProvider),
        data: (value) => <Widget>[
          ResponsiveGrid(
            minTileWidth: 150,
            maxColumns: 3,
            spacing: EcomsbdSpacing.sm,
            children: <Widget>[
              MetricTile(
                label: context.tr('ins.activeCustomers'),
                value: '${value.active.current}',
                caption: changeCaption(context, value.active),
              ),
              MetricTile(
                label: context.tr('ins.newCustomers'),
                value: '${value.newCustomers.current}',
                caption: changeCaption(context, value.newCustomers),
              ),
              MetricTile(
                label: context.tr('ins.returningCustomers'),
                value: '${value.returning}',
              ),
              MetricTile(
                label: context.tr('ins.repeatShare'),
                value: value.ordersWithCustomer == 0
                    ? context.tr('ins.notEnough')
                    : value.sufficient && value.repeatOrderRateBps != null
                    ? bpsLabel(value.repeatOrderRateBps!)
                    : context.tr('ins.limited'),
                caption: context.tr('ins.repeatShareCaption', <String, Object?>{
                  'repeat': value.repeatOrders,
                  'orders': value.ordersWithCustomer,
                }),
              ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          WhatChangedCard(explanations: value.explanations),
          SectionHeader(title: context.tr('ins.lifetime')),
          GlassCard(
            child: Column(
              children: <Widget>[
                FactRow(
                  label: context.tr('ins.totalCustomers'),
                  value: '${value.totalCustomers}',
                ),
                FactRow(
                  label: context.tr('ins.repeatCustomers'),
                  value: '${value.repeatCustomers}',
                ),
                FactRow(
                  label: context.tr('ins.multiDelivered'),
                  value: '${value.multiDeliveryCustomers}',
                ),
                FactRow(
                  label: context.tr('ins.repeatRto'),
                  value: '${value.repeatRtoCustomers}',
                ),
              ],
            ),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => const CustomersScreen(initialSegment: 'REPEAT'),
              ),
            ),
            child: Text(context.tr('crm.REPEAT')),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) =>
                    const CustomersScreen(initialSegment: 'INACTIVE'),
              ),
            ),
            child: Text(context.tr('crm.INACTIVE')),
          ),
          if (value.repeatRtoCustomers > 0)
            TextButton(
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute<void>(builder: (_) => const RtoScreen()),
              ),
              child: Text(context.tr('ins.repeatRtoLink')),
            ),
        ],
      ),
    );
  }
}
