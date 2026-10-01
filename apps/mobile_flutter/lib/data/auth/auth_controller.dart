import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:meta/meta.dart';
import 'package:supabase_flutter/supabase_flutter.dart' as sb;

import '../../core/api/api_error.dart';
import 'auth_models.dart';
import 'auth_repository.dart';
import 'provider_sign_in.dart';

/// Where the app is in the sign-in lifecycle.
///
/// This is what the router switches on, so the rule "which screen does a
/// seller see" lives in one enum rather than being re-derived per screen.
enum AuthStage {
  /// Reading the stored session on launch.
  restoring,

  /// No usable session.
  signedOut,

  passwordRecovery,

  /// Several existing shops: select one before opening the home screen.
  needsShopSelection,

  /// Signed in, but no shop yet — onboarding.
  needsOnboarding,

  /// Signed in with a shop.
  ready,
}

@immutable
class AuthState {
  const AuthState({
    required this.stage,
    this.profile,
    this.error,
    this.isBusy = false,
    this.verificationEmail,
    this.verificationSent,
  });

  const AuthState.restoring() : this(stage: AuthStage.restoring);

  final AuthStage stage;
  final AccountProfile? profile;

  /// The last failure, for the screen to display. Cleared on the next action.
  final ApiError? error;

  final bool isBusy;
  final String? verificationEmail;
  final bool? verificationSent;

  bool get isSignedIn =>
      stage == AuthStage.ready ||
      stage == AuthStage.needsOnboarding ||
      stage == AuthStage.needsShopSelection;

  String? get tenantId => profile?.tenantId;

  AuthState copyWith({
    AuthStage? stage,
    AccountProfile? profile,
    ApiError? error,
    bool clearError = false,
    bool? isBusy,
    String? verificationEmail,
    bool? verificationSent,
    bool clearVerification = false,
  }) => AuthState(
    stage: stage ?? this.stage,
    profile: profile ?? this.profile,
    error: clearError ? null : (error ?? this.error),
    isBusy: isBusy ?? this.isBusy,
    verificationEmail: clearVerification
        ? null
        : (verificationEmail ?? this.verificationEmail),
    verificationSent: clearVerification
        ? null
        : (verificationSent ?? this.verificationSent),
  );
}

/// Owns the session lifecycle.
class AuthController extends StateNotifier<AuthState> {
  AuthController(
    this._repository, {
    ProviderSignIn? providerSignIn,
    // How long to wait before asking again after a restore the server could
    // not answer. Injected by tests only.
    Duration Function(int attempt)? restoreRetryDelay,
  }) : _providerSignIn = providerSignIn ?? NativeProviderSignIn(),
       _restoreRetryDelay = restoreRetryDelay ?? _backoff,
       super(const AuthState.restoring()) {
    _invalidationSub = _repository.onSessionInvalid.listen(_onInvalidated);
    _authSub = _repository.authChanges.listen(
      (event) {
        if (event.event == sb.AuthChangeEvent.passwordRecovery) {
          _repository.beginRecovery();
          state = const AuthState(stage: AuthStage.passwordRecovery);
        } else if (event.event == sb.AuthChangeEvent.signedOut) {
          state = const AuthState(stage: AuthStage.signedOut);
        } else if (event.event == sb.AuthChangeEvent.signedIn &&
            !state.isBusy &&
            !_repository.isRecovering) {
          unawaited(restore());
        }
      },
      onError: (Object error) {
        // A token refresh that could not reach Supabase (no connection, 5xx).
        // The SDK keeps the session and tries again on its next tick, so a
        // signed-in seller is not sent to the login screen for it.
        if (error is sb.AuthRetryableFetchException &&
            state.stage != AuthStage.signedOut) {
          return;
        }
        state = const AuthState(
          stage: AuthStage.signedOut,
          error: ApiError(
            code: 'INVALID_TOKEN',
            messageBn: '',
            messageEn: 'Open a new sign-in link.',
            retryable: false,
          ),
        );
      },
    );
  }

  final AuthRepository _repository;
  final ProviderSignIn _providerSignIn;
  final Duration Function(int attempt) _restoreRetryDelay;
  late final StreamSubscription<ApiError> _invalidationSub;
  late final StreamSubscription<sb.AuthState> _authSub;
  bool _signingOut = false;
  Timer? _restoreRetry;
  int _restoreAttempts = 0;
  Future<void>? _restoring;

  /// 2, 4, 8, 16, then every 30 seconds.
  static Duration _backoff(int attempt) =>
      Duration(seconds: attempt >= 4 ? 30 : 2 << attempt);

  /// Restore the stored session and confirm it is still valid.
  ///
  /// The stored token is not trusted on its own: a session revoked from
  /// another device must not appear signed in here, so the profile is fetched
  /// before the app leaves the splash screen.
  ///
  /// Only the server rejecting the credential ends the session. Offline, a
  /// timeout, a 5xx or a busy database is the server failing to answer, and
  /// says nothing about the session, so it is kept.
  ///
  /// A call while one is running joins it: reconnecting, a retry tick and a
  /// Supabase sign-in event can all ask at once, and one `/me` answers them.
  Future<void> restore() =>
      _restoring ??= _restore().whenComplete(() => _restoring = null);

  Future<void> _restore() async {
    _restoreRetry?.cancel();
    if (_repository.isRecovering) {
      state = const AuthState(stage: AuthStage.passwordRecovery);
      return;
    }
    final stored = await _repository.currentSession();
    if (stored == null) {
      state = const AuthState(stage: AuthStage.signedOut);
      return;
    }
    try {
      final profile = await _repository.fetchProfile();
      _restoreAttempts = 0;
      if (!_repository.isRecovering) {
        state = AuthState(stage: _stageFor(profile), profile: profile);
      }
    } on ApiError catch (error) {
      // A refresh the SDK found invalid has already removed the session.
      if (_rejectsSession(error) ||
          await _repository.currentSession() == null) {
        await _repository.signOutLocally();
        state = AuthState(stage: AuthStage.signedOut, error: error);
        return;
      }
      final known = state.profile;
      if (known != null && known.userId == stored.userId) {
        // Confirmed earlier in this run: keep the seller where they were.
        state = AuthState(
          stage: _stageFor(known),
          profile: known,
          error: error,
        );
        return;
      }
      // Nothing confirmed yet this launch, so the right screen is unknown.
      // Guessing "no shop" would open onboarding over an existing shop and
      // could create a second one, so wait on the splash and ask again.
      state = AuthState(stage: AuthStage.restoring, error: error);
      _restoreRetry = Timer(_restoreRetryDelay(_restoreAttempts++), () {
        if (mounted && state.stage == AuthStage.restoring) {
          unawaited(restore());
        }
      });
    }
  }

  /// The server said this credential is no good (a 401 auth code, or a 403),
  /// as opposed to failing to answer.
  static bool _rejectsSession(ApiError error) =>
      error.isAuthFailure || error.code == ApiErrorCode.forbidden;

  Future<OtpChallenge?> requestOtp(String phone) async {
    state = state.copyWith(isBusy: true, clearError: true);
    try {
      final challenge = await _repository.requestOtp(phone);
      state = state.copyWith(isBusy: false);
      return challenge;
    } on ApiError catch (error) {
      state = state.copyWith(isBusy: false, error: error);
      return null;
    }
  }

  Future<bool> verifyOtp({
    required String challengeId,
    required String code,
    required Map<String, dynamic> device,
  }) async {
    state = state.copyWith(isBusy: true, clearError: true);
    try {
      final envelope = await _repository.verifyOtp(
        challengeId: challengeId,
        code: code,
        device: device,
      );
      await _adoptSession(envelope);
      return true;
    } on ApiError catch (error) {
      state = state.copyWith(isBusy: false, error: error);
      return false;
    }
  }

  Future<bool> createShop(Map<String, dynamic> payload) async {
    state = state.copyWith(isBusy: true, clearError: true);
    try {
      // Finish the existing create/complete sequence before leaving this page.
      await _adoptSession(
        await _repository.createShop(payload),
        keepOnboarding: true,
      );
      return true;
    } on ApiError catch (error) {
      state = state.copyWith(isBusy: false, error: error);
      return false;
    }
  }

  Future<bool> completeOnboarding() async {
    state = state.copyWith(isBusy: true, clearError: true);
    try {
      await _repository.updateShop(<String, dynamic>{
        'onboarding_step': 'COMPLETE',
      });
      final profile = await _repository.fetchProfile();
      state = AuthState(stage: AuthStage.ready, profile: profile);
      return true;
    } on ApiError catch (error) {
      state = state.copyWith(isBusy: false, error: error);
      return false;
    }
  }

  Future<void> signOut({bool allDevices = false}) async {
    _signingOut = true;
    try {
      state = state.copyWith(isBusy: true, clearError: true);
      try {
        await _repository.signOut(allDevices: allDevices);
      } on ApiError catch (error) {
        // The local session is cleared before the remote revoke is attempted;
        // once it is gone this device is signed out, whatever the server said.
        if (await _repository.currentSession() != null) {
          state = state.copyWith(isBusy: false, error: error);
          return;
        }
      }
      await _clearProviderSession();
      state = const AuthState(stage: AuthStage.signedOut);
    } finally {
      _signingOut = false;
    }
  }

  void clearError() {
    if (state.error != null) {
      state = state.copyWith(clearError: true);
    }
  }

  Future<void> _adoptSession(
    SessionEnvelope envelope, {
    String? verificationEmail,
    bool? verificationSent,
    bool keepOnboarding = false,
  }) async {
    // The profile is re-fetched rather than derived from the envelope so the
    // permission list and shop state come from a single server-owned source.
    try {
      final profile = await _repository.fetchProfile();
      state = AuthState(
        stage: keepOnboarding ? AuthStage.needsOnboarding : _stageFor(profile),
        profile: profile,
        verificationEmail: verificationEmail,
        verificationSent: verificationSent,
      );
    } on ApiError catch (error) {
      if (!error.isOffline &&
          !error.isTimeout &&
          error.code != ApiErrorCode.serviceUnavailable) {
        rethrow;
      }
      state = AuthState(
        stage: keepOnboarding
            ? AuthStage.needsOnboarding
            : envelope.tenantId != null
            ? AuthStage.ready
            : envelope.tenants.isNotEmpty
            ? AuthStage.needsShopSelection
            : AuthStage.needsOnboarding,
        profile: AccountProfile(
          userId: envelope.userId,
          maskedPhone: null,
          locale: 'bn',
          sessionId: envelope.sessionId,
          tenantId: envelope.tenantId,
          role: envelope.role,
          permissions: const [],
          tenants: envelope.tenants,
          needsOnboarding: envelope.needsOnboarding,
        ),
        verificationEmail: verificationEmail,
        verificationSent: verificationSent,
      );
    }
  }

  void _onInvalidated(ApiError error) {
    // Requests already in flight when the seller signs out come back 401 once
    // the session is revoked. That is the sign-out working, not a failure to
    // show on the login screen.
    if (_signingOut || state.stage == AuthStage.signedOut) return;
    state = AuthState(stage: AuthStage.signedOut, error: error);
  }

  static AuthStage _stageFor(AccountProfile profile) {
    if (profile.tenantId == null && profile.tenants.isNotEmpty) {
      return AuthStage.needsShopSelection;
    }
    return profile.tenantId == null
        ? AuthStage.needsOnboarding
        : AuthStage.ready;
  }

  Future<bool> _authAction(Future<void> Function() action) async {
    if (state.isBusy) return false;
    state = state.copyWith(isBusy: true, clearError: true);
    try {
      await action();
      state = state.copyWith(isBusy: false);
      return true;
    } on ApiError catch (error) {
      state = state.copyWith(isBusy: false, error: error);
      return false;
    } on Object {
      // Storage/platform failures are never rendered from their raw exception.
      state = state.copyWith(
        isBusy: false,
        error: const ApiError(
          code: ApiErrorCode.internal,
          messageBn: '',
          messageEn: '',
          retryable: true,
        ),
      );
      return false;
    }
  }

  Future<bool> login(String email, String password) => _authAction(() async {
    try {
      await _adoptSession(
        await _repository.login(email: email, password: password),
      );
    } on ApiError catch (error) {
      if (error.code == 'EMAIL_NOT_VERIFIED') {
        state = state.copyWith(verificationEmail: email.trim());
      }
      rethrow;
    }
  });

  Future<bool> register(String email, String password) => _authAction(() async {
    final result = await _repository.register(email: email, password: password);
    final session = result.session;
    if (session == null) {
      state = AuthState(
        stage: AuthStage.signedOut,
        verificationEmail: email.trim(),
      );
    } else {
      await _adoptSession(session);
    }
  });

  Future<bool> signInWithProvider(
    SignInProvider provider,
  ) => _authAction(() async {
    if (provider == SignInProvider.apple &&
        _providerSignIn.usesHostedAppleSignIn) {
      await _repository.startAppleOAuth();
      // Normally the PKCE callback restores through authChanges later.
      // Also handle a callback that arrives before the browser launch returns.
      if (await _repository.currentSession() != null) await restore();
      return;
    }
    final token = await _providerSignIn.identityToken(provider);
    final envelope = await _repository.signInWithProvider(
      provider,
      token,
      nonce: _providerSignIn.nonce,
    );
    try {
      await _adoptSession(envelope);
    } on ApiError catch (error) {
      throw providerProfileError(provider, error);
    }
  });

  Future<bool> forgotPassword(String email) =>
      _authAction(() => _repository.forgotPassword(email));

  Future<bool> resendVerification(String email) =>
      _authAction(() => _repository.resendVerification(email));

  Future<bool> verifyEmail(String token) => _authAction(() async {
    await _repository.verifyEmail(token);
    state = state.copyWith(clearVerification: true);
  });

  Future<bool> resetPassword(String token, String password) =>
      _authAction(() async {
        await _repository.resetPassword(token, password);
        await _clearProviderSession();
        state = const AuthState(stage: AuthStage.signedOut);
      });

  Future<bool> selectShop(String tenantId) => _authAction(() async {
    await _adoptSession(await _repository.selectTenant(tenantId));
  });

  void continueAfterVerification() {
    state = state.copyWith(clearVerification: true, clearError: true);
  }

  Future<void> _clearProviderSession() async {
    try {
      await _providerSignIn.signOut();
    } on Object {
      // Provider cleanup must not undo an already completed backend logout/reset.
    }
  }

  @override
  void dispose() {
    _restoreRetry?.cancel();
    unawaited(_invalidationSub.cancel());
    unawaited(_authSub.cancel());
    super.dispose();
  }
}
