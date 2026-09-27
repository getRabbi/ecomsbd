import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/channels/channel_models.dart';
import '../../data/couriers/courier_providers.dart';
import '../../data/couriers/models.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/seller_blocks.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../shared/data_state.dart';
import 'courier_accounts_screen.dart';
import 'integrations_screen.dart';

/// Sales channels and courier connections, with their real state.
///
/// A channel reads "Connected" only when the integrations hub has a healthy
/// connection for it; the phone never infers one.
class ConnectionsScreen extends ConsumerWidget {
  const ConnectionsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final channels = ref.watch(salesChannelsProvider);
    final couriers = ref.watch(courierAccountsProvider);

    return DetailScaffold(
      eyebrow: context.tr('conn.eyebrow'),
      title: context.tr('conn.title'),
      subtitle: context.tr('conn.subtitle'),
      children: <Widget>[
        SectionHeader(
          title: context.tr('conn.channels'),
          actionLabel: context.tr('conn.manage'),
          onAction: () => _push(context, const IntegrationsScreen()),
        ),
        channels.when(
          loading: () => const ContentLoader(minHeight: 120),
          error: (_, __) => Text(
            context.tr('conn.unavailable'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          data: (list) => ListCard(
            children: <Widget>[
              for (final status in inDisplayOrder(list))
                channelListRow(
                  context,
                  status,
                  onTap: status.health == ChannelHealth.unavailable
                      ? null
                      : () => _push(context, const IntegrationsScreen()),
                ),
            ],
          ),
        ),
        SectionHeader(
          title: context.tr('conn.couriers'),
          actionLabel: context.tr('conn.manage'),
          onAction: () => _push(context, const CourierAccountsScreen()),
        ),
        couriers.when(
          loading: () => const ContentLoader(minHeight: 80),
          error: (_, __) => Text(
            context.tr('conn.unavailable'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          data: (accounts) => accounts.isEmpty
              ? EmptyState(
                  icon: Icons.local_shipping_outlined,
                  title: context.tr('conn.noCourierTitle'),
                  message: context.tr('conn.noCourierBody'),
                )
              : ListCard(
                  children: <Widget>[
                    for (final account in accounts)
                      _CourierRow(
                        account: account,
                        onTap: () =>
                            _push(context, const CourierAccountsScreen()),
                      ),
                  ],
                ),
        ),
        const SizedBox(height: 12),
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: const Color(0xFFEEF7FF),
            borderRadius: EcomsbdRadii.row,
            border: Border.all(color: const Color(0xFFDBEAFF)),
          ),
          child: Text(
            context.tr('conn.note'),
            style: EcomsbdType.caption.copyWith(
              color: const Color(0xFF38526C),
              height: 1.5,
            ),
          ),
        ),
      ],
    );
  }
}

/// A courier provider code ("steadfast") as a name ("Steadfast").
String courierDisplayName(String provider) => provider.isEmpty
    ? provider
    : '${provider[0].toUpperCase()}${provider.substring(1)}';

String channelName(BuildContext context, SalesChannel channel) =>
    context.tr('chan.${channel.name}');

(String, Tone) channelHealthLabel(
  BuildContext context,
  ChannelHealth health,
) => switch (health) {
  ChannelHealth.connected => (context.tr('chan.connected'), Tone.good),
  ChannelHealth.needsAttention => (
    context.tr('chan.needsAttention'),
    Tone.warning,
  ),
  ChannelHealth.notConnected => (context.tr('chan.notConnected'), Tone.neutral),
  ChannelHealth.unavailable => (context.tr('chan.unavailable'), Tone.neutral),
};

/// The order channels are listed in, as the prototype lists them.
const List<SalesChannel> channelDisplayOrder = <SalesChannel>[
  SalesChannel.facebook,
  SalesChannel.whatsapp,
  SalesChannel.instagram,
  SalesChannel.website,
];

/// [statuses] in [channelDisplayOrder].
List<ChannelStatus> inDisplayOrder(List<ChannelStatus> statuses) =>
    <ChannelStatus>[...statuses]..sort(
      (a, b) => channelDisplayOrder
          .indexOf(a.channel)
          .compareTo(channelDisplayOrder.indexOf(b.channel)),
    );

IconData channelIcon(SalesChannel channel) => switch (channel) {
  SalesChannel.website => Icons.language_rounded,
  SalesChannel.facebook => Icons.facebook_rounded,
  SalesChannel.whatsapp => Icons.chat_rounded,
  SalesChannel.instagram => Icons.camera_alt_outlined,
};

/// The trailing state word of a connection row, coloured by its health.
Widget connectionStateLabel(BuildContext context, ChannelHealth health) {
  final (label, tone) = channelHealthLabel(context, health);
  return Text(
    label,
    style: EcomsbdType.label.copyWith(
      fontWeight: FontWeight.w800,
      color: tone == Tone.neutral ? EcomsbdColors.muted : tone.ink,
    ),
  );
}

/// `.list-row` for one channel: icon, name, account, and its state.
/// Shared by Connections and Inbox.
Widget channelListRow(
  BuildContext context,
  ChannelStatus status, {
  VoidCallback? onTap,
}) => ListCardRow(
  icon: channelIcon(status.channel),
  title: channelName(context, status.channel),
  subtitle: status.accountName ?? context.tr('chan.${status.channel.name}Sub'),
  trailing: connectionStateLabel(context, status.health),
  onTap: onTap,
);

/// One channel with its state, as a standalone row.
class ChannelStatusRow extends StatelessWidget {
  const ChannelStatusRow({required this.status, super.key, this.onTap});

  final ChannelStatus status;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final (label, tone) = channelHealthLabel(context, status.health);
    return Padding(
      padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
      child: GlassListRow(
        leading: RowIcon(
          label: switch (status.channel) {
            SalesChannel.website => 'W',
            SalesChannel.facebook => 'f',
            SalesChannel.whatsapp => 'WA',
            SalesChannel.instagram => 'IG',
          },
        ),
        title: channelName(context, status.channel),
        subtitle:
            status.accountName ?? context.tr('chan.${status.channel.name}Sub'),
        trailing: StatusChip(label: label, tone: tone),
        onTap: onTap,
      ),
    );
  }
}

class _CourierRow extends StatelessWidget {
  const _CourierRow({required this.account, required this.onTap});

  final CourierAccount account;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final health = account.needsReconnect
        ? ChannelHealth.needsAttention
        : (account.connected
              ? ChannelHealth.connected
              : ChannelHealth.notConnected);
    final name = account.label ?? courierDisplayName(account.provider);
    return ListCardRow(
      leading: CourierMark(provider: account.provider, name: name, size: 38),
      title: name,
      subtitle: account.maskedIdentifier,
      trailing: connectionStateLabel(context, health),
      onTap: onTap,
    );
  }
}

void _push(BuildContext context, Widget page) {
  unawaited(
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => page)),
  );
}
