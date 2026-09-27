import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:dio/dio.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../env.dart';

/// Told by the HTTP client what happened to each request.
abstract class ReachabilityReporter {
  /// The server answered, whatever the status code.
  void reportReachable();

  /// The request never reached the server.
  void reportUnreachable();
}

/// Whether the API can be reached right now. The state is `true` while
/// offline.
///
/// One answer for the whole app, so every screen agrees on it and the offline
/// banner is drawn once rather than per screen. It is decided by what actually
/// happens to requests: a phone can be on Wi-Fi with no route to the server,
/// and a 500 is a server fault, not a lost connection.
///
/// Until [start] is called the monitor only records what the client reports;
/// it probes nothing and schedules nothing. Tests rely on that.
class NetworkMonitor extends StateNotifier<bool>
    implements ReachabilityReporter {
  NetworkMonitor() : super(false);

  Future<bool> Function()? _probe;
  Duration Function(int attempt) _retryDelay = _backoff;
  StreamSubscription<bool>? _networkSub;
  AppLifecycleListener? _lifecycle;
  Timer? _retry;
  int _attempt = 0;
  bool _probing = false;
  bool _foreground = true;

  /// Whether the phone has any network interface at all, as the OS reports.
  /// Null until the OS has said.
  bool? _hasNetwork;

  bool get isOffline => state;

  /// 3, 6, 12, 24, then every 30 seconds while offline.
  static Duration _backoff(int attempt) =>
      Duration(seconds: attempt >= 4 ? 30 : 3 << attempt);

  /// Watch the phone's network, and probe the server while offline so the app
  /// recovers without a restart.
  ///
  /// [hasNetwork] is the OS's view: losing every interface is offline at once,
  /// and an interface coming back triggers a probe. It is never trusted as
  /// "online" on its own.
  void start({
    required Future<bool> Function() probe,
    Stream<bool>? hasNetwork,
    Duration Function(int attempt)? retryDelay,
    bool watchLifecycle = true,
  }) {
    _probe = probe;
    if (retryDelay != null) _retryDelay = retryDelay;
    _networkSub = hasNetwork?.listen(_onNetworkChanged);
    if (watchLifecycle) {
      _lifecycle = AppLifecycleListener(
        onResume: () => setForeground(true),
        onHide: () => setForeground(false),
      );
    }
    if (state) _schedule();
  }

  @override
  void reportReachable() {
    _attempt = 0;
    _retry?.cancel();
    if (state) state = false;
  }

  @override
  void reportUnreachable() {
    if (!state) state = true;
    _schedule();
  }

  /// Ask the server now. Used by the banner's Retry button and when the phone
  /// regains a network. Returns whether it answered.
  Future<bool> checkNow() async {
    final probe = _probe;
    if (probe == null || _probing) return !state;
    _probing = true;
    _retry?.cancel();
    bool reachable;
    try {
      reachable = await probe();
    } on Object {
      reachable = false;
    } finally {
      // Cleared before reporting, which schedules the next probe.
      _probing = false;
    }
    if (!mounted) return reachable;
    if (reachable) {
      reportReachable();
    } else {
      _attempt++;
      reportUnreachable();
    }
    return reachable;
  }

  /// Stop probing in the background; probe once on return.
  void setForeground(bool foreground) {
    if (_foreground == foreground) return;
    _foreground = foreground;
    if (!foreground) {
      _retry?.cancel();
    } else if (state) {
      unawaited(checkNow());
    }
  }

  void _onNetworkChanged(bool hasNetwork) {
    _hasNetwork = hasNetwork;
    if (!hasNetwork) {
      _retry?.cancel();
      if (!state) state = true;
      return;
    }
    // A network appeared or changed (Wi-Fi to data, say): ask straight away
    // rather than waiting out the backoff.
    if (state) {
      _attempt = 0;
      unawaited(checkNow());
    }
  }

  void _schedule() {
    if (_probe == null || !_foreground || _hasNetwork == false) return;
    if (_probing || (_retry?.isActive ?? false)) return;
    _retry = Timer(_retryDelay(_attempt), () => unawaited(checkNow()));
  }

  @override
  void dispose() {
    _retry?.cancel();
    unawaited(_networkSub?.cancel());
    _lifecycle?.dispose();
    super.dispose();
  }
}

/// The production probe: any HTTP answer from the API's liveness endpoint.
///
/// `/health/live` touches neither the database nor Redis, so it measures the
/// route to the server rather than the server's load.
Future<bool> probeApi() async {
  final dio = Dio(
    BaseOptions(
      connectTimeout: const Duration(seconds: 6),
      receiveTimeout: const Duration(seconds: 6),
      validateStatus: (_) => true,
    ),
  );
  try {
    await dio.get<void>('${Env.apiBaseUrl}/health/live');
    return true;
  } on DioException {
    return false;
  } finally {
    dio.close();
  }
}

/// The OS's network state: false only when every interface is down.
Stream<bool> platformHasNetwork() async* {
  final connectivity = Connectivity();
  bool any(List<ConnectivityResult> results) =>
      results.any((result) => result != ConnectivityResult.none);
  try {
    yield any(await connectivity.checkConnectivity());
  } on Object {
    // No answer from the platform: rely on request outcomes alone.
  }
  yield* connectivity.onConnectivityChanged.map(any);
}
