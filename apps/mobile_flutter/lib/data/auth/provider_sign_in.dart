import 'dart:convert';
import 'package:crypto/crypto.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:flutter/foundation.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:sign_in_with_apple/sign_in_with_apple.dart';

import '../../core/api/api_error.dart';
import '../../core/env.dart';

enum SignInProvider { google, apple }

/// Obtains provider credentials only. It cannot create an ecomsbd session.
abstract class ProviderSignIn {
  String? get nonce => null;
  bool get usesHostedAppleSignIn => false;
  Future<String> identityToken(SignInProvider provider);
  Future<void> signOut();
}

/// How a provider sign-in ended, as far as the app can honestly tell.
enum ProviderSignInOutcome {
  /// The person dismissed it, and the platform said so unambiguously.
  cancelled,

  /// It ended with no credential and the platform cannot say why.
  ///
  /// Android's Credential Manager reports a dismissal and a *rejected OAuth
  /// registration* as the same failure, so neither can be claimed. Rendering
  /// this as "you cancelled" is what sent the last investigation to the wrong
  /// place: GMS had actually answered "this android application is not
  /// registered to use OAuth2.0".
  incomplete,

  /// It failed for a reason the platform named.
  unavailable,
}

/// Builds the failure the UI will render.
///
/// The seller-facing wording is chosen from [ApiError.code] by
/// `authErrorMessage`, never from [detail]: an SDK string is for whoever is
/// debugging, not for a seller. [detail] is kept in `details` and printed in
/// debug builds so the real cause is still recoverable.
ApiError providerSignInError(
  SignInProvider provider, {
  ProviderSignInOutcome outcome = ProviderSignInOutcome.unavailable,
  String? detail,
}) {
  final String suffix = switch (outcome) {
    ProviderSignInOutcome.cancelled => 'CANCELLED',
    ProviderSignInOutcome.incomplete => 'INCOMPLETE',
    ProviderSignInOutcome.unavailable => 'UNAVAILABLE',
  };
  final hasDetail = detail != null && detail.isNotEmpty;
  assert(() {
    if (hasDetail) {
      debugPrint('${provider.name} sign-in failed [$suffix]: $detail');
    }
    return true;
  }());
  return ApiError(
    code: '${provider.name.toUpperCase()}_$suffix',
    messageBn: '',
    messageEn: '',
    retryable: true,
    details: hasDetail ? <String, dynamic>{'provider_error': detail} : null,
  );
}

class NativeProviderSignIn implements ProviderSignIn {
  static Future<void>? _googleInitialization;
  String? _nonce;
  @override
  String? get nonce => _nonce;

  @override
  bool get usesHostedAppleSignIn =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android;

  bool get _isApplePlatform =>
      !kIsWeb &&
      (defaultTargetPlatform == TargetPlatform.iOS ||
          defaultTargetPlatform == TargetPlatform.macOS);

  @override
  Future<String> identityToken(SignInProvider provider) async {
    try {
      _nonce = null;
      final String? token;
      if (provider == SignInProvider.google) {
        if (kIsWeb ||
            (!_isApplePlatform &&
                defaultTargetPlatform != TargetPlatform.android) ||
            Env.googleServerClientId.isEmpty) {
          throw providerSignInError(provider);
        }
        final google = GoogleSignIn.instance;
        try {
          await (_googleInitialization ??= google.initialize(
            serverClientId: Env.googleServerClientId,
            clientId: _isApplePlatform && Env.googleIosClientId.isNotEmpty
                ? Env.googleIosClientId
                : null,
          ));
        } on Object {
          _googleInitialization = null;
          rethrow;
        }
        if (!google.supportsAuthenticate()) {
          throw providerSignInError(provider);
        }
        final account = await google.authenticate();
        token = account.authentication.idToken;
      } else {
        // Android is handled by Supabase's hosted PKCE flow in AuthController.
        if (!_isApplePlatform || !await SignInWithApple.isAvailable()) {
          throw providerSignInError(provider);
        }
        _nonce = Supabase.instance.client.auth.generateRawNonce();
        final credential = await SignInWithApple.getAppleIDCredential(
          nonce: sha256.convert(utf8.encode(_nonce!)).toString(),
          scopes: const [AppleIDAuthorizationScopes.email],
        );
        token = credential.identityToken;
      }
      if (token == null || token.isEmpty) {
        throw providerSignInError(provider);
      }
      return token;
    } on GoogleSignInException catch (error) {
      throw providerSignInError(
        provider,
        // The enum is documented as non-exhaustive, so this keeps a fallback.
        outcome: switch (error.code) {
          GoogleSignInExceptionCode.canceled =>
            defaultTargetPlatform == TargetPlatform.android
                // Ambiguous on Android: dismissal and a rejected OAuth client
                // arrive identically. Do not call it a cancellation.
                ? ProviderSignInOutcome.incomplete
                : ProviderSignInOutcome.cancelled,
          _ => ProviderSignInOutcome.unavailable,
        },
        detail:
            '${error.code.name}: ${error.description ?? ''} '
                    '${error.details ?? ''}'
                .trim(),
      );
    } on SignInWithAppleAuthorizationException catch (error) {
      throw providerSignInError(
        provider,
        outcome: error.code == AuthorizationErrorCode.canceled
            ? ProviderSignInOutcome.cancelled
            : ProviderSignInOutcome.unavailable,
        detail: '${error.code.name}: ${error.message}',
      );
    } on ApiError {
      rethrow;
    } on Object {
      throw providerSignInError(provider);
    }
  }

  @override
  Future<void> signOut() async {
    if (_googleInitialization == null) return;
    try {
      await _googleInitialization;
      await GoogleSignIn.instance.signOut();
    } on Object {
      // Provider cleanup must not prevent local/backend session logout.
    }
  }
}
