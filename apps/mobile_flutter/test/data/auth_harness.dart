import 'package:dio/dio.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/core/api/api_error.dart';
import 'package:ecomsbd/core/storage/token_store.dart';
import 'package:ecomsbd/data/auth/auth_controller.dart';
import 'package:ecomsbd/data/auth/auth_repository.dart';
import 'package:ecomsbd/data/auth/provider_sign_in.dart';

import 'fake_api.dart';
import 'supabase_fake.dart' show SupabaseTransport, MemoryPkce;
import 'package:supabase_flutter/supabase_flutter.dart' as sb;

class MemoryTokens extends TokenStore {
  StoredSession? session;
  @override
  Future<StoredSession?> read() async => session;
  @override
  Future<void> write(StoredSession value) async {
    session = value;
  }

  @override
  Future<void> clear() async {
    session = null;
  }

  @override
  Future<String> installId(String Function() generate) async => 'install-test';
}

class FakeProviderSignIn implements ProviderSignIn {
  @override
  bool get usesHostedAppleSignIn => false;
  @override
  String? get nonce => null;
  ApiError? error;
  bool failCleanup = false;
  final requested = <SignInProvider>[];
  int signOuts = 0;
  @override
  Future<String> identityToken(SignInProvider provider) async {
    requested.add(provider);
    if (error != null) throw error!;
    return '${provider.name}-identity-token';
  }

  @override
  Future<void> signOut() async {
    signOuts++;
    if (failCleanup) throw StateError('private provider details');
  }
}

class AuthHarness {
  AuthHarness() {
    auth = sb.GoTrueClient(
      url: 'https://project.supabase.co/auth/v1',
      httpClient: transport,
      autoRefreshToken: false,
      flowType: sb.AuthFlowType.pkce,
      asyncStorage: MemoryPkce(),
    );
    repository = AuthRepository(tokenStore: tokens, auth: auth);
    repository.client = ApiClient(
      sessionProvider: repository,
      dio: Dio()..httpClientAdapter = adapter,
    );
    controller = AuthController(repository, providerSignIn: provider);
    profile();
    adapter.onJson('POST', '/auth/device', {});
    adapter.onJson('POST', '/auth/logout', {});
  }
  final tokens = MemoryTokens();
  final transport = SupabaseTransport();
  late final sb.GoTrueClient auth;
  final adapter = FakeApiAdapter();
  final provider = FakeProviderSignIn();
  late final AuthRepository repository;
  late final AuthController controller;

  void profile({
    String? tenantId = 'shop-1',
    bool needsOnboarding = false,
    List<Map<String, dynamic>> tenants = const [],
  }) {
    adapter.onJson('GET', '/me', {
      'user_id': 'seller-1',
      'session_id': 'session-1',
      'masked_phone': null,
      'tenant_id': tenantId,
      'needs_onboarding': needsOnboarding,
      'tenants': tenants,
    });
  }

  void fail(String path, String code, {int status = 401}) {
    transport.failCode = code.toLowerCase();
    adapter.on(
      'POST',
      path,
      (_) => FakeReply({
        'code': code,
        'message_en': 'private server details',
        'message_bn': 'private translated details',
        'retryable': false,
      }, statusCode: status),
    );
  }

  void dispose() {
    if (controller.mounted) controller.dispose();
    repository.dispose();
    auth.dispose();
  }
}

Map<String, dynamic> sessionReply({
  String? tenantId = 'shop-1',
  String accessToken = 'backend-access',
  String refreshToken = 'backend-refresh',
  List<Map<String, dynamic>> tenants = const [],
}) => {
  'access_token': accessToken,
  'refresh_token': refreshToken,
  'expires_in_seconds': 3600,
  'session_id': 'session-1',
  'user_id': 'seller-1',
  'tenant_id': tenantId,
  'role': tenantId == null ? null : 'OWNER',
  'needs_onboarding': tenantId == null,
  'tenants': tenants,
};
