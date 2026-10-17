import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

/// Application close codes from the API (`backend/app/api/v1/realtime.py`).
abstract final class RealtimeClose {
  static const int unauthenticated = 4401;
  static const int forbidden = 4403;
  static const int rebind = 4409;
  static const int unavailable = 1013;
}

/// One open socket. [ioRealtimeConnector] wraps `dart:io`; tests use a fake.
abstract class RealtimeSocket {
  Stream<dynamic> get messages;
  int? get closeCode;
  void send(String data);
  Future<void> close([int? code, String? reason]);
}

typedef RealtimeConnector = Future<RealtimeSocket> Function(Uri uri);

/// The production connector.
Future<RealtimeSocket> ioRealtimeConnector(Uri uri) async {
  final socket = await WebSocket.connect(
    uri.toString(),
  ).timeout(const Duration(seconds: 12));
  // Keeps a quiet connection alive through carrier NATs and proxies.
  socket.pingInterval = const Duration(seconds: 20);
  return _IoRealtimeSocket(socket);
}

class _IoRealtimeSocket implements RealtimeSocket {
  _IoRealtimeSocket(this._socket);

  final WebSocket _socket;

  @override
  Stream<dynamic> get messages => _socket;

  @override
  int? get closeCode => _socket.closeCode;

  @override
  void send(String data) => _socket.add(data);

  @override
  Future<void> close([int? code, String? reason]) =>
      _socket.close(code, reason);
}

/// `https://api.example.com` + `/v1` → `wss://api.example.com/v1/realtime`.
Uri realtimeUri(String apiBaseUrl, String versionPrefix) {
  final base = Uri.parse(apiBaseUrl);
  final path =
      '${base.path.replaceAll(RegExp(r'/$'), '')}$versionPrefix/realtime';
  return base.replace(scheme: base.scheme == 'http' ? 'ws' : 'wss', path: path);
}

/// How long things take. Production values by default; tests shrink them.
class RealtimeTimings {
  const RealtimeTimings({
    this.retryBase = const Duration(seconds: 1),
    this.retryMax = const Duration(seconds: 30),
    this.retryRefused = const Duration(seconds: 60),
    this.retryDisabled = const Duration(minutes: 5),
    this.coalesce = const Duration(milliseconds: 500),
    this.minRefreshGap = const Duration(seconds: 2),
    this.heartbeatMisses = 2.5,
  });

  final Duration retryBase;
  final Duration retryMax;
  final Duration retryRefused;
  final Duration retryDisabled;

  /// Invalidations this close together cause one refresh.
  final Duration coalesce;

  /// Never two refreshes closer than this, however busy the shop.
  final Duration minRefreshGap;

  /// Silence for this many heartbeats means the connection is dead.
  final double heartbeatMisses;
}

/// The delay before retry number [attempt] (0-based): doubling to [max],
/// never less than half the step, so a server restart does not bring every
/// phone back in the same second.
Duration realtimeRetryDelay(
  int attempt, {
  required Duration base,
  required Duration max,
  required double random,
}) {
  final ceiling = min(
    max.inMilliseconds,
    base.inMilliseconds * pow(2, min(attempt, 20)).toInt(),
  );
  return Duration(milliseconds: (ceiling / 2 + random * ceiling / 2).round());
}

/// Hears from the API, while the app is open, that shop data changed.
///
/// The `/v1/realtime` socket sends **invalidations only**: "orders changed",
/// never the order. Nothing here writes to Drift or to a provider's state; an
/// invalidation only asks the screens that are showing shop data to fetch it
/// again through their repositories, the way a pull to refresh would. The
/// server's PostgreSQL stays the one copy of the shop.
///
/// Foreground only. When the app is hidden the socket is closed, and FCM
/// carries anything that must reach the seller. Coming back, the existing
/// foreground refresh runs and the socket reconnects. A message missed while
/// disconnected costs freshness, not correctness: every reconnect after the
/// first asks for a full refresh.
class RealtimeService {
  RealtimeService({
    required this.uri,
    required this.token,
    required this.onInvalidate,
    required this.onResync,
    this.connector = ioRealtimeConnector,
    this.timings = const RealtimeTimings(),
    Random? random,
  }) : _random = random ?? Random();

  /// The socket's address. Never carries a credential.
  final Uri uri;

  /// The current access token, refreshed by the Supabase client if expired.
  final Future<String?> Function() token;

  /// These topics changed: refresh what shows them.
  final void Function(Set<String> topics) onInvalidate;

  /// Messages may have been missed: refresh everything shown.
  final void Function() onResync;
  final RealtimeConnector connector;
  final RealtimeTimings timings;
  final Random _random;

  bool _running = false;
  String? _shopId;
  RealtimeSocket? _socket;
  StreamSubscription<dynamic>? _subscription;
  bool _connecting = false;
  int _attempt = 0;
  bool _everReady = false;
  Duration _heartbeat = const Duration(seconds: 25);
  Timer? _retry;
  Timer? _watchdog;
  Timer? _flush;
  DateTime? _lastRefresh;
  final Set<String> _pending = <String>{};

  /// The shop the server bound the socket to, once it said so.
  String? boundShopId;

  bool get isConnected => _socket != null && boundShopId != null;
  bool get isRunning => _running;

  /// Start, or keep, a socket for [shopId]. A different shop rebinds.
  void start(String shopId) {
    if (_running && _shopId == shopId) {
      if (_socket == null && !_connecting) wake();
      return;
    }
    final rebinding = _running;
    _running = true;
    _shopId = shopId;
    if (rebinding) {
      // The old shop's feed ends here, before the new one is asked for.
      _drop();
    }
    _attempt = 0;
    _cancelRetry();
    unawaited(_connect());
  }

  /// Backgrounded or signed out: close and stay closed.
  void stop() {
    _running = false;
    _shopId = null;
    _everReady = false;
    _cancelRetry();
    _flush?.cancel();
    _flush = null;
    _pending.clear();
    _drop();
  }

  /// The network is back: reconnect now instead of after the backoff.
  void wake() {
    if (!_running || _socket != null || _connecting) return;
    _cancelRetry();
    _attempt = 0;
    unawaited(_connect());
  }

  /// The access token was refreshed: hand the open socket the new one.
  Future<void> reauth() async {
    final socket = _socket;
    if (socket == null) return;
    final fresh = await token();
    if (fresh != null && fresh.isNotEmpty && identical(socket, _socket)) {
      socket.send(
        jsonEncode(<String, String>{'type': 'auth', 'access_token': fresh}),
      );
    }
  }

  Future<void> _connect() async {
    if (!_running || _socket != null || _connecting) return;
    _connecting = true;
    final shopAtStart = _shopId;
    try {
      final current = await token();
      if (!_running || _shopId != shopAtStart) return;
      if (current == null || current.isEmpty) {
        _scheduleRetry();
        return;
      }
      final RealtimeSocket socket;
      try {
        // No token in the address: it goes in the first message.
        socket = await connector(uri);
      } on Object {
        _scheduleRetry();
        return;
      }
      if (!_running || _shopId != shopAtStart) {
        unawaited(socket.close(1000));
        return;
      }
      _socket = socket;
      boundShopId = null;
      _subscription = socket.messages.listen(
        (dynamic data) {
          if (identical(socket, _socket)) _receive(data);
        },
        onDone: () {
          if (identical(socket, _socket)) _closed(socket.closeCode);
        },
        onError: (Object _) {
          if (identical(socket, _socket)) _closed(socket.closeCode);
        },
        cancelOnError: true,
      );
      socket.send(
        jsonEncode(<String, String>{'type': 'auth', 'access_token': current}),
      );
      _armWatchdog();
    } finally {
      _connecting = false;
      // The shop changed while this attempt was out: ask for the new one.
      if (_running && _socket == null && _shopId != shopAtStart) {
        unawaited(_connect());
      }
    }
  }

  void _receive(dynamic raw) {
    _armWatchdog();
    final Object? decoded;
    try {
      decoded = jsonDecode(raw is String ? raw : utf8.decode(raw as List<int>));
    } on Object {
      return;
    }
    if (decoded is! Map<String, dynamic>) return;
    switch (decoded['type']) {
      case 'ready':
        final heartbeat = decoded['heartbeat_seconds'];
        if (heartbeat is int && heartbeat > 0) {
          _heartbeat = Duration(seconds: heartbeat);
          _armWatchdog();
        }
        _attempt = 0;
        final shop = decoded['shop_id'];
        boundShopId = shop is String ? shop : null;
        // Anything committed while disconnected was not announced here. The
        // first connection follows the foreground refresh, which covered it.
        if (_everReady) _requestRefresh(null);
        _everReady = true;
      case 'invalidate':
        final topic = decoded['topic'];
        if (topic is String) _requestRefresh(topic);
      case 'resync':
        _requestRefresh(null);
      default:
        // ping, pong, and anything a newer server adds.
        break;
    }
  }

  /// Coalesce: one refresh for a burst, and never two too close together.
  void _requestRefresh(String? topic) {
    _pending.add(topic ?? '*');
    if (_flush != null) return;
    var wait = timings.coalesce;
    final last = _lastRefresh;
    if (last != null) {
      final sinceLast = DateTime.now().difference(last);
      final gapLeft = timings.minRefreshGap - sinceLast;
      if (gapLeft > wait) wait = gapLeft;
    }
    _flush = Timer(wait, () {
      _flush = null;
      if (!_running || _pending.isEmpty) return;
      final topics = Set<String>.of(_pending);
      _pending.clear();
      _lastRefresh = DateTime.now();
      if (topics.contains('*')) {
        onResync();
      } else {
        onInvalidate(topics);
      }
    });
  }

  void _closed(int? code) {
    _detach();
    if (!_running) return;
    switch (code) {
      case RealtimeClose.rebind:
        // The session now points at another shop: bind to it straight away.
        _attempt = 0;
        unawaited(_connect());
      case RealtimeClose.forbidden:
        _scheduleRetry(timings.retryRefused);
      case RealtimeClose.unavailable:
        _scheduleRetry(timings.retryDisabled);
      default:
        // 4401 included: the next attempt reads the token again, and the
        // Supabase client refreshes an expired one.
        _scheduleRetry();
    }
  }

  void _scheduleRetry([Duration? delay]) {
    _cancelRetry();
    if (!_running) return;
    final wait =
        delay ??
        realtimeRetryDelay(
          _attempt,
          base: timings.retryBase,
          max: timings.retryMax,
          random: _random.nextDouble(),
        );
    _attempt++;
    _retry = Timer(wait, () {
      _retry = null;
      unawaited(_connect());
    });
  }

  void _cancelRetry() {
    _retry?.cancel();
    _retry = null;
  }

  void _armWatchdog() {
    _watchdog?.cancel();
    final limit = Duration(
      milliseconds: (_heartbeat.inMilliseconds * timings.heartbeatMisses)
          .round(),
    );
    _watchdog = Timer(limit, () {
      final socket = _socket;
      if (socket == null) return;
      // Silence this long is a dead connection the OS has not noticed.
      _detach();
      unawaited(socket.close(4000, 'heartbeat timeout'));
      if (_running) _scheduleRetry();
    });
  }

  /// Forget the current socket without reacting to its close.
  void _detach() {
    _watchdog?.cancel();
    _watchdog = null;
    unawaited(_subscription?.cancel());
    _subscription = null;
    _socket = null;
    boundShopId = null;
  }

  void _drop() {
    final socket = _socket;
    _detach();
    if (socket != null) unawaited(socket.close(1000));
  }

  /// Release everything. The service cannot be restarted afterwards.
  void dispose() => stop();
}
