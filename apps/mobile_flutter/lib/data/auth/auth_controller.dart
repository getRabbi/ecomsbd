import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:meta/meta.dart';

import '../../core/api/api_error.dart';
import 'auth_models.dart';
import 'auth_repository.dart';

/// Where the app is in the sign-in lifecycle.
///
/// This is what the router switches on, so the rule "which screen does a
/// seller see" lives in one enum rather than being re-derived per screen.
enum AuthStage {
  /// Reading the stored session on launch.
  restoring,

  /// No usable session.
  signedOut,

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
  });

  const AuthState.restoring() : this(stage: AuthStage.restoring);

  final AuthStage stage;
  final AccountProfile? profile;

  /// The last failure, for the screen to display. Cleared on the next action.
  final ApiError? error;

  final bool isBusy;

  bool get isSignedIn =>
      stage == AuthStage.ready || stage == AuthStage.needsOnboarding;

  String? get tenantId => profile?.tenantId;

  AuthState copyWith({
    AuthStage? stage,
    AccountProfile? profile,
    ApiError? error,
    bool clearError = false,
    bool? isBusy,
  }) => AuthState(
    stage: stage ?? this.stage,
    profile: profile ?? this.profile,
    error: clearError ? null : (error ?? this.error),
    isBusy: isBusy ?? this.isBusy,
  );
}

/// Owns the session lifecycle.
class AuthController extends StateNotifier<AuthState> {
  AuthController(this._repository) : super(const AuthState.restoring()) {
    _invalidationSub = _repository.onSessionInvalid.listen(_onInvalidated);
  }

  final AuthRepository _repository;
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
      state = AuthState(
        stage: profile.needsOnboarding
            ? AuthStage.needsOnboarding
            : AuthStage.ready,
        profile: profile,
      );
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
      await _adoptSession(await _repository.createShop(payload));
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
    state = const AuthState(stage: AuthStage.signedOut);
  }

  void clearError() {
    if (state.error != null) {
      state = state.copyWith(clearError: true);
    }
  }

  Future<void> _adoptSession(SessionEnvelope envelope) async {
    // The profile is re-fetched rather than derived from the envelope so the
    // permission list and shop state come from a single server-owned source.
    try {
      final profile = await _repository.fetchProfile();
      state = AuthState(
        stage: profile.needsOnboarding
            ? AuthStage.needsOnboarding
            : AuthStage.ready,
        profile: profile,
      );
    } on ApiError {
      state = AuthState(
        stage: envelope.needsOnboarding
            ? AuthStage.needsOnboarding
            : AuthStage.ready,
      );
    }
  }

  void _onInvalidated(ApiError error) {
    state = AuthState(stage: AuthStage.signedOut, error: error);
  }

  @override
  void dispose() {
    unawaited(_invalidationSub.cancel());
    super.dispose();
  }
}
