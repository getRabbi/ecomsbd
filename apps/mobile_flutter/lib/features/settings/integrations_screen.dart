import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../core/env.dart';
import '../../design/tokens.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/app_strings_data.dart';

/// One store or channel connection: its health, its sync, its problems, and
/// the fixes that can be made from a phone. Listed from the Connections &
/// Integrations hub (`ConnectionsScreen`); connecting a new one, and signing
/// in to a provider again, happen on the web.
class IntegrationDetailScreen extends ConsumerStatefulWidget {
  const IntegrationDetailScreen({super.key, required this.id});

  final String id;

  @override
  ConsumerState<IntegrationDetailScreen> createState() => _DetailState();
}

class _DetailState extends ConsumerState<IntegrationDetailScreen> {
  Map<String, dynamic>? _detail;
  Map<String, dynamic>? _sync;
  List<dynamic> _conflicts = const [];
  Map<String, dynamic>? _test;
  final Set<String> _queued = <String>{};
  final _key = TextEditingController();
  final _secret = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    Future.microtask(() => _run(_load));
  }

  @override
  void dispose() {
    _key.dispose();
    _secret.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final api = ref.read(apiClientProvider);
    final result = await api.get('/integrations/${widget.id}');
    final provider = (result['connection'] as Map?)?['provider'];
    Map<String, dynamic>? syncView;
    List<dynamic> open = const [];
    if (provider == 'SHOPIFY' || provider == 'WOOCOMMERCE') {
      syncView = await api.get('/integrations/${widget.id}/sync-settings');
    }
    if (provider != 'MESSENGER') {
      final conflicts = await api.get(
        '/integrations/conflicts',
        query: {'connection_id': widget.id},
      );
      open = conflicts['items'] as List;
    }
    if (mounted) {
      setState(() {
        _detail = result;
        _sync = syncView;
        _conflicts = open;
      });
    }
  }

  Future<void> _run(Future<void> Function() work) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await work();
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = explain(context, e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final detail = _detail;
    final c = detail?['connection'] as Map<String, dynamic>?;
    final manage = detail?['can_manage'] == true;
    final canRetry = detail?['can_retry'] == true;
    final state = '${c?['state']}';
    final events = (detail?['events'] as List?) ?? const [];
    return Scaffold(
      appBar: AppBar(title: Text('${c?['name'] ?? context.tr('int.title')}')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_busy) const LinearProgressIndicator(),
          if (_error != null)
            Text(_error!, style: const TextStyle(color: EcomsbdColors.red)),
          if (c != null) ...[
            Row(
              children: [
                Expanded(
                  child: Text(
                    [
                      context.tr('int.provider.${c['provider']}'),
                      if (c['account_name'] != null) '${c['account_name']}',
                    ].join(' · '),
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                HealthBadge(health: '${c['health']}'),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              context.tr('int.lastContact', {
                'when': when(context, c['last_success_at']),
              }),
            ),
            Text(
              context.tr('int.lastUpdate', {
                'when': when(context, c['last_webhook_at']),
              }),
            ),
            if (c['provider'] == 'SHOPIFY' || c['provider'] == 'WOOCOMMERCE')
              Text(
                context.tr('int.lastSync', {
                  'when': when(context, c['last_sync_at']),
                }),
              ),
            if (c['last_error_code'] != null)
              Text(
                context.tr('int.latestProblem', {
                  'problem': codeLabel(context, '${c['last_error_code']}'),
                }),
              ),
            const SizedBox(height: 12),
            if (manage && state != 'PENDING' && state != 'DISCONNECTED')
              FilledButton(
                onPressed: _busy
                    ? null
                    : () => _run(() async {
                        final result = await ref
                            .read(apiClientProvider)
                            .post('/integrations/${widget.id}/test');
                        _test = result;
                        await _load();
                      }),
                child: Text(context.tr('int.test')),
              ),
            if (_test != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  context.tr(
                    _test!['ok'] == true ? 'int.testOk' : 'int.testFailed',
                  ),
                  style: TextStyle(
                    color: _test!['ok'] == true
                        ? EcomsbdColors.green
                        : EcomsbdColors.red,
                  ),
                ),
              ),
            if (state == 'AUTH_EXPIRED' &&
                c['provider'] == 'WOOCOMMERCE' &&
                manage)
              ..._wooKeys(context),
            if (state == 'AUTH_EXPIRED' && c['provider'] != 'WOOCOMMERCE') ...[
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(context.tr('int.reconnectWeb')),
              ),
              if (manage)
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(
                    key: const Key('integration-reconnect-web'),
                    icon: const Icon(Icons.open_in_new_rounded, size: 16),
                    label: Text(context.tr('conn.setUpOnWeb')),
                    onPressed: () =>
                        openWebDashboard(context, '/integrations/${widget.id}'),
                  ),
                ),
            ],
            if (_sync != null) ..._syncSection(context, _sync!),
            if (_conflicts.isNotEmpty) ..._conflictSection(context),
            const SizedBox(height: 16),
            Text(
              context.tr('int.activity'),
              style: Theme.of(context).textTheme.titleMedium,
            ),
            if (events.isEmpty) Text(context.tr('int.noIssues')),
            for (final event in events)
              IssueTile(
                issue: event as Map<String, dynamic>,
                queued: _queued.contains(event['id']),
                canRetry: canRetry,
                busy: _busy,
                onRetry: () => _run(() async {
                  await ref
                      .read(apiClientProvider)
                      .post('/integrations/events/${event['id']}/retry');
                  _queued.add('${event['id']}');
                }),
                onResolve: () => _run(() async {
                  await ref
                      .read(apiClientProvider)
                      .post('/integrations/events/${event['id']}/resolve');
                  await _load();
                }),
              ),
          ],
        ],
      ),
    );
  }

  List<Widget> _syncSection(BuildContext context, Map<String, dynamic> view) {
    final settings = view['settings'] as Map;
    final links = (view['links'] as Map?) ?? const {};
    return [
      const SizedBox(height: 16),
      Text(
        context.tr('int.sync.title'),
        style: Theme.of(context).textTheme.titleMedium,
      ),
      Text(
        context.tr('int.sync.stock', {
          'value': context.tr('int.sync.stock.${settings['inventory']}'),
        }),
      ),
      Text(
        context.tr('int.sync.status', {
          'value': context.tr('int.sync.status.${settings['order_status']}'),
        }),
      ),
      Text(
        context.tr('int.sync.tracking', {
          'value': context.tr('int.sync.tracking.${settings['fulfillment']}'),
        }),
      ),
      Text(
        context.tr('int.sync.mapped', {
          'matched': links['MATCHED'] ?? 0,
          'unmatched': links['UNMATCHED'] ?? 0,
        }),
      ),
      if (settings['inventory'] != 'NONE')
        Text(
          context.tr('int.sync.lastStock', {
            'when': when(context, view['inventory_synced_at']),
          }),
        ),
      Text(
        context.tr('int.sync.conflicts', {
          'count': view['open_conflicts'] ?? 0,
        }),
      ),
      Align(
        alignment: Alignment.centerLeft,
        child: TextButton.icon(
          icon: const Icon(Icons.link),
          label: Text(context.tr('int.sync.mappings')),
          onPressed: () => Navigator.of(context).push(
            MaterialPageRoute<void>(
              builder: (_) => IntegrationMappingsScreen(id: widget.id),
            ),
          ),
        ),
      ),
    ];
  }

  List<Widget> _conflictSection(BuildContext context) => [
    const SizedBox(height: 8),
    Text(
      context.tr('int.sync.conflictsList'),
      style: Theme.of(context).textTheme.titleMedium,
    ),
    for (final conflict in _conflicts)
      ListTile(
        contentPadding: EdgeInsets.zero,
        leading: const Icon(Icons.call_split, color: EcomsbdColors.amber),
        title: Text(conflictLabel(context, '${conflict['kind']}')),
        subtitle: Text(when(context, conflict['updated_at'])),
      ),
    Text(
      context.tr('int.sync.conflictsWeb'),
      style: const TextStyle(color: EcomsbdColors.muted),
    ),
  ];

  List<Widget> _wooKeys(BuildContext context) => [
    const SizedBox(height: 12),
    Text(context.tr('int.wooKeys')),
    TextField(
      controller: _key,
      autocorrect: false,
      decoration: InputDecoration(labelText: context.tr('int.consumerKey')),
    ),
    TextField(
      controller: _secret,
      autocorrect: false,
      obscureText: true,
      decoration: InputDecoration(labelText: context.tr('int.consumerSecret')),
    ),
    const SizedBox(height: 8),
    FilledButton.tonal(
      onPressed: _busy
          ? null
          : () => _run(() async {
              final result = await ref
                  .read(apiClientProvider)
                  .post(
                    '/integrations/${widget.id}/woocommerce/keys',
                    body: {
                      'consumer_key': _key.text.trim(),
                      'consumer_secret': _secret.text.trim(),
                    },
                  );
              _secret.clear();
              _test = result;
              await _load();
            }),
      child: Text(context.tr('int.saveKeys')),
    ),
  ];
}

/// A retryable import puts the same store order through the same dedupe on
/// the server: it can finish an import, never duplicate one.
class IssueTile extends StatelessWidget {
  const IssueTile({
    super.key,
    required this.issue,
    required this.queued,
    required this.canRetry,
    required this.busy,
    required this.onRetry,
    required this.onResolve,
  });

  final Map<String, dynamic> issue;
  final bool queued;
  final bool canRetry;
  final bool busy;
  final VoidCallback onRetry;
  final VoidCallback onResolve;

  @override
  Widget build(BuildContext context) {
    final failed = issue['status'] == 'FAILED';
    final ref = issue['external_ref'];
    // Actions sit on their own row: two buttons beside the text do not fit a
    // 360px phone, least of all in Bangla.
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ListTile(
          contentPadding: EdgeInsets.zero,
          title: Text(
            issue['kind'] == 'OUTBOUND' &&
                    englishStrings.containsKey('int.op.${issue['operation']}')
                ? context.tr('int.op.${issue['operation']}')
                : issue['code'] != null
                ? codeLabel(context, '${issue['code']}')
                : context.tr('int.status.${issue['status']}'),
          ),
          subtitle: Text(
            [
              if (ref != null) context.tr('int.order', {'ref': ref}),
              when(context, issue['updated_at']),
              if (queued) context.tr('int.queued'),
            ].join(' · '),
          ),
        ),
        if (failed && canRetry && !queued)
          Wrap(
            spacing: 8,
            children: [
              if (issue['retryable'] == true)
                FilledButton.tonal(
                  onPressed: busy ? null : onRetry,
                  child: Text(context.tr('int.retry')),
                ),
              TextButton(
                onPressed: busy ? null : onResolve,
                child: Text(context.tr('int.resolve')),
              ),
            ],
          ),
        const Divider(),
      ],
    );
  }
}

class HealthBadge extends StatelessWidget {
  const HealthBadge({super.key, required this.health});

  final String health;

  @override
  Widget build(BuildContext context) {
    final (Color ink, Color soft) = switch (health) {
      'CONNECTED' => (EcomsbdColors.green, EcomsbdColors.greenSoft),
      'DEGRADED' ||
      'SETUP_INCOMPLETE' ||
      'DISABLED' => (EcomsbdColors.amber, EcomsbdColors.amberSoft),
      _ => (EcomsbdColors.red, EcomsbdColors.redSoft),
    };
    final key = 'int.health.$health';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: soft,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        englishStrings.containsKey(key) ? context.tr(key) : health,
        style: TextStyle(color: ink, fontSize: 12, fontWeight: FontWeight.w600),
      ),
    );
  }
}

/// Read-only list of how store products map to ecomsbd products. Matching
/// by hand stays on the web, where search and a keyboard are.
class IntegrationMappingsScreen extends ConsumerStatefulWidget {
  const IntegrationMappingsScreen({super.key, required this.id});

  final String id;

  @override
  ConsumerState<IntegrationMappingsScreen> createState() => _MappingsState();
}

class _MappingsState extends ConsumerState<IntegrationMappingsScreen> {
  List<dynamic>? _items;
  String? _error;

  @override
  void initState() {
    super.initState();
    Future.microtask(_load);
  }

  Future<void> _load() async {
    try {
      final result = await ref
          .read(apiClientProvider)
          .get('/integrations/${widget.id}/links');
      if (mounted) setState(() => _items = result['items'] as List);
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = explain(context, e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final items = _items;
    return Scaffold(
      appBar: AppBar(title: Text(context.tr('int.sync.mappings'))),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_error != null)
            Text(_error!, style: const TextStyle(color: EcomsbdColors.red)),
          if (items == null && _error == null) const LinearProgressIndicator(),
          if (items != null && items.isEmpty)
            Text(context.tr('int.sync.noMappings')),
          for (final link in items ?? const [])
            ListTile(
              contentPadding: EdgeInsets.zero,
              title: Text('${link['external_title']}'),
              subtitle: Text(
                [
                  if (link['external_sku'] != null) '${link['external_sku']}',
                  if (link['internal_name'] != null)
                    '→ ${link['internal_name']}',
                  if (link['external_qty'] != null)
                    context.tr('int.sync.storeStock', {
                      'count': link['external_qty'],
                    }),
                ].join(' · '),
              ),
              trailing: Text(
                context.tr('int.sync.link.${link['state']}'),
                style: TextStyle(
                  color: link['state'] == 'MATCHED'
                      ? EcomsbdColors.green
                      : link['state'] == 'CONFLICT'
                      ? EcomsbdColors.red
                      : EcomsbdColors.muted,
                ),
              ),
            ),
          Text(
            context.tr('int.sync.conflictsWeb'),
            style: const TextStyle(color: EcomsbdColors.muted),
          ),
        ],
      ),
    );
  }
}

String conflictLabel(BuildContext context, String kind) {
  final key = 'int.sync.kind.$kind';
  return englishStrings.containsKey(key) ? context.tr(key) : kind;
}

String codeLabel(BuildContext context, String code) {
  final key = 'int.code.$code';
  return context.tr(englishStrings.containsKey(key) ? key : 'int.code.OTHER');
}

/// The server's code in the seller's words, else the server's own message.
String explain(BuildContext context, ApiError error) {
  final code = error.details?['code'] ?? error.details?['blocker'];
  if (code is String && englishStrings.containsKey('int.code.$code')) {
    return context.tr('int.code.$code');
  }
  return context.strings.locale == AppLocale.bn
      ? error.messageBn
      : error.messageEn;
}

/// Opens [path] on the ecomsbd web dashboard in the browser.
///
/// Only real dashboard pages are linked. When no browser opens, the address is
/// shown instead, so the seller can type it on any device.
Future<void> openWebDashboard(BuildContext context, String path) async {
  final uri = Uri.https(Env.webDashboardHost, path);
  var opened = false;
  try {
    opened = await launchUrl(uri, mode: LaunchMode.externalApplication);
  } on Object {
    // Shown below.
  }
  if (!opened && context.mounted) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          context.tr('conn.webOpenError', <String, Object?>{'url': '$uri'}),
        ),
      ),
    );
  }
}

String when(BuildContext context, Object? value) {
  final parsed = value is String ? DateTime.tryParse(value)?.toLocal() : null;
  if (parsed == null) return context.tr('int.never');
  String two(int n) => n.toString().padLeft(2, '0');
  return '${parsed.year}-${two(parsed.month)}-${two(parsed.day)} '
      '${two(parsed.hour)}:${two(parsed.minute)}';
}
