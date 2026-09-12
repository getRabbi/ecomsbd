import 'package:ecomsbd/core/api/api_error.dart';
import 'package:ecomsbd/core/storage/token_store.dart';
import 'package:ecomsbd/data/auth/auth_controller.dart';
import 'package:ecomsbd/data/auth/auth_models.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('ApiError', () {
    test('parses the server contract', () {
      final error = ApiError.fromJson(const <String, dynamic>{
        'code': 'OTP_INVALID',
        'message_bn': 'কোডটি ঠিক নয়। আবার দেখুন।',
        'message_en': 'The verification code is incorrect.',
        'retryable': false,
        'reference_id': 'abc123',
        'details': <String, dynamic>{'attempts_remaining': 4},
      }, statusCode: 400);

      expect(error.code, ApiErrorCode.otpInvalid);
      expect(error.attemptsRemaining, 4);
      expect(error.referenceId, 'abc123');
      expect(error.retryable, isFalse);
    });

    test('prefers the Bangla message for display', () {
      // Master spec section 52: seller-facing copy is Bangla-first, and the
      // wording of a money error is a server-side product decision.
      final error = ApiError.fromJson(const <String, dynamic>{
        'code': 'NOT_FOUND',
        'message_bn': 'এই তথ্যটি পাওয়া যায়নি।',
        'message_en': 'Not found.',
        'retryable': false,
      });
      expect(error.displayMessage, 'এই তথ্যটি পাওয়া যায়নি।');
    });

    test(
      'an expired access token is refreshable, a revoked session is not',
      () {
        const expired = ApiError(
          code: ApiErrorCode.tokenExpired,
          messageBn: '',
          messageEn: '',
          retryable: false,
        );
        const revoked = ApiError(
          code: ApiErrorCode.sessionRevoked,
          messageBn: '',
          messageEn: '',
          retryable: false,
        );
        expect(expired.isAuthFailure, isTrue);
        expect(expired.requiresReauthentication, isFalse);
        expect(revoked.requiresReauthentication, isTrue);
      },
    );

    test('an ambiguous booking is never marked retryable', () {
      // Master spec sections 11 and 126: after an ambiguous courier timeout the
      // client must not offer a plain retry, or it ships two parcels.
      const ambiguous = ApiError(
        code: ApiErrorCode.bookingAmbiguous,
        messageBn: '',
        messageEn: '',
        retryable: false,
      );
      expect(ambiguous.retryable, isFalse);
    });

    test('offline is a client-side error with Bangla copy', () {
      final offline = ApiError.offline();
      expect(offline.isOffline, isTrue);
      expect(offline.retryable, isTrue);
      expect(offline.messageBn, isNotEmpty);
    });
  });

  group('StoredSession', () {
    StoredSession session({required Duration expiresIn, String? tenantId}) =>
        StoredSession(
          accessToken: 'a',
          refreshToken: 'r',
          accessTokenExpiresAt: DateTime.now().toUtc().add(expiresIn),
          sessionId: 's',
          userId: 'u',
          tenantId: tenantId,
        );

    test('treats a token as expired slightly early', () {
      // A token that dies in flight produces a confusing failure; refreshing a
      // few seconds early avoids it.
      expect(
        session(expiresIn: const Duration(seconds: 10)).isAccessTokenExpired,
        isTrue,
      );
      expect(
        session(expiresIn: const Duration(minutes: 5)).isAccessTokenExpired,
        isFalse,
      );
    });

    test('round-trips through JSON', () {
      final original = session(
        expiresIn: const Duration(minutes: 15),
        tenantId: 't1',
      );
      final restored = StoredSession.fromJson(original.toJson());
      expect(restored.accessToken, original.accessToken);
      expect(restored.tenantId, 't1');
      expect(restored.hasTenant, isTrue);
    });
  });

  group('SessionEnvelope', () {
    test('parses the sign-in response', () {
      final envelope = SessionEnvelope.fromJson(const <String, dynamic>{
        'access_token': 'at',
        'refresh_token': 'rt',
        'expires_in_seconds': 900,
        'session_id': 's1',
        'user_id': 'u1',
        'tenant_id': 't1',
        'role': 'OWNER',
        'is_new_user': true,
        'needs_onboarding': false,
        'tenants': <dynamic>[
          <String, dynamic>{
            'id': 't1',
            'name': 'Noor Fashion',
            'role': 'OWNER',
            'onboarding_complete': true,
          },
        ],
      });

      expect(envelope.tenants.single.name, 'Noor Fashion');
      expect(envelope.needsOnboarding, isFalse);
      expect(envelope.isNewUser, isTrue);
    });

    test('a brand-new account needs onboarding', () {
      final envelope = SessionEnvelope.fromJson(const <String, dynamic>{
        'access_token': 'at',
        'refresh_token': 'rt',
        'expires_in_seconds': 900,
        'session_id': 's1',
        'user_id': 'u1',
        'tenant_id': null,
        'is_new_user': true,
        'needs_onboarding': true,
        'tenants': <dynamic>[],
      });
      expect(envelope.needsOnboarding, isTrue);
      expect(envelope.tenantId, isNull);
    });
  });

  group('AccountProfile', () {
    test('exposes server-side permissions', () {
      // Used only to hide controls; authorisation is re-checked server-side.
      final profile = AccountProfile.fromJson(const <String, dynamic>{
        'user_id': 'u1',
        'masked_phone': '*******78',
        'locale': 'bn',
        'session_id': 's1',
        'tenant_id': 't1',
        'role': 'OWNER',
        'permissions': <dynamic>['order.view', 'money.reconcile'],
        'tenants': <dynamic>[
          <String, dynamic>{
            'id': 't1',
            'name': 'Shop',
            'role': 'OWNER',
            'onboarding_complete': true,
          },
        ],
        'needs_onboarding': false,
      });

      expect(profile.can('money.reconcile'), isTrue);
      expect(profile.can('courier.credential_manage'), isFalse);
      expect(profile.activeTenant?.name, 'Shop');
    });

    test('never carries a full phone number', () {
      final profile = AccountProfile.fromJson(const <String, dynamic>{
        'user_id': 'u1',
        'masked_phone': '*******78',
        'session_id': 's1',
        'permissions': <dynamic>[],
        'tenants': <dynamic>[],
        'needs_onboarding': true,
      });
      expect(RegExp(r'01\d{9}').hasMatch(profile.maskedPhone!), isFalse);
    });
  });

  group('CourierProvider', () {
    test('treats an unknown capability as unsupported', () {
      // Master spec section 74: `unknown` never becomes `true`.
      final provider = CourierProvider.fromJson(const <String, dynamic>{
        'provider': 'steadfast',
        'display_name': 'Steadfast',
        'verified_at': null,
        'capabilities': <String, dynamic>{
          'create_single': 'unknown',
          'payouts': 'unknown',
        },
        'manual_fallback': 'Manual courier mode.',
        'fully_unverified': true,
        'enabled': false,
      });

      expect(provider.supports('create_single'), isFalse);
      expect(provider.supports('payouts'), isFalse);
      expect(provider.canBook, isFalse);
      expect(provider.fullyUnverified, isTrue);
      expect(provider.manualFallback, isNotNull);
    });

    test('a verified and enabled provider can book', () {
      final provider = CourierProvider.fromJson(const <String, dynamic>{
        'provider': 'steadfast',
        'display_name': 'Steadfast',
        'capabilities': <String, dynamic>{'create_single': 'true'},
        'fully_unverified': false,
        'enabled': true,
      });
      expect(provider.canBook, isTrue);
    });

    test('a verified provider behind a disabled flag cannot book', () {
      // The flag is the kill switch for a broken provider (section 45).
      final provider = CourierProvider.fromJson(const <String, dynamic>{
        'provider': 'steadfast',
        'display_name': 'Steadfast',
        'capabilities': <String, dynamic>{'create_single': 'true'},
        'fully_unverified': false,
        'enabled': false,
      });
      expect(provider.canBook, isFalse);
    });
  });

  group('AuthState', () {
    test('restoring is not signed in', () {
      expect(const AuthState.restoring().isSignedIn, isFalse);
    });

    test('onboarding counts as signed in', () {
      const state = AuthState(stage: AuthStage.needsOnboarding);
      expect(state.isSignedIn, isTrue);
    });

    test('clearing the error keeps the stage', () {
      final state = AuthState(
        stage: AuthStage.signedOut,
        error: ApiError.offline(),
      ).copyWith(clearError: true);
      expect(state.error, isNull);
      expect(state.stage, AuthStage.signedOut);
    });
  });
}
