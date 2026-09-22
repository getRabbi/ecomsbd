import 'dart:convert';
import 'package:ecomsbd/core/storage/token_store.dart';
import 'package:ecomsbd/data/auth/provider_sign_in.dart';
import 'package:http/http.dart' as http;
import 'package:supabase_flutter/supabase_flutter.dart' as sb;

class MemoryTokens extends TokenStore {
  bool cleared = false;
  @override
  Future<String> installId(String Function() generate) async => 'test-install';
  @override
  Future<void> clear() async {
    cleared = true;
  }
}

class MemoryPkce extends sb.GotrueAsyncStorage {
  final values = <String, String>{};
  @override
  Future<String?> getItem({required String key}) async => values[key];
  @override
  Future<void> setItem({required String key, required String value}) async {
    values[key] = value;
  }

  @override
  Future<void> removeItem({required String key}) async {
    values.remove(key);
  }
}

class ProviderCredential extends ProviderSignIn {
  @override
  String? get nonce => 'native-apple-nonce';
  @override
  Future<String> identityToken(SignInProvider provider) async =>
      'native-${provider.name}-id-token';
  @override
  Future<void> signOut() async {}
}

class SupabaseTransport extends http.BaseClient {
  final requests = <http.Request>[];
  String? failCode;
  Map<String, dynamic> get user => {
    'id': '00000000-0000-4000-8000-000000000001',
    'aud': 'authenticated',
    'email': 'seller@example.com',
    'app_metadata': <String, dynamic>{},
    'user_metadata': <String, dynamic>{},
    'created_at': '2026-09-13T00:00:00Z',
  };
  Map<String, dynamic> get session {
    final exp = DateTime.now().millisecondsSinceEpoch ~/ 1000 + 3600;
    String part(Object value) =>
        base64Url.encode(utf8.encode(jsonEncode(value))).replaceAll('=', '');
    return {
      'access_token':
          '${part({'alg': 'ES256'})}.${part({'exp': exp, 'sub': user['id']})}.test',
      'refresh_token': 'supabase-refresh-token',
      'expires_in': 3600,
      'token_type': 'bearer',
      'user': user,
    };
  }

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    final r = request as http.Request;
    requests.add(r);
    final error = failCode;
    failCode = null;
    Object body = {};
    if (error != null) {
      body = {
        'code': error,
        'error_code': error,
        'msg': 'private provider error',
      };
    } else if (r.url.path.endsWith('/token')) {
      body = session;
    } else if (r.url.path.endsWith('/signup')) {
      body = user;
    } else if (r.url.path.endsWith('/user')) {
      body = user;
    }
    return http.StreamedResponse(
      Stream.value(utf8.encode(jsonEncode(body))),
      error == null ? 200 : 400,
      headers: {'content-type': 'application/json'},
    );
  }
}
