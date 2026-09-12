import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:meta/meta.dart';

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
  AuthController(this._repository, {ProviderSignIn? providerSignIn})
    : _providerSignIn = providerSignIn ?? NativeProviderSignIn(),
      super(const AuthState.restoring()) {
    _invalidationSub = _repository.onSessionInvalid.listen(_onInvalidated);
  }

  final AuthRepository _repository;
  final ProviderSignIn _providerSignIn;
  late final StreamSubscription<ApiError> _invalidationSub;

  /// Restore the stored session and confirm it is still valid.
  ///
  /// The stored token is not trusted on its own: a session revoked from
  /// another device must not appear signed in here, so the profile is fetched
  /// before the app leaves the splash screen.
  Future<void> restore() async {
    final stored = await _repository.currentSession();
    if (stored == null) {
      state = const AuthState(stage: AuthStage.signedOut);
      return;
    }
    try {
      final profile = await _repository.fetchProfile();
      state = AuthState(stage: _stageFor(profile), profile: profile);
    } on ApiError catch (error) {
      if (error.isOffline) {
        // Offline launch: the stored session is the best information available
        // and the app is offline-first, so the seller continues into cached
        // screens rather than being bounced to a login they cannot complete.
        state = AuthState(
          stage: stored.hasTenant ? AuthStage.ready : AuthStage.needsOnboarding,
          error: error,
        );
        return;
      }
      await _repository.signOutLocally();
      state = AuthState(stage: AuthStage.signedOut, error: error);
    }
  }

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
    state = state.copyWith(isBusy: true, clearError: true);
    await _repository.signOut(allDevices: allDevices);
    await _clearProviderSession();
    state = const AuthState(stage: AuthStage.signedOut);
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
      if (!error.isOffline && error.code != ApiErrorCode.serviceUnavailable) {
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
    await _adoptSession(
      result.session,
      verificationEmail: email.trim(),
      verificationSent: result.verificationSent,
    );
  });

  Future<bool> signInWithProvider(SignInProvider provider) =>
      _authAction(() async {
        final token = await _providerSignIn.identityToken(provider);
        await _adoptSession(
          await _repository.signInWithProvider(provider, token),
        );
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
    unawaited(_invalidationSub.cancel());
    super.dispose();
  }
}
