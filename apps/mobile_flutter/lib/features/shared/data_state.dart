import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/commerce/paged_list_controller.dart';
import '../../data/commerce/repository_support.dart';
import '../../data/local/tables.dart';
import '../../design/components/badges.dart';
import '../../design/components/states.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';
import '../billing/plans_screen.dart';

/// Says that what is on screen came off the device, and when it was true.
///
/// Master spec section 126: cached data is labelled with its timestamp. A
/// seller deciding whether to send a parcel needs to know they are looking at
/// this morning's stock, not this minute's.
class StaleDataNotice extends StatelessWidget {
  const StaleDataNotice({required this.fetchedAt, super.key, this.onRetry});

  final DateTime? fetchedAt;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    return GlassSurface(
      fill: Tone.info.surface,
      borderColor: Tone.info.ink.withValues(alpha: 0.18),
      borderRadius: EcomsbdRadii.cardMedium,
      shadows: const <BoxShadow>[],
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      child: Row(
        children: <Widget>[
          Icon(Icons.history_rounded, size: 18, color: Tone.info.ink),
          const SizedBox(width: EcomsbdSpacing.sm),
          Expanded(
            child: Text(
              context.tr('stale.notice', <String, Object?>{
                'when': formatRelative(fetchedAt),
              }),
              style: EcomsbdType.caption.copyWith(color: Tone.info.ink),
            ),
          ),
          if (onRetry != null)
            TextButton(
              onPressed: onRetry,
              style: TextButton.styleFrom(
                foregroundColor: Tone.info.ink,
                minimumSize: const Size(0, EcomsbdTouch.minTarget),
                textStyle: EcomsbdType.chip,
              ),
              child: Text(context.tr('common.retry')),
            ),
        ],
      ),
    );
  }
}

/// Whether one record has reached the server.
///
/// Renders nothing for a synced record — a badge on every row would be noise.
/// Master spec section 127: a seller must be able to see which records are not
/// yet on the server.
class SyncBadge extends StatelessWidget {
  const SyncBadge({required this.state, super.key});

  final LocalSyncState state;

  @override
  Widget build(BuildContext context) {
    return switch (state) {
      LocalSyncState.synced => const SizedBox.shrink(),
      LocalSyncState.localOnly => StatusChip(
        label: context.tr('sync.notSynced'),
        tone: Tone.warning,
        icon: Icons.cloud_upload_outlined,
      ),
      LocalSyncState.syncing => StatusChip(
        label: context.tr('sync.sending'),
        tone: Tone.info,
        icon: Icons.sync_rounded,
      ),
      LocalSyncState.conflict => StatusChip(
        label: context.tr('sync.needsChoice'),
        tone: Tone.bad,
        icon: Icons.call_split_rounded,
      ),
      LocalSyncState.failedValidation => StatusChip(
        label: context.tr('sync.needsFixing'),
        tone: Tone.bad,
        icon: Icons.error_outline,
      ),
    };
  }
}

/// A failure with what the seller can do about it.
///
/// Uses the server's own Bangla message rather than inventing copy: the wording
/// of a money error is a product decision that belongs on the server (master
/// spec section 46).
class ErrorStateCard extends StatelessWidget {
  const ErrorStateCard({required this.error, super.key, this.onRetry});

  final ApiError error;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    if (error.isOffline) {
      return EmptyState(
        icon: Icons.cloud_off_rounded,
        title: context.tr('common.noConnectionTitle'),
        message: context.tr('error.offlineListBody'),
        actionLabel: onRetry == null ? null : context.tr('common.tryAgain'),
        onAction: onRetry,
      );
    }
    return EmptyState(
      icon: Icons.error_outline,
      title: context.tr('common.couldNotLoad'),
      message: error.displayMessage,
      // A retry is offered only when the server said one is safe. For anything
      // that could duplicate money or external work it says so, and the UI
      // respects that rather than deciding for itself (section 62).
      actionLabel: error.retryable && onRetry != null
          ? context.tr('common.tryAgain')
          : null,
      onAction: error.retryable ? onRetry : null,
    );
  }
}

/// The list body shared by Products, Customers and Orders.
///
/// Owns the four states a paginated list actually has — loading, error, empty,
/// content — plus the "load more" trigger, so each screen supplies only its row
/// widget and its empty copy.
class PagedListBody<T> extends StatelessWidget {
  const PagedListBody({
    required this.state,
    required this.itemBuilder,
    required this.emptyIcon,
    required this.emptyTitle,
    required this.emptyMessage,
    required this.onRetry,
    required this.onLoadMore,
    super.key,
    this.emptyActionLabel,
    this.onEmptyAction,
  });

  final PagedListState<T> state;
  final Widget Function(BuildContext, T) itemBuilder;
  final IconData emptyIcon;
  final String emptyTitle;
  final String emptyMessage;
  final String? emptyActionLabel;
  final VoidCallback? onEmptyAction;
  final VoidCallback onRetry;
  final VoidCallback onLoadMore;

  @override
  Widget build(BuildContext context) {
    if (state.isLoading && state.items.isEmpty) {
      return Column(
        children: <Widget>[
          for (var i = 0; i < 4; i++)
            Padding(
              padding: const EdgeInsets.only(bottom: EcomsbdSpacing.sm),
              child: SkeletonLoader.card(height: 96),
            ),
        ],
      );
    }

    if (state.items.isEmpty && state.error != null) {
      return ErrorStateCard(error: state.error!, onRetry: onRetry);
    }

    if (state.isEmpty) {
      return EmptyState(
        icon: emptyIcon,
        title: emptyTitle,
        message: emptyMessage,
        actionLabel: emptyActionLabel,
        onAction: onEmptyAction,
      );
    }

    return Column(
      children: <Widget>[
        for (final item in state.items)
          Padding(
            padding: const EdgeInsets.only(bottom: EcomsbdSpacing.sm),
            child: itemBuilder(context, item),
          ),
        if (state.hasMore)
          Padding(
            padding: const EdgeInsets.only(top: EcomsbdSpacing.xs),
            child: state.isLoadingMore
                ? const Padding(
                    padding: EdgeInsets.all(EcomsbdSpacing.md),
                    child: SizedBox(
                      height: 22,
                      width: 22,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                  )
                : OutlinedButton(
                    onPressed: onLoadMore,
                    style: OutlinedButton.styleFrom(
                      minimumSize: const Size.fromHeight(
                        EcomsbdTouch.minTarget,
                      ),
                      shape: const StadiumBorder(),
                      foregroundColor: EcomsbdColors.ink,
                      textStyle: EcomsbdType.label,
                    ),
                    child: Text(context.tr('common.loadMore')),
                  ),
          ),
      ],
    );
  }
}

/// `2 minutes ago`, `3 hours ago`, `10 Sep`.
///
/// Reads the selected language from [activeAppLocale] rather than taking a
/// `BuildContext`: this is called from row builders and string interpolations
/// across the app, and threading a context through all of them buys nothing.
/// Changing language rebuilds the tree, so the next paint is already correct.
String formatRelative(DateTime? timestamp) {
  final strings = AppStrings(activeAppLocale);
  if (timestamp == null) {
    return strings.t('common.unknownTime');
  }
  final delta = DateTime.now().toUtc().difference(timestamp.toUtc());
  if (delta.inMinutes < 1) {
    return strings.t('common.justNow');
  }
  if (delta.inMinutes < 60) {
    return strings.plural('common.minutesAgo', delta.inMinutes);
  }
  if (delta.inHours < 24) {
    return strings.plural('common.hoursAgo', delta.inHours);
  }
  if (delta.inDays < 7) {
    return strings.plural('common.daysAgo', delta.inDays);
  }
  // Dhaka time, so a seller reading "10 Sep" sees their own day.
  final dhaka = timestamp.toUtc().add(const Duration(hours: 6));
  return '${dhaka.day} ${strings.t('month.${dhaka.month}')}';
}

/// The standard detail-screen chrome: a back pill and a title over the app's
/// gradient, with content beneath.
class DetailScaffold extends StatelessWidget {
  const DetailScaffold({
    required this.title,
    required this.children,
    super.key,
    this.subtitle,
    this.actions = const <Widget>[],
    this.bottomBar,
  });

  final String title;
  final String? subtitle;
  final List<Widget> children;
  final List<Widget> actions;
  final Widget? bottomBar;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: EcomsbdColors.background,
      body: EcomsbdBackground(
        child: SafeArea(
          child: Column(
            children: <Widget>[
              Padding(
                padding: const EdgeInsets.fromLTRB(
                  EcomsbdSpacing.md,
                  EcomsbdLayout.pushedTopPadding,
                  EcomsbdSpacing.md,
                  EcomsbdSpacing.xs,
                ),
                child: Row(
                  children: <Widget>[
                    IconButton(
                      onPressed: () => Navigator.of(context).maybePop(),
                      icon: const Icon(Icons.arrow_back_rounded),
                      tooltip: context.tr('common.back'),
                      constraints: const BoxConstraints(
                        minWidth: EcomsbdTouch.minTarget,
                        minHeight: EcomsbdTouch.minTarget,
                      ),
                    ),
                    const SizedBox(width: EcomsbdSpacing.xs),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisSize: MainAxisSize.min,
                        children: <Widget>[
                          Text(
                            title,
                            style: EcomsbdType.sectionTitle,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                          if (subtitle != null)
                            Text(
                              subtitle!,
                              style: EcomsbdType.caption.copyWith(
                                color: EcomsbdColors.muted,
                              ),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                        ],
                      ),
                    ),
                    ...actions,
                  ],
                ),
              ),
              Expanded(
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(
                    EcomsbdSpacing.page,
                    EcomsbdSpacing.xs,
                    EcomsbdSpacing.page,
                    EcomsbdSpacing.xxl,
                  ),
                  children: children,
                ),
              ),
              if (bottomBar != null) bottomBar!,
            ],
          ),
        ),
      ),
    );
  }
}

/// A chart body that never renders a fixture.
///
/// Owns the four states a chart actually has: loading, failed, genuinely
/// empty, and populated. The empty case is a sentence explaining *why* there
/// is nothing rather than a blank frame — a seller with no returns should read
/// "no returns in the last 30 days", not wonder whether the chart is broken.
///
/// [isEmpty] is asked of the loaded value because a successful response full
/// of zeroes is still nothing to draw, and a zeroed chart looks like a chart.
class ChartData<T> extends StatelessWidget {
  const ChartData({
    required this.value,
    required this.builder,
    required this.emptyMessage,
    super.key,
    this.isEmpty,
    this.height = 150,
  });

  final AsyncValue<Sourced<T>> value;
  final Widget Function(T value) builder;
  final String emptyMessage;
  final bool Function(T value)? isEmpty;

  /// Reserved height for the loading and empty states, so the card does not
  /// resize under the seller's thumb when the data arrives.
  final double height;

  @override
  Widget build(BuildContext context) {
    return value.when(
      loading: () => SizedBox(
        height: height,
        child: const Center(child: CircularProgressIndicator()),
      ),
      error: (error, _) => SizedBox(
        height: height,
        child: error is ApiError && error.isPlanLimited
            ? const PlanLockedNotice()
            : Center(
                child: Text(
                  error is ApiError && error.isOffline
                      ? context.tr('common.notSavedOnDevice')
                      : context.tr('common.couldNotLoadThis'),
                  textAlign: TextAlign.center,
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ),
      ),
      data: (sourced) {
        if (isEmpty?.call(sourced.value) ?? false) {
          return SizedBox(
            height: height,
            child: Center(
              child: Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: EcomsbdSpacing.md,
                ),
                child: Text(
                  emptyMessage,
                  textAlign: TextAlign.center,
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ),
            ),
          );
        }
        return builder(sourced.value);
      },
    );
  }
}

/// Shown where the server refused a read because the shop's plan does not
/// include it (`ENTITLEMENT_REQUIRED`).
///
/// This is not a failure and must not read like one: "Could not load this"
/// sends a seller on the free plan looking for a connection problem that does
/// not exist. It says what is going on and where to change it.
class PlanLockedNotice extends StatelessWidget {
  const PlanLockedNotice({super.key});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: EcomsbdSpacing.md),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Row(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                const Icon(
                  Icons.lock_outline_rounded,
                  size: 18,
                  color: EcomsbdColors.muted,
                ),
                const SizedBox(width: EcomsbdSpacing.xs),
                Flexible(
                  child: Text(
                    context.tr('plan.lockedTitle'),
                    style: EcomsbdType.bodyStrong,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 2),
            Text(
              context.tr('plan.lockedBody'),
              textAlign: TextAlign.center,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
            TextButton(
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute<void>(builder: (_) => const PlansScreen()),
              ),
              child: Text(context.tr('plan.seePlans')),
            ),
          ],
        ),
      ),
    );
  }
}
