import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:meta/meta.dart';

/// A stored session.
@immutable
class StoredSession {
  const StoredSession({
    required this.accessToken,
    required this.refreshToken,
    required this.accessTokenExpiresAt,
    required this.sessionId,
    required this.userId,
    this.tenantId,
    this.role,
  });

  factory StoredSession.fromJson(Map<String, dynamic> json) => StoredSession(
    accessToken: json['access_token'] as String,
    refreshToken: json['refresh_token'] as String,
    accessTokenExpiresAt: DateTime.parse(
      json['access_token_expires_at'] as String,
    ),
    sessionId: json['session_id'] as String,
    userId: json['user_id'] as String,
    tenantId: json['tenant_id'] as String?,
    role: json['role'] as String?,
  );

  final String accessToken;
  final String refreshToken;
  final DateTime accessTokenExpiresAt;
  final String sessionId;
  final String userId;
  final String? tenantId;
  final String? role;

  bool get hasTenant => tenantId != null;

  /// Treated as expired slightly early, so a request is not sent with a token
  /// that dies in flight.
  bool get isAccessTokenExpired => DateTime.now().toUtc().isAfter(
    accessTokenExpiresAt.subtract(const Duration(seconds: 30)),
  );

  StoredSession copyWith({
    String? accessToken,
    String? refreshToken,
    DateTime? accessTokenExpiresAt,
    String? tenantId,
    String? role,
  }) => StoredSession(
    accessToken: accessToken ?? this.accessToken,
    refreshToken: refreshToken ?? this.refreshToken,
    accessTokenExpiresAt: accessTokenExpiresAt ?? this.accessTokenExpiresAt,
    sessionId: sessionId,
    userId: userId,
    tenantId: tenantId ?? this.tenantId,
    role: role ?? this.role,
  );

  Map<String, dynamic> toJson() => <String, dynamic>{
    'access_token': accessToken,
    'refresh_token': refreshToken,
    'access_token_expires_at': accessTokenExpiresAt.toIso8601String(),
    'session_id': sessionId,
    'user_id': userId,
    'tenant_id': tenantId,
    'role': role,
  };
}

/// Persists the session in Android's `EncryptedSharedPreferences`.
///
/// Tokens never touch plain `SharedPreferences` or the Drift database: a
/// refresh token is a bearer credential for a seller's money data, and the
/// local database is readable by anything that can read the app's files on a
/// rooted device.
class TokenStore {
  TokenStore({FlutterSecureStorage? storage})
    : _storage =
          storage ??
          const FlutterSecureStorage(
            aOptions: AndroidOptions(encryptedSharedPreferences: true),
          );

  static const String _sessionKey = 'ecomsbd.session.v1';
  static const String _installKey = 'ecomsbd.install_id.v1';

  final FlutterSecureStorage _storage;

  Future<StoredSession?> read() async {
    final raw = await _storage.read(key: _sessionKey);
    if (raw == null) {
      return null;
    }
    try {
      return StoredSession.fromJson(jsonDecode(raw) as Map<String, dynamic>);
    } on Object {
      // A malformed record is unusable and unrecoverable; drop it so the app
      // shows the login screen rather than crashing on every launch.
      await clear();
      return null;
    }
  }

  Future<void> write(StoredSession session) =>
      _storage.write(key: _sessionKey, value: jsonEncode(session.toJson()));

  Future<void> clear() => _storage.delete(key: _sessionKey);

  /// Stable installation id reported at sign-in.
  ///
  /// Generated once and kept here rather than derived from a hardware
  /// identifier, which Android restricts and which would follow the user
  /// across uninstalls.
  Future<String> installId(String Function() generate) async {
    final existing = await _storage.read(key: _installKey);
    if (existing != null && existing.isNotEmpty) {
      return existing;
    }
    final generated = generate();
    await _storage.write(key: _installKey, value: generated);
    return generated;
  }
}
