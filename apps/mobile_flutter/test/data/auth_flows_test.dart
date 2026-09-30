import 'dart:async';
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
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:supabase_flutter/supabase_flutter.dart' as sb;

import 'fake_api.dart';
import 'supabase_fake.dart';

const _shopProfile = <String, dynamic>{
  'user_id': 'internal-user',
  'session_id': 'internal-session',
  'locale': 'bn',
  'tenant_id': 'existing-shop',
  'role': 'OWNER',
  'permissions': ['order.view'],
  'needs_onboarding': false,
  'tenants': [],
};

FakeReply _error(int status, String code) => FakeReply(<String, dynamic>{
  'code': code,
  'message_bn': '',
  'message_en': 'server said $code',
  'retryable': status >= 500,
}, statusCode: status);

/// Lets a test drive the Supabase auth stream directly.
class _StreamedAuthRepository extends AuthRepository {
  _StreamedAuthRepository({required super.tokenStore, super.auth});

  final changes = StreamController<sb.AuthState>.broadcast();

  @override
  Stream<sb.AuthState> get authChanges => changes.stream;
}

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
    api.onJson('GET', '/me', _shopProfile);
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
        'com.ecomsbd.app://auth/callback',
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
    'a request revoked by signing out does not show a sign-in error',
    () async {
      expect(
        await controller.login('seller@example.com', 'strong password'),
        isTrue,
      );
      await controller.signOut();
      // Home had a request in flight; it comes back 401 once logout revokes it.
      await repository.onSessionInvalidated(
        const ApiError(
          code: 'SESSION_REVOKED',
          messageBn: '',
          messageEn: 'Session revoked.',
          retryable: false,
        ),
      );
      await pumpEventQueue();
      expect(controller.state.stage, AuthStage.signedOut);
      expect(controller.state.error, isNull);
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

  for (final callbackDuringLaunch in [false, true]) {
    test(
      'Android Apple PKCE callback during launch: $callbackDuringLaunch',
      () async {
        debugDefaultTargetPlatformOverride = TargetPlatform.android;
        const channel = MethodChannel('plugins.flutter.io/url_launcher');
        addTearDown(() {
          debugDefaultTargetPlatformOverride = null;
          TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
              .setMockMethodCallHandler(channel, null);
        });
        controller.dispose();
        controller = AuthController(repository);
        await controller.restore();
        Uri? launched;
        final callback = Uri.parse(
          'com.ecomsbd.app://auth/callback?code=apple-code',
        );
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
            .setMockMethodCallHandler(channel, (call) async {
              expect(call.method, 'launch');
              final args = call.arguments as Map;
              launched = Uri.parse(args['url'] as String);
              expect(args['useWebView'], isFalse);
              if (callbackDuringLaunch) {
                await repository.handleAuthLink(callback);
              }
              return true;
            });

        expect(
          await controller.signInWithProvider(SignInProvider.apple),
          isTrue,
        );
        expect(launched!.host, 'project.supabase.co');
        expect(launched!.queryParameters['provider'], 'apple');
        expect(
          launched!.queryParameters['redirect_to'],
          'com.ecomsbd.app://auth/callback',
        );
        expect(launched!.queryParameters['code_challenge'], isNotEmpty);
        expect(launched!.queryParameters['code_challenge_method'], 's256');
        if (!callbackDuringLaunch) {
          expect(controller.state.stage, AuthStage.signedOut);
          expect(controller.state.isBusy, isFalse);
          expect(auth.currentSession, isNull);
          expect(api.requests, isEmpty);
          final restored = Completer<void>();
          final removeListener = controller.addListener((state) {
            if (state.stage == AuthStage.ready && !restored.isCompleted) {
              restored.complete();
            }
          }, fireImmediately: false);
          addTearDown(removeListener);
          await repository.handleAuthLink(callback);
          await restored.future.timeout(const Duration(seconds: 5));
        }
        expect(controller.state.stage, AuthStage.ready);
        final exchange = jsonDecode(transport.requests.single.body) as Map;
        expect(exchange['auth_code'], 'apple-code');
        expect(exchange['code_verifier'], isNotEmpty);
        expect(api.to('POST', '/auth/oauth/apple'), isEmpty);
      },
    );
  }

  test(
    'validated PKCE recovery opens reset; URL token injection does not',
    () async {
      await repository.forgotPassword('seller@example.com');
      await repository.handleAuthLink(
        Uri.parse('com.ecomsbd.app://auth/callback?code=valid-code'),
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
      // A callback intended for the old installation must not exchange a code.
      await repository.handleAuthLink(
        Uri.parse('com.smply.app://auth/callback?code=old-install'),
      );
      expect(transport.requests.length, before);
      expect(
        isAuthCallback(Uri.parse('com.ecomsbd.app://wrong/callback')),
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

  group('restore when the server cannot answer', () {
    // A launch with a saved session, retrying at once so a test can watch it.
    Future<void> launch() async {
      controller.dispose();
      controller = AuthController(
        repository,
        providerSignIn: ProviderCredential(),
        restoreRetryDelay: (_) => Duration.zero,
      );
      await auth.setInitialSession(jsonEncode(transport.session));
    }

    Future<void> reaches(AuthStage stage) async {
      if (controller.state.stage == stage) return;
      final reached = Completer<void>();
      final remove = controller.addListener((state) {
        if (state.stage == stage && !reached.isCompleted) reached.complete();
      }, fireImmediately: false);
      try {
        await reached.future.timeout(const Duration(seconds: 5));
      } finally {
        remove();
      }
    }

    // Kept, but not guessed into onboarding: the splash waits, then Home.
    Future<void> expectKeptThenRecovers() async {
      expect(auth.currentSession, isNotNull);
      expect(controller.state.stage, AuthStage.restoring);
      expect(controller.state.error, isNotNull);
      expect(authRedirect(controller.state, Routes.home), Routes.splash);
      api.offline = false;
      api.onJson('GET', '/me', _shopProfile);
      await reaches(AuthStage.ready);
      expect(controller.state.profile!.tenantId, 'existing-shop');
      expect(auth.currentSession, isNotNull);
    }

    test('valid session + 500 keeps the session and retries', () async {
      await launch();
      api.on('GET', '/me', (_) => _error(500, 'INTERNAL_ERROR'));
      await controller.restore();
      expect(controller.state.error!.statusCode, 500);
      await expectKeptThenRecovers();
    });

    test('valid session + no connection keeps the session', () async {
      await launch();
      api.offline = true;
      await controller.restore();
      expect(controller.state.error!.isOffline, isTrue);
      await expectKeptThenRecovers();
    });

    test('valid session + timeout keeps the session', () async {
      await launch();
      api.on(
        'GET',
        '/me',
        (_) => throw DioException(
          requestOptions: RequestOptions(path: '/me'),
          type: DioExceptionType.receiveTimeout,
        ),
      );
      await controller.restore();
      expect(controller.state.error!.code, ApiErrorCode.timeout);
      expect(controller.state.error!.retryable, isTrue);
      await expectKeptThenRecovers();
    });

    test(
      'valid session + temporary backend error (503) keeps the session',
      () async {
        await launch();
        api.on('GET', '/me', (_) => _error(503, 'SERVICE_UNAVAILABLE'));
        await controller.restore();
        await expectKeptThenRecovers();
      },
    );

    test(
      'a failed device attach after /me confirmed the session opens Home',
      () async {
        await launch();
        api.on('POST', '/auth/device', (_) => _error(500, 'INTERNAL_ERROR'));
        await controller.restore();
        expect(controller.state.stage, AuthStage.ready);
        expect(controller.state.profile!.tenantId, 'existing-shop');
        expect(auth.currentSession, isNotNull);
      },
    );

    test('the splash does not wait for the device attach', () async {
      await launch();
      final held = Completer<FakeReply>();
      api.on('POST', '/auth/device', (_) => held.future);
      await controller.restore();
      expect(controller.state.stage, AuthStage.ready);
      await pumpEventQueue();
      // Sent, and still unanswered: Home is already open.
      expect(api.to('POST', '/auth/device'), hasLength(1));
      held.complete(const FakeReply(<String, dynamic>{}));
      await pumpEventQueue();
      // One request attaches the install; the push token rides along with it
      // when Firebase has one, instead of a second call.
      expect(api.to('POST', '/auth/device'), hasLength(1));
      expect(
        api.to('POST', '/auth/device').single.jsonBody['install_id'],
        'test-install',
      );
    });

    for (final (platform, expected) in <(TargetPlatform, String)>[
      (TargetPlatform.android, 'ANDROID'),
      (TargetPlatform.iOS, 'IOS'),
    ]) {
      test('the device attaches as $expected on ${platform.name}', () async {
        debugDefaultTargetPlatformOverride = platform;
        addTearDown(() => debugDefaultTargetPlatformOverride = null);
        await launch();
        await controller.restore();
        await pumpEventQueue();
        expect(
          api.to('POST', '/auth/device').single.jsonBody['platform'],
          expected,
        );
      });
    }

    test('concurrent restores share one profile request', () async {
      await launch();
      await Future.wait(<Future<void>>[
        controller.restore(),
        controller.restore(),
      ]);
      expect(controller.state.stage, AuthStage.ready);
      expect(api.to('GET', '/me'), hasLength(1));
    });

    test('a seller already in Home stays there through a 500', () async {
      expect(
        await controller.login('seller@example.com', 'strong password'),
        isTrue,
      );
      api.on('GET', '/me', (_) => _error(500, 'INTERNAL_ERROR'));
      await controller.restore();
      expect(controller.state.stage, AuthStage.ready);
      expect(controller.state.profile!.tenantId, 'existing-shop');
      expect(auth.currentSession, isNotNull);
    });

    for (final (status, code) in [
      (401, 'INVALID_TOKEN'),
      (401, 'SESSION_REVOKED'),
      (403, 'FORBIDDEN'),
    ]) {
      test('$status $code clears the local session', () async {
        await launch();
        api.on('GET', '/me', (_) => _error(status, code));
        await controller.restore();
        expect(controller.state.stage, AuthStage.signedOut);
        expect(controller.state.error!.code, code);
        expect(auth.currentSession, isNull);
        await pumpEventQueue();
        expect(api.to('GET', '/me'), hasLength(1));
      });
    }

    test('signing in with no connection says so', () async {
      transport.unreachable = true;
      expect(
        await controller.login('seller@example.com', 'strong password'),
        isFalse,
      );
      expect(controller.state.error!.code, ApiErrorCode.offline);
      expect(auth.currentSession, isNull);
    });

    test('a Supabase refresh that cannot connect is not a sign-out', () async {
      final streamed = _StreamedAuthRepository(tokenStore: tokens, auth: auth);
      streamed.client = ApiClient(
        sessionProvider: streamed,
        dio: Dio()..httpClientAdapter = api,
      );
      final seller = AuthController(
        streamed,
        providerSignIn: ProviderCredential(),
      );
      addTearDown(() async {
        seller.dispose();
        streamed.dispose();
        await streamed.changes.close();
      });
      expect(
        await seller.login('seller@example.com', 'strong password'),
        isTrue,
      );
      streamed.changes.addError(sb.AuthRetryableFetchException());
      await pumpEventQueue();
      expect(seller.state.stage, AuthStage.ready);

      streamed.changes.addError(const sb.AuthException('bad callback'));
      await pumpEventQueue();
      expect(seller.state.stage, AuthStage.signedOut);
    });
  });
}
