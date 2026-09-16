import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/analytics/analytics_providers.dart';
import '../../data/analytics/models.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
import '../shared/responsive.dart';

/// The in-app notification centre.
///
/// Master spec section 94: *"push is not enough."* A seller who has
/// notifications switched off, or who missed one on the bus, still has to be
/// able to find out that delivered parcels were never paid for — so everything
/// the server raised lives here.
///
/// Ordered by arrival rather than by amount on purpose. The seller opens this
/// to see what changed since they last looked; the Money screen is where they
/// work through a backlog by size.
class NotificationCentreScreen extends ConsumerWidget {
  const NotificationCentreScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(notificationListProvider);
    final controller = ref.read(notificationListProvider.notifier);
    final unread = state.items.where((item) => !item.isRead).length;

    return Scaffold(
      backgroundColor: EcomsbdColors.background,
      body: EcomsbdBackground(
        child: SafeArea(
          child: ContentWidthLimit(
            child: RefreshIndicator(
              edgeOffset: EcomsbdLayout.pushedRefreshOffset,
              onRefresh: controller.refresh,
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
                          eyebrow: context.tr('notif.eyebrow'),
                          title: context.tr('notif.title'),
                          description: context.tr('notif.description'),
                        ),
                      ),
                      if (unread > 0)
                        TextButton(
                          onPressed: () async {
                            await controller.markAllRead();
                            ref.invalidate(unreadNotificationCountProvider);
                          },
                          child: Text(context.tr('common.markRead')),
                        ),
                    ],
                  ),
                  if (state.isStale) ...<Widget>[
                    StaleDataNotice(
                      fetchedAt: state.fetchedAt,
                      onRetry: controller.refresh,
                    ),
                    const SizedBox(height: EcomsbdSpacing.sm),
                  ],
                  Wrap(
                    spacing: EcomsbdSpacing.xs,
                    runSpacing: EcomsbdSpacing.xs,
                    children: <Widget>[
                      FilterToggle(
                        label: context.tr('notif.everything'),
                        selected: !controller.unreadOnly,
                        onChanged: (_) => controller.setUnreadOnly(false),
                      ),
                      FilterToggle(
                        label: context.tr('notif.unread'),
                        selected: controller.unreadOnly,
                        onChanged: (selected) =>
                            controller.setUnreadOnly(selected),
                      ),
                    ],
                  ),
                  const SizedBox(height: EcomsbdSpacing.sm),
                  PagedListBody<AppNotification>(
                    state: state,
                    onRetry: controller.refresh,
                    onLoadMore: controller.loadMore,
                    emptyIcon: Icons.notifications_none_rounded,
                    emptyTitle: controller.unreadOnly
                        ? context.tr('notif.nothingUnread')
                        : context.tr('activity.nothingTitle'),
                    emptyMessage: context.tr('activity.nothingBody'),
                    itemBuilder: (context, notification) => Padding(
                      padding: const EdgeInsets.only(bottom: EcomsbdSpacing.sm),
                      child: NotificationRow(
                        notification: notification,
                        onTap: () => _open(context, ref, notification),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Future<void> _open(
    BuildContext context,
    WidgetRef ref,
    AppNotification notification,
  ) async {
    if (!notification.isRead) {
      try {
        await ref
            .read(notificationListProvider.notifier)
            .markRead(notification.id);
        ref.invalidate(unreadNotificationCountProvider);
      } on ApiError catch (error) {
        if (context.mounted) {
          ScaffoldMessenger.of(
            context,
          ).showSnackBar(SnackBar(content: Text(error.displayMessage)));
        }
      }
    }
    if (notification.kind == 'WEEKLY_SUMMARY' && context.mounted) {
      await showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        backgroundColor: Colors.transparent,
        builder: (_) => WeeklySummarySheet(payload: notification.payload),
      );
    }
  }
}

class NotificationRow extends StatelessWidget {
  const NotificationRow({required this.notification, super.key, this.onTap});

  final AppNotification notification;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final tone = severityTone(notification.severity);

    return GlassCard(
      onTap: onTap,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              // A dot rather than a bold typeface: bold on a glass surface at
              // 360dp is hard to tell from regular weight.
              if (!notification.isRead)
                Container(
                  width: 8,
                  height: 8,
                  margin: const EdgeInsets.only(
                    top: 6,
                    right: EcomsbdSpacing.xs,
                  ),
                  decoration: BoxDecoration(
                    color: tone.ink,
                    shape: BoxShape.circle,
                  ),
                ),
              Expanded(
                child: Text(notification.title, style: EcomsbdType.bodyStrong),
              ),
              const SizedBox(width: EcomsbdSpacing.xs),
              Flexible(
                child: StatusChip(
                  label: severityLabel(context, notification.severity),
                  tone: tone,
                ),
              ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          Text(
            notification.body,
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  formatRelative(notification.createdAt),
                  style: EcomsbdType.chip.copyWith(color: EcomsbdColors.muted),
                ),
              ),
              if (notification.amount.paisa != 0)
                Text(notification.amount.format(), style: EcomsbdType.money),
            ],
          ),
        ],
      ),
    );
  }
}

Tone severityTone(NotificationSeverity severity) => switch (severity) {
  NotificationSeverity.critical => Tone.bad,
  NotificationSeverity.warning => Tone.warning,
  NotificationSeverity.action => Tone.info,
  NotificationSeverity.info => Tone.neutral,
};

String severityLabel(BuildContext context, NotificationSeverity severity) =>
    switch (severity) {
      NotificationSeverity.critical => context.tr('severity.critical'),
      NotificationSeverity.warning => context.tr('severity.warning'),
      NotificationSeverity.action => context.tr('severity.action'),
      NotificationSeverity.info => context.tr('severity.info'),
    };

/// The Friday summary, opened from its notification.
///
/// Reads the figures out of the notification's own payload rather than
/// recomputing them. Master spec section 23's summary is a statement about a
/// week that has ended; recalculating it from today's data would silently
/// rewrite what the seller was told on Friday evening.
class WeeklySummarySheet extends StatelessWidget {
  const WeeklySummarySheet({required this.payload, super.key});

  final Map<String, dynamic> payload;

  @override
  Widget build(BuildContext context) {
    final rows = <(String, String)>[
      (context.tr('week.orders'), '${payload['order_count'] ?? 0}'),
      (context.tr('week.delivered'), '${payload['delivered_count'] ?? 0}'),
      (context.tr('week.returned'), '${payload['return_count'] ?? 0}'),
      (context.tr('week.sales'), _money(payload['sales_paisa'])),
      (
        context.tr('week.contributionProfit'),
        _money(payload['contribution_profit_paisa']),
      ),
      (context.tr('week.returnLoss'), _money(payload['return_loss_paisa'])),
      (context.tr('week.adSpend'), _money(payload['ad_spend_paisa'])),
      (
        context.tr('week.codOutstanding'),
        _money(payload['cod_outstanding_paisa']),
      ),
      (context.tr('week.overdue'), _money(payload['overdue_paisa'])),
      (context.tr('week.openMismatches'), '${payload['mismatch_count'] ?? 0}'),
    ];

    return Container(
      decoration: const BoxDecoration(
        color: EcomsbdColors.backgroundLight,
        borderRadius: BorderRadius.vertical(
          top: Radius.circular(EcomsbdRadii.lg),
        ),
      ),
      padding: const EdgeInsets.fromLTRB(
        EcomsbdSpacing.lg,
        EcomsbdSpacing.md,
        EcomsbdSpacing.lg,
        EcomsbdSpacing.xl,
      ),
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Text(context.tr('notif.yourWeek'), style: EcomsbdType.sectionTitle),
            const SizedBox(height: 3),
            Text(
              '${payload['week_start'] ?? ''} to ${payload['week_end'] ?? ''}',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
            const SizedBox(height: EcomsbdSpacing.md),
            for (final (label, value) in rows)
              Padding(
                padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
                child: Row(
                  children: <Widget>[
                    Expanded(
                      child: Text(
                        label,
                        style: EcomsbdType.caption.copyWith(
                          color: EcomsbdColors.muted,
                        ),
                      ),
                    ),
                    Text(value, style: EcomsbdType.money),
                  ],
                ),
              ),
            const SizedBox(height: EcomsbdSpacing.sm),
            _Ranking(
              title: context.tr('week.bestProduct'),
              entry: payload['best_product'],
              note: payload['ranking_note'] as String?,
            ),
            _Ranking(
              title: context.tr('week.worstProduct'),
              entry: payload['worst_product'],
              note: payload['ranking_note'] as String?,
            ),
            _Ranking(
              title: context.tr('week.bestCourier'),
              entry: payload['best_courier'],
              note: payload['courier_note'] as String?,
              isRate: true,
            ),
            _Ranking(
              title: context.tr('week.worstCourier'),
              entry: payload['worst_courier'],
              note: payload['courier_note'] as String?,
              isRate: true,
            ),
          ],
        ),
      ),
    );
  }

  static String _money(Object? paisa) {
    if (paisa is! int) return '—';
    return Money(paisa).format();
  }
}

/// A ranking, or the reason there isn't one.
///
/// Master spec section 24: *"do not show unreliable rankings before enough
/// sample exists"* and *"sample-size rule must be visible."* The note replaces
/// the figure, not the row, so the seller learns why the app is staying quiet
/// rather than assuming it had nothing to say.
class _Ranking extends StatelessWidget {
  const _Ranking({
    required this.title,
    required this.entry,
    this.note,
    this.isRate = false,
  });

  final String title;
  final Object? entry;
  final String? note;
  final bool isRate;

  @override
  Widget build(BuildContext context) {
    final parsed = RankedEntry.tryParse(entry);

    return Padding(
      padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
      child: GlassListRow(
        title: title,
        subtitle:
            parsed?.label ?? (note ?? context.tr('common.notEnoughDataYet')),
        trailing: parsed == null
            ? StatusChip(
                label: context.tr('common.noRankYet'),
                tone: Tone.neutral,
              )
            : Text(
                isRate
                    ? '${(parsed.value / 100).toStringAsFixed(1)}%'
                    : Money(parsed.value).formatCompact(),
                style: EcomsbdType.money,
              ),
      ),
    );
  }
}
