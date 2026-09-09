import 'dart:async';

import '../../core/api/api_client.dart';
import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../core/storage/token_store.dart';
import 'auth_models.dart';

/// Talks to `/v1/auth` and `/v1/me`, and owns the stored session.
///
/// Implements [SessionProvider] so the HTTP client can attach and refresh
/// credentials without knowing anything about the auth feature.
class AuthRepository implements SessionProvider {
  AuthRepository({required TokenStore tokenStore}) : _tokens = tokenStore;

  final TokenStore _tokens;

  ApiClient? _client;
  StoredSession? _cached;
  bool _loaded = false;

  final StreamController<ApiError> _invalidations =
      StreamController<ApiError>.broadcast();

  /// Emits when the session becomes unusable and the app must return to login.
  Stream<ApiError> get onSessionInvalid => _invalidations.stream;

  /// Wired after construction: the client needs this repository, and this
  /// repository needs the client.
  set client(ApiClient value) => _client = value;

  /// The shared HTTP client.
  ///
  /// Exposed so feature repositories use the *same* instance, which is what
  /// keeps token refresh single-flight. A second client would refresh
  /// concurrently, rotate the token twice and trip the server's reuse
  /// detection, killing the session.
  ApiClient get api => _api;

  ApiClient get _api {
    final client = _client;
    if (client == null) {
      throw StateError('AuthRepository.client was never assigned');
    }
    return client;
  }

  // --- SessionProvider ------------------------------------------------------

  @override
  Future<StoredSession?> currentSession() async {
    if (!_loaded) {
      _cached = await _tokens.read();
      _loaded = true;
    }
    return _cached;
  }

  @override
  Future<StoredSession?> refreshSession() async {
    final session = await currentSession();
    if (session == null) {
      return null;
    }
    try {
      final json = await _api.post(
        '/auth/refresh',
        body: <String, dynamic>{'refresh_token': session.refreshToken},
        authenticated: false,
      );
      return _persist(SessionEnvelope.fromJson(json));
    } on ApiError catch (error) {
      // The refresh token is single-use and the server revokes a session on
      // reuse. A failure here is terminal: clear local state rather than
      // retrying, which would only burn the replacement token too.
      await signOutLocally();
      _invalidations.add(error);
      return null;
    }
  }

  @override
  Future<void> onSessionInvalidated(ApiError error) async {
    await signOutLocally();
    _invalidations.add(error);
  }

  // --- OTP ------------------------------------------------------------------

  /// Request a verification code.
  ///
  /// Bangla digits are normalised before sending so a seller typing `০১৭…`
  /// reaches the same account as one typing `017…`.
  Future<OtpChallenge> requestOtp(String phone) async {
    final json = await _api.post(
      '/auth/otp/request',
      body: <String, dynamic>{'phone': normalizeDigits(phone.trim())},
      authenticated: false,
    );
    return OtpChallenge.fromJson(json);
  }

  Future<SessionEnvelope> verifyOtp({
    required String challengeId,
    required String code,
    required Map<String, dynamic> device,
  }) async {
    final json = await _api.post(
      '/auth/otp/verify',
      body: <String, dynamic>{
        'challenge_id': challengeId,
        'code': normalizeDigits(code.trim()),
        'device': device,
      },
      authenticated: false,
    );
    final envelope = SessionEnvelope.fromJson(json);
    await _persist(envelope);
    return envelope;
  }

  // --- session --------------------------------------------------------------

  Future<AccountProfile> fetchProfile() async {
    return AccountProfile.fromJson(await _api.get('/me'));
  }

  Future<SessionEnvelope> createShop(Map<String, dynamic> payload) async {
    final envelope = SessionEnvelope.fromJson(
      await _api.post('/tenants', body: payload),
    );
    await _persist(envelope);
    return envelope;
  }

  Future<SessionEnvelope> selectTenant(String tenantId) async {
    final envelope = SessionEnvelope.fromJson(
      await _api.post(
        '/auth/select-tenant',
        body: <String, dynamic>{'tenant_id': tenantId},
      ),
    );
    await _persist(envelope);
    return envelope;
  }

  Future<Map<String, dynamic>> updateShop(Map<String, dynamic> payload) =>
      _api.patch('/tenant', body: payload);

  Future<Map<String, dynamic>> fetchShop() => _api.get('/tenant');

  Future<List<CourierProvider>> fetchProviders() async {
    final rows = await _api.getList('/couriers/providers');
    return <CourierProvider>[
      for (final row in rows)
        CourierProvider.fromJson(row as Map<String, dynamic>),
    ];
  }

  Future<Map<String, dynamic>> fetchEntitlements() =>
      _api.get('/billing/entitlements');

  /// Revoke the session server-side, then clear it locally.
  ///
  /// A network failure must not strand the seller in a signed-in shell they
  /// cannot use, so the local clear happens either way; the server-side
  /// session expires on its own.
  Future<void> signOut({bool allDevices = false}) async {
    try {
      await _api.post(
        '/auth/logout',
        body: <String, dynamic>{'all_devices': allDevices},
      );
    } on ApiError {
      // Ignored deliberately, see above.
    } finally {
      await signOutLocally();
    }
  }

  Future<void> signOutLocally() async {
    _cached = null;
    _loaded = true;
    await _tokens.clear();
  }

  Future<StoredSession> _persist(SessionEnvelope envelope) async {
    final session = StoredSession(
      accessToken: envelope.accessToken,
      refreshToken: envelope.refreshToken,
      accessTokenExpiresAt: DateTime.now().toUtc().add(
        Duration(seconds: envelope.expiresInSeconds),
      ),
      sessionId: envelope.sessionId,
      userId: envelope.userId,
      tenantId: envelope.tenantId,
      role: envelope.role,
    );
    _cached = session;
    _loaded = true;
    await _tokens.write(session);
    return session;
  }

  void dispose() {
    unawaited(_invalidations.close());
  }
}
