import '../../core/api/api_client.dart';
import 'models.dart';

/// Team membership, roles and invitations.
///
/// Not cached. Every read here answers an access-control question — who is in
/// this shop, and what may they do — and a stale answer is the kind that shows
/// someone removed this morning as still holding their role.
class TeamRepository {
  const TeamRepository(this._api);

  final ApiClient _api;

  Future<List<TeamMember>> members() async {
    final rows = await _api.getList('/team');
    return <TeamMember>[
      for (final row in rows) TeamMember.fromJson(row as Map<String, dynamic>),
    ];
  }

  /// What each role unlocks, straight from the server's matrix.
  ///
  /// Fetched rather than hard-coded so the screen explains a role by the
  /// permissions it actually grants, and so the matrix has one definition.
  Future<Map<String, List<String>>> roleMatrix() async {
    final json = await _api.get('/team/roles');
    return <String, List<String>>{
      for (final entry in json.entries)
        entry.key: <String>[
          for (final row
              in (entry.value as List<dynamic>? ?? const <dynamic>[]))
            '$row',
        ],
    };
  }

  Future<TeamMember> changeRole(String userId, TeamRole role) async {
    final json = await _api.patch(
      '/team/$userId',
      body: <String, dynamic>{'role': role.wireValue},
    );
    return TeamMember.fromJson(json);
  }

  Future<void> removeMember(String userId) async {
    await _api.delete('/team/$userId');
  }

  // --- invitations ----------------------------------------------------------

  Future<List<TeamInvitation>> invitations({bool includeClosed = false}) async {
    final rows = await _api.getList(
      '/team/invitations',
      query: <String, dynamic>{if (includeClosed) 'include_closed': true},
    );
    return <TeamInvitation>[
      for (final row in rows)
        TeamInvitation.fromJson(row as Map<String, dynamic>),
    ];
  }

  /// Offer membership to a phone number.
  ///
  /// An offer, not a membership: the person joins when they accept. The number
  /// leaves the device once, over TLS, and is never read back — the response
  /// carries its last four digits and nothing else.
  Future<TeamInvitation> invite({
    required String phone,
    required TeamRole role,
    String? displayName,
  }) async {
    final json = await _api.post(
      '/team/invitations',
      body: <String, dynamic>{
        'phone': phone,
        'role': role.wireValue,
        if (displayName != null && displayName.isNotEmpty)
          'display_name': displayName,
      },
    );
    return TeamInvitation.fromJson(json);
  }

  Future<TeamInvitation> revokeInvitation(String invitationId) async {
    final json = await _api.delete('/team/invitations/$invitationId');
    return TeamInvitation.fromJson(json);
  }

  // --- the invitee's own side ----------------------------------------------

  /// Shops waiting for this person to join.
  ///
  /// Callable before belonging to any shop, which is the point: an invitee is
  /// by definition not a member of one yet.
  Future<List<PendingInvitation>> myInvitations() async {
    final rows = await _api.getList('/team/invitations/mine');
    return <PendingInvitation>[
      for (final row in rows)
        PendingInvitation.fromJson(row as Map<String, dynamic>),
    ];
  }

  Future<TeamMember> acceptInvitation(String invitationId) async {
    final json = await _api.post('/team/invitations/$invitationId/accept');
    return TeamMember.fromJson(json);
  }
}
