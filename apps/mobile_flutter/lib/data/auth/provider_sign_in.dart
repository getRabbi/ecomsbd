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

ApiError providerSignInError(
  SignInProvider provider, {
  bool cancelled = false,
  String? detail,
}) => ApiError(
  code:
      '${provider.name.toUpperCase()}_${cancelled ? 'CANCELLED' : 'UNAVAILABLE'}',
  messageBn: '',
  // Only a genuine user dismissal says "cancelled". Anything else keeps the
  // provider's own words: a configuration rejection reported as a cancellation
  // sends everyone looking at the wrong thing.
  messageEn: cancelled
      ? 'Sign-in cancelled.'
      : detail == null || detail.isEmpty
      ? 'Sign-in unavailable.'
      : 'Google sign-in failed: $detail',
  retryable: true,
  details: detail == null ? null : <String, dynamic>{'provider_error': detail},
);

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
      final cancelled = error.code == GoogleSignInExceptionCode.canceled;
      throw providerSignInError(
        provider,
        cancelled: cancelled,
        detail: cancelled
            ? null
            : '${error.code.name}: ${error.description ?? ''}'.trim(),
      );
    } on SignInWithAppleAuthorizationException catch (error) {
      throw providerSignInError(
        provider,
        cancelled: error.code == AuthorizationErrorCode.canceled,
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
