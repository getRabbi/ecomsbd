import 'package:flutter/foundation.dart';

import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';

String _t(String key, [Map<String, Object?>? vars]) =>
    AppStrings(activeAppLocale).t(key, vars);

/// Team and role models.
///
/// Mirrors `backend/app/api/v1/team.py`. Two things are deliberately absent:
/// no field that could hold anyone's phone number in full, and no field that
/// could hold an invitation token — the backend has neither.

/// What someone may do in a shop.
///
/// The names are the server's. The *copy* lives here, because a role should be
/// explained by what it unlocks rather than by its name.
enum TeamRole {
  owner,
  manager,
  orderOperator,
  finance,
  viewer,
  unknown;

  static TeamRole parse(String? value) => switch (value) {
    'OWNER' => TeamRole.owner,
    'MANAGER' => TeamRole.manager,
    // V1's names for the two middle roles. A membership row or a token written
    // before the rename still says these, and a role that stopped resolving
    // would render as "unknown" with no permissions — a lockout in the UI.
    'ORDER_OPERATOR' || 'PACKER' => TeamRole.orderOperator,
    'FINANCE' || 'ACCOUNTANT' => TeamRole.finance,
    'VIEWER' => TeamRole.viewer,
    _ => TeamRole.unknown,
  };

  /// The value the server expects back.
  String get wireValue => switch (this) {
    TeamRole.owner => 'OWNER',
    TeamRole.manager => 'MANAGER',
    TeamRole.orderOperator => 'ORDER_OPERATOR',
    TeamRole.finance => 'FINANCE',
    TeamRole.viewer => 'VIEWER',
    TeamRole.unknown => 'VIEWER',
  };

  String get label => switch (this) {
    TeamRole.owner => _t('team.roleOwner'),
    TeamRole.manager => _t('team.roleManager'),
    TeamRole.orderOperator => _t('team.roleOrderOperator'),
    TeamRole.finance => _t('team.roleFinance'),
    TeamRole.viewer => _t('team.roleViewer'),
    TeamRole.unknown => _t('team.roleUnknown'),
  };

  /// One line on what this role is for.
  String get summary => switch (this) {
    TeamRole.owner => _t('team.roleOwnerSub'),
    TeamRole.manager => _t('team.roleManagerSub'),
    TeamRole.orderOperator => _t('team.roleOrderOperatorSub'),
    TeamRole.finance => _t('team.roleFinanceSub'),
    TeamRole.viewer => _t('team.roleViewerSub'),
    TeamRole.unknown => _t('team.roleUnknownSub'),
  };

  /// The roles a shop can assign, in the order they are offered.
  static const List<TeamRole> assignable = <TeamRole>[
    TeamRole.owner,
    TeamRole.manager,
    TeamRole.orderOperator,
    TeamRole.finance,
    TeamRole.viewer,
  ];
}

@immutable
class TeamMember {
  const TeamMember({
    required this.userId,
    required this.role,
    required this.isActive,
    required this.isSelf,
    required this.permissions,
    this.maskedPhone,
    this.displayName,
    this.joinedAt,
  });

  factory TeamMember.fromJson(Map<String, dynamic> json) {
    return TeamMember(
      userId: json['user_id'] as String,
      role: TeamRole.parse(json['role'] as String?),
      isActive: (json['is_active'] as bool?) ?? false,
      isSelf: (json['is_self'] as bool?) ?? false,
      permissions: <String>[
        for (final row
            in (json['permissions'] as List<dynamic>? ?? const <dynamic>[]))
          '$row',
      ],
      maskedPhone: json['masked_phone'] as String?,
      displayName: json['display_name'] as String?,
      joinedAt: DateTime.tryParse((json['joined_at'] as String?) ?? ''),
    );
  }

  final String userId;
  final TeamRole role;
  final bool isActive;

  /// Whether this row is the person looking at the screen. Used to stop them
  /// removing or demoting themselves by accident.
  final bool isSelf;

  final List<String> permissions;

  /// Masked. The full number is never sent to a client.
  final String? maskedPhone;
  final String? displayName;
  final DateTime? joinedAt;

  String get name => displayName ?? maskedPhone ?? _t('team.someone');
}

/// Where an invitation stands.
enum InvitationState {
  pending,
  accepted,
  revoked,
  expired,
  unknown;

  static InvitationState parse(String? value) => switch (value) {
    'PENDING' => InvitationState.pending,
    'ACCEPTED' => InvitationState.accepted,
    'REVOKED' => InvitationState.revoked,
    'EXPIRED' => InvitationState.expired,
    _ => InvitationState.unknown,
  };

  String get label => switch (this) {
    InvitationState.pending => _t('team.invitePending'),
    InvitationState.accepted => _t('team.inviteAccepted'),
    InvitationState.revoked => _t('team.inviteRevoked'),
    InvitationState.expired => _t('team.inviteExpired'),
    InvitationState.unknown => _t('team.inviteUnknown'),
  };

  bool get isOpen => this == InvitationState.pending;
}

/// An invitation as the shop sees it.
@immutable
class TeamInvitation {
  const TeamInvitation({
    required this.id,
    required this.role,
    required this.state,
    required this.phoneLast4,
    this.displayName,
    this.invitedAt,
    this.expiresAt,
  });

  factory TeamInvitation.fromJson(Map<String, dynamic> json) {
    return TeamInvitation(
      id: json['id'] as String,
      role: TeamRole.parse(json['role'] as String?),
      state: InvitationState.parse(json['status'] as String?),
      phoneLast4: (json['phone_last4'] as String?) ?? '',
      displayName: json['display_name'] as String?,
      invitedAt: DateTime.tryParse((json['invited_at'] as String?) ?? ''),
      expiresAt: DateTime.tryParse((json['expires_at'] as String?) ?? ''),
    );
  }

  final String id;
  final TeamRole role;
  final InvitationState state;

  /// The last four digits only — enough to recognise which invitation is
  /// which, useless to anyone who obtains it.
  final String phoneLast4;

  final String? displayName;
  final DateTime? invitedAt;
  final DateTime? expiresAt;

  String get name => displayName ?? '•••• $phoneLast4';
}

/// An invitation as the *invitee* sees it, before they belong to the shop.
@immutable
class PendingInvitation {
  const PendingInvitation({
    required this.invitationId,
    required this.shopName,
    required this.role,
    this.invitedAt,
    this.expiresAt,
  });

  factory PendingInvitation.fromJson(Map<String, dynamic> json) {
    return PendingInvitation(
      invitationId: json['invitation_id'] as String,
      shopName: (json['shop_name'] as String?) ?? '',
      role: TeamRole.parse(json['role'] as String?),
      invitedAt: DateTime.tryParse((json['invited_at'] as String?) ?? ''),
      expiresAt: DateTime.tryParse((json['expires_at'] as String?) ?? ''),
    );
  }

  final String invitationId;

  /// The one thing an invitee needs in order to decide. Nothing else about the
  /// shop is sent, because someone who has not accepted is not a member of it.
  final String shopName;
  final TeamRole role;
  final DateTime? invitedAt;
  final DateTime? expiresAt;
}
