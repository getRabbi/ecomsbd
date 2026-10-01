import 'dart:convert';

import 'package:ecomsbd/core/api/api_error.dart';
import 'package:ecomsbd/data/auth/provider_sign_in.dart';
import 'package:ecomsbd/features/auth/auth_error.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

/// An unsigned JWT-shaped token carrying [claims]. Only its payload is read.
String _token(Map<String, Object?> claims) {
  String part(Object value) =>
      base64Url.encode(utf8.encode(jsonEncode(value))).replaceAll('=', '');
  return '${part({'alg': 'none'})}.${part(claims)}.sig';
}

void main() {
  group('providerDiagnostic', () {
    test('names the provider and the failed step', () {
      expect(
        providerDiagnostic(
          SignInProvider.google,
          ProviderSignInFailure.authorizationFailed,
          reasons: <Object?>['canceled'],
        ),
        'GOOGLE_AUTHORIZATION_FAILED canceled',
      );
    });

    test('keeps a status and machine codes, drops free text', () {
      final diagnostic = providerDiagnostic(
        SignInProvider.apple,
        ProviderSignInFailure.supabaseRejected,
        status: 400,
        reasons: <Object?>[
          'provider_disabled',
          'seller@example.com was refused',
          'Bearer eyJhbGciOi.payload.sig with spaces',
          null,
        ],
      );
      expect(diagnostic, 'APPLE_SUPABASE_REJECTED 400 provider_disabled');
      expect(diagnostic, isNot(contains('@')));
      expect(diagnostic, isNot(contains('Bearer')));
    });

    test('ignores a status that is not an HTTP status', () {
      expect(
        providerDiagnostic(
          SignInProvider.google,
          ProviderSignInFailure.supabaseRejected,
          status: 'not-a-status',
        ),
        'GOOGLE_SUPABASE_REJECTED',
      );
    });
  });

  group('providerSignInError', () {
    test('an ambiguous Android result is incomplete, not a cancellation', () {
      final error = providerSignInError(
        SignInProvider.google,
        outcome: ProviderSignInOutcome.incomplete,
        failure: ProviderSignInFailure.authorizationFailed,
        reason: 'canceled',
        detail: 'canceled: [16] Account reauth failed.',
      );
      expect(error.code, 'GOOGLE_INCOMPLETE');
      expect(error.diagnostic, 'GOOGLE_AUTHORIZATION_FAILED canceled');
      // The SDK sentence is kept for debugging only; it is not the diagnostic.
      expect(error.diagnostic, isNot(contains('reauth')));
      expect(
        authErrorDiagnostic(error),
        'GOOGLE_AUTHORIZATION_FAILED canceled',
      );
    });

    test('a real cancellation shows no diagnostic code', () {
      final error = providerSignInError(
        SignInProvider.apple,
        outcome: ProviderSignInOutcome.cancelled,
      );
      expect(error.code, 'APPLE_CANCELLED');
      expect(error.diagnostic, 'APPLE_CANCELLED');
      expect(authErrorDiagnostic(error), isNull);
    });

    test('defaults to an authorization failure when no step is named', () {
      final error = providerSignInError(SignInProvider.google);
      expect(error.code, 'GOOGLE_UNAVAILABLE');
      expect(error.diagnostic, 'GOOGLE_AUTHORIZATION_FAILED');
    });
  });

  group('providerProfileError', () {
    test('marks a profile failure after Supabase accepted the token', () {
      const error = ApiError(
        code: 'INTERNAL_ERROR',
        messageBn: '',
        messageEn: 'x',
        retryable: true,
        statusCode: 500,
      );
      final marked = providerProfileError(SignInProvider.google, error);
      expect(marked.code, 'INTERNAL_ERROR');
      expect(
        marked.diagnostic,
        'GOOGLE_PROFILE_FETCH_FAILED 500 INTERNAL_ERROR',
      );
    });

    test('keeps a diagnostic that is already there', () {
      final error = providerSignInError(SignInProvider.google);
      expect(
        providerProfileError(SignInProvider.google, error).diagnostic,
        error.diagnostic,
      );
    });
  });

  group('supabaseRejectionReason', () {
    test('prefers the error code Supabase sent', () {
      expect(
        supabaseRejectionReason(
          const AuthException('anything', code: 'provider_disabled'),
        ),
        'provider_disabled',
      );
    });

    test('classifies a message without keeping it', () {
      expect(
        supabaseRejectionReason(
          const AuthException('Unacceptable audience in id_token: [abc]'),
        ),
        'audience',
      );
      expect(
        supabaseRejectionReason(
          const AuthException('Provider (issuer x) is not enabled'),
        ),
        'provider_disabled',
      );
      expect(
        supabaseRejectionReason(const AuthException('Nonces mismatch')),
        'nonce',
      );
      expect(
        supabaseRejectionReason(const AuthException('something else')),
        'unknown',
      );
    });
  });

  group('identityTokenAudience', () {
    test('reads only a plain client id audience', () {
      const client = '939255107253-abc.apps.googleusercontent.com';
      expect(
        identityTokenAudience(
          _token(<String, Object?>{
            'aud': client,
            'email': 'seller@example.com',
            'sub': '1234567890',
          }),
        ),
        client,
      );
      expect(
        identityTokenAudience(
          _token(<String, Object?>{
            'aud': <String>[client],
          }),
        ),
        client,
      );
    });

    test('answers null for anything else', () {
      expect(identityTokenAudience('not-a-token'), isNull);
      expect(
        identityTokenAudience(
          _token(<String, Object?>{
            'aud': <String>['a', 'b'],
          }),
        ),
        isNull,
      );
      expect(
        identityTokenAudience(
          _token(<String, Object?>{'aud': 'has spaces in it'}),
        ),
        isNull,
      );
    });
  });
}
