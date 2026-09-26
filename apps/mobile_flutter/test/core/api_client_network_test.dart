import 'package:dio/dio.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/core/api/api_error.dart';
import 'package:ecomsbd/core/network/network_monitor.dart';
import 'package:ecomsbd/core/storage/token_store.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/fake_api.dart';

class _Reports implements ReachabilityReporter {
  final List<String> events = <String>[];

  @override
  void reportReachable() => events.add('reached');

  @override
  void reportUnreachable() => events.add('unreachable');
}

/// A session whose access token has already expired until it is refreshed.
class _ExpiringSessions implements SessionProvider {
  int refreshes = 0;

  StoredSession _session({required bool fresh}) => StoredSession(
    accessToken: fresh ? 'fresh' : 'stale',
    refreshToken: '',
    accessTokenExpiresAt: DateTime.now().toUtc().add(
      fresh ? const Duration(hours: 1) : const Duration(minutes: -5),
    ),
    sessionId: 'session-1',
    userId: 'user-1',
    tenantId: testTenantId,
  );

  @override
  Future<StoredSession?> currentSession() async =>
      _session(fresh: refreshes > 0);

  @override
  Future<StoredSession?> refreshSession() async {
    refreshes++;
    return _session(fresh: true);
  }

  @override
  Future<void> onSessionInvalidated(ApiError error) async {}
}

FakeReply _error(int status, String code) => FakeReply(<String, dynamic>{
  'code': code,
  'message_bn': 'বাংলা',
  'message_en': 'server said $code',
  'retryable': status >= 500,
}, statusCode: status);

DioException _timeout(DioExceptionType type, String method) => DioException(
  requestOptions: RequestOptions(path: '/orders', method: method),
  type: type,
);

void main() {
  late FakeApiAdapter api;
  late _Reports reports;
  late ApiClient client;

  setUp(() {
    api = FakeApiAdapter();
    reports = _Reports();
    client = ApiClient(
      sessionProvider: FakeSessions(),
      dio: Dio()..httpClientAdapter = api,
      reachability: reports,
    );
  });

  test('no connection is offline, with the connection copy', () async {
    api.offline = true;

    final error = await client
        .get('/orders')
        .then<ApiError?>(
          (_) => null,
          onError: (Object error) => error as ApiError,
        );

    expect(error!.isOffline, isTrue);
    expect(error.retryable, isTrue);
    expect(error.messageEn, contains('No internet connection'));
    expect(reports.events, <String>['unreachable']);
  });

  test('a connection that could not even be opened is offline too', () async {
    api.on(
      'GET',
      '/orders',
      (_) => throw _timeout(DioExceptionType.connectionTimeout, 'GET'),
    );

    await expectLater(
      client.get('/orders'),
      throwsA(isA<ApiError>().having((e) => e.isOffline, 'isOffline', true)),
    );
    expect(reports.events, <String>['unreachable']);
  });

  for (final (status, code) in <(int, String)>[
    (500, ApiErrorCode.internal),
    (503, ApiErrorCode.serviceUnavailable),
    (401, ApiErrorCode.sessionRevoked),
    (403, ApiErrorCode.forbidden),
    (402, ApiErrorCode.entitlementRequired),
    (422, ApiErrorCode.validation),
  ]) {
    test('$status $code is not offline: the server answered', () async {
      api.on('GET', '/orders', (_) => _error(status, code));

      final error = await client
          .get('/orders')
          .then<ApiError?>(
            (_) => null,
            onError: (Object error) => error as ApiError,
          );

      expect(error!.code, code);
      expect(error.isOffline, isFalse);
      expect(reports.events, <String>['reached']);
    });
  }

  test('a slow read is a retryable timeout, not "offline"', () async {
    api.on(
      'GET',
      '/orders',
      (_) => throw _timeout(DioExceptionType.receiveTimeout, 'GET'),
    );

    final error = await client
        .get('/orders')
        .then<ApiError?>(
          (_) => null,
          onError: (Object error) => error as ApiError,
        );

    expect(error!.isTimeout, isTrue);
    expect(error.isOffline, isFalse);
    expect(error.retryable, isTrue);
    expect(reports.events, isEmpty);
  });

  test('a slow write is an unconfirmed outcome, never a plain retry', () async {
    api.on(
      'POST',
      '/orders',
      (_) => throw _timeout(DioExceptionType.receiveTimeout, 'POST'),
    );

    final error = await client
        .post('/orders')
        .then<ApiError?>(
          (_) => null,
          onError: (Object error) => error as ApiError,
        );

    expect(error!.isTimeout, isTrue);
    expect(error.retryable, isFalse);
    expect(error.messageEn, contains('unconfirmed'));
  });

  test('an expired token is refreshed before the request, not after a 401', () {
    final sessions = _ExpiringSessions();
    final expiring = ApiClient(
      sessionProvider: sessions,
      dio: Dio()..httpClientAdapter = api,
    );
    api.on(
      'GET',
      '/me',
      (_) => sessions.refreshes == 0
          ? _error(401, ApiErrorCode.tokenExpired)
          : const FakeReply(<String, dynamic>{'ok': true}),
    );

    return expiring.get('/me').then((body) {
      expect(body['ok'], isTrue);
      expect(sessions.refreshes, 1);
      expect(api.to('GET', '/me'), hasLength(1));
    });
  });

  test('a non-API failure is shown as a generic message', () {
    final error = ApiError.from(const FormatException('type Null is not'));

    expect(error.messageEn, 'Something went wrong. Please try again.');
    expect(error.retryable, isTrue);
    expect(ApiError.from(ApiError.offline()).isOffline, isTrue);
  });
}
