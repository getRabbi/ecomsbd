import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:meta/meta.dart';

import '../../app/providers.dart';

/// The integrations hub (`GET /integrations`) and its open problems
/// (`GET /integrations/issues`), read once and shared.
///
/// The Connections & Integrations screen, Home's channel strip and the Inbox
/// all read the same answer, so a store cannot look connected on one screen
/// and broken on another.
final integrationsHubProvider =
    FutureProvider.autoDispose<Map<String, dynamic>>(
      (ref) => ref.watch(apiClientProvider).get('/integrations'),
    );

final integrationIssuesProvider =
    FutureProvider.autoDispose<List<Map<String, dynamic>>>((ref) async {
      final body = await ref
          .watch(apiClientProvider)
          .get('/integrations/issues');
      return (body['items'] as List<dynamic>? ?? const <dynamic>[])
          .whereType<Map<String, dynamic>>()
          .toList();
    });

/// Where one store or channel stands for this shop, as the hub renders it.
///
/// Each value is something the server actually said. Nothing is inferred on
/// the phone: a provider is never shown as connectable unless the server lists
/// it as available, and never as connected unless the server has a connection.
enum IntegrationSetupState {
  /// A connection the server reports healthy.
  connected,

  /// A connection whose sign-in expired or lost a permission.
  needsReconnect,

  /// A connection with a failing sync, a failing webhook or unfinished setup.
  needsAttention,

  /// A connection the seller disconnected or turned off.
  inactive,

  /// Nothing connected yet, and the server is ready for one. Connecting a
  /// store or channel takes a sign-in at the provider or copying keys, so it
  /// is done in ecomsbd on the web.
  setUpOnWeb,

  /// The provider must approve ecomsbd's own app first (Shopify's app review,
  /// Meta's app review). Nothing the seller can do, and nothing wrong with
  /// their account.
  providerApprovalRequired,

  /// ecomsbd has no integration for this provider.
  notImplemented,

  /// The server lists the provider as unavailable for a reason this version
  /// of the app does not know.
  temporarilyUnavailable,
}

/// Health values that mean a connection needs the seller.
const Set<String> _attentionHealth = <String>{
  'DEGRADED',
  'WEBHOOK_FAILING',
  'SYNC_FAILING',
  'SETUP_INCOMPLETE',
};

/// Server blockers that mean a provider has not approved ecomsbd's app yet.
const Set<String> _approvalBlockers = <String>{
  'SHOPIFY_APP_SETUP_REQUIRED',
  'META_APP_SETUP_REQUIRED',
};

/// The state of one existing connection, from its server `health`.
IntegrationSetupState connectionSetupState(Map<String, dynamic> connection) =>
    switch (connection['health']) {
      'CONNECTED' => IntegrationSetupState.connected,
      'AUTH_EXPIRED' => IntegrationSetupState.needsReconnect,
      final String health when _attentionHealth.contains(health) =>
        IntegrationSetupState.needsAttention,
      _ => IntegrationSetupState.inactive,
    };

/// The state of a provider with no connection yet, from the server's
/// availability row for it ([availability] is null when the server does not
/// list the provider at all).
IntegrationSetupState providerSetupState(Map<String, dynamic>? availability) {
  if (availability == null) return IntegrationSetupState.notImplemented;
  if (availability['available'] == true) {
    return IntegrationSetupState.setUpOnWeb;
  }
  return _approvalBlockers.contains(availability['blocker'])
      ? IntegrationSetupState.providerApprovalRequired
      : IntegrationSetupState.temporarilyUnavailable;
}

/// One row of the stores and channels section.
@immutable
class IntegrationRow {
  const IntegrationRow({
    required this.provider,
    required this.state,
    this.connection,
    this.blocker,
  });

  /// The server's provider code (`SHOPIFY`), or `INSTAGRAM`, which the server
  /// does not offer.
  final String provider;
  final IntegrationSetupState state;

  /// The connection this row shows, or null for a provider with none yet.
  final Map<String, dynamic>? connection;

  /// The server's reason a provider is unavailable, when it gave one.
  final String? blocker;
}

/// Instagram has no connector on the server. It is listed so a seller sees
/// that, rather than wondering where it went.
const String instagramProvider = 'INSTAGRAM';

/// Every store and channel row, in the server's provider order: each existing
/// connection, and one row for each provider that has none yet.
List<IntegrationRow> integrationRowsFromHub(Map<String, dynamic> hub) {
  final items = (hub['items'] as List<dynamic>? ?? const <dynamic>[])
      .whereType<Map<String, dynamic>>()
      .toList();
  final providers = (hub['providers'] as List<dynamic>? ?? const <dynamic>[])
      .whereType<Map<String, dynamic>>()
      .toList();

  final rows = <IntegrationRow>[];
  final listed = <String>{};
  for (final availability in providers) {
    final provider = '${availability['provider']}';
    listed.add(provider);
    final connections = items.where((c) => c['provider'] == provider);
    if (connections.isEmpty) {
      rows.add(
        IntegrationRow(
          provider: provider,
          state: providerSetupState(availability),
          blocker: availability['blocker'] as String?,
        ),
      );
    } else {
      for (final connection in connections) {
        rows.add(
          IntegrationRow(
            provider: provider,
            state: connectionSetupState(connection),
            connection: connection,
          ),
        );
      }
    }
  }
  // A connection to a provider the server no longer lists is still shown:
  // hiding it would hide its problems too.
  for (final connection in items) {
    final provider = '${connection['provider']}';
    if (listed.contains(provider)) continue;
    rows.add(
      IntegrationRow(
        provider: provider,
        state: connectionSetupState(connection),
        connection: connection,
      ),
    );
  }
  rows.add(
    const IntegrationRow(
      provider: instagramProvider,
      state: IntegrationSetupState.notImplemented,
    ),
  );
  return rows;
}
