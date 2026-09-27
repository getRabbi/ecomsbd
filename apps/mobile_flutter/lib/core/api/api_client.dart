import 'dart:async';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:uuid/uuid.dart';

import '../env.dart';
import '../network/network_monitor.dart';
import '../storage/token_store.dart';
import 'api_error.dart';

/// Supplies and refreshes the session for outgoing requests.
///
/// Implemented by the auth repository. Kept as an interface so the HTTP layer
/// does not depend on the auth feature, and so tests can drive token expiry.
abstract class SessionProvider {
  Future<StoredSession?> currentSession();

  /// Exchange the refresh token for a new pair. Returns null if it failed.
  Future<StoredSession?> refreshSession();

  /// Called when the session is unrecoverable; the app returns to login.
  Future<void> onSessionInvalidated(ApiError error);
}

/// The HTTP client.
///
/// Everything cross-cutting lives in interceptors rather than at call sites:
/// bearer tokens, single-flight refresh, trace propagation, idempotency keys
/// and error translation. A feature repository calls [get]/[post] and receives
/// either data or a typed [ApiError].
class ApiClient {
  ApiClient({
    required SessionProvider sessionProvider,
    Dio? dio,
    String? appVersion,
    this.reachability,
  }) : _sessions = sessionProvider,
       _uuid = const Uuid(),
       _dio = dio ?? Dio() {
    _dio.options = _dio.options.copyWith(
      baseUrl: '${Env.apiBaseUrl}${Env.apiVersionPrefix}',
      connectTimeout: Env.connectTimeout,
      receiveTimeout: Env.receiveTimeout,
      sendTimeout: Env.connectTimeout,
      headers: <String, String>{
        'accept': 'application/json',
        if (appVersion != null) 'x-app-version': appVersion,
      },
      // Non-2xx responses are handled by the error interceptor, which turns
      // them into ApiError; Dio must not throw first.
      validateStatus: (status) => status != null && status < 500,
    );
    _dio.interceptors.add(
      InterceptorsWrapper(onRequest: _onRequest, onError: _onTransportError),
    );
  }

  final Dio _dio;
  final SessionProvider _sessions;
  final Uuid _uuid;

  /// Told whether each request reached the server, which is what drives the
  /// app-wide offline state.
  final ReachabilityReporter? reachability;

  /// Guards refresh so a burst of 401s produces one refresh, not many. Several
  /// concurrent refreshes would rotate the token repeatedly and trip the
  /// server's reuse detection, killing the session.
  Future<StoredSession?>? _inFlightRefresh;

  Dio get raw => _dio;

  Future<void> _onRequest(
    RequestOptions options,
    RequestInterceptorHandler handler,
  ) async {
    if (options.extra['skipAuth'] != true) {
      var session = await _sessions.currentSession();
      if (session != null && _expiresSoon(session)) {
        // A token already known to be expired would only earn a 401 and a
        // second round trip; refresh it first. The one on a launch after an
        // hour away cost a whole round trip before the first screen.
        try {
          session = await _refreshOnce() ?? session;
        } on Object {
          // Send what there is: the server's answer decides what happens next.
        }
      }
      if (session != null) {
        options.headers['authorization'] = 'Bearer ${session.accessToken}';
      }
    }
    options.headers['x-trace-id'] ??= _uuid.v4().replaceAll('-', '');
    handler.next(options);
  }

  void _onTransportError(DioException error, ErrorInterceptorHandler handler) {
    handler.next(error);
  }

  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, dynamic>? query,
    bool authenticated = true,
  }) => _send(
    () => _dio.get<dynamic>(
      path,
      queryParameters: query,
      options: Options(extra: <String, dynamic>{'skipAuth': !authenticated}),
    ),
  );

  Future<List<dynamic>> getList(
    String path, {
    Map<String, dynamic>? query,
    bool authenticated = true,
  }) async {
    final response = await _sendRaw(
      () => _dio.get<dynamic>(
        path,
        queryParameters: query,
        options: Options(extra: <String, dynamic>{'skipAuth': !authenticated}),
      ),
    );
    final data = response.data;
    if (data is List) {
      return data;
    }
    throw ApiError.unexpected('Expected a JSON array from $path');
  }

  Future<Map<String, dynamic>> post(
    String path, {
    Object? body,
    Map<String, dynamic>? query,
    bool authenticated = true,
    String? idempotencyKey,
  }) => _send(
    () => _dio.post<dynamic>(
      path,
      data: body,
      queryParameters: query,
      options: Options(
        extra: <String, dynamic>{'skipAuth': !authenticated},
        headers: <String, dynamic>{
          if (idempotencyKey != null) 'idempotency-key': idempotencyKey,
        },
      ),
    ),
  );

  Future<Map<String, dynamic>> patch(String path, {Object? body}) =>
      _send(() => _dio.patch<dynamic>(path, data: body));

  /// Delete a resource.
  ///
  /// Used only where the server models removal as a DELETE — disconnecting a
  /// courier account, revoking a device. It is deliberately not offered a
  /// request body: a DELETE that carries one is a POST wearing a disguise, and
  /// proxies are entitled to drop it.
  Future<Map<String, dynamic>> delete(
    String path, {
    Map<String, dynamic>? query,
  }) => _send(() => _dio.delete<dynamic>(path, queryParameters: query));

  /// Upload a file as `multipart/form-data`.
  ///
  /// Used only by the import flow. The bytes are passed rather than a path so
  /// the caller decides what is read from disk, and so a test can post a
  /// fixture without touching the filesystem.
  Future<Map<String, dynamic>> postFile(
    String path, {
    required List<int> bytes,
    required String filename,
    Map<String, dynamic> fields = const <String, dynamic>{},
  }) {
    final form = FormData.fromMap(<String, dynamic>{
      ...fields,
      'file': MultipartFile.fromBytes(bytes, filename: filename),
    });
    return _send(() => _dio.post<dynamic>(path, data: form));
  }

  /// Generate an idempotency key for an operation that could duplicate money or
  /// external work (master spec section 77).
  ///
  /// The key must be generated once by the caller and reused across retries of
  /// the *same* intent — generating a fresh one per attempt defeats the point.
  String newIdempotencyKey() => _uuid.v4();

  Future<Map<String, dynamic>> _send(
    Future<Response<dynamic>> Function() request,
  ) async {
    final response = await _sendRaw(request);
    final data = response.data;
    if (data == null || (data is String && data.isEmpty)) {
      return const <String, dynamic>{};
    }
    if (data is Map<String, dynamic>) {
      return data;
    }
    throw ApiError.unexpected(
      'Expected a JSON object, got ${data.runtimeType}',
    );
  }

  Future<Response<dynamic>> _sendRaw(
    Future<Response<dynamic>> Function() request, {
    bool allowRefresh = true,
  }) async {
    final Response<dynamic> response;
    try {
      response = await request();
    } on DioException catch (error) {
      final translated = _translateTransportError(error);
      if (translated.isOffline) {
        reachability?.reportUnreachable();
      } else if (error.response != null) {
        reachability?.reportReachable();
      }
      throw translated;
    }
    reachability?.reportReachable();

    if (response.statusCode != null && response.statusCode! < 300) {
      return response;
    }

    final error = _translateResponse(response);

    final authenticated = response.requestOptions.extra['skipAuth'] != true;
    if (authenticated &&
        allowRefresh &&
        error.code == ApiErrorCode.tokenExpired) {
      // Only an *expired* access token is refreshable. A revoked session or an
      // invalid token means the credential is gone, and retrying would just
      // burn the refresh token too.
      final refreshed = await _refreshOnce();
      if (refreshed != null) {
        return _sendRaw(request, allowRefresh: false);
      }
    }

    if (authenticated && error.requiresReauthentication) {
      await _sessions.onSessionInvalidated(error);
    }
    throw error;
  }

  static bool _expiresSoon(StoredSession session) {
    final expiresAt = session.accessTokenExpiresAt;
    // Epoch zero means the expiry is unknown; let the server judge it.
    if (expiresAt.millisecondsSinceEpoch == 0) return false;
    return expiresAt.isBefore(
      DateTime.now().toUtc().add(const Duration(seconds: 20)),
    );
  }

  Future<StoredSession?> _refreshOnce() {
    return _inFlightRefresh ??= _sessions.refreshSession().whenComplete(() {
      _inFlightRefresh = null;
    });
  }

  ApiError _translateResponse(Response<dynamic> response) {
    final data = response.data;
    if (data is Map<String, dynamic> && data['code'] is String) {
      return ApiError.fromJson(data, statusCode: response.statusCode);
    }
    return ApiError(
      code: ApiErrorCode.internal,
      messageBn: 'সার্ভার থেকে অপ্রত্যাশিত উত্তর এসেছে।',
      messageEn: 'Unexpected response from the server.',
      retryable: true,
      statusCode: response.statusCode,
    );
  }

  ApiError _translateTransportError(DioException error) {
    // No connection could be made within the connect timeout either: nothing
    // was sent, and the seller's next step is the same as with no signal.
    final isOffline =
        error.error is SocketException ||
        error.type == DioExceptionType.connectionError ||
        error.type == DioExceptionType.connectionTimeout;
    if (isOffline) {
      return ApiError.offline();
    }

    if (error.type == DioExceptionType.receiveTimeout ||
        error.type == DioExceptionType.sendTimeout) {
      final method = error.requestOptions.method.toUpperCase();
      if (method == 'GET' || method == 'HEAD') {
        // A read that was slow changed nothing, so asking again is safe.
        return ApiError.timeout();
      }
      // A timeout means the outcome is UNKNOWN, not failed. For a request that
      // creates something externally, the caller must treat this as ambiguous
      // and let the server reconcile (master spec sections 11, 36, 126).
      return const ApiError(
        code: ApiErrorCode.timeout,
        messageBn: 'সার্ভার সাড়া দিচ্ছে না। আমরা আগের চেষ্টাটি যাচাই করছি।',
        messageEn:
            'The server did not respond in time. The outcome is unconfirmed.',
        retryable: false,
      );
    }

    if (error.response != null) {
      return _translateResponse(error.response!);
    }
    return ApiError.unexpected(error);
  }
}
