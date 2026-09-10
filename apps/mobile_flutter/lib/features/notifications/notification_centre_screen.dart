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
              onRefresh: controller.refresh,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(
                  EcomsbdSpacing.page,
                  0,
                  EcomsbdSpacing.page,
                  EcomsbdSpacing.xxl,
                ),
                children: <Widget>[
                  Row(
                    children: <Widget>[
                      IconButton(
                        onPressed: () => Navigator.of(context).maybePop(),
                        icon: const Icon(Icons.arrow_back_rounded),
                        tooltip: 'Back',
                      ),
                      const Expanded(
                        child: PageHeader(
                          eyebrow: 'Everything the app has told you',
                          title: 'Notifications',
                          description:
                              'Kept here whether or not a push arrived, so '
                              'nothing about your money depends on catching '
                              'one.',
                        ),
                      ),
                      if (unread > 0)
                        TextButton(
                          onPressed: () async {
                            await controller.markAllRead();
                            ref.invalidate(unreadNotificationCountProvider);
                          },
                          child: const Text('Mark read'),
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
                        label: 'Everything',
                        selected: !controller.unreadOnly,
                        onChanged: (_) => controller.setUnreadOnly(false),
                      ),
                      FilterToggle(
                        label: 'Unread',
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
                        ? 'Nothing unread'
                        : 'Nothing needs you right now',
                    emptyMessage:
                        'Alerts appear here when money is at risk — delivered '
                        'parcels that were never paid for, payments that came '
                        'up short, parcels stuck with a courier.',
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
                  label: severityLabel(notification.severity),
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

String severityLabel(NotificationSeverity severity) => switch (severity) {
  NotificationSeverity.critical => 'Losing money',
  NotificationSeverity.warning => 'At risk',
  NotificationSeverity.action => 'Needs you',
  NotificationSeverity.info => 'For info',
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
      ('Orders', '${payload['order_count'] ?? 0}'),
      ('Delivered', '${payload['delivered_count'] ?? 0}'),
      ('Returned', '${payload['return_count'] ?? 0}'),
      ('Sales', _money(payload['sales_paisa'])),
      ('Contribution profit', _money(payload['contribution_profit_paisa'])),
      ('Return loss', _money(payload['return_loss_paisa'])),
      ('Ad spend', _money(payload['ad_spend_paisa'])),
      ('COD outstanding', _money(payload['cod_outstanding_paisa'])),
      ('Overdue', _money(payload['overdue_paisa'])),
      ('Open mismatches', '${payload['mismatch_count'] ?? 0}'),
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
            const Text('Your week', style: EcomsbdType.sectionTitle),
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
              title: 'Best product',
              entry: payload['best_product'],
              note: payload['ranking_note'] as String?,
            ),
            _Ranking(
              title: 'Worst product',
              entry: payload['worst_product'],
              note: payload['ranking_note'] as String?,
            ),
            _Ranking(
              title: 'Best courier',
              entry: payload['best_courier'],
              note: payload['courier_note'] as String?,
              isRate: true,
            ),
            _Ranking(
              title: 'Worst courier',
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
        subtitle: parsed?.label ?? (note ?? 'Not enough data yet'),
        trailing: parsed == null
            ? const StatusChip(label: 'No rank yet', tone: Tone.neutral)
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
