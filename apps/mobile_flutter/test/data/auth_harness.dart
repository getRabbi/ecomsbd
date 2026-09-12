import 'package:dio/dio.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/core/api/api_error.dart';
import 'package:ecomsbd/core/storage/token_store.dart';
import 'package:ecomsbd/data/auth/auth_controller.dart';
import 'package:ecomsbd/data/auth/auth_repository.dart';
import 'package:ecomsbd/data/auth/provider_sign_in.dart';

import 'fake_api.dart';

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
    repository = AuthRepository(tokenStore: tokens);
    repository.client = ApiClient(
      sessionProvider: repository,
      dio: Dio()..httpClientAdapter = adapter,
    );
    controller = AuthController(repository, providerSignIn: provider);
    profile();
  }
  final tokens = MemoryTokens();
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
