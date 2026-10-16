import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/api/api_error.dart';
import '../core/network/network_monitor.dart';
import '../data/analytics/analytics_providers.dart';
import '../data/auth/auth_controller.dart';
import '../data/commerce/commerce_providers.dart';
import '../data/commerce/crm_repository.dart';
import '../data/commerce/list_controllers.dart';
import '../data/commerce/paged_list_controller.dart';
import '../data/commerce/repository_support.dart';
import '../data/couriers/courier_providers.dart';
import '../data/money/money_providers.dart';
import '../data/team/team_providers.dart';
import '../features/home/widgets/seller_today.dart';
import 'providers.dart';

/// Reads of shop data that return a plain value rather than a [Sourced] one or
/// a list, and are refreshed with them when the app returns to the foreground.
///
/// Every one is a GET with no side effect. Courier quotes, risk checks and
/// phone reveals are deliberately absent: each is metered or audited, and
/// repeating one because the seller switched apps would spend their quota.
final Set<Object> shopDataReads = <Object>{
  orderProvider,
  productProvider,
  customerProvider,
  customerOrdersProvider,
  crmCustomerProvider,
  payoutProvider,
  courierAccountsProvider,
  teamMembersProvider,
  unreadNotificationCountProvider,
  sellerQueueProvider,
};

/// Re-runs what failed for lack of a connection once the connection is back.
///
/// Watches every provider instead of asking each screen to: one whose latest
/// value is an offline failure, or cached data served because the server was
/// unreachable, is remembered, and when the app is back online each one still
/// alive is refreshed once. Nothing polls, and nothing is asked twice. A
/// provider no widget is watching is only marked stale by the invalidation;
/// lists reload in place, since their filters live in their controllers.
class NetworkRecoveryObserver extends ProviderObserver {
  NetworkRecoveryObserver({Set<Object>? shopReads})
    : _shopReads = shopReads ?? shopDataReads;

  final Set<Object> _shopReads;
  final Set<ProviderBase<Object?>> _waiting = <ProviderBase<Object?>>{};

  /// Every read of shop data still alive, fetched again by [refreshAll].
  final Set<ProviderBase<Object?>> _live = <ProviderBase<Object?>>{};

  /// How many providers are waiting for the connection.
  int get waitingCount => _waiting.length;

  /// How many reads of shop data a return to the foreground would refresh.
  int get liveCount => _live.length;

  @override
  void didAddProvider(
    ProviderBase<Object?> provider,
    Object? value,
    ProviderContainer container,
  ) => _track(provider, value);

  @override
  void didUpdateProvider(
    ProviderBase<Object?> provider,
    Object? previousValue,
    Object? newValue,
    ProviderContainer container,
  ) => _track(provider, newValue);

  @override
  void didDisposeProvider(
    ProviderBase<Object?> provider,
    ProviderContainer container,
  ) {
    _waiting.remove(provider);
    _live.remove(provider);
  }

  void _track(ProviderBase<Object?> provider, Object? value) {
    if (_holdsShopData(provider, value)) {
      _live.add(provider);
    }
    if (_waitsForNetwork(value)) {
      _waiting.add(provider);
    } else {
      _waiting.remove(provider);
    }
  }

  static bool _waitsForNetwork(Object? value) {
    if (value is AsyncValue<Object?>) {
      if (value.isLoading) return false;
      if (value.hasError) {
        final error = value.error;
        return error is ApiError && error.isOffline;
      }
      final data = value.valueOrNull;
      return data is Sourced && data.isStale;
    }
    if (value is PagedListState) {
      return value.error?.isOffline ?? false;
    }
    return false;
  }

  bool _holdsShopData(ProviderBase<Object?> provider, Object? value) {
    if (value is PagedListState) return true;
    if (_shopReads.contains(provider.from ?? provider)) return true;
    return value is AsyncValue<Object?> && value.valueOrNull is Sourced;
  }

  /// Refresh everything that was waiting. Called once per return online.
  void retry(ProviderContainer container) {
    final waiting = _waiting.toList();
    _waiting.clear();
    for (final provider in waiting) {
      _refresh(provider, container);
    }
  }

  /// Refresh every live read of shop data, and whatever was waiting for the
  /// network. Called when the app returns to the foreground, since the web or
  /// another phone may have changed the shop meanwhile.
  ///
  /// A read nothing is watching is only marked stale and costs no request
  /// until a screen shows it again.
  void refreshAll(ProviderContainer container) {
    final providers = <ProviderBase<Object?>>{..._waiting, ..._live};
    _waiting.clear();
    for (final provider in providers) {
      _refresh(provider, container);
    }
  }

  void _refresh(ProviderBase<Object?> provider, ProviderContainer container) {
    // A list keeps its filters in its controller, so it is asked to reload
    // rather than rebuilt from scratch.
    final list = _pagedList(provider, container);
    if (list != null) {
      unawaited(list.refresh());
    } else {
      container.invalidate(provider);
    }
  }

  static PagedListController<Object?>? _pagedList(
    ProviderBase<Object?> provider,
    ProviderContainer container,
  ) {
    final Object? notifier = switch (provider) {
      final StateNotifierProvider<StateNotifier<Object?>, Object?> p =>
        container.read(p.notifier),
      final AutoDisposeStateNotifierProvider<StateNotifier<Object?>, Object?>
      p =>
        container.read(p.notifier),
      _ => null,
    };
    return notifier is PagedListController<Object?> ? notifier : null;
  }
}

/// Connect the network monitor to the phone and to the recovery work.
///
/// Production only: tests drive the monitor directly.
void startNetworkRecovery(
  ProviderContainer container,
  NetworkRecoveryObserver observer,
) {
  container
      .read(networkMonitorProvider.notifier)
      .start(probe: probeApi, hasNetwork: platformHasNetwork());
  container.listen<bool>(isOfflineProvider, (previous, offline) {
    if (previous == true && !offline) {
      unawaited(onReconnected(container, observer));
    }
  });
  var hidden = false;
  // Registered with the binding, which holds it for the app's lifetime.
  AppLifecycleListener(
    onHide: () => hidden = true,
    // `onShow` follows `onHide` only: a notification shade or a system dialog
    // makes the app inactive without hiding it, and is not a return.
    onShow: () {
      if (!hidden) return;
      hidden = false;
      unawaited(onForeground(container, observer));
    },
  );
}

/// When the last return to the foreground refreshed the shop.
DateTime? _lastForegroundRefresh;

/// What happens when the seller comes back to the app.
///
/// The web, or a teammate's phone, may have changed the shop meanwhile, and the
/// server holds the one copy of it. Every read of shop data is fetched again
/// and work queued here goes up. Nothing polls while the app stays open: the
/// seller pulls to refresh for that, on a connection that is metered.
Future<void> onForeground(
  ProviderContainer container,
  NetworkRecoveryObserver observer, {
  DateTime? now,
}) async {
  // Signed in, in a shop: there is no shop without a profile.
  if (container.read(tenantIdProvider) == null) return;
  // Offline, the reconnect refreshes everything instead.
  if (container.read(isOfflineProvider)) return;
  final at = now ?? DateTime.now();
  final last = _lastForegroundRefresh;
  // A share sheet or a camera hides the app for a moment; one refresh covers
  // the round trip.
  if (last != null && at.difference(last) < minForegroundRefreshGap) return;
  _lastForegroundRefresh = at;

  observer.refreshAll(container);
  await _sendQueued(container);
}

/// The shortest gap between two foreground refreshes.
const Duration minForegroundRefreshGap = Duration(seconds: 10);

/// Forget the last foreground refresh. Tests only.
@visibleForTesting
void resetForegroundRefresh() => _lastForegroundRefresh = null;

/// What happens when the app is back online.
Future<void> onReconnected(
  ProviderContainer container,
  NetworkRecoveryObserver observer,
) async {
  final auth = container.read(authControllerProvider);
  if (auth.stage == AuthStage.restoring) {
    // A launch that could not confirm the session is waiting on the splash:
    // ask now rather than at its next backoff tick. Screens load after it.
    await container.read(authControllerProvider.notifier).restore();
    return;
  }
  observer.retry(container);
  if (!auth.isSignedIn || auth.tenantId == null) return;
  // Work queued offline goes up now, not at the next pull to refresh.
  await _sendQueued(container);
}

Future<void> _sendQueued(ProviderContainer container) async {
  try {
    final pending = await container.read(pendingMutationCountProvider.future);
    if (pending > 0) {
      await container.read(syncControllerProvider.notifier).sync();
    }
  } on Object {
    // The outbox keeps it; the next sync sends it.
  }
}
