import 'package:meta/meta.dart';

/// A shop the signed-in user belongs to.
@immutable
class TenantSummary {
  const TenantSummary({
    required this.id,
    required this.name,
    required this.role,
    required this.onboardingComplete,
  });

  factory TenantSummary.fromJson(Map<String, dynamic> json) => TenantSummary(
    id: json['id'] as String,
    name: json['name'] as String,
    role: json['role'] as String,
    onboardingComplete: json['onboarding_complete'] as bool? ?? false,
  );

  final String id;
  final String name;
  final String role;
  final bool onboardingComplete;
}

/// Response to `POST /v1/auth/otp/request`.
@immutable
class OtpChallenge {
  const OtpChallenge({
    required this.challengeId,
    required this.maskedPhone,
    required this.expiresInSeconds,
    required this.resendAvailableInSeconds,
    this.debugCode,
  });

  factory OtpChallenge.fromJson(Map<String, dynamic> json) => OtpChallenge(
    challengeId: json['challenge_id'] as String,
    maskedPhone: json['masked_phone'] as String,
    expiresInSeconds: json['expires_in_seconds'] as int,
    resendAvailableInSeconds: json['resend_available_in_seconds'] as int,
    debugCode: json['debug_code'] as String?,
  );

  final String challengeId;

  /// Already masked by the server (`01712****78`).
  final String maskedPhone;

  final int expiresInSeconds;
  final int resendAvailableInSeconds;

  /// The code itself, present only when the server is running its
  /// development OTP provider. Always null in staging and production, and the
  /// UI treats it as a convenience, never as a supported flow.
  final String? debugCode;

  bool get hasDebugCode => debugCode != null && debugCode!.isNotEmpty;
}

/// Response to sign-in, refresh and shop creation.
@immutable
class SessionEnvelope {
  const SessionEnvelope({
    required this.accessToken,
    required this.refreshToken,
    required this.expiresInSeconds,
    required this.sessionId,
    required this.userId,
    required this.isNewUser,
    required this.needsOnboarding,
    required this.tenants,
    this.tenantId,
    this.role,
  });

  factory SessionEnvelope.fromJson(Map<String, dynamic> json) =>
      SessionEnvelope(
        accessToken: json['access_token'] as String,
        refreshToken: json['refresh_token'] as String,
        expiresInSeconds: json['expires_in_seconds'] as int,
        sessionId: json['session_id'] as String,
        userId: json['user_id'] as String,
        tenantId: json['tenant_id'] as String?,
        role: json['role'] as String?,
        isNewUser: json['is_new_user'] as bool? ?? false,
        needsOnboarding: json['needs_onboarding'] as bool? ?? true,
        tenants: <TenantSummary>[
          for (final item in (json['tenants'] as List<dynamic>? ?? <dynamic>[]))
            TenantSummary.fromJson(item as Map<String, dynamic>),
        ],
      );

  final String accessToken;
  final String refreshToken;
  final int expiresInSeconds;
  final String sessionId;
  final String userId;
  final String? tenantId;
  final String? role;
  final bool isNewUser;

  /// Computed by the server, not inferred by the client, so the routing rule
  /// lives in one place.
  final bool needsOnboarding;

  final List<TenantSummary> tenants;
}

/// Response to `GET /v1/me`.
@immutable
class AccountProfile {
  const AccountProfile({
    required this.userId,
    required this.maskedPhone,
    required this.locale,
    required this.sessionId,
    required this.permissions,
    required this.tenants,
    required this.needsOnboarding,
    this.displayName,
    this.tenantId,
    this.role,
  });

  factory AccountProfile.fromJson(Map<String, dynamic> json) => AccountProfile(
    userId: json['user_id'] as String,
    displayName: json['display_name'] as String?,
    maskedPhone: json['masked_phone'] as String?,
    locale: json['locale'] as String? ?? 'bn',
    sessionId: json['session_id'] as String,
    tenantId: json['tenant_id'] as String?,
    role: json['role'] as String?,
    permissions: <String>[
      for (final p in (json['permissions'] as List<dynamic>? ?? <dynamic>[]))
        p as String,
    ],
    tenants: <TenantSummary>[
      for (final item in (json['tenants'] as List<dynamic>? ?? <dynamic>[]))
        TenantSummary.fromJson(item as Map<String, dynamic>),
    ],
    needsOnboarding: json['needs_onboarding'] as bool? ?? true,
  );

  final String userId;
  final String? displayName;
  final String? maskedPhone;
  final String locale;
  final String sessionId;
  final String? tenantId;
  final String? role;

  /// Server-side permissions for the active role.
  ///
  /// Used to hide controls the seller cannot use. Authorisation itself is
  /// always re-checked on the server (master spec section 88).
  final List<String> permissions;

  final List<TenantSummary> tenants;
  final bool needsOnboarding;

  TenantSummary? get activeTenant {
    for (final tenant in tenants) {
      if (tenant.id == tenantId) {
        return tenant;
      }
    }
    return null;
  }

  bool can(String permission) => permissions.contains(permission);
}

/// A courier provider and its verified capabilities.
@immutable
class CourierProvider {
  const CourierProvider({
    required this.provider,
    required this.displayName,
    required this.capabilities,
    required this.fullyUnverified,
    required this.enabled,
    this.verifiedAt,
    this.manualFallback,
  });

  factory CourierProvider.fromJson(Map<String, dynamic> json) =>
      CourierProvider(
        provider: json['provider'] as String,
        displayName: json['display_name'] as String,
        verifiedAt: json['verified_at'] as String?,
        capabilities: <String, String>{
          for (final entry
              in (json['capabilities'] as Map<String, dynamic>? ?? const {})
                  .entries)
            entry.key: entry.value as String,
        },
        manualFallback: json['manual_fallback'] as String?,
        fullyUnverified: json['fully_unverified'] as bool? ?? true,
        enabled: json['enabled'] as bool? ?? false,
      );

  final String provider;
  final String displayName;
  final String? verifiedAt;

  /// Capability name to `true` / `false` / `unknown`.
  final Map<String, String> capabilities;

  final String? manualFallback;

  /// True when nothing has been verified against real provider documentation.
  final bool fullyUnverified;

  final bool enabled;

  /// Only an explicitly verified capability may be offered in the UI.
  /// `unknown` is treated as unsupported (master spec section 74).
  bool supports(String capability) => capabilities[capability] == 'true';

  /// Whether the seller can actually book through this provider today.
  bool get canBook => enabled && supports('create_single');
}
