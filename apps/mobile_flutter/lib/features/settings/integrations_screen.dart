import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../design/tokens.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/app_strings_data.dart';
import 'integration_setup_screens.dart';

/// One store or channel connection: its health, its sync, its problems, and
/// every fix — signing in again, new keys, a website's API key and webhook,
/// disconnecting — made right here on the phone. Listed from the Connections &
/// Integrations hub (`ConnectionsScreen`).
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
            if (manage &&
                c['provider'] != 'CUSTOM_WEBSITE' &&
                const <String>{
                  'AUTH_EXPIRED',
                  'PENDING',
                  'DISCONNECTED',
                }.contains(state))
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: FilledButton.tonal(
                  key: const Key('integration-reconnect'),
                  onPressed: _busy
                      ? null
                      : () async {
                          final changed = await startIntegrationSetup(
                            context,
                            '${c['provider']}',
                            connectionId: widget.id,
                            initialAddress: c['account_id'] as String?,
                          );
                          if (changed) await _run(_load);
                        },
                  child: Text(
                    context.tr(
                      c['provider'] == 'WHATSAPP'
                          ? 'ics.waReconnect'
                          : state == 'PENDING'
                          ? 'ics.continueSetup'
                          : 'ca.reconnect',
                    ),
                  ),
                ),
              ),
            if (detail?['custom'] is Map<String, dynamic>)
              CustomWebsitePanel(
                connectionId: widget.id,
                connection: c,
                custom: detail!['custom'] as Map<String, dynamic>,
                busy: _busy,
                run: _run,
                reload: _load,
              ),
            if (c['provider'] == 'MESSENGER' || c['provider'] == 'WHATSAPP')
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  context.tr('ics.chatNote'),
                  style: const TextStyle(color: EcomsbdColors.muted),
                ),
              ),
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
            if (manage && state != 'DISCONNECTED') ...[
              const SizedBox(height: 16),
              OutlinedButton.icon(
                key: const Key('integration-disconnect'),
                icon: const Icon(Icons.link_off_rounded, size: 18),
                label: Text(context.tr('ics.disconnect')),
                style: OutlinedButton.styleFrom(
                  foregroundColor: EcomsbdColors.red,
                ),
                onPressed: _busy ? null : _disconnect,
              ),
            ],
          ],
        ],
      ),
    );
  }

  Future<void> _disconnect() async {
    final sure = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(context.tr('ics.disconnectTitle')),
        content: Text(
          context.tr(
            (_detail?['connection'] as Map?)?['provider'] == 'WHATSAPP'
                ? 'ics.waDisconnectBody'
                : 'ics.disconnectBody',
          ),
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: Text(context.tr('common.cancel')),
          ),
          TextButton(
            key: const Key('integration-disconnect-confirm'),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: Text(context.tr('ics.disconnect')),
          ),
        ],
      ),
    );
    if (sure != true) return;
    await _run(() async {
      await ref
          .read(apiClientProvider)
          .post('/integrations/${widget.id}/disconnect');
      await _load();
    });
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

String when(BuildContext context, Object? value) {
  final parsed = value is String ? DateTime.tryParse(value)?.toLocal() : null;
  if (parsed == null) return context.tr('int.never');
  String two(int n) => n.toString().padLeft(2, '0');
  return '${parsed.year}-${two(parsed.month)}-${two(parsed.day)} '
      '${two(parsed.hour)}:${two(parsed.minute)}';
}

// ------------------------------------------------------- Custom website --

/// Everything a developer needs to send orders from a website, and the
/// checks that it works: the API address, the key's header, whether the first
/// request and first order arrived, the outbound webhook, and the actions —
/// test webhook, test order, new key, go live. All of it is the server's own
/// state; the API key itself is never shown again after it was created.
class CustomWebsitePanel extends ConsumerStatefulWidget {
  const CustomWebsitePanel({
    required this.connectionId,
    required this.connection,
    required this.custom,
    required this.busy,
    required this.run,
    required this.reload,
    super.key,
  });

  final String connectionId;
  final Map<String, dynamic> connection;
  final Map<String, dynamic> custom;
  final bool busy;
  final Future<void> Function(Future<void> Function()) run;
  final Future<void> Function() reload;

  @override
  ConsumerState<CustomWebsitePanel> createState() => _CustomWebsitePanelState();
}

class _CustomWebsitePanelState extends ConsumerState<CustomWebsitePanel> {
  late final TextEditingController _hook = TextEditingController(
    text: widget.custom['webhook_url'] as String? ?? '',
  );
  String? _result;

  @override
  void dispose() {
    _hook.dispose();
    super.dispose();
  }

  String get _base => '/integrations/${widget.connectionId}';

  Future<void> _secretOnce(String title, String secret) async {
    await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => SecretOnceScreen(title: title, secret: secret),
      ),
    );
  }

  void _act(Future<void> Function() work) => unawaited(widget.run(work));

  void _saveWebhook() => _act(() async {
    final result = await ref
        .read(apiClientProvider)
        .post(
          '$_base/webhook',
          body: <String, dynamic>{'url': _hook.text.trim()},
        );
    if (mounted) {
      await _secretOnce(
        context.tr('ics.signingSecret'),
        '${result['signing_secret']}',
      );
    }
    await widget.reload();
  });

  void _testWebhook() => _act(() async {
    await ref.read(apiClientProvider).post('$_base/webhook/test');
    if (mounted) setState(() => _result = context.tr('ics.webhookSent'));
    await widget.reload();
  });

  void _testOrder() => _act(() async {
    final result = await ref
        .read(apiClientProvider)
        .post('$_base/test-order', body: <String, dynamic>{});
    final problems = (result['problems'] as List<dynamic>? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map((p) => '${p['code']}')
        .join(', ');
    if (mounted) {
      setState(
        () => _result = result['valid'] == true
            ? context.tr('ics.testOrderOk')
            : context.tr('ics.testOrderBad', <String, Object?>{
                'problems': problems,
              }),
      );
    }
  });

  void _rotate() => _act(() async {
    final sure = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(context.tr('ics.rotateTitle')),
        content: Text(context.tr('ics.rotateBody')),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: Text(context.tr('common.cancel')),
          ),
          TextButton(
            key: const Key('ics-rotate-confirm'),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: Text(context.tr('ics.rotate')),
          ),
        ],
      ),
    );
    if (sure != true) return;
    final result = await ref.read(apiClientProvider).post('$_base/api-key');
    if (mounted) {
      await _secretOnce(context.tr('ics.keyTitle'), '${result['api_key']}');
    }
    await widget.reload();
  });

  void _goLive() => _act(() async {
    await ref.read(apiClientProvider).post('$_base/go-live');
    await widget.reload();
  });

  @override
  Widget build(BuildContext context) {
    final custom = widget.custom;
    final deliveries = custom['deliveries'] as Map<String, dynamic>? ?? {};
    final topics =
        ((custom['webhook_topics'] as List<dynamic>?)?.isNotEmpty ?? false)
        ? custom['webhook_topics'] as List<dynamic>
        : (custom['topics'] as List<dynamic>? ?? const <dynamic>[]);
    final live = widget.connection['state'] == 'CONNECTED';
    final busy = widget.busy;

    Widget fact(String label, String value, {bool copy = false}) => Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  label,
                  style: const TextStyle(
                    color: EcomsbdColors.muted,
                    fontSize: 12,
                  ),
                ),
                SelectableText(value),
              ],
            ),
          ),
          if (copy)
            IconButton(
              tooltip: context.tr('ics.copy'),
              icon: const Icon(Icons.copy_rounded, size: 18),
              onPressed: () =>
                  unawaited(Clipboard.setData(ClipboardData(text: value))),
            ),
        ],
      ),
    );

    Widget check(String label, bool ok) => Row(
      children: <Widget>[
        Icon(
          ok ? Icons.check_circle_rounded : Icons.radio_button_unchecked,
          size: 18,
          color: ok ? EcomsbdColors.green : EcomsbdColors.muted,
        ),
        const SizedBox(width: 6),
        Expanded(child: Text(label)),
      ],
    );

    return Column(
      key: const Key('custom-website-panel'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        const SizedBox(height: 16),
        Text(
          context.tr('ics.siteSetup'),
          style: Theme.of(context).textTheme.titleMedium,
        ),
        fact(
          context.tr('ics.apiBase'),
          '${custom['api_base_url']}',
          copy: true,
        ),
        fact(
          context.tr('ics.ordersEndpoint'),
          '${custom['orders_endpoint']}',
          copy: true,
        ),
        fact(context.tr('ics.authHeader'), 'Authorization: Bearer <API key>'),
        Padding(
          padding: const EdgeInsets.only(top: 4),
          child: Text(
            context.tr('ics.authNote'),
            style: const TextStyle(color: EcomsbdColors.muted, fontSize: 12),
          ),
        ),
        fact(context.tr('ics.connectionId'), widget.connectionId, copy: true),
        const SizedBox(height: 12),
        Text(
          context.tr('ics.checklist'),
          style: Theme.of(context).textTheme.titleSmall,
        ),
        check(context.tr('ics.check.key'), custom['key_active'] == true),
        check(
          context.tr('ics.check.firstRequest', <String, Object?>{
            'when': when(context, custom['last_api_call_at']),
          }),
          custom['last_api_call_at'] != null,
        ),
        check(
          context.tr('ics.check.firstOrder', <String, Object?>{
            'when': when(context, custom['last_order_at']),
          }),
          custom['last_order_at'] != null,
        ),
        check(context.tr('ics.check.live'), live),
        const SizedBox(height: 12),
        Text(
          context.tr('ics.webhook'),
          style: Theme.of(context).textTheme.titleSmall,
        ),
        TextField(
          key: const Key('ics-webhook-url'),
          controller: _hook,
          keyboardType: TextInputType.url,
          decoration: InputDecoration(
            labelText: context.tr('ics.webhookUrl'),
            hintText: 'https://mystore.com/ecomsbd-webhook',
          ),
        ),
        if (topics.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text(
              context.tr('ics.topics', <String, Object?>{
                'topics': topics.join(', '),
              }),
              style: const TextStyle(color: EcomsbdColors.muted, fontSize: 12),
            ),
          ),
        if (deliveries['last_status'] != null)
          Text(
            context.tr('ics.lastDelivery', <String, Object?>{
              'status': '${deliveries['last_status']}',
              'when': when(context, deliveries['last_at']),
            }),
            style: const TextStyle(fontSize: 12),
          ),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 4,
          children: <Widget>[
            FilledButton.tonal(
              key: const Key('ics-webhook-save'),
              onPressed: busy ? null : _saveWebhook,
              child: Text(context.tr('ics.webhookSave')),
            ),
            OutlinedButton(
              key: const Key('ics-webhook-test'),
              onPressed: busy || custom['webhook_url'] == null
                  ? null
                  : _testWebhook,
              child: Text(context.tr('ics.webhookTest')),
            ),
            OutlinedButton(
              key: const Key('ics-test-order'),
              onPressed: busy ? null : _testOrder,
              child: Text(context.tr('ics.testOrder')),
            ),
            OutlinedButton(
              key: const Key('ics-rotate'),
              onPressed: busy ? null : _rotate,
              child: Text(context.tr('ics.rotate')),
            ),
            if (!live && widget.connection['state'] != 'DISCONNECTED')
              FilledButton(
                key: const Key('ics-go-live'),
                onPressed: busy ? null : _goLive,
                child: Text(context.tr('ics.goLive')),
              ),
          ],
        ),
        if (_result != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(_result!, key: const Key('ics-result')),
          ),
      ],
    );
  }
}
