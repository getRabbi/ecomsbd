import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../data/channels/channel_models.dart';
import '../../data/channels/integration_hub.dart';
import '../../data/couriers/courier_providers.dart';
import '../../data/couriers/models.dart';
import '../../design/components/badges.dart';
import '../../design/components/seller_blocks.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../shared/data_state.dart';
import '../shared/network_status.dart';
import 'courier_accounts_screen.dart';
import 'integrations_screen.dart';

/// Connections & Integrations: the one place a seller sees every store,
/// sales channel and courier the shop is connected to, and what needs fixing.
///
/// Everything shown is the server's own state. A store reads "Connected" only
/// when the integrations hub has a healthy connection for it, and a courier's
/// state comes from the same functions the courier accounts screen uses, so
/// the two screens cannot disagree. Nothing here invents a count.
///
/// Stores and channels are connected on the web, where the provider sign-in,
/// keys and copy-paste happen; this screen links to the real page for that.
/// Couriers are connected right here.
class ConnectionsScreen extends ConsumerStatefulWidget {
  const ConnectionsScreen({super.key});

  @override
  ConsumerState<ConnectionsScreen> createState() => _ConnectionsScreenState();
}

class _ConnectionsScreenState extends ConsumerState<ConnectionsScreen>
    with ReloadOnReconnect {
  final Set<String> _queued = <String>{};
  bool _busy = false;

  @override
  void onReconnect() => _reload();

  void _reload() {
    ref.invalidate(integrationsHubProvider);
    ref.invalidate(integrationIssuesProvider);
    ref.invalidate(courierProvidersProvider);
    ref.invalidate(courierAccountsProvider);
    ref.invalidate(bookableCouriersProvider);
  }

  Future<void> _act(Future<void> Function() work) async {
    setState(() => _busy = true);
    try {
      await work();
    } on ApiError catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(explain(context, error))));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _retry(String id) => unawaited(
    _act(() async {
      await ref.read(apiClientProvider).post('/integrations/events/$id/retry');
      if (mounted) setState(() => _queued.add(id));
    }),
  );

  void _resolve(String id) => unawaited(
    _act(() async {
      await ref
          .read(apiClientProvider)
          .post('/integrations/events/$id/resolve');
      ref.invalidate(integrationIssuesProvider);
    }),
  );

  @override
  Widget build(BuildContext context) {
    final hub = ref.watch(integrationsHubProvider);
    final issues = ref.watch(integrationIssuesProvider);
    final couriers = ref.watch(_courierRowsProvider);
    final rows = hub.whenData(integrationRowsFromHub);

    return DetailScaffold(
      eyebrow: context.tr('conn.eyebrow'),
      title: context.tr('conn.title'),
      subtitle: context.tr('conn.subtitle'),
      actions: <Widget>[
        IconButton(
          tooltip: context.tr('conn.refresh'),
          onPressed: _busy ? null : _reload,
          icon: const Icon(Icons.refresh_rounded),
        ),
      ],
      children: <Widget>[
        if (_busy) const LinearProgressIndicator(),
        _Summary(rows: rows.valueOrNull, couriers: couriers.valueOrNull),
        _Attention(
          rows: rows.valueOrNull ?? const <IntegrationRow>[],
          couriers: couriers.valueOrNull ?? const <_CourierRowState>[],
          issues: issues.valueOrNull ?? const <Map<String, dynamic>>[],
          canRetry: hub.valueOrNull?['can_retry'] == true,
          queued: _queued,
          busy: _busy,
          onRetry: _retry,
          onResolve: _resolve,
          onChanged: _reload,
        ),
        SectionHeader(title: context.tr('conn.channels')),
        rows.when(
          loading: () => const ContentLoader(minHeight: 120),
          error: (error, _) => error is ApiError
              ? ErrorStateCard(
                  error: error,
                  onRetry: () => ref.invalidate(integrationsHubProvider),
                )
              : Text(
                  context.tr('conn.unavailable'),
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
          data: (list) => ListCard(
            children: <Widget>[
              for (final row in list)
                _IntegrationTile(
                  row: row,
                  canManage: hub.valueOrNull?['can_manage'] == true,
                  onChanged: _reload,
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
          error: (error, _) => error is ApiError
              ? ErrorStateCard(error: error, onRetry: _reload)
              : Text(
                  context.tr('conn.unavailable'),
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
          data: (list) => list.isEmpty
              ? EmptyState(
                  icon: Icons.local_shipping_outlined,
                  title: context.tr('conn.noCourierTitle'),
                  message: context.tr('conn.noCourierBody'),
                )
              : ListCard(
                  children: <Widget>[
                    for (final courier in list) _CourierTile(state: courier),
                  ],
                ),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        Text(
          context.tr('conn.note'),
          style: EcomsbdType.caption.copyWith(
            color: EcomsbdColors.muted,
            height: 1.5,
          ),
        ),
      ],
    );
  }
}

// ------------------------------------------------------------- couriers --

/// One courier as the hub shows it: the same state the accounts screen shows.
class _CourierRowState {
  const _CourierRowState({
    required this.info,
    required this.connection,
    required this.needsStore,
    required this.viewOnly,
    this.account,
  });

  final CourierProviderInfo info;
  final CourierConnection connection;
  final bool needsStore;
  final bool viewOnly;
  final CourierAccount? account;

  bool get isConnected =>
      connection == CourierConnection.connected && !needsStore;

  bool get needsAttention =>
      connection == CourierConnection.needsReconnect || needsStore;
}

/// Every connectable courier with its state, from the courier screens' own
/// providers and functions. Someone who may not manage credentials gets the
/// booking-availability view, exactly as on the courier accounts screen.
final _courierRowsProvider = FutureProvider.autoDispose<List<_CourierRowState>>(
  (ref) async {
    final providers = await ref.watch(courierProvidersProvider.future);
    List<CourierAccount>? accounts;
    var viewOnly = false;
    try {
      accounts = await ref.watch(courierAccountsProvider.future);
    } on ApiError catch (error) {
      if (!isCourierRoleRefusal(error)) rethrow;
      viewOnly = true;
    }
    List<BookableCourier>? bookable;
    if (viewOnly) {
      try {
        bookable = await ref.watch(bookableCouriersProvider.future);
      } on ApiError {
        bookable = null;
      }
    }
    return <_CourierRowState>[
      for (final info in providers)
        if (info.provider != CourierAccountsScreen.manual)
          () {
            final account = courierAccountOf(accounts, info.provider);
            final booking = bookableCourierOf(bookable, info.provider);
            final connection = courierConnectionFor(
              info,
              account: account,
              bookable: booking,
              viewOnly: viewOnly,
            );
            return _CourierRowState(
              info: info,
              connection: connection,
              needsStore: courierNeedsPickupStore(
                info,
                connection,
                account: account,
                bookable: booking,
                viewOnly: viewOnly,
              ),
              viewOnly: viewOnly,
              account: account,
            );
          }(),
    ];
  },
);

class _CourierTile extends ConsumerWidget {
  const _CourierTile({required this.state});

  final _CourierRowState state;

  void _manage(BuildContext context) =>
      _push(context, CourierAccountManageScreen(info: state.info));

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final info = state.info;
    final name = <String, Object?>{'provider': info.displayName};
    final connection = state.connection;
    final masked = state.account?.maskedIdentifier;

    final String? note = switch (connection) {
      _ when state.needsStore => context.tr('ca.needsStoreSub'),
      CourierConnection.notConnected when !state.viewOnly => context.tr(
        'ca.notConnectedProviderSub',
        name,
      ),
      CourierConnection.needsReconnect => context.tr(
        'ca.needsReconnectProviderSub',
        name,
      ),
      CourierConnection.disabled => context.tr('ca.disabledSub', name),
      CourierConnection.unavailable => context.tr('ca.unavailableSub', name),
      CourierConnection.unknown => context.tr('ca.unreadable'),
      _ => null,
    };

    // Only someone who may manage credentials gets a button; the server
    // refuses everyone else regardless.
    final Widget? action = state.viewOnly
        ? null
        : switch (connection) {
            CourierConnection.notConnected => FilledButton(
              key: ValueKey('hub-connect-${info.provider}'),
              onPressed: () => connectCourier(context, ref, info),
              child: Text(context.tr('common.connect')),
            ),
            CourierConnection.needsReconnect => FilledButton(
              key: ValueKey('hub-reconnect-${info.provider}'),
              onPressed: () => _manage(context),
              child: Text(context.tr('ca.reconnect')),
            ),
            CourierConnection.connected ||
            CourierConnection.unknown => OutlinedButton(
              key: ValueKey('hub-manage-${info.provider}'),
              onPressed: () => _manage(context),
              child: Text(context.tr('ca.manage')),
            ),
            _ => null,
          };

    return _HubTile(
      key: ValueKey('hub-courier-${info.provider}'),
      leading: CourierMark(
        provider: info.provider,
        name: info.displayName,
        size: 38,
      ),
      title: info.displayName,
      subtitle: masked != null && connection == CourierConnection.connected
          ? maskedForDisplay(masked)
          : null,
      status: StatusChip(
        label: state.needsStore
            ? context.tr('chan.needsAttention')
            : connection.label(context),
        tone: state.needsStore ? Tone.warning : connection.tone,
        showIcon: false,
      ),
      note: note,
      noteIsProblem: state.needsAttention,
      action: action,
    );
  }
}

// ------------------------------------------------- stores and channels --

class _IntegrationTile extends StatelessWidget {
  const _IntegrationTile({
    required this.row,
    required this.canManage,
    required this.onChanged,
  });

  final IntegrationRow row;
  final bool canManage;
  final VoidCallback onChanged;

  Future<void> _openDetail(BuildContext context, String id) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => IntegrationDetailScreen(id: id)),
    );
    onChanged();
  }

  @override
  Widget build(BuildContext context) {
    final connection = row.connection;
    final providerName = integrationProviderName(context, row.provider);
    final icon = _integrationIcon(row.provider);

    if (connection != null) {
      final account = connection['account_name'];
      return _HubTile(
        key: ValueKey('hub-connection-${connection['id']}'),
        leading: SoftIcon(icon: icon),
        title: '${connection['name'] ?? providerName}',
        subtitle: <String>[
          providerName,
          if (account != null) '$account',
        ].join(' · '),
        status: HealthBadge(health: '${connection['health']}'),
        note: row.state == IntegrationSetupState.needsReconnect
            ? context.tr('conn.reconnectNote')
            : null,
        noteIsProblem: row.state == IntegrationSetupState.needsReconnect,
        onTap: () => _openDetail(context, '${connection['id']}'),
      );
    }

    final name = <String, Object?>{'provider': providerName};
    final (String label, Tone tone, String note) = switch (row.state) {
      IntegrationSetupState.setUpOnWeb => (
        context.tr('chan.notConnected'),
        Tone.neutral,
        canManage
            ? context.tr('conn.setUpOnWebNote', name)
            : context.tr('conn.ownerConnects'),
      ),
      IntegrationSetupState.providerApprovalRequired => (
        context.tr('conn.approvalPending'),
        Tone.info,
        context.tr('conn.approvalNote', <String, Object?>{
          'approver': row.blocker == 'SHOPIFY_APP_SETUP_REQUIRED'
              ? 'Shopify'
              : 'Meta',
        }),
      ),
      IntegrationSetupState.notImplemented => (
        context.tr('chan.unavailable'),
        Tone.neutral,
        context.tr('conn.notAvailableNote', name),
      ),
      _ => (
        context.tr('conn.unavailableNow'),
        Tone.neutral,
        context.tr('conn.unavailableNote'),
      ),
    };

    return _HubTile(
      key: ValueKey('hub-provider-${row.provider}'),
      leading: SoftIcon(icon: icon),
      title: providerName,
      subtitle: context.tr('conn.about.${row.provider}'),
      status: StatusChip(label: label, tone: tone, showIcon: false),
      note: note,
      action: row.state == IntegrationSetupState.setUpOnWeb && canManage
          ? OutlinedButton.icon(
              key: ValueKey('hub-setup-${row.provider}'),
              onPressed: () => openWebDashboard(context, '/integrations'),
              icon: const Icon(Icons.open_in_new_rounded, size: 16),
              label: Text(context.tr('conn.setUpOnWeb')),
            )
          : null,
    );
  }
}

/// A store or channel provider's name in the seller's language.
String integrationProviderName(BuildContext context, String provider) =>
    provider == instagramProvider
    ? context.tr('chan.instagram')
    : context.tr('int.provider.$provider');

IconData _integrationIcon(String provider) => switch (provider) {
  'SHOPIFY' => Icons.shopping_bag_outlined,
  'WOOCOMMERCE' => Icons.storefront_outlined,
  'CUSTOM_WEBSITE' => Icons.language_rounded,
  'MESSENGER' => Icons.facebook_rounded,
  'WHATSAPP' => Icons.chat_rounded,
  instagramProvider => Icons.camera_alt_outlined,
  _ => Icons.hub_outlined,
};

// -------------------------------------------------------------- summary --

/// "Connected: 3 · Needs attention: 1", counted from what loaded. Nothing is
/// drawn until something has.
class _Summary extends StatelessWidget {
  const _Summary({required this.rows, required this.couriers});

  final List<IntegrationRow>? rows;
  final List<_CourierRowState>? couriers;

  @override
  Widget build(BuildContext context) {
    if (rows == null && couriers == null) return const SizedBox.shrink();
    final connected =
        (rows ?? const <IntegrationRow>[])
            .where((r) => r.state == IntegrationSetupState.connected)
            .length +
        (couriers ?? const <_CourierRowState>[])
            .where((c) => c.isConnected)
            .length;
    final attention =
        (rows ?? const <IntegrationRow>[])
            .where(
              (r) =>
                  r.state == IntegrationSetupState.needsReconnect ||
                  r.state == IntegrationSetupState.needsAttention,
            )
            .length +
        (couriers ?? const <_CourierRowState>[])
            .where((c) => c.needsAttention)
            .length;
    return Padding(
      padding: const EdgeInsets.only(top: EcomsbdSpacing.xs),
      child: Wrap(
        key: const Key('hub-summary'),
        spacing: EcomsbdSpacing.xs,
        runSpacing: EcomsbdSpacing.xs,
        children: <Widget>[
          StatusChip(
            label: context.tr('conn.summaryConnected', <String, Object?>{
              'count': connected,
            }),
            tone: connected > 0 ? Tone.good : Tone.neutral,
            showIcon: false,
          ),
          StatusChip(
            label: context.tr('conn.summaryAttention', <String, Object?>{
              'count': attention,
            }),
            tone: attention > 0 ? Tone.warning : Tone.neutral,
            showIcon: false,
          ),
        ],
      ),
    );
  }
}

// ------------------------------------------------------------- attention --

/// What needs the seller, and only when something does: connections whose
/// sign-in expired or whose sync or webhook is failing, couriers that need
/// reconnecting or a pickup store, and orders a store could not import.
class _Attention extends StatelessWidget {
  const _Attention({
    required this.rows,
    required this.couriers,
    required this.issues,
    required this.canRetry,
    required this.queued,
    required this.busy,
    required this.onRetry,
    required this.onResolve,
    required this.onChanged,
  });

  final List<IntegrationRow> rows;
  final List<_CourierRowState> couriers;
  final List<Map<String, dynamic>> issues;
  final bool canRetry;
  final Set<String> queued;
  final bool busy;
  final void Function(String id) onRetry;
  final void Function(String id) onResolve;
  final VoidCallback onChanged;

  @override
  Widget build(BuildContext context) {
    final connections = rows.where(
      (r) =>
          r.connection != null &&
          (r.state == IntegrationSetupState.needsReconnect ||
              r.state == IntegrationSetupState.needsAttention),
    );
    final stuck = couriers.where((c) => c.needsAttention);
    if (connections.isEmpty && stuck.isEmpty && issues.isEmpty) {
      return const SizedBox.shrink();
    }
    return Column(
      key: const Key('hub-attention'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        SectionHeader(title: context.tr('conn.attentionTitle')),
        if (connections.isNotEmpty || stuck.isNotEmpty)
          ListCard(
            children: <Widget>[
              for (final row in connections)
                ListCardRow(
                  icon: _integrationIcon(row.provider),
                  title: '${row.connection!['name']}',
                  subtitle: integrationProviderName(context, row.provider),
                  trailing: HealthBadge(health: '${row.connection!['health']}'),
                  onTap: () async {
                    await Navigator.of(context).push(
                      MaterialPageRoute<void>(
                        builder: (_) => IntegrationDetailScreen(
                          id: '${row.connection!['id']}',
                        ),
                      ),
                    );
                    onChanged();
                  },
                ),
              for (final courier in stuck)
                ListCardRow(
                  leading: CourierMark(
                    provider: courier.info.provider,
                    name: courier.info.displayName,
                    size: 38,
                  ),
                  title: courier.info.displayName,
                  subtitle: courier.needsStore
                      ? context.tr('ca.needsStoreSub')
                      : context.tr(
                          'ca.needsReconnectProviderSub',
                          <String, Object?>{
                            'provider': courier.info.displayName,
                          },
                        ),
                  onTap: courier.viewOnly
                      ? null
                      : () => _push(
                          context,
                          CourierAccountManageScreen(info: courier.info),
                        ),
                ),
            ],
          ),
        if (issues.isNotEmpty)
          GlassCard(
            margin: const EdgeInsets.only(top: EcomsbdSpacing.sm),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                Text(context.tr('int.issuesTitle'), style: EcomsbdType.label),
                for (final issue in issues)
                  IssueTile(
                    issue: issue,
                    queued: queued.contains(issue['id']),
                    canRetry: canRetry,
                    busy: busy,
                    onRetry: () => onRetry('${issue['id']}'),
                    onResolve: () => onResolve('${issue['id']}'),
                  ),
              ],
            ),
          ),
      ],
    );
  }
}

// ---------------------------------------------------------------- layout --

/// A hub row: mark, name, state, and — on lines of their own, so nothing is
/// cut off on a small phone or in Bangla — what the state means and the one
/// action that moves it forward.
class _HubTile extends StatelessWidget {
  const _HubTile({
    required this.leading,
    required this.title,
    required this.status,
    super.key,
    this.subtitle,
    this.note,
    this.noteIsProblem = false,
    this.action,
    this.onTap,
  });

  final Widget leading;
  final String title;
  final String? subtitle;
  final Widget status;
  final String? note;
  final bool noteIsProblem;
  final Widget? action;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 15, vertical: 11),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                leading,
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: <Widget>[
                      Text(
                        title,
                        style: EcomsbdType.bodyStrong,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      if (subtitle != null && subtitle!.isNotEmpty)
                        Text(
                          subtitle!,
                          style: EcomsbdType.caption.copyWith(
                            color: EcomsbdColors.muted,
                          ),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                // Capped, so a long state in Bangla shortens the chip rather
                // than pushing the name off a small phone. The state stands
                // in for a chevron, as on every list row in the app.
                ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 140),
                  child: status,
                ),
              ],
            ),
            if (note != null) ...<Widget>[
              const SizedBox(height: EcomsbdSpacing.xs),
              Text(
                note!,
                style: EcomsbdType.caption.copyWith(
                  color: noteIsProblem
                      ? EcomsbdColors.red
                      : EcomsbdColors.muted,
                  height: 1.4,
                ),
              ),
            ],
            if (action != null) ...<Widget>[
              const SizedBox(height: EcomsbdSpacing.sm),
              action!,
            ],
          ],
        ),
      ),
    );
  }
}

// -------------------------------------- shared with Home, Inbox and Money --

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
/// Used by the Inbox.
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

void _push(BuildContext context, Widget page) {
  unawaited(
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => page)),
  );
}
