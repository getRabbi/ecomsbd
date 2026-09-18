import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import 'models.dart';
import 'team_repository.dart';

/// Wiring for team management.
///
/// Nothing here is cached on disk. A stale roster would show someone removed
/// this morning as still holding their role, which is the one kind of staleness
/// an access-control screen must not have.

final teamRepositoryProvider = Provider<TeamRepository>((ref) {
  return TeamRepository(ref.watch(apiClientProvider));
});

final teamMembersProvider = FutureProvider<List<TeamMember>>((ref) {
  return ref.watch(teamRepositoryProvider).members();
});

final teamInvitationsProvider = FutureProvider<List<TeamInvitation>>((ref) {
  return ref.watch(teamRepositoryProvider).invitations();
});

/// What each role unlocks. Fetched so the matrix has one definition — the
/// server's — rather than a copy in the app that can drift from it.
final roleMatrixProvider = FutureProvider<Map<String, List<String>>>((ref) {
  return ref.watch(teamRepositoryProvider).roleMatrix();
});

/// Shops waiting for this person to join.
final myInvitationsProvider = FutureProvider<List<PendingInvitation>>((ref) {
  return ref.watch(teamRepositoryProvider).myInvitations();
});
