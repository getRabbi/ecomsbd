import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../../core/env.dart';

const _storage = FlutterSecureStorage(
  aOptions: AndroidOptions(encryptedSharedPreferences: true),
);

class SecureSupabaseStorage extends LocalStorage {
  const SecureSupabaseStorage();
  static const _key = 'ecomsbd.supabase.session.v1';
  @override
  Future<void> initialize() async {}
  @override
  Future<String?> accessToken() => _storage.read(key: _key);
  @override
  Future<bool> hasAccessToken() => _storage.containsKey(key: _key);
  @override
  Future<void> persistSession(String persistSessionString) =>
      _storage.write(key: _key, value: persistSessionString);
  @override
  Future<void> removePersistedSession() => _storage.delete(key: _key);
}

class SecurePkceStorage extends GotrueAsyncStorage {
  @override
  Future<String?> getItem({required String key}) =>
      _storage.read(key: 'supabase.pkce.$key');
  @override
  Future<void> setItem({required String key, required String value}) =>
      _storage.write(key: 'supabase.pkce.$key', value: value);
  @override
  Future<void> removeItem({required String key}) =>
      _storage.delete(key: 'supabase.pkce.$key');
}

bool isAuthCallback(Uri uri) =>
    uri.scheme == 'com.smply.app' &&
    uri.host == 'auth' &&
    uri.path == '/callback';

Future<void> initializeSupabaseAuth() async {
  if (!Env.supabaseUrl.startsWith('https://') || Env.supabaseAnonKey.isEmpty) {
    throw StateError(
      'SUPABASE_URL and SUPABASE_ANON_KEY are required public build settings.',
    );
  }
  await Supabase.initialize(
    url: Env.supabaseUrl,
    publishableKey: Env.supabaseAnonKey,
    debug: false,
    authOptions: FlutterAuthClientOptions(
      authFlowType: AuthFlowType.pkce,
      autoRefreshToken: true,
      localStorage: const SecureSupabaseStorage(),
      pkceAsyncStorage: SecurePkceStorage(),
      // Start link handling after AuthController subscribes, including cold starts.
      detectSessionInUri: false,
    ),
  );
}
