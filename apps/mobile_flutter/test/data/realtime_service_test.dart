import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:ecomsbd/app/realtime_wiring.dart';
import 'package:ecomsbd/data/realtime/realtime_service.dart';
import 'package:flutter_test/flutter_test.dart';

/// A socket the test plays the server for.
class FakeSocket implements RealtimeSocket {
  FakeSocket(this.uri);

  final Uri uri;
  final _incoming = StreamController<dynamic>();
  final sent = <Map<String, dynamic>>[];
  int? clientCloseCode;
  int? _closeCode;
  bool get isClosed => clientCloseCode != null || _closeCode != null;

  @override
  Stream<dynamic> get messages => _incoming.stream;

  @override
  int? get closeCode => _closeCode;

  @override
  void send(String data) => sent.add(jsonDecode(data) as Map<String, dynamic>);

  @override
  Future<void> close([int? code, String? reason]) async {
    clientCloseCode = code ?? 1000;
    if (!_incoming.isClosed) await _incoming.close();
  }

  /// Nothing arrives on a socket that is already closed.
  void message(Map<String, Object?> body) {
    if (!_incoming.isClosed) _incoming.add(jsonEncode(body));
  }

  void ready([String shop = 'shop-a']) => message(<String, Object?>{
    'type': 'ready',
    'shop_id': shop,
    'heartbeat_seconds': 1,
  });

  Future<void> serverClose(int code) async {
    _closeCode = code;
    await _incoming.close();
  }
}

const _fast = RealtimeTimings(
  retryBase: Duration(milliseconds: 40),
  retryMax: Duration(milliseconds: 200),
  retryRefused: Duration(milliseconds: 400),
  retryDisabled: Duration(milliseconds: 800),
  coalesce: Duration(milliseconds: 30),
  minRefreshGap: Duration(milliseconds: 30),
  heartbeatMisses: 0.2,
);

void main() {
  late List<FakeSocket> sockets;
  late List<Set<String>> invalidated;
  late int resyncs;
  late String? token;
  final started = <RealtimeService>[];

  RealtimeService service({RealtimeTimings timings = _fast}) {
    final created = RealtimeService(
      uri: realtimeUri('https://api.example.com', '/v1'),
      token: () async => token,
      onInvalidate: invalidated.add,
      onResync: () => resyncs++,
      connector: (uri) async {
        final socket = FakeSocket(uri);
        sockets.add(socket);
        return socket;
      },
      timings: timings,
      random: Random(1),
    );
    started.add(created);
    return created;
  }

  Future<void> pump([int ms = 5]) =>
      Future<void>.delayed(Duration(milliseconds: ms));

  setUp(() {
    sockets = <FakeSocket>[];
    invalidated = <Set<String>>[];
    resyncs = 0;
    token = 'header.payload.signature';
  });

  tearDown(() {
    for (final realtime in started) {
      realtime.stop();
    }
    started.clear();
  });

  test('the address comes from the API base and carries no token', () async {
    expect(
      realtimeUri('https://api.example.com', '/v1').toString(),
      'wss://api.example.com/v1/realtime',
    );
    expect(
      realtimeUri('http://10.0.2.2:8000', '/v1').toString(),
      'ws://10.0.2.2:8000/v1/realtime',
    );

    final realtime = service()..start('shop-a');
    await pump();
    expect(sockets.single.uri.toString(), isNot(contains('signature')));
    expect(sockets.single.sent.first, <String, dynamic>{
      'type': 'auth',
      'access_token': 'header.payload.signature',
    });
    realtime.stop();
  });

  test('retries back off with jitter, up to a ceiling', () {
    const base = Duration(seconds: 1);
    const max = Duration(seconds: 30);
    expect(
      realtimeRetryDelay(0, base: base, max: max, random: 0),
      const Duration(milliseconds: 500),
    );
    expect(
      realtimeRetryDelay(3, base: base, max: max, random: 1),
      const Duration(seconds: 8),
    );
    expect(realtimeRetryDelay(40, base: base, max: max, random: 1), max);
  });

  test('a burst of invalidations becomes one refresh', () async {
    final realtime = service()..start('shop-a');
    await pump();
    final socket = sockets.single..ready();
    socket
      ..message(<String, Object?>{'type': 'invalidate', 'topic': 'orders'})
      ..message(<String, Object?>{'type': 'invalidate', 'topic': 'products'})
      ..message(<String, Object?>{'type': 'invalidate', 'topic': 'orders'});
    await pump(80);
    expect(invalidated, <Set<String>>[
      <String>{'orders', 'products'},
    ]);
    expect(resyncs, 0, reason: 'the first connection follows a refresh');
    realtime.stop();
  });

  test('a dropped socket reconnects and refreshes what it missed', () async {
    final realtime = service()..start('shop-a');
    await pump();
    sockets.single.ready();
    await pump();
    await sockets.single.serverClose(1006);
    await pump(150);
    expect(sockets, hasLength(2));
    sockets.last.ready();
    await pump(80);
    expect(resyncs, 1);
    realtime.stop();
  });

  test('a shop switch drops the old feed and binds the new one', () async {
    final realtime = service()..start('shop-a');
    await pump();
    final old = sockets.single..ready('shop-a');
    await pump();

    realtime.start('shop-b');
    expect(old.isClosed, isTrue);
    old.message(<String, Object?>{'type': 'invalidate', 'topic': 'orders'});
    await pump(80);
    expect(invalidated, isEmpty, reason: 'the old shop is no longer heard');
    expect(sockets, hasLength(2));
    sockets.last.ready('shop-b');
    await pump();
    expect(realtime.boundShopId, 'shop-b');
    realtime.stop();
  });

  test('a server rebind reconnects at once', () async {
    final realtime = service()..start('shop-a');
    await pump();
    sockets.single.ready();
    await pump();
    await sockets.single.serverClose(RealtimeClose.rebind);
    await pump();
    expect(sockets, hasLength(2));
    realtime.stop();
  });

  test('refused, it waits long before trying again', () async {
    final realtime = service()..start('shop-a');
    await pump();
    await sockets.single.serverClose(RealtimeClose.forbidden);
    await pump(250);
    expect(sockets, hasLength(1));
    await pump(250);
    expect(sockets, hasLength(2));
    realtime.stop();
  });

  test('stopped, it stays closed', () async {
    final realtime = service()..start('shop-a');
    await pump();
    final socket = sockets.single..ready();
    await pump();
    realtime.stop();
    expect(socket.clientCloseCode, 1000);
    await pump(300);
    expect(sockets, hasLength(1));
    expect(realtime.isConnected, isFalse);
  });

  test('silence past the heartbeat counts as a dead connection', () async {
    final realtime = service()..start('shop-a');
    await pump();
    sockets.single.ready();
    // heartbeat 1 s x 0.2 misses = 200 ms of silence.
    await pump(450);
    expect(sockets.first.isClosed, isTrue);
    expect(sockets.length, greaterThanOrEqualTo(2));
    realtime.stop();
  });

  test('back online, it reconnects without waiting out the backoff', () async {
    final realtime = service(
      timings: const RealtimeTimings(
        retryBase: Duration(seconds: 30),
        retryMax: Duration(seconds: 30),
        coalesce: Duration(milliseconds: 30),
        minRefreshGap: Duration(milliseconds: 30),
      ),
    )..start('shop-a');
    await pump();
    await sockets.single.serverClose(1006);
    await pump();
    expect(sockets, hasLength(1));
    realtime.wake();
    await pump();
    expect(sockets, hasLength(2));
    realtime.stop();
  });

  test('a refreshed token is handed to the open socket', () async {
    final realtime = service()..start('shop-a');
    await pump();
    sockets.single.ready();
    token = 'fresh.token.value';
    await realtime.reauth();
    expect(sockets.single.sent.last['access_token'], 'fresh.token.value');
    realtime.stop();
  });

  test('without a token there is no socket', () async {
    token = null;
    final realtime = service()..start('shop-a');
    await pump(30);
    expect(sockets, isEmpty);
    realtime.stop();
  });

  group('gate', () {
    test('open only in the foreground, signed in, in a shop', () async {
      final realtime = service();
      final gate = RealtimeGate(realtime)
        ..signedIn = true
        ..shopId = 'shop-a'
        ..apply();
      await pump();
      expect(sockets, hasLength(1));

      gate
        ..foreground = false
        ..apply();
      expect(sockets.single.isClosed, isTrue);
      expect(realtime.isRunning, isFalse);

      gate
        ..foreground = true
        ..apply();
      await pump();
      expect(sockets, hasLength(2));

      gate
        ..signedIn = false
        ..apply();
      expect(sockets.last.isClosed, isTrue);
      realtime.stop();
    });

    test('the same shop keeps its socket; another rebinds it', () async {
      final realtime = service();
      final gate = RealtimeGate(realtime)
        ..signedIn = true
        ..shopId = 'shop-a'
        ..apply();
      await pump();
      gate.apply();
      await pump();
      expect(sockets, hasLength(1));
      gate
        ..shopId = 'shop-b'
        ..apply();
      await pump();
      expect(sockets.first.isClosed, isTrue);
      expect(sockets, hasLength(2));
      realtime.stop();
    });
  });
}
