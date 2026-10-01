import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:meta/meta.dart';

import '../../core/api/api_error.dart';
import 'integration_hub.dart';

/// Sales and messaging channels, and the inbox boundary.
///
/// A channel's state is read from the integrations hub (`/integrations`), the
/// server's record of what the shop actually connected. Nothing here marks a
/// channel connected on the phone's say-so.
enum SalesChannel {
  website(<String>{'CUSTOM_WEBSITE', 'SHOPIFY', 'WOOCOMMERCE'}),
  facebook(<String>{'MESSENGER'}),
  whatsapp(<String>{'WHATSAPP'}),

  /// No Instagram connector exists on the server yet.
  instagram(<String>{});

  const SalesChannel(this.integrationProviders);

  /// Integration hub provider codes that count as this channel.
  final Set<String> integrationProviders;
}

enum ChannelHealth { connected, needsAttention, notConnected, unavailable }

@immutable
class ChannelStatus {
  const ChannelStatus({
    required this.channel,
    required this.health,
    this.accountName,
  });

  final SalesChannel channel;
  final ChannelHealth health;

  /// The connected page, number or store, as the server names it.
  final String? accountName;
}

/// Parse the integrations hub into one status per channel.
@visibleForTesting
List<ChannelStatus> channelStatusesFromHub(Map<String, dynamic> hub) {
  final items = (hub['items'] as List<dynamic>? ?? const <dynamic>[])
      .whereType<Map<String, dynamic>>()
      .toList();
  final providers = (hub['providers'] as List<dynamic>? ?? const <dynamic>[])
      .whereType<Map<String, dynamic>>()
      .toList();

  ChannelStatus statusOf(SalesChannel channel) {
    if (channel.integrationProviders.isEmpty) {
      return ChannelStatus(channel: channel, health: ChannelHealth.unavailable);
    }
    final connections = items
        .where((c) => channel.integrationProviders.contains(c['provider']))
        .toList();
    if (connections.isNotEmpty) {
      final healthy = connections.firstWhere(
        (c) => c['health'] == 'CONNECTED',
        orElse: () => connections.first,
      );
      return ChannelStatus(
        channel: channel,
        health: healthy['health'] == 'CONNECTED'
            ? ChannelHealth.connected
            : ChannelHealth.needsAttention,
        accountName:
            (healthy['account_name'] as String?) ?? healthy['name'] as String?,
      );
    }
    final available = providers.any(
      (p) =>
          channel.integrationProviders.contains(p['provider']) &&
          p['available'] == true,
    );
    return ChannelStatus(
      channel: channel,
      health: available
          ? ChannelHealth.notConnected
          : ChannelHealth.unavailable,
    );
  }

  return <ChannelStatus>[
    for (final channel in SalesChannel.values) statusOf(channel),
  ];
}

final salesChannelsProvider = FutureProvider.autoDispose<List<ChannelStatus>>((
  ref,
) async {
  final hub = await ref.watch(integrationsHubProvider.future);
  return channelStatusesFromHub(hub);
});

// --------------------------------------------------------------------------- //
// Inbox
// --------------------------------------------------------------------------- //

/// One customer conversation from a sales channel.
@immutable
class InboxThread {
  const InboxThread({
    required this.id,
    required this.channel,
    required this.customerLabel,
    required this.lastMessage,
    required this.lastMessageAt,
    this.unreadCount = 0,
    this.customerId,
    this.orderId,
  });

  final String id;
  final SalesChannel channel;
  final String customerLabel;
  final String lastMessage;
  final DateTime lastMessageAt;
  final int unreadCount;

  /// The shop's customer record, when the conversation is matched to one.
  final String? customerId;

  /// An open order already linked to this conversation.
  final String? orderId;

  bool get awaitingReply => unreadCount > 0;
}

/// Where inbound customer conversations come from.
///
/// The server's `/messaging/conversations` are outbound contacts (consent and
/// order updates), not inbound chats, so no source exists yet.
/// TODO(inbox): implement a source over an inbound-message sync endpoint once
/// Messenger/WhatsApp webhooks store conversations.
abstract class InboxSource {
  /// Null when the source cannot read conversations at all — the screen then
  /// says so instead of showing an empty inbox as if nobody wrote.
  Future<List<InboxThread>?> threads();
}

class UnavailableInboxSource implements InboxSource {
  const UnavailableInboxSource();

  @override
  Future<List<InboxThread>?> threads() async => null;
}

final inboxSourceProvider = Provider<InboxSource>(
  (ref) => const UnavailableInboxSource(),
);

final inboxThreadsProvider = FutureProvider.autoDispose<List<InboxThread>?>((
  ref,
) async {
  try {
    return await ref.watch(inboxSourceProvider).threads();
  } on ApiError {
    return null;
  }
});

/// Conversations waiting for a reply; null when unknown.
final unansweredThreadCountProvider = Provider.autoDispose<int?>((ref) {
  final threads = ref.watch(inboxThreadsProvider).valueOrNull;
  return threads?.where((t) => t.awaitingReply).length;
});
