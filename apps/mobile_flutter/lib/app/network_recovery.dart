import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/api/api_error.dart';
import '../core/network/network_monitor.dart';
import '../data/auth/auth_controller.dart';
import '../data/commerce/commerce_providers.dart';
import '../data/commerce/paged_list_controller.dart';
import '../data/commerce/repository_support.dart';
import 'providers.dart';

/// Re-runs what failed for lack of a connection once the connection is back.
///
/// Watches every provider instead of asking each screen to: one whose latest
/// value is an offline failure, or cached data served because the server was
/// unreachable, is remembered, and when the app is back online each one still
/// alive is refreshed once. Nothing polls, and nothing is asked twice. A
/// provider no widget is watching is only marked stale by the invalidation;
/// lists reload in place, since their filters live in their controllers.
class NetworkRecoveryObserver extends ProviderObserver {
  final Set<ProviderBase<Object?>> _waiting = <ProviderBase<Object?>>{};

  /// How many providers are waiting for the connection.
  int get waitingCount => _waiting.length;

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
  ) => _waiting.remove(provider);

  void _track(ProviderBase<Object?> provider, Object? value) {
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

  /// Refresh everything that was waiting. Called once per return online.
  void retry(ProviderContainer container) {
    final waiting = _waiting.toList();
    _waiting.clear();
    for (final provider in waiting) {
      // A list keeps its filters in its controller, so it is asked to reload
      // rather than rebuilt from scratch.
      final list = _pagedList(provider, container);
      if (list != null) {
        unawaited(list.refresh());
      } else {
        container.invalidate(provider);
      }
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
}

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
  try {
    final pending = await container.read(pendingMutationCountProvider.future);
    if (pending > 0) {
      await container.read(syncControllerProvider.notifier).sync();
    }
  } on Object {
    // The outbox keeps it; the next sync sends it.
  }
}
