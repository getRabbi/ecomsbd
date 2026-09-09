import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/core/api/api_error.dart';
import 'package:ecomsbd/core/storage/token_store.dart';

/// A request the app made, captured for assertions.
class RecordedRequest {
  RecordedRequest({
    required this.method,
    required this.path,
    required this.query,
    this.body,
  });

  final String method;
  final String path;
  final Map<String, dynamic> query;
  final Object? body;

  Map<String, dynamic> get jsonBody =>
      body is Map<String, dynamic> ? body! as Map<String, dynamic> : const {};
}

/// A canned reply.
class FakeReply {
  const FakeReply(this.body, {this.statusCode = 200});

  final Object body;
  final int statusCode;
}

/// Serves canned replies to the real [ApiClient].
///
/// The client under test is the production one — interceptors, error
/// translation and refresh included — so a repository test exercises the same
/// code path a device does. Only the socket is fake.
class FakeApiAdapter implements HttpClientAdapter {
  FakeApiAdapter();

  final List<RecordedRequest> requests = <RecordedRequest>[];
  final Map<String, FakeReply Function(RecordedRequest)> _routes =
      <String, FakeReply Function(RecordedRequest)>{};

  /// When true every request fails the way a dead connection does.
  bool offline = false;

  void on(
    String method,
    String path,
    FakeReply Function(RecordedRequest) handler,
  ) {
    _routes['${method.toUpperCase()} $path'] = handler;
  }

  void onJson(String method, String path, Object body) {
    on(method, path, (_) => FakeReply(body));
  }

  /// Requests made to a path, in order.
  List<RecordedRequest> to(String method, String path) => requests
      .where(
        (request) =>
            request.method == method.toUpperCase() && request.path == path,
      )
      .toList();

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    final request = RecordedRequest(
      method: options.method.toUpperCase(),
      path: options.path,
      query: Map<String, dynamic>.from(options.queryParameters),
      body: options.data,
    );
    requests.add(request);

    if (offline) {
      throw DioException(
        requestOptions: options,
        type: DioExceptionType.connectionError,
        error: const SocketException('no route to host'),
      );
    }

    final handler = _routes['${request.method} ${request.path}'];
    if (handler == null) {
      return ResponseBody.fromString(
        jsonEncode(<String, dynamic>{
          'code': 'NOT_FOUND',
          'message_bn': 'পাওয়া যায়নি।',
          'message_en': 'No fake route for ${request.method} ${request.path}',
          'retryable': false,
        }),
        404,
        headers: _jsonHeaders,
      );
    }

    final reply = handler(request);
    return ResponseBody.fromString(
      jsonEncode(reply.body),
      reply.statusCode,
      headers: _jsonHeaders,
    );
  }

  @override
  void close({bool force = false}) {}

  static const Map<String, List<String>> _jsonHeaders = <String, List<String>>{
    Headers.contentTypeHeader: <String>['application/json'],
  };
}

/// The shop every fake response belongs to.
const String testTenantId = 'tenant-1';

/// A session that is always present and never needs refreshing.
class FakeSessions implements SessionProvider {
  ApiError? invalidatedWith;

  @override
  Future<StoredSession?> currentSession() async => StoredSession(
    accessToken: 'access',
    refreshToken: 'refresh',
    accessTokenExpiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
    sessionId: 'session-1',
    userId: 'user-1',
    tenantId: testTenantId,
  );

  @override
  Future<StoredSession?> refreshSession() async => null;

  @override
  Future<void> onSessionInvalidated(ApiError error) async {
    invalidatedWith = error;
  }
}

/// An [ApiClient] wired to [adapter].
({ApiClient client, FakeApiAdapter adapter}) buildFakeApi() {
  final adapter = FakeApiAdapter();
  final dio = Dio()..httpClientAdapter = adapter;
  final client = ApiClient(sessionProvider: FakeSessions(), dio: dio);
  return (client: client, adapter: adapter);
}
