import 'package:ecomsbd/app/router.dart';
import 'package:ecomsbd/core/api/api_error.dart';
import 'package:ecomsbd/data/auth/auth_controller.dart';
import 'package:ecomsbd/data/auth/provider_sign_in.dart';
import 'package:ecomsbd/features/auth/auth_error.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:package_info_plus/package_info_plus.dart';

import 'auth_harness.dart';
import 'fake_api.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late AuthHarness h;
  setUp(() async {
    PackageInfo.setMockInitialValues(
      appName: 'ecomsbd',
      packageName: 'test.ecomsbd',
      version: '1.0.0',
      buildNumber: '1',
      buildSignature: '',
    );
    h = AuthHarness();
    await h.controller.restore();
  });
  tearDown(() => h.dispose());

  test(
    'stored email session restores through /me and revoked sessions return to login',
    () async {
      h.adapter.onJson('POST', '/auth/login', sessionReply());
      await h.controller.login('seller@example.com', 'secret phrase');
      final restored = AuthController(h.repository, providerSignIn: h.provider);
      addTearDown(restored.dispose);
      await restored.restore();
      expect(restored.state.stage, AuthStage.ready);
      h.adapter.on(
        'GET',
        '/me',
        (_) => const FakeReply({'code': 'SESSION_REVOKED'}, statusCode: 401),
      );
      await restored.restore();
      expect(restored.state.stage, AuthStage.signedOut);
      expect(h.tokens.session, isNull);
      expect(h.adapter.to('POST', '/auth/refresh'), isEmpty);
    },
  );

  test(
    'email login uses backend tokens, trims email only and opens existing shop',
    () async {
      h.adapter.onJson('POST', '/auth/login', sessionReply());
      h.profile(
        needsOnboarding: true,
      ); // Existing shop must not be created again.
      expect(
        await h.controller.login(' seller@example.com ', ' secret phrase '),
        isTrue,
      );
      final body = h.adapter.to('POST', '/auth/login').single.jsonBody;
      expect(body['email'], 'seller@example.com');
      expect(body['password'], ' secret phrase ');
      expect((body['device'] as Map)['install_id'], 'install-test');
      expect(h.tokens.session?.accessToken, 'backend-access');
      expect(h.tokens.session?.refreshToken, 'backend-refresh');
      expect(h.controller.state.profile?.maskedPhone, isNull);
      expect(h.controller.state.stage, AuthStage.ready);
      expect(authRedirect(h.controller.state, Routes.login), Routes.home);
    },
  );

  test(
    'register gates verification then enters the existing no-shop onboarding',
    () async {
      h.profile(tenantId: null, needsOnboarding: true);
      h.adapter.onJson('POST', '/auth/register', {
        'session': sessionReply(tenantId: null),
        'email_verification_sent': false,
      });
      expect(
        await h.controller.register('new@example.com', 'new secret phrase'),
        isTrue,
      );
      expect(h.controller.state.verificationSent, isFalse);
      expect(
        authRedirect(h.controller.state, Routes.register),
        Routes.verificationPending,
      );
      h.adapter.onJson('POST', '/auth/email/resend', {'ok': true});
      expect(await h.controller.resendVerification('new@example.com'), isTrue);
      expect(h.adapter.to('POST', '/auth/email/resend').single.jsonBody, {
        'email': 'new@example.com',
      });
      h.controller.continueAfterVerification();
      expect(authRedirect(h.controller.state, Routes.login), Routes.onboarding);
    },
  );

  for (final provider in SignInProvider.values) {
    for (final hasShop in [true, false]) {
      test(
        '${provider.name} submits identity token to backend; hasShop=$hasShop',
        () async {
          final tenant = hasShop ? 'shop-1' : null;
          h.profile(tenantId: tenant, needsOnboarding: !hasShop);
          h.adapter.onJson(
            'POST',
            '/auth/oauth/${provider.name}',
            sessionReply(tenantId: tenant),
          );
          expect(await h.controller.signInWithProvider(provider), isTrue);
          expect(h.provider.requested, [provider]);
          final body = h.adapter
              .to('POST', '/auth/oauth/${provider.name}')
              .single
              .jsonBody;
          expect(body.keys, unorderedEquals(['id_token', 'device']));
          expect(body['id_token'], '${provider.name}-identity-token');
          expect(h.tokens.session?.accessToken, 'backend-access');
          expect(
            h.controller.state.stage,
            hasShop ? AuthStage.ready : AuthStage.needsOnboarding,
          );
        },
      );
    }
    test(
      '${provider.name} cancellation never creates a backend session',
      () async {
        h.provider.error = providerSignInError(provider, cancelled: true);
        expect(await h.controller.signInWithProvider(provider), isFalse);
        expect(h.adapter.requests, isEmpty);
        expect(h.tokens.session, isNull);
        expect(h.controller.state.stage, AuthStage.signedOut);
        expect(
          authErrorMessage(h.controller.state.error!),
          contains('cancelled'),
        );
      },
    );
    test(
      '${provider.name} client success is insufficient if backend refuses identity',
      () async {
        h.fail(
          '/auth/oauth/${provider.name}',
          'IDENTITY_LINK_REFUSED',
          status: 409,
        );
        expect(await h.controller.signInWithProvider(provider), isFalse);
        expect(h.tokens.session, isNull);
        expect(h.controller.state.stage, AuthStage.signedOut);
        expect(
          authErrorMessage(h.controller.state.error!),
          contains('original sign-in method'),
        );
      },
    );
  }

  test(
    'wrong password and offline failure remain signed out with safe copy',
    () async {
      h.fail('/auth/login', 'INVALID_CREDENTIALS');
      expect(await h.controller.login('seller@example.com', 'wrong'), isFalse);
      expect(
        authErrorMessage(h.controller.state.error!),
        contains('do not match'),
      );
      expect(
        authErrorMessage(h.controller.state.error!),
        isNot(contains('private')),
      );
      h.adapter.offline = true;
      expect(
        await h.controller.login('seller@example.com', 'secret phrase'),
        isFalse,
      );
      expect(
        authErrorMessage(h.controller.state.error!),
        contains('No internet'),
      );
      expect(h.tokens.session, isNull);
    },
  );

  test(
    'email verification required routes to pending without a session',
    () async {
      h.fail('/auth/login', 'EMAIL_NOT_VERIFIED', status: 403);
      expect(
        await h.controller.login('seller@example.com', 'secret phrase'),
        isFalse,
      );
      expect(h.controller.state.isSignedIn, isFalse);
      expect(
        authRedirect(h.controller.state, Routes.login),
        Routes.verificationPending,
      );
      h.adapter.onJson('POST', '/auth/email/verify', {'ok': true});
      expect(await h.controller.verifyEmail('email-link-token'), isTrue);
      expect(h.adapter.to('POST', '/auth/email/verify').single.jsonBody, {
        'token': 'email-link-token',
      });
      expect(h.controller.state.verificationEmail, isNull);
    },
  );

  test(
    'reset sends the link token and clears the existing session even if SDK cleanup fails',
    () async {
      h.adapter.onJson('POST', '/auth/login', sessionReply());
      await h.controller.login('seller@example.com', 'old secret phrase');
      h.adapter.onJson('POST', '/auth/password/forgot', {'ok': true});
      expect(await h.controller.forgotPassword(' seller@example.com '), isTrue);
      expect(h.adapter.to('POST', '/auth/password/forgot').single.jsonBody, {
        'email': 'seller@example.com',
      });
      h.adapter.onJson('POST', '/auth/password/reset', {'ok': true});
      h.provider.failCleanup = true;
      expect(
        await h.controller.resetPassword(
          'reset-link-token',
          'new secret phrase',
        ),
        isTrue,
      );
      expect(h.adapter.to('POST', '/auth/password/reset').single.jsonBody, {
        'token': 'reset-link-token',
        'new_password': 'new secret phrase',
      });
      expect(h.tokens.session, isNull);
      expect(await h.repository.currentSession(), isNull);
      expect(h.controller.state.stage, AuthStage.signedOut);
      expect(h.provider.signOuts, 1);
    },
  );

  for (final code in ['INVALID_TOKEN', 'TOKEN_EXPIRED']) {
    test(
      'public $code cannot refresh or invalidate the active app session',
      () async {
        h.adapter.onJson('POST', '/auth/login', sessionReply());
        await h.controller.login('seller@example.com', 'secret phrase');
        h.fail('/auth/email/verify', code);
        expect(await h.controller.verifyEmail('bad-link'), isFalse);
        expect(h.adapter.to('POST', '/auth/refresh'), isEmpty);
        expect(h.adapter.to('POST', '/auth/email/verify'), hasLength(1));
        expect(h.tokens.session?.accessToken, 'backend-access');
        expect(h.controller.state.stage, AuthStage.ready);
      },
    );
  }

  test(
    'shared refresh rotates tokens, logout revokes and clears locally offline',
    () async {
      h.adapter.onJson('POST', '/auth/login', sessionReply());
      await h.controller.login('seller@example.com', 'secret phrase');
      h.adapter.onJson(
        'POST',
        '/auth/refresh',
        sessionReply(
          accessToken: 'rotated-access',
          refreshToken: 'rotated-refresh',
        ),
      );
      // Drive refresh through the authenticated API, not a second auth client.
      var first = true;
      h.adapter.on('GET', '/protected', (_) {
        if (first) {
          first = false;
          return const FakeReply({'code': 'TOKEN_EXPIRED'}, statusCode: 401);
        }
        return const FakeReply({'ok': true});
      });
      expect(await h.repository.api.get('/protected'), {'ok': true});
      expect(h.adapter.to('POST', '/auth/refresh').single.jsonBody, {
        'refresh_token': 'backend-refresh',
      });
      expect(h.tokens.session?.refreshToken, 'rotated-refresh');
      h.adapter.offline = true;
      h.provider.failCleanup = true;
      await h.controller.signOut();
      expect(h.adapter.to('POST', '/auth/logout').single.jsonBody, {
        'all_devices': false,
      });
      expect(h.tokens.session, isNull);
      expect(h.controller.state.stage, AuthStage.signedOut);
    },
  );

  test(
    'multiple shops use existing tenant selection, never Create Shop',
    () async {
      final shops = [
        {
          'id': 'shop-1',
          'name': 'My shop',
          'role': 'OWNER',
          'onboarding_complete': true,
        },
      ];
      h.profile(tenantId: null, tenants: shops);
      h.adapter.onJson(
        'POST',
        '/auth/login',
        sessionReply(tenantId: null, tenants: shops),
      );
      await h.controller.login('seller@example.com', 'secret phrase');
      expect(authRedirect(h.controller.state, Routes.login), Routes.selectShop);
      h.profile();
      h.adapter.onJson('POST', '/auth/select-tenant', sessionReply());
      expect(await h.controller.selectShop('shop-1'), isTrue);
      expect(h.adapter.to('POST', '/auth/select-tenant').single.jsonBody, {
        'tenant_id': 'shop-1',
      });
      expect(h.controller.state.stage, AuthStage.ready);
      expect(h.adapter.to('POST', '/tenants'), isEmpty);
    },
  );

  test(
    'new shop stays in onboarding until the existing completion call succeeds',
    () async {
      h.adapter.onJson('POST', '/tenants', sessionReply());
      h.profile(needsOnboarding: true);
      expect(await h.controller.createShop({'name': 'My shop'}), isTrue);
      expect(h.controller.state.stage, AuthStage.needsOnboarding);
      expect(h.controller.state.tenantId, 'shop-1');
      h.adapter.onJson('PATCH', '/tenant', {});
      expect(await h.controller.completeOnboarding(), isTrue);
      expect(h.adapter.to('PATCH', '/tenant').single.jsonBody, {
        'onboarding_step': 'COMPLETE',
      });
      expect(h.controller.state.stage, AuthStage.ready);
    },
  );

  test(
    'OTP routes hidden by default, retained for explicit future enablement',
    () {
      const signedOut = AuthState(stage: AuthStage.signedOut);
      for (final route in [Routes.phoneLogin, Routes.verify]) {
        expect(authRedirect(signedOut, route), Routes.login);
        expect(authRedirect(signedOut, route, phoneOtpEnabled: true), isNull);
      }
      for (final route in [
        Routes.login,
        Routes.register,
        Routes.forgotPassword,
        Routes.resetPassword,
        Routes.emailLink,
      ]) {
        expect(authRedirect(signedOut, route), isNull);
      }
    },
  );

  test(
    'Apple on Android returns a safe unavailable error without a platform call',
    () async {
      debugDefaultTargetPlatformOverride = TargetPlatform.android;
      addTearDown(() => debugDefaultTargetPlatformOverride = null);
      await expectLater(
        NativeProviderSignIn().identityToken(SignInProvider.apple),
        throwsA(
          isA<ApiError>().having(
            (error) => error.code,
            'code',
            'APPLE_UNAVAILABLE',
          ),
        ),
      );
    },
  );
}
