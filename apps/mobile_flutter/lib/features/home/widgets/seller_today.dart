import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/api/api_error.dart';
import '../../../core/money.dart';
import '../../../data/analytics/analytics_providers.dart';
import '../../../data/analytics/models.dart';
import '../../../data/channels/channel_models.dart';
import '../../../data/commerce/commerce_providers.dart';
import '../../../data/commerce/models.dart';
import '../../../data/commerce/repository_support.dart';
import '../../../data/couriers/courier_compare.dart';
import '../../../data/couriers/courier_providers.dart';
import '../../../design/components/badges.dart';
import '../../../design/components/navigation.dart';
import '../../../design/components/seller_blocks.dart';
import '../../../design/components/states.dart';
import '../../../design/components/surfaces.dart';
import '../../../design/tokens.dart';
import '../../../l10n/app_strings.dart';
import '../../couriers/courier_compare_screen.dart';
import '../../money/receivables_screen.dart';
import '../../orders/order_compose_screen.dart';
import '../../products/products_screen.dart';
import '../../risk/risk_review_sheet.dart';
import '../../settings/connections_screen.dart';
import '../../settings/courier_accounts_screen.dart';
import '../../shared/data_state.dart';

// --------------------------------------------------------------------------- //
// Data
// --------------------------------------------------------------------------- //

/// The orders waiting on the seller before a courier has them.
@immutable
class SellerQueue {
  const SellerQueue({
    required this.toConfirm,
    required this.toBook,
    required this.capped,
    required this.risky,
    this.oldestToBook,
  });

  /// Drafts to confirm.
  final int toConfirm;

  /// Confirmed or packed, not yet with a courier.
  final int toBook;

  /// True when a count reached the read limit and is a floor, not a total.
  final bool capped;

  /// Pre-courier orders the server marked high risk.
  final List<SellerOrder> risky;
  final DateTime? oldestToBook;
}

const int _queueLimit = 50;

/// Read from the server's own order statuses; nothing is counted on a guess.
///
/// Starts after Home's main figures and reads one status at a time: the
/// server's database pooler has few connections, and a burst of parallel
/// Home requests exhausted it on a real device (EMAXCONNSESSION → 500s).
final sellerQueueProvider = FutureProvider<SellerQueue>((ref) async {
  try {
    await ref.watch(homeMetricsProvider.future);
  } on ApiError {
    // The queue is still worth reading when the summary failed.
  }
  final orders = ref.watch(ordersRepositoryProvider);
  final pages = <Sourced<PagedResult<SellerOrder>>>[
    for (final status in const <String>['DRAFT', 'CONFIRMED', 'PACKED'])
      await orders.list(status: status, limit: _queueLimit),
  ];
  final drafts = pages[0].value;
  final toBook = <SellerOrder>[
    ...pages[1].value.items,
    ...pages[2].value.items,
  ];
  DateTime? oldest;
  for (final order in toBook) {
    if (oldest == null || order.createdAt.isBefore(oldest)) {
      oldest = order.createdAt;
    }
  }
  return SellerQueue(
    toConfirm: drafts.items.length,
    toBook: toBook.length,
    capped: pages.any((page) => page.value.hasMore),
    risky: <SellerOrder>[
      for (final order in <SellerOrder>[...drafts.items, ...toBook])
        if (order.riskState == 'HIGH') order,
    ],
    oldestToBook: oldest,
  );
});

/// First-run setup, from what the server says the shop has.
@immutable
class SetupProgress {
  const SetupProgress(this.steps);

  final List<({String key, bool done})> steps;

  int get done => steps.where((s) => s.done).length;
  bool get isComplete => done == steps.length;
}

final setupProgressProvider = FutureProvider.autoDispose<SetupProgress>((
  ref,
) async {
  // Checked after the order queue, never in Home's first burst.
  await ref.watch(sellerQueueProvider.future);
  // A failed check is unknown, not "not done": let it fail the provider so
  // the row hides rather than showing a wrong count.
  Future<bool> safely(Future<bool> Function() check) => check();

  final channels = await safely(() async {
    final list = await ref.watch(salesChannelsProvider.future);
    bool connected(SalesChannel c) =>
        list.any((s) => s.channel == c && s.health == ChannelHealth.connected);
    return connected(SalesChannel.facebook);
  });
  final website = await safely(() async {
    final list = await ref.watch(salesChannelsProvider.future);
    return list.any(
      (s) =>
          s.channel == SalesChannel.website &&
          s.health == ChannelHealth.connected,
    );
  });
  final courier = await safely(() async {
    final rows = await ref.watch(bookableCouriersProvider.future);
    return rows.any((row) => row.bookable);
  });
  final product = await safely(() async {
    final page = await ref.read(productsRepositoryProvider).list(limit: 1);
    return page.value.items.isNotEmpty;
  });
  final order = await safely(() async {
    final page = await ref.read(ordersRepositoryProvider).list(limit: 1);
    return page.value.items.isNotEmpty;
  });
  final costs = await safely(() async {
    final metrics = (await ref.watch(homeMetricsProvider.future)).value;
    return product && metrics.incompleteParcels == 0;
  });
  return SetupProgress(<({String key, bool done})>[
    (key: 'courier', done: courier),
    (key: 'facebook', done: channels),
    (key: 'product', done: product),
    (key: 'order', done: order),
    (key: 'cost', done: costs),
    (key: 'website', done: website),
  ]);
});

// --------------------------------------------------------------------------- //
// Needs your attention
// --------------------------------------------------------------------------- //

/// Compact, count-first rows. Each one opens the screen that clears it.
class TodayAttention extends ConsumerWidget {
  const TodayAttention({required this.metrics, super.key, this.onNavigate});

  final HomeMetrics metrics;
  final ValueChanged<String>? onNavigate;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final queueState = ref.watch(sellerQueueProvider);
    final queue = queueState.valueOrNull;
    final unanswered = ref.watch(unansweredThreadCountProvider);
    // Read after the queue so it is not part of Home's first burst.
    final lowStock = queue == null
        ? null
        : ref
              .watch(insightsOverviewProvider)
              .valueOrNull
              ?.value
              .stock
              ?.lowStockItems;
    String count(int value) =>
        queue != null && queue.capped && value >= _queueLimit
        ? '$value+'
        : '$value';
    final hasUnderpaidAlert = metrics.alerts.any((a) => a.kind == 'UNDERPAID');

    final rows = <_ActionRowData>[
      if (queue != null && queue.toBook > 0)
        _ActionRowData(
          icon: Icons.local_shipping_outlined,
          title: context.tr('today.toBook', <String, Object?>{
            'count': count(queue.toBook),
          }),
          detail: context.tr('today.oldestWaiting', <String, Object?>{
            'when': formatRelative(queue.oldestToBook),
          }),
          tone: Tone.warning,
          onTap: () => onNavigate?.call('orders:confirmed'),
        ),
      if (queue != null && queue.toConfirm > 0)
        _ActionRowData(
          icon: Icons.fact_check_outlined,
          title: context.tr('today.toConfirm', <String, Object?>{
            'count': count(queue.toConfirm),
          }),
          detail: context.tr('today.toConfirmDetail'),
          tone: Tone.info,
          onTap: () => onNavigate?.call('orders:actionNeeded'),
        ),
      if (unanswered != null && unanswered > 0)
        _ActionRowData(
          icon: Icons.forum_outlined,
          title: context.trPlural('today.unanswered', unanswered),
          detail: context.tr('today.unansweredDetail'),
          tone: Tone.warning,
          onTap: () => onNavigate?.call('inbox'),
        ),
      if (metrics.mismatchCount > 0 && !hasUnderpaidAlert)
        _ActionRowData(
          icon: Icons.rule_rounded,
          title: context.tr('today.mismatch', <String, Object?>{
            'amount': metrics.mismatch.format(),
          }),
          detail: context.trPlural(
            'today.mismatchDetail',
            metrics.mismatchCount,
          ),
          tone: Tone.bad,
          onTap: () => onNavigate?.call('money'),
        ),
      if (queue != null && queue.risky.isNotEmpty)
        _ActionRowData(
          icon: Icons.warning_amber_rounded,
          title: context.trPlural('today.risky', queue.risky.length),
          detail:
              queue.risky.first.customerName ?? queue.risky.first.orderNumber,
          tone: Tone.bad,
          onTap: () => unawaited(
            RiskReviewSheet.review(context, order: queue.risky.first),
          ),
        ),
      if (lowStock != null && lowStock > 0)
        _ActionRowData(
          icon: Icons.inventory_2_outlined,
          title: context.trPlural('today.lowStock', lowStock),
          detail: context.tr('today.lowStockDetail'),
          tone: Tone.warning,
          onTap: () => _push(context, const ProductsScreen()),
        ),
      // The server's own money alerts, in its own words.
      for (final alert in metrics.alerts)
        _ActionRowData(
          icon: Icons.payments_outlined,
          title: alert.title,
          detail: alert.detail,
          tone: switch (alert.severity) {
            'CRITICAL' => Tone.bad,
            'WARNING' => Tone.warning,
            _ => Tone.info,
          },
          onTap: () => onNavigate?.call('money'),
        ),
    ];

    // A quiet day is said in one line; a failed read is never "all clear".
    final Widget body;
    if (rows.isEmpty && queueState.isLoading) {
      body = const ContentLoader(minHeight: 60);
    } else if (rows.isEmpty) {
      body = _QuietLine(
        icon: queueState.hasError
            ? Icons.sync_problem_rounded
            : Icons.check_circle_outline,
        tone: queueState.hasError ? Tone.neutral : Tone.good,
        text: queueState.hasError
            ? context.tr('today.unavailable')
            : context.tr('today.allClear'),
      );
    } else {
      body = Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          for (var i = 0; i < rows.length; i++) ...<Widget>[
            if (i > 0) const SizedBox(height: 9),
            AttentionTile(
              icon: rows[i].icon,
              title: rows[i].title,
              detail: rows[i].detail,
              tone: rows[i].tone,
              onTap: rows[i].onTap,
            ),
          ],
        ],
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        SectionHeader(
          title: context.tr('today.attention'),
          subtitle: context.tr('today.attentionSub'),
        ),
        body,
      ],
    );
  }
}

/// One line on a white strip, for "nothing to do" and "could not check".
class _QuietLine extends StatelessWidget {
  const _QuietLine({
    required this.icon,
    required this.tone,
    required this.text,
  });

  final IconData icon;
  final Tone tone;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(13),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: EcomsbdRadii.row,
        border: Border.all(color: EcomsbdColors.line),
      ),
      child: Row(
        children: <Widget>[
          SoftIcon(icon: icon, tone: tone, size: 36),
          const SizedBox(width: 12),
          Expanded(child: Text(text, style: EcomsbdType.body)),
        ],
      ),
    );
  }
}

@immutable
class _ActionRowData {
  const _ActionRowData({
    required this.icon,
    required this.title,
    required this.detail,
    required this.tone,
    this.onTap,
  });

  final IconData icon;
  final String title;
  final String detail;
  final Tone tone;
  final VoidCallback? onTap;
}

// --------------------------------------------------------------------------- //
// Today's suggestions
// --------------------------------------------------------------------------- //

/// What a suggestion's one button does.
enum SuggestionAction {
  compareCouriers,
  bookCourier,
  reviewRisk,
  reconcile,
  reply,
  chaseCod,
  addCosts,
  connectCourier,
}

@immutable
class TodaySuggestion {
  const TodaySuggestion({
    required this.action,
    required this.titleKey,
    required this.whyKey,
    this.vars = const <String, Object?>{},
  });

  final SuggestionAction action;
  final String titleKey;

  /// Why it appeared — every suggestion says what data raised it.
  final String whyKey;
  final Map<String, Object?> vars;
}

/// The rules, in priority order. Deterministic and explainable on purpose:
/// no score, just "this data says this".
@visibleForTesting
List<TodaySuggestion> buildTodaySuggestions({
  required HomeMetrics metrics,
  SellerQueue? queue,
  int? bookableCouriers,
  int? unanswered,
}) {
  final out = <TodaySuggestion>[];
  if (queue != null && queue.risky.isNotEmpty) {
    out.add(
      TodaySuggestion(
        action: SuggestionAction.reviewRisk,
        titleKey: 'sug.risk',
        whyKey: 'sug.riskWhy',
        vars: <String, Object?>{
          'name':
              queue.risky.first.customerName ?? queue.risky.first.orderNumber,
        },
      ),
    );
  }
  // Booking itself is already on the attention list; a suggestion here only
  // adds advice — compare when there is a choice, connect when there is none.
  if (queue != null && queue.toBook > 0) {
    if ((bookableCouriers ?? 0) >= 2) {
      out.add(
        TodaySuggestion(
          action: SuggestionAction.compareCouriers,
          titleKey: 'sug.compare',
          whyKey: 'sug.compareWhy',
          vars: <String, Object?>{
            'count': queue.toBook,
            'couriers': bookableCouriers,
          },
        ),
      );
    } else if (bookableCouriers == 0) {
      out.add(
        TodaySuggestion(
          action: SuggestionAction.connectCourier,
          titleKey: 'sug.connect',
          whyKey: 'sug.connectWhy',
          vars: <String, Object?>{'count': queue.toBook},
        ),
      );
    }
  }
  if (unanswered != null && unanswered > 0) {
    out.add(
      TodaySuggestion(
        action: SuggestionAction.reply,
        titleKey: 'sug.reply',
        whyKey: 'sug.replyWhy',
        vars: <String, Object?>{'count': unanswered},
      ),
    );
  }
  if (metrics.mismatchCount > 0) {
    out.add(
      TodaySuggestion(
        action: SuggestionAction.reconcile,
        titleKey: 'sug.reconcile',
        whyKey: 'sug.reconcileWhy',
        vars: <String, Object?>{
          'amount': metrics.mismatch.format(),
          'count': metrics.mismatchCount,
        },
      ),
    );
  }
  if (metrics.codOverdue.paisa > 0) {
    out.add(
      TodaySuggestion(
        action: SuggestionAction.chaseCod,
        titleKey: 'sug.cod',
        whyKey: 'sug.codWhy',
        vars: <String, Object?>{'amount': metrics.codOverdue.format()},
      ),
    );
  }
  if (metrics.incompleteParcels > 0) {
    out.add(
      TodaySuggestion(
        action: SuggestionAction.addCosts,
        titleKey: 'sug.costs',
        whyKey: 'sug.costsWhy',
        vars: <String, Object?>{'count': metrics.incompleteParcels},
      ),
    );
  }
  return out;
}

class TodaySuggestions extends ConsumerWidget {
  const TodaySuggestions({required this.metrics, super.key, this.onNavigate});

  final HomeMetrics metrics;
  final ValueChanged<String>? onNavigate;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final queue = ref.watch(sellerQueueProvider).valueOrNull;
    final couriers = queue != null && queue.toBook > 0
        ? ref.watch(bookableCouriersProvider).valueOrNull
        : null;
    final suggestions = buildTodaySuggestions(
      metrics: metrics,
      queue: queue,
      bookableCouriers: couriers?.where((c) => c.bookable).length,
      unanswered: ref.watch(unansweredThreadCountProvider),
    ).take(3).toList();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        SectionHeader(
          title: context.tr('sug.title'),
          subtitle: context.tr('sug.subtitle'),
        ),
        DarkPanel(
          colors: EcomsbdColors.dailyGradient,
          radius: 24,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(
                          context.tr('sug.planTitle'),
                          style: EcomsbdType.sectionHead.copyWith(
                            color: Colors.white,
                          ),
                        ),
                        const SizedBox(height: 3),
                        Text(
                          context.tr('sug.planSub'),
                          style: EcomsbdType.caption.copyWith(
                            color: EcomsbdColors.onDarkMuted,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 10),
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 9,
                      vertical: 6,
                    ),
                    decoration: BoxDecoration(
                      color: const Color(0x1FFFFFFF),
                      borderRadius: EcomsbdRadii.round,
                      border: Border.all(color: const Color(0x1FFFFFFF)),
                    ),
                    child: Text(
                      context.tr('sug.badge'),
                      style: EcomsbdType.chip.copyWith(
                        fontSize: 10,
                        color: Colors.white,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 14),
              if (suggestions.isEmpty)
                _DarkNote(
                  checking: queue == null,
                  title: queue == null
                      ? context.tr('sug.checking')
                      : context.tr('sug.emptyTitle'),
                  body: context.tr('sug.emptyBody'),
                )
              else
                for (var i = 0; i < suggestions.length; i++) ...<Widget>[
                  if (i > 0) const SizedBox(height: 8),
                  _SuggestionRow(
                    suggestion: suggestions[i],
                    primary: i == 0,
                    onPressed: () => _run(context, suggestions[i], queue),
                  ),
                ],
              Container(
                margin: const EdgeInsets.only(top: 11),
                padding: const EdgeInsets.only(top: 11),
                decoration: const BoxDecoration(
                  border: Border(top: BorderSide(color: Color(0x1AFFFFFF))),
                ),
                child: Text(
                  context.tr('sug.footer'),
                  style: EcomsbdType.caption.copyWith(
                    fontSize: 11,
                    color: const Color(0xFFB9C7D4),
                  ),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  void _run(BuildContext context, TodaySuggestion s, SellerQueue? queue) {
    switch (s.action) {
      case SuggestionAction.compareCouriers:
        unawaited(CourierCompareScreen.open(context));
      case SuggestionAction.bookCourier:
        onNavigate?.call('orders:confirmed');
      case SuggestionAction.reviewRisk:
        final order = queue?.risky.firstOrNull;
        if (order != null) {
          unawaited(RiskReviewSheet.review(context, order: order));
        }
      case SuggestionAction.reconcile:
        onNavigate?.call('money');
      case SuggestionAction.reply:
        onNavigate?.call('inbox');
      case SuggestionAction.chaseCod:
        _push(context, const ReceivablesScreen());
      case SuggestionAction.addCosts:
        _push(context, const ProductsScreen());
      case SuggestionAction.connectCourier:
        _push(context, const CourierAccountsScreen());
    }
  }
}

/// The positive state of the suggestions card: nothing needs a decision.
class _DarkNote extends StatelessWidget {
  const _DarkNote({
    required this.checking,
    required this.title,
    required this.body,
  });

  final bool checking;
  final String title;
  final String body;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: const Color(0x14FFFFFF),
        borderRadius: BorderRadius.circular(15),
        border: Border.all(color: const Color(0x1AFFFFFF)),
      ),
      child: Row(
        children: <Widget>[
          _DarkIcon(
            icon: checking
                ? Icons.hourglass_top_rounded
                : Icons.check_circle_outline_rounded,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  title,
                  style: EcomsbdType.bodyStrong.copyWith(
                    color: Colors.white,
                    fontSize: 13.5,
                  ),
                ),
                if (!checking) ...<Widget>[
                  const SizedBox(height: 2),
                  Text(
                    body,
                    style: EcomsbdType.caption.copyWith(
                      color: const Color(0xFFB9C7D4),
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _DarkIcon extends StatelessWidget {
  const _DarkIcon({required this.icon});

  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 32,
      height: 32,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: const Color(0x1CFFFFFF),
        borderRadius: BorderRadius.circular(11),
      ),
      child: Icon(icon, size: 17, color: Colors.white),
    );
  }
}

/// `.suggestion` — icon, the advice, why it appeared, and one button.
class _SuggestionRow extends StatelessWidget {
  const _SuggestionRow({
    required this.suggestion,
    required this.primary,
    required this.onPressed,
  });

  final TodaySuggestion suggestion;

  /// The first suggestion's button is orange; the rest are white.
  final bool primary;
  final VoidCallback onPressed;

  static IconData _iconFor(SuggestionAction action) => switch (action) {
    SuggestionAction.compareCouriers => Icons.local_shipping_outlined,
    SuggestionAction.bookCourier => Icons.local_shipping_outlined,
    SuggestionAction.reviewRisk => Icons.warning_amber_rounded,
    SuggestionAction.reconcile => Icons.rule_rounded,
    SuggestionAction.reply => Icons.forum_outlined,
    SuggestionAction.chaseCod => Icons.schedule_rounded,
    SuggestionAction.addCosts => Icons.inventory_2_outlined,
    SuggestionAction.connectCourier => Icons.link_rounded,
  };

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(10, 9, 8, 9),
      decoration: BoxDecoration(
        color: const Color(0x14FFFFFF),
        borderRadius: BorderRadius.circular(15),
        border: Border.all(color: const Color(0x1AFFFFFF)),
      ),
      child: Row(
        children: <Widget>[
          _DarkIcon(icon: _iconFor(suggestion.action)),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  context.tr(suggestion.titleKey, suggestion.vars),
                  style: EcomsbdType.bodyStrong.copyWith(
                    color: Colors.white,
                    fontSize: 13.5,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  context.tr(suggestion.whyKey, suggestion.vars),
                  style: EcomsbdType.caption.copyWith(
                    color: const Color(0xFFB9C7D4),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          TextButton(
            onPressed: onPressed,
            style: TextButton.styleFrom(
              backgroundColor: primary ? EcomsbdColors.orange : Colors.white,
              foregroundColor: primary ? Colors.white : EcomsbdColors.ink,
              minimumSize: const Size(0, 36),
              padding: const EdgeInsets.symmetric(horizontal: 11),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(11),
              ),
              textStyle: EcomsbdType.chip.copyWith(
                fontSize: 12,
                fontWeight: FontWeight.w800,
              ),
            ),
            child: Text(context.tr('sug.cta.${suggestion.action.name}')),
          ),
        ],
      ),
    );
  }
}

// --------------------------------------------------------------------------- //
// Courier saving opportunity
// --------------------------------------------------------------------------- //

/// `.saving-card` — what today's unbooked parcels would cost with each
/// courier, cheapest first.
///
/// Uses the comparison screen's own rates for a standard parcel (inside
/// Dhaka, up to 1 kg). Those are the bundled sample rates until an order is
/// chosen, and the card says so; it never presents them as the shop's price.
class CourierSavingPreview extends ConsumerWidget {
  const CourierSavingPreview({super.key});

  static const CompareRequest _standard = CompareRequest();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // After the order queue, never in Home's first burst of requests.
    final queue = ref.watch(sellerQueueProvider).valueOrNull;
    if (queue == null) return const SizedBox.shrink();
    final comparison = ref
        .watch(courierComparisonProvider(_standard))
        .valueOrNull;
    if (comparison == null) return const SizedBox.shrink();
    final priced =
        comparison.estimates
            .where((estimate) => estimate.total != null)
            .toList()
          ..sort((a, b) => a.total!.paisa.compareTo(b.total!.paisa));
    if (priced.length < 2) return const SizedBox.shrink();

    final rows = priced.take(3).toList();
    final parcels = queue.toBook > 0 ? queue.toBook : 1;
    int costOf(CourierRateEstimate estimate) => estimate.total!.paisa * parcels;
    final lowest = costOf(rows.first);
    final saving = Money(costOf(rows.last) - lowest);
    final sample = comparison.origin == RateOrigin.sample;
    void open() => unawaited(CourierCompareScreen.open(context));

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        SectionHeader(
          title: context.tr('save.title'),
          subtitle: context.tr('save.subtitle'),
          actionLabel: context.tr('save.full'),
          onAction: open,
        ),
        Material(
          color: Colors.transparent,
          child: InkWell(
            onTap: open,
            borderRadius: EcomsbdRadii.card,
            child: Ink(
              padding: const EdgeInsets.all(15),
              decoration: BoxDecoration(
                borderRadius: EcomsbdRadii.card,
                border: Border.all(color: EcomsbdColors.savingBorder),
                gradient: const LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: <Color>[Color(0xFFFFF7F2), Colors.white],
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[
                  Row(
                    children: <Widget>[
                      const SoftIcon(
                        icon: Icons.local_shipping_outlined,
                        size: 42,
                        background: EcomsbdColors.savingIcon,
                      ),
                      const SizedBox(width: 11),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: <Widget>[
                            Text(
                              queue.toBook > 0
                                  ? context.trPlural(
                                      'save.unbooked',
                                      queue.toBook,
                                    )
                                  : context.tr('save.oneParcel'),
                              style: EcomsbdType.bodyStrong,
                            ),
                            Text(
                              sample
                                  ? context.tr('save.sample')
                                  : context.tr('save.quoted'),
                              style: EcomsbdType.caption.copyWith(
                                color: sample
                                    ? EcomsbdColors.amber
                                    : EcomsbdColors.muted,
                                fontWeight: sample ? FontWeight.w700 : null,
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(width: 8),
                      Column(
                        crossAxisAlignment: CrossAxisAlignment.end,
                        children: <Widget>[
                          Text(
                            saving.format(),
                            style: EcomsbdType.metricValue.copyWith(
                              color: EcomsbdColors.green,
                              fontWeight: FontWeight.w900,
                            ),
                          ),
                          Text(
                            context.tr('save.possible'),
                            style: EcomsbdType.caption.copyWith(
                              color: EcomsbdColors.muted,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  const Divider(
                    height: 1,
                    thickness: 1,
                    color: Color(0xFFF2DDD4),
                  ),
                  _SavingLine(
                    cells: <String>[
                      context.tr('save.courier'),
                      context.trPlural('save.parcels', parcels),
                      context.tr('save.difference'),
                    ],
                    header: true,
                  ),
                  for (var i = 0; i < rows.length; i++)
                    _SavingLine(
                      cells: <String>[
                        rows[i].displayName,
                        Money(costOf(rows[i])).format(),
                        i == 0
                            ? context.tr('save.lowest')
                            : '+${Money(costOf(rows[i]) - lowest).format()}',
                      ],
                      emphasise: i == 0,
                      isLast: i == rows.length - 1,
                    ),
                  // The parcel behind every figure above: a sample, not the
                  // shop's own orders.
                  Text(
                    context.tr('save.assumption'),
                    style: EcomsbdType.caption.copyWith(
                      fontSize: 11,
                      color: EcomsbdColors.muted,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ],
    );
  }
}

class _SavingLine extends StatelessWidget {
  const _SavingLine({
    required this.cells,
    this.header = false,
    this.emphasise = false,
    this.isLast = false,
  });

  final List<String> cells;
  final bool header;
  final bool emphasise;
  final bool isLast;

  @override
  Widget build(BuildContext context) {
    final base = header
        ? EcomsbdType.chip.copyWith(
            fontSize: 10.5,
            color: EcomsbdColors.muted,
            fontWeight: FontWeight.w800,
          )
        : EcomsbdType.caption.copyWith(
            fontSize: 12.5,
            color: EcomsbdColors.ink,
          );
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 8),
      decoration: BoxDecoration(
        border: isLast || header
            ? null
            : const Border(
                bottom: BorderSide(color: EcomsbdColors.savingDivider),
              ),
      ),
      child: Row(
        children: <Widget>[
          Expanded(
            flex: 12,
            child: Text(
              cells[0],
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: emphasise
                  ? base.copyWith(fontWeight: FontWeight.w800)
                  : base,
            ),
          ),
          Expanded(
            flex: 9,
            child: Text(cells[1], textAlign: TextAlign.right, style: base),
          ),
          Expanded(
            flex: 9,
            child: Text(
              cells[2],
              textAlign: TextAlign.right,
              style: emphasise
                  ? base.copyWith(
                      color: EcomsbdColors.green,
                      fontWeight: FontWeight.w800,
                    )
                  : base,
            ),
          ),
        ],
      ),
    );
  }
}

// --------------------------------------------------------------------------- //
// Sales channels
// --------------------------------------------------------------------------- //

/// `.channel-strip` — Facebook, WhatsApp and the website, each with the state
/// the integrations hub reports. Hidden until that state is known.
class SalesChannelStrip extends ConsumerWidget {
  const SalesChannelStrip({super.key});

  static const List<SalesChannel> _shown = <SalesChannel>[
    SalesChannel.facebook,
    SalesChannel.whatsapp,
    SalesChannel.website,
  ];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // After the order queue, never in Home's first burst of requests.
    final queue = ref.watch(sellerQueueProvider);
    if (queue.isLoading) return const SizedBox.shrink();
    final channels = ref.watch(salesChannelsProvider).valueOrNull;
    if (channels == null) return const SizedBox.shrink();
    void manage() => _push(context, const ConnectionsScreen());

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        SectionHeader(
          title: context.tr('home.channelsTitle'),
          subtitle: context.tr('home.channelsSub'),
          actionLabel: context.tr('conn.manage'),
          onAction: manage,
        ),
        Row(
          children: <Widget>[
            for (var i = 0; i < _shown.length; i++) ...<Widget>[
              if (i > 0) const SizedBox(width: 8),
              Expanded(
                child: _ChannelPill(
                  status: channels.firstWhere(
                    (status) => status.channel == _shown[i],
                    orElse: () => ChannelStatus(
                      channel: _shown[i],
                      health: ChannelHealth.notConnected,
                    ),
                  ),
                  onTap: manage,
                ),
              ),
            ],
          ],
        ),
      ],
    );
  }
}

class _ChannelPill extends StatelessWidget {
  const _ChannelPill({required this.status, required this.onTap});

  final ChannelStatus status;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final (label, tone) = channelHealthLabel(context, status.health);
    final color = tone == Tone.neutral ? EcomsbdColors.muted : tone.ink;
    return Material(
      color: Colors.white,
      borderRadius: EcomsbdRadii.row,
      child: InkWell(
        onTap: onTap,
        borderRadius: EcomsbdRadii.row,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 11),
          decoration: BoxDecoration(
            borderRadius: EcomsbdRadii.row,
            border: Border.all(color: EcomsbdColors.line),
          ),
          child: Column(
            children: <Widget>[
              Icon(
                switch (status.channel) {
                  SalesChannel.facebook => Icons.facebook_rounded,
                  SalesChannel.whatsapp => Icons.chat_rounded,
                  SalesChannel.instagram => Icons.camera_alt_outlined,
                  SalesChannel.website => Icons.language_rounded,
                },
                size: 20,
                color: EcomsbdColors.ink,
              ),
              const SizedBox(height: 4),
              Text(
                channelName(context, status.channel),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: EcomsbdType.bodyStrong.copyWith(fontSize: 12.5),
              ),
              const SizedBox(height: 2),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: <Widget>[
                  Container(
                    width: 7,
                    height: 7,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: color,
                    ),
                  ),
                  const SizedBox(width: 4),
                  Flexible(
                    child: Text(
                      label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: EcomsbdType.chip.copyWith(
                        fontSize: 10.5,
                        color: color,
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// --------------------------------------------------------------------------- //
// Setup checklist
// --------------------------------------------------------------------------- //

class SetupChecklistSheet extends ConsumerWidget {
  const SetupChecklistSheet({super.key});

  static Future<void> show(BuildContext context) => GlassBottomSheet.show<void>(
    context: context,
    title: context.tr('setup.title'),
    description: context.tr('setup.description'),
    child: const SetupChecklistSheet(),
  );

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final progress = ref.watch(setupProgressProvider);
    return progress.when(
      loading: () => const Padding(
        padding: EdgeInsets.all(EcomsbdSpacing.lg),
        child: Center(child: CircularProgressIndicator()),
      ),
      error: (_, __) => Text(context.tr('setup.unavailable')),
      // ListTile needs a Material to paint its ink on; the sheet is glass.
      data: (value) => Material(
        type: MaterialType.transparency,
        child: Column(
          children: <Widget>[
            for (final step in value.steps)
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Icon(
                  step.done
                      ? Icons.check_circle_rounded
                      : Icons.radio_button_unchecked_rounded,
                  color: step.done ? EcomsbdColors.green : EcomsbdColors.muted2,
                ),
                title: Text(
                  context.tr('setup.${step.key}'),
                  style: EcomsbdType.bodyStrong,
                ),
                subtitle: Text(
                  context.tr('setup.${step.key}Sub'),
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
                trailing: step.done
                    ? null
                    : const Icon(Icons.chevron_right_rounded),
                onTap: step.done
                    ? null
                    : () {
                        final Widget page = switch (step.key) {
                          'courier' => const CourierAccountsScreen(),
                          'product' || 'cost' => const ProductsScreen(),
                          'order' => const OrderComposeScreen(),
                          _ => const ConnectionsScreen(),
                        };
                        Navigator.of(context).pop();
                        _push(context, page);
                      },
              ),
          ],
        ),
      ),
    );
  }
}

void _push(BuildContext context, Widget page) {
  unawaited(
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => page)),
  );
}
