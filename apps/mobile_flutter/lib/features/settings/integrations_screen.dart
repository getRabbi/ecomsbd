import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../design/tokens.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/app_strings_data.dart';

/// The phone's view of the Integrations Hub: what is connected, whether it is
/// healthy, and what needs fixing. Adding a store, provider sign-in and
/// website developer setup are on the web, where a keyboard and copy-paste are.
class IntegrationsScreen extends ConsumerStatefulWidget {
  const IntegrationsScreen({super.key});

  @override
  ConsumerState<IntegrationsScreen> createState() => _IntegrationsState();
}

class _IntegrationsState extends ConsumerState<IntegrationsScreen> {
  Map<String, dynamic>? _hub;
  List<dynamic> _issues = const [];
  final Set<String> _queued = <String>{};
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    Future.microtask(() => _run(_load));
  }

  Future<void> _load() async {
    final api = ref.read(apiClientProvider);
    final hub = await api.get('/integrations');
    final issues = await api.get('/integrations/issues');
    if (mounted) {
      setState(() {
        _hub = hub;
        _issues = issues['items'] as List;
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
    final hub = _hub;
    final items = (hub?['items'] as List?) ?? const [];
    final providers = (hub?['providers'] as List?) ?? const [];
    final canRetry = hub?['can_retry'] == true;
    final blocked = providers.where(
      (p) =>
          p['available'] != true &&
          !items.any((c) => c['provider'] == p['provider']),
    );
    return Scaffold(
      appBar: AppBar(
        title: Text(context.tr('int.title')),
        actions: [
          IconButton(
            tooltip: context.tr('int.title'),
            onPressed: _busy ? null : () => _run(_load),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (_busy) const LinearProgressIndicator(),
            if (_error != null)
              Text(_error!, style: const TextStyle(color: EcomsbdColors.red)),
            Text(context.tr('int.subtitle')),
            const SizedBox(height: 4),
            Text(
              context.tr('int.webOnly'),
              style: const TextStyle(color: EcomsbdColors.muted),
            ),
            const SizedBox(height: 12),
            if (hub != null && items.isEmpty) Text(context.tr('int.empty')),
            for (final c in items)
              Card(
                child: ListTile(
                  key: ValueKey('connection-${c['id']}'),
                  title: Wrap(
                    spacing: 8,
                    runSpacing: 4,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: [
                      Text('${c['name']}'),
                      HealthBadge(health: '${c['health']}'),
                    ],
                  ),
                  subtitle: Text(
                    [
                      [
                        context.tr('int.provider.${c['provider']}'),
                        if (c['account_name'] != null) '${c['account_name']}',
                      ].join(' · '),
                      context.tr('int.ordersToday', {
                        'count': c['orders_today'],
                      }),
                      context.tr('int.problems', {'count': c['open_issues']}),
                      context.tr('int.lastSync', {
                        'when': when(context, c['last_sync_at']),
                      }),
                    ].join('\n'),
                  ),
                  isThreeLine: true,
                  onTap: () async {
                    await Navigator.of(context).push(
                      MaterialPageRoute<void>(
                        builder: (_) =>
                            IntegrationDetailScreen(id: '${c['id']}'),
                      ),
                    );
                    if (mounted) await _run(_load);
                  },
                ),
              ),
            for (final p in blocked)
              ListTile(
                title: Text(context.tr('int.provider.${p['provider']}')),
                subtitle: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const HealthBadge(health: 'OFFICIAL_SETUP_REQUIRED'),
                    Text(context.tr('int.setupRequired')),
                  ],
                ),
              ),
            const SizedBox(height: 16),
            Text(
              context.tr('int.issuesTitle'),
              style: Theme.of(context).textTheme.titleMedium,
            ),
            if (hub != null && _issues.isEmpty)
              Text(context.tr('int.noIssues')),
            for (final issue in _issues)
              IssueTile(
                issue: issue as Map<String, dynamic>,
                queued: _queued.contains(issue['id']),
                canRetry: canRetry,
                busy: _busy,
                onRetry: () => _run(() async {
                  await ref
                      .read(apiClientProvider)
                      .post('/integrations/events/${issue['id']}/retry');
                  _queued.add('${issue['id']}');
                }),
                onResolve: () => _run(() async {
                  await ref
                      .read(apiClientProvider)
                      .post('/integrations/events/${issue['id']}/resolve');
                  await _load();
                }),
              ),
          ],
        ),
      ),
    );
  }
}

class IntegrationDetailScreen extends ConsumerStatefulWidget {
  const IntegrationDetailScreen({super.key, required this.id});

  final String id;

  @override
  ConsumerState<IntegrationDetailScreen> createState() => _DetailState();
}

class _DetailState extends ConsumerState<IntegrationDetailScreen> {
  Map<String, dynamic>? _detail;
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
    final result = await ref
        .read(apiClientProvider)
        .get('/integrations/${widget.id}');
    if (mounted) setState(() => _detail = result);
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
            if (state == 'AUTH_EXPIRED' && c['provider'] != 'WOOCOMMERCE')
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(context.tr('int.reconnectWeb')),
              ),
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
            issue['code'] != null
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
      'DISABLED' ||
      'OFFICIAL_SETUP_REQUIRED' => (
        EcomsbdColors.amber,
        EcomsbdColors.amberSoft,
      ),
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
