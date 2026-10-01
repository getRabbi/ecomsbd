import 'dart:async';
import 'dart:convert';

import 'package:app_links/app_links.dart';
import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:uuid/uuid.dart';

import '../../core/api/api_client.dart';
import '../../core/api/api_error.dart';
import '../../core/env.dart';
import '../../core/storage/token_store.dart';
import 'auth_models.dart';
import 'provider_sign_in.dart';
import 'supabase_bootstrap.dart';
import '../notifications/push_registration.dart';

/// Supabase owns credentials. FastAPI owns profiles, shops and permissions.
class AuthRepository implements SessionProvider {
  AuthRepository({required TokenStore tokenStore, GoTrueClient? auth})
    : _tokens = tokenStore,
      _injectedAuth = auth;

  final TokenStore _tokens;
  final GoTrueClient? _injectedAuth;
  GoTrueClient get _auth => _injectedAuth ?? Supabase.instance.client.auth;
  ApiClient? _client;
  AccountProfile? _profile;
  bool _legacyCleared = false;
  bool _recovering = false;
  StreamSubscription<Uri>? _linkSubscription;
  String? _lastCallback;
  String? _deviceSessionId;
  bool _attachingDevice = false;
  final _push = PushRegistration();
  bool get isRecovering => _recovering;
  final _invalidations = StreamController<ApiError>.broadcast();
  Stream<ApiError> get onSessionInvalid => _invalidations.stream;
  Stream<AuthState> get authChanges => _auth.onAuthStateChange;
  set client(ApiClient value) => _client = value;
  ApiClient get api => _client!;

  Future<void> startAuthLinks() async {
    final links = AppLinks();
    _linkSubscription = links.uriLinkStream.listen((uri) {
      unawaited(handleAuthLink(uri));
    });
    final initial = await links.getInitialLink();
    if (initial != null) await handleAuthLink(initial);
  }

  Future<void> handleAuthLink(Uri uri) async {
    final callbackHash = sha256.convert(utf8.encode(uri.toString())).toString();
    if (!isAuthCallback(uri) || _lastCallback == callbackHash) return;
    _lastCallback = callbackHash;
    try {
      // PKCE only: never accept an injected implicit bearer session.
      if (!uri.queryParameters.containsKey('code') || uri.fragment.isNotEmpty) {
        throw const AuthException('Invalid callback');
      }
      await _auth.getSessionFromUrl(uri);
    } on AuthException {
      _invalidations.add(
        const ApiError(
          code: 'INVALID_TOKEN',
          messageBn: '',
          messageEn: 'Open a new sign-in link.',
          retryable: false,
        ),
      );
    }
  }

  @override
  Future<StoredSession?> currentSession() async {
    if (!_legacyCleared) {
      await _tokens.clear();
      _legacyCleared = true;
    }
    final session = _auth.currentSession;
    if (session == null) return null;
    return StoredSession(
      accessToken: session.accessToken,
      refreshToken: '',
      accessTokenExpiresAt: DateTime.fromMillisecondsSinceEpoch(
        (session.expiresAt ?? 0) * 1000,
        isUtc: true,
      ),
      sessionId: _profile?.sessionId ?? '',
      userId: _profile?.userId ?? '',
      tenantId: _profile?.tenantId,
      role: _profile?.role,
    );
  }

  /// Runs a Supabase auth call, turning its failures into [ApiError]s.
  ///
  /// [diagnose] names a refusal for [ApiError.diagnostic]; the SDK's message
  /// is not kept.
  Future<T> _authCall<T>(
    Future<T> Function() action, {
    String Function(AuthException error)? diagnose,
  }) async {
    try {
      return await action();
    } on AuthException catch (error) {
      // The SDK raises this with no status when Supabase could not be reached
      // at all. That is the seller's connection, not a failed sign-in.
      if (error is AuthRetryableFetchException && error.statusCode == null) {
        _client?.reachability?.reportUnreachable();
        throw ApiError.offline();
      }
      final diagnostic = diagnose?.call(error);
      if (diagnostic != null) recordAuthDiagnostic(diagnostic);
      throw ApiError(
        code: switch (error.code) {
          'email_not_confirmed' => 'EMAIL_NOT_VERIFIED',
          'invalid_credentials' => 'INVALID_CREDENTIALS',
          'over_email_send_rate_limit' ||
          'over_request_rate_limit' => 'RATE_LIMITED',
          _ => 'AUTH_FAILED',
        },
        messageBn: '',
        messageEn: 'Authentication could not be completed. Please try again.',
        retryable: true,
        details: diagnostic == null
            ? null
            : <String, dynamic>{'diagnostic': diagnostic},
      );
    }
  }

  @override
  Future<StoredSession?> refreshSession() async {
    if (_auth.currentSession == null) return null;
    await _authCall(() => _auth.refreshSession());
    return currentSession();
  }

  @override
  Future<void> onSessionInvalidated(ApiError error) async {
    await signOutLocally();
    _invalidations.add(error);
  }

  Future<({SessionEnvelope? session, bool verificationSent})> register({
    required String email,
    required String password,
  }) async {
    final response = await _authCall(
      () => _auth.signUp(
        email: email.trim(),
        password: password,
        emailRedirectTo: Env.authRedirectUrl,
      ),
    );
    return (
      session: response.session == null ? null : await _envelope(),
      verificationSent: false,
    );
  }

  Future<SessionEnvelope> login({
    required String email,
    required String password,
  }) async {
    await _authCall(
      () => _auth.signInWithPassword(email: email.trim(), password: password),
    );
    return _envelope();
  }

  /// Signs in to Supabase with a provider's identity token [token], then loads
  /// the ecomsbd profile.
  ///
  /// A failure says which of the two steps failed in its
  /// [ApiError.diagnostic]. A refusal from Supabase also names the token's
  /// audience, the client id Supabase compared with its list.
  Future<SessionEnvelope> signInWithProvider(
    SignInProvider provider,
    String token, {
    String? nonce,
  }) async {
    await _authCall(
      () => _auth.signInWithIdToken(
        provider: provider == SignInProvider.google
            ? OAuthProvider.google
            : OAuthProvider.apple,
        idToken: token,
        nonce: nonce,
      ),
      diagnose: (error) => providerDiagnostic(
        provider,
        ProviderSignInFailure.supabaseRejected,
        status: error.statusCode,
        reasons: <Object?>[
          supabaseRejectionReason(error),
          if (identityTokenAudience(token) case final audience?)
            'aud=$audience',
        ],
      ),
    );
    try {
      return await _envelope();
    } on ApiError catch (error) {
      throw providerProfileError(provider, error);
    }
  }

  Future<void> startAppleOAuth() => _authCall(() async {
    final launched = await _auth.signInWithOAuth(
      OAuthProvider.apple,
      redirectTo: Env.authRedirectUrl,
      authScreenLaunchMode: LaunchMode.externalApplication,
    );
    if (!launched) {
      throw providerSignInError(
        SignInProvider.apple,
        failure: ProviderSignInFailure.notAvailable,
        reason: 'browser',
      );
    }
  });

  Future<void> resendVerification(String email) => _authCall(() async {
    await _auth.resend(
      type: OtpType.signup,
      email: email.trim(),
      emailRedirectTo: Env.authRedirectUrl,
    );
  });

  Future<void> forgotPassword(String email) => _authCall(
    () => _auth.resetPasswordForEmail(
      email.trim(),
      redirectTo: Env.authRedirectUrl,
    ),
  );

  // The SDK validates/exchanges the PKCE callback; URL claims never grant access.
  void beginRecovery() => _recovering = true;

  Future<void> resetPassword(String token, String password) async {
    if (!_recovering || _auth.currentSession == null) {
      throw const ApiError(
        code: 'INVALID_TOKEN',
        messageBn: '',
        messageEn: 'Open a new reset link.',
        retryable: false,
      );
    }
    await _authCall(() => _auth.updateUser(UserAttributes(password: password)));
    await signOut(allDevices: true);
  }

  Future<void> verifyEmail(String token) async {
    throw const ApiError(
      code: 'INVALID_TOKEN',
      messageBn: '',
      messageEn: 'Request a new confirmation email.',
      retryable: false,
    );
  }

  Future<OtpChallenge> requestOtp(String phone) async => throw _phoneDisabled;
  Future<SessionEnvelope> verifyOtp({
    required String challengeId,
    required String code,
    required Map<String, dynamic> device,
  }) async => throw _phoneDisabled;
  static const _phoneDisabled = ApiError(
    code: 'FEATURE_DISABLED',
    messageBn: '',
    messageEn: 'Phone login is disabled.',
    retryable: false,
  );

  Future<AccountProfile> fetchProfile() async {
    final profile = AccountProfile.fromJson(await api.get('/me'));
    if (_deviceSessionId != profile.sessionId) {
      // /me has confirmed the session; attaching this install is push
      // bookkeeping, so the seller does not wait on the splash for it (it
      // held every launch for another 3-5 s round trip).
      unawaited(_attachDevice(profile.sessionId));
    }
    return _profile = profile;
  }

  /// Attach this install, with its push token when one is ready, in a single
  /// request.
  ///
  /// A failure to answer is retried on the next profile fetch rather than
  /// ending a valid session (a 500 here once signed a seller out at launch).
  /// A revoked or invalid credential still signs out, through the client's
  /// session invalidation.
  Future<void> _attachDevice(String sessionId) async {
    if (_attachingDevice) return;
    _attachingDevice = true;
    try {
      final pending = _push.token();
      String? token;
      try {
        token = await pending.timeout(const Duration(seconds: 5));
      } on TimeoutException {
        // Register without it now; send it once Firebase has one.
        unawaited(
          pending.then((late) async {
            if (late != null) await _registerPush(late);
          }),
        );
      }
      await api.post(
        '/auth/device',
        body: {
          'install_id': await _tokens.installId(() => const Uuid().v4()),
          'platform': _devicePlatform,
          if (token != null) 'push_token': token,
        },
      );
      _deviceSessionId = sessionId;
      _push.listen(_registerPush);
    } on ApiError {
      // Retried on the next profile fetch.
    } finally {
      _attachingDevice = false;
    }
  }

  /// The platform the API records for this device and its push token. A
  /// refreshed token must not relabel an iPhone as Android.
  static String get _devicePlatform {
    if (kIsWeb) return 'UNKNOWN';
    return switch (defaultTargetPlatform) {
      TargetPlatform.android => 'ANDROID',
      TargetPlatform.iOS => 'IOS',
      _ => 'UNKNOWN',
    };
  }

  Future<void> _registerPush(String token) async {
    if (_auth.currentSession == null || _deviceSessionId == null) return;
    try {
      await api.post(
        '/auth/device',
        body: {
          'install_id': await _tokens.installId(() => const Uuid().v4()),
          'platform': _devicePlatform,
          'push_token': token,
        },
      );
    } on ApiError {
      // Try again on the next profile refresh or token update.
      _deviceSessionId = null;
    }
  }

  Future<SessionEnvelope> _envelope() async {
    final profile = await fetchProfile();
    return SessionEnvelope(
      accessToken: '',
      refreshToken: '',
      expiresInSeconds: 0,
      sessionId: profile.sessionId,
      userId: profile.userId,
      tenantId: profile.tenantId,
      role: profile.role,
      isNewUser: false,
      needsOnboarding: profile.needsOnboarding,
      tenants: profile.tenants,
    );
  }

  Future<SessionEnvelope> createShop(Map<String, dynamic> payload) async {
    await api.post('/tenants', body: payload);
    return _envelope();
  }

  Future<SessionEnvelope> selectTenant(String tenantId) async {
    await api.post('/auth/select-tenant', body: {'tenant_id': tenantId});
    return _envelope();
  }

  Future<Map<String, dynamic>> updateShop(Map<String, dynamic> payload) =>
      api.patch('/tenant', body: payload);
  Future<Map<String, dynamic>> fetchShop() => api.get('/tenant');
  Future<List<CourierProvider>> fetchProviders() async => [
    for (final row in await api.getList('/couriers/providers'))
      CourierProvider.fromJson(row as Map<String, dynamic>),
  ];
  Future<Map<String, dynamic>> fetchEntitlements() =>
      api.get('/billing/entitlements');

  Future<void> signOut({bool allDevices = false}) async {
    _deviceSessionId = null;
    await _push.clear();
    try {
      await api.post('/auth/logout', body: {'all_devices': allDevices});
    } on ApiError {
      // Supabase still revokes refresh tokens if the business API is unavailable.
    }
    try {
      await _authCall(
        () => _auth.signOut(
          scope: allDevices ? SignOutScope.global : SignOutScope.local,
        ),
      );
    } finally {
      _profile = null;
      _recovering = false;
      await _tokens.clear();
    }
  }

  Future<void> signOutLocally() async {
    _deviceSessionId = null;
    await _push.clear();
    _profile = null;
    _recovering = false;
    await _auth.signOut(scope: SignOutScope.local);
    await _tokens.clear();
  }

  void dispose() {
    _push.dispose();
    unawaited(_linkSubscription?.cancel());
    unawaited(_invalidations.close());
  }
}
