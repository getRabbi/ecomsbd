import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:ecomsbd/app/router.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/core/api/api_error.dart';
import 'package:ecomsbd/data/auth/auth_controller.dart';
import 'package:ecomsbd/data/auth/auth_repository.dart';
import 'package:ecomsbd/data/auth/provider_sign_in.dart';
import 'package:ecomsbd/data/auth/supabase_bootstrap.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:supabase_flutter/supabase_flutter.dart' as sb;

import 'fake_api.dart';
import 'supabase_fake.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late SupabaseTransport transport;
  late sb.GoTrueClient auth;
  late AuthRepository repository;
  late AuthController controller;
  late FakeApiAdapter api;
  late MemoryTokens tokens;

  setUp(() {
    transport = SupabaseTransport();
    auth = sb.GoTrueClient(
      url: 'https://project.supabase.co/auth/v1',
      headers: {'apikey': 'public-test-key'},
      httpClient: transport,
      autoRefreshToken: false,
      flowType: sb.AuthFlowType.pkce,
      asyncStorage: MemoryPkce(),
    );
    api = FakeApiAdapter();
    tokens = MemoryTokens();
    repository = AuthRepository(tokenStore: tokens, auth: auth);
    repository.client = ApiClient(
      sessionProvider: repository,
      dio: Dio()..httpClientAdapter = api,
    );
    api.onJson('GET', '/me', {
      'user_id': 'internal-user',
      'session_id': 'internal-session',
      'locale': 'bn',
      'tenant_id': 'existing-shop',
      'role': 'OWNER',
      'permissions': ['order.view'],
      'needs_onboarding': false,
      'tenants': [],
    });
    api.onJson('POST', '/auth/logout', {});
    api.onJson('POST', '/auth/device', {});
    controller = AuthController(
      repository,
      providerSignIn: ProviderCredential(),
    );
  });
  tearDown(() async {
    controller.dispose();
    repository.dispose();
    auth.dispose();
  });

  test(
    'email signup waits for confirmation; resend and recovery use Supabase',
    () async {
      expect(
        await controller.register(' seller@example.com ', 'strong password'),
        isTrue,
      );
      expect(controller.state.stage, AuthStage.signedOut);
      expect(controller.state.verificationEmail, 'seller@example.com');
      expect(auth.currentSession, isNull);
      expect(api.requests, isEmpty);
      await repository.resendVerification('seller@example.com');
      await repository.forgotPassword('seller@example.com');
      expect(
        transport.requests.map((r) => r.url.path),
        containsAll(['/auth/v1/signup', '/auth/v1/resend', '/auth/v1/recover']),
      );
      expect(
        transport.requests.first.url.queryParameters['redirect_to'],
        'com.smply.app://auth/callback',
      );
    },
  );

  test(
    'login uses Supabase access token and internal profile; restore and refresh stay in SDK',
    () async {
      expect(
        await controller.login('seller@example.com', 'strong password'),
        isTrue,
      );
      expect(controller.state.stage, AuthStage.ready);
      expect(controller.state.profile!.userId, 'internal-user');
      expect(
        (await repository.currentSession())!.accessToken,
        auth.currentSession!.accessToken,
      );
      expect((await repository.currentSession())!.refreshToken, isEmpty);
      expect(tokens.cleared, isTrue);
      await controller.restore();
      await repository.refreshSession();
      expect(
        transport.requests.last.url.queryParameters['grant_type'],
        'refresh_token',
      );
      expect(api.to('POST', '/auth/refresh'), isEmpty);
      await controller.signOut();
      expect(auth.currentSession, isNull);
      expect(controller.state.stage, AuthStage.signedOut);
      expect(transport.requests.last.url.path, '/auth/v1/logout');
    },
  );

  test(
    'saved Supabase session restores and no shop uses existing onboarding',
    () async {
      api.onJson('GET', '/me', {
        'user_id': 'internal-user',
        'session_id': 'internal-session',
        'locale': 'bn',
        'tenant_id': null,
        'role': null,
        'permissions': [],
        'needs_onboarding': true,
        'tenants': [],
      });
      await auth.setInitialSession(jsonEncode(transport.session));
      await controller.restore();
      expect(controller.state.stage, AuthStage.needsOnboarding);
      expect(authRedirect(controller.state, Routes.login), Routes.onboarding);
    },
  );

  for (final provider in SignInProvider.values) {
    test('${provider.name} native credential goes to Supabase', () async {
      expect(await controller.signInWithProvider(provider), isTrue);
      final request = transport.requests.single;
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      expect(request.url.queryParameters['grant_type'], 'id_token');
      expect(body['provider'], provider.name);
      expect(body['id_token'], 'native-${provider.name}-id-token');
      expect(body['nonce'], 'native-apple-nonce');
      expect(api.to('POST', '/auth/oauth/${provider.name}'), isEmpty);
    });
  }

  test(
    'validated PKCE recovery opens reset; URL token injection does not',
    () async {
      await repository.forgotPassword('seller@example.com');
      await repository.handleAuthLink(
        Uri.parse('com.smply.app://auth/callback?code=valid-code'),
      );
      await Future<void>.delayed(Duration.zero);
      expect(controller.state.stage, AuthStage.passwordRecovery);
      expect(authRedirect(controller.state, Routes.home), Routes.resetPassword);
      expect(
        await controller.resetPassword('sdk-recovery', 'new strong password'),
        isTrue,
      );
      expect(
        transport.requests.any(
          (r) => r.method == 'PUT' && r.url.path.endsWith('/user'),
        ),
        isTrue,
      );
      expect(auth.currentSession, isNull);
      await expectLater(
        repository.resetPassword('forged-token', 'password'),
        throwsA(isA<ApiError>()),
      );
      final before = transport.requests.length;
      await repository.handleAuthLink(
        Uri.parse('https://evil.example/auth/callback?code=bad'),
      );
      expect(transport.requests.length, before);
      expect(
        isAuthCallback(Uri.parse('com.smply.app://wrong/callback')),
        isFalse,
      );
    },
  );

  test(
    'unverified email state, safe errors and disabled phone login',
    () async {
      transport.failCode = 'email_not_confirmed';
      expect(await controller.login('seller@example.com', 'password'), isFalse);
      expect(controller.state.verificationEmail, 'seller@example.com');
      expect(controller.state.error!.messageEn, isNot(contains('private')));
      await expectLater(
        repository.requestOtp('01712345678'),
        throwsA(isA<ApiError>()),
      );
    },
  );
}
