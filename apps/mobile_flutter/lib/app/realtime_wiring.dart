import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:supabase_flutter/supabase_flutter.dart' as sb;

import '../core/env.dart';
import '../data/auth/auth_controller.dart';
import '../data/auth/auth_repository.dart';
import '../data/commerce/commerce_providers.dart';
import '../data/realtime/realtime_service.dart';
import 'network_recovery.dart';
import 'providers.dart';

/// When the socket should be open: in the foreground, signed in, in a shop.
///
/// Kept apart from the platform hooks so the rule itself can be tested.
class RealtimeGate {
  RealtimeGate(this.service);

  final RealtimeService service;
  bool foreground = true;
  bool signedIn = false;
  String? shopId;

  void apply() {
    final shop = shopId;
    if (foreground && signedIn && shop != null) {
      // A different shop than the socket's rebinds it; the same one keeps it.
      service.start(shop);
    } else {
      service.stop();
    }
  }
}

/// The access token for the socket, refreshed first if it is about to lapse.
Future<String?> realtimeToken(AuthRepository repository) async {
  var session = await repository.currentSession();
  final soon = DateTime.now().toUtc().add(const Duration(seconds: 30));
  if (session != null && session.accessTokenExpiresAt.isBefore(soon)) {
    try {
      session = await repository.refreshSession();
    } on Object {
      // Offline, or the session ended: the service retries, and an ended
      // session reaches the sign-in screen through the API's own 401.
      return null;
    }
  }
  final token = session?.accessToken;
  return token == null || token.isEmpty ? null : token;
}

/// Connect realtime to the app's lifecycle, session and shop.
///
/// Production only: tests drive [RealtimeService] and [RealtimeGate] directly.
/// An invalidation refreshes the same live reads a return to the foreground
/// does ([NetworkRecoveryObserver.refreshAll]), through their repositories.
RealtimeService startRealtime(
  ProviderContainer container,
  NetworkRecoveryObserver observer,
) {
  final repository = container.read(authRepositoryProvider);
  final service = RealtimeService(
    uri: realtimeUri(Env.apiBaseUrl, Env.apiVersionPrefix),
    token: () => realtimeToken(repository),
    onInvalidate: (_) => observer.refreshAll(container),
    onResync: () => observer.refreshAll(container),
    // Every refresh here is several requests on a metered connection.
    timings: const RealtimeTimings(minRefreshGap: Duration(seconds: 5)),
  );
  final gate = RealtimeGate(service);

  void update() {
    final auth = container.read(authControllerProvider);
    gate
      ..signedIn = auth.isSignedIn
      ..shopId = container.read(tenantIdProvider);
    gate.apply();
  }

  container.listen<AuthState>(authControllerProvider, (_, _) => update());
  container.listen<String?>(tenantIdProvider, (_, _) => update());
  container.listen<bool>(isOfflineProvider, (previous, offline) {
    if (previous == true && !offline) service.wake();
  });
  repository.authChanges.listen((event) {
    if (event.event == sb.AuthChangeEvent.tokenRefreshed) {
      unawaited(service.reauth());
    }
  }, onError: (Object _) {});
  // Registered with the binding, which holds it for the app's lifetime. No
  // socket is kept for the background: FCM covers that, and `onForeground`
  // refreshes on the way back while the socket reconnects.
  AppLifecycleListener(
    onHide: () {
      gate.foreground = false;
      gate.apply();
    },
    onShow: () {
      gate.foreground = true;
      update();
    },
  );
  update();
  return service;
}
