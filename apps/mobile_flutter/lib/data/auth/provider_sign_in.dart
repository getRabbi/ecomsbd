import 'dart:convert';
import 'package:crypto/crypto.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
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

/// The step at which a provider sign-in failed.
///
/// This is for diagnosis, not for the seller: the wording they see is chosen
/// from [ApiError.code]. It becomes the error's [ApiError.diagnostic], such as
/// `APPLE_SUPABASE_REJECTED 400 provider_disabled`, which survives a release
/// build. The SDK's own message does not: it is printed from an `assert`, and
/// TestFlight builds strip those, which is how the Apple failure in 3.0.3 (1)
/// left nothing to read.
enum ProviderSignInFailure {
  /// The platform offers no sign-in with this provider here.
  notAvailable('NOT_AVAILABLE'),

  /// The person dismissed the provider's sheet.
  cancelled('CANCELLED'),

  /// The provider's sheet failed, or answered with an error.
  authorizationFailed('AUTHORIZATION_FAILED'),

  /// The provider answered without an identity token.
  idTokenMissing('ID_TOKEN_MISSING'),

  /// Supabase did not accept the identity token.
  supabaseRejected('SUPABASE_REJECTED'),

  /// Supabase signed the person in, but their ecomsbd profile did not load.
  profileFetchFailed('PROFILE_FETCH_FAILED');

  const ProviderSignInFailure(this.code);

  final String code;
}

/// A diagnostic safe to show and log: `APPLE_SUPABASE_REJECTED 400 audience`.
///
/// Only [failure] is always present. [status] and each of [reasons] are kept
/// only if they look like a status or a machine code, so free text from an SDK
/// or a server (which could carry an address or a token) never gets in.
String providerDiagnostic(
  SignInProvider provider,
  ProviderSignInFailure failure, {
  Object? status,
  List<Object?> reasons = const <Object?>[],
}) => <String?>[
  '${provider.name.toUpperCase()}_${failure.code}',
  if (status != null && RegExp(r'^\d{3}$').hasMatch('$status')) '$status',
  for (final reason in reasons) _machineCode(reason),
].nonNulls.join(' ');

final RegExp _machineCodePattern = RegExp(r'^[A-Za-z0-9_.=-]{1,64}$');

String? _machineCode(Object? value) {
  final text = value?.toString();
  return text != null && _machineCodePattern.hasMatch(text) ? text : null;
}

/// Writes [diagnostic] to the device log, in release builds too.
///
/// Only ever called with a [providerDiagnostic], which holds no credentials.
void recordAuthDiagnostic(String diagnostic) =>
    debugPrint('ecomsbd auth diagnostic: $diagnostic');

/// Builds the failure the UI will render.
///
/// The seller-facing wording is chosen from [ApiError.code] by
/// `authErrorMessage`, never from [detail]: an SDK string is for whoever is
/// debugging, not for a seller. [detail] is kept in `details` and printed in
/// debug builds only. [failure] and [reason] become the error's
/// [ApiError.diagnostic], which is recorded in every build.
ApiError providerSignInError(
  SignInProvider provider, {
  ProviderSignInOutcome outcome = ProviderSignInOutcome.unavailable,
  ProviderSignInFailure? failure,
  Object? reason,
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
  final diagnostic = providerDiagnostic(
    provider,
    failure ??
        (outcome == ProviderSignInOutcome.cancelled
            ? ProviderSignInFailure.cancelled
            : ProviderSignInFailure.authorizationFailed),
    reasons: <Object?>[reason],
  );
  recordAuthDiagnostic(diagnostic);
  return ApiError(
    code: '${provider.name.toUpperCase()}_$suffix',
    messageBn: '',
    messageEn: '',
    retryable: true,
    details: <String, dynamic>{
      'diagnostic': diagnostic,
      if (hasDetail) 'provider_error': detail,
    },
  );
}

/// [error], from loading the profile once Supabase accepted a provider's
/// token, marked as that step. Its code, and so the seller's wording, is kept.
ApiError providerProfileError(SignInProvider provider, ApiError error) {
  if (error.diagnostic != null) return error;
  final diagnostic = providerDiagnostic(
    provider,
    ProviderSignInFailure.profileFetchFailed,
    status: error.statusCode,
    reasons: <Object?>[error.code],
  );
  recordAuthDiagnostic(diagnostic);
  return error.withDiagnostic(diagnostic);
}

/// Why Supabase refused a provider's identity token: the error code it sent,
/// or else the kind of refusal its message names. The message is not kept.
String supabaseRejectionReason(AuthException error) {
  final code = _machineCode(error.code);
  if (code != null) return code;
  final message = error.message.toLowerCase();
  if (message.contains('not enabled')) return 'provider_disabled';
  if (message.contains('audience')) return 'audience';
  if (message.contains('nonce')) return 'nonce';
  if (message.contains('id token') || message.contains('id_token')) {
    return 'bad_id_token';
  }
  return 'unknown';
}

/// The `aud` claim of an identity token, when it is a plain client id.
///
/// Read without verifying the token, for diagnosis only: it says which client
/// the provider minted the token for, which Supabase checks against its list
/// of client ids. Nothing else in the token is read.
String? identityTokenAudience(String token) {
  try {
    final parts = token.split('.');
    if (parts.length != 3) return null;
    final claims = jsonDecode(
      utf8.decode(base64Url.decode(base64Url.normalize(parts[1]))),
    );
    if (claims is! Map) return null;
    final audience = claims['aud'];
    final value = audience is List && audience.length == 1
        ? audience.single
        : audience;
    return value is String ? _machineCode(value) : null;
  } on Object {
    return null;
  }
}

class NativeProviderSignIn implements ProviderSignIn {
  NativeProviderSignIn({@visibleForTesting String Function()? rawNonce})
    : _rawNonce = rawNonce ?? _supabaseRawNonce;

  static String _supabaseRawNonce() =>
      Supabase.instance.client.auth.generateRawNonce();

  final String Function() _rawNonce;
  static Future<void>? _googleInitialization;

  /// The raw nonce bound into Google ID tokens on iOS.
  ///
  /// The iOS Google SDK always puts a nonce in the ID token, and Supabase
  /// rejects a token carrying a nonce it was not given. GoogleSignIn can be
  /// initialized only once per process, so one nonce serves that process;
  /// Supabase checks the token's nonce against the SHA-256 of this value.
  static String? _googleNonce;
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
          throw providerSignInError(
            provider,
            failure: ProviderSignInFailure.notAvailable,
          );
        }
        final google = GoogleSignIn.instance;
        try {
          await (_googleInitialization ??= () {
            // Android sends no nonce; Supabase then checks only the audience.
            _googleNonce = _isApplePlatform ? _rawNonce() : null;
            return google.initialize(
              serverClientId: Env.googleServerClientId,
              // Null falls back to GIDClientID in ios/Runner/Info.plist.
              clientId: _isApplePlatform && Env.googleIosClientId.isNotEmpty
                  ? Env.googleIosClientId
                  : null,
              nonce: _googleNonce == null ? null : _sha256(_googleNonce!),
            );
          }());
        } on Object {
          _googleInitialization = null;
          _googleNonce = null;
          rethrow;
        }
        if (!google.supportsAuthenticate()) {
          throw providerSignInError(
            provider,
            failure: ProviderSignInFailure.notAvailable,
          );
        }
        final account = await google.authenticate();
        token = account.authentication.idToken;
        _nonce = _googleNonce;
      } else {
        // Android is handled by Supabase's hosted PKCE flow in AuthController.
        if (!_isApplePlatform || !await SignInWithApple.isAvailable()) {
          throw providerSignInError(
            provider,
            failure: ProviderSignInFailure.notAvailable,
          );
        }
        // Apple copies the value it is given into the token; Supabase hashes
        // the raw nonce it is sent and compares the two.
        final rawNonce = _rawNonce();
        final credential = await SignInWithApple.getAppleIDCredential(
          nonce: _sha256(rawNonce),
          scopes: const [AppleIDAuthorizationScopes.email],
        );
        token = credential.identityToken;
        _nonce = rawNonce;
      }
      if (token == null || token.isEmpty) {
        throw providerSignInError(
          provider,
          failure: ProviderSignInFailure.idTokenMissing,
        );
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
        failure:
            error.code == GoogleSignInExceptionCode.canceled &&
                defaultTargetPlatform != TargetPlatform.android
            ? ProviderSignInFailure.cancelled
            : ProviderSignInFailure.authorizationFailed,
        reason: error.code.name,
        detail:
            '${error.code.name}: ${error.description ?? ''} '
                    '${error.details ?? ''}'
                .trim(),
      );
    } on SignInWithAppleAuthorizationException catch (error) {
      final cancelled = error.code == AuthorizationErrorCode.canceled;
      throw providerSignInError(
        provider,
        outcome: cancelled
            ? ProviderSignInOutcome.cancelled
            : ProviderSignInOutcome.unavailable,
        failure: cancelled
            ? ProviderSignInFailure.cancelled
            : ProviderSignInFailure.authorizationFailed,
        // Apple's AuthorizationError; `unknown` is its error 1000.
        reason: error.code.name,
        detail: '${error.code.name}: ${error.message}',
      );
    } on SignInWithAppleNotSupportedException catch (error) {
      throw providerSignInError(
        provider,
        failure: ProviderSignInFailure.notAvailable,
        detail: error.message,
      );
    } on SignInWithAppleCredentialsException catch (error) {
      throw providerSignInError(
        provider,
        failure: ProviderSignInFailure.authorizationFailed,
        reason: 'credentials',
        detail: error.message,
      );
    } on PlatformException catch (error) {
      throw providerSignInError(
        provider,
        failure: ProviderSignInFailure.authorizationFailed,
        reason: error.code,
        detail: '${error.code}: ${error.message ?? ''}',
      );
    } on ApiError {
      rethrow;
    } on Object catch (error) {
      throw providerSignInError(
        provider,
        failure: ProviderSignInFailure.authorizationFailed,
        reason: error.runtimeType,
        detail: '$error',
      );
    }
  }

  /// What the provider embeds in the ID token; Supabase gets the raw value.
  static String _sha256(String rawNonce) =>
      sha256.convert(utf8.encode(rawNonce)).toString();

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
