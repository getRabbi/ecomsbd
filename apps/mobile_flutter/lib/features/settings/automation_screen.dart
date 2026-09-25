import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/automation_strings.dart';

/// Automation on the phone (V3.4): workflows on/off, run status, failures and
/// retry, and simple recipes. Building and editing stays on the web; the
/// server decides who may do what.
class AutomationScreen extends ConsumerStatefulWidget {
  const AutomationScreen({super.key});
  @override
  ConsumerState<AutomationScreen> createState() => _AutomationState();
}

class _AutomationState extends ConsumerState<AutomationScreen> {
  List<dynamic> _workflows = [];
  List<dynamic> _failures = [];
  List<dynamic> _recipes = [];
  Map<String, dynamic>? _metrics;
  bool _manage = false;
  bool _operate = false;
  bool _busy = false;
  String? _error;
  String? _notice;

  bool get _bn => context.strings.locale == AppLocale.bn;

  @override
  void initState() {
    super.initState();
    Future.microtask(() => _run(_load));
  }

  Future<void> _load() async {
    final api = ref.read(apiClientProvider);
    final data = await Future.wait([
      api.get('/automation/catalog'),
      api.get('/automation/workflows'),
      api.get('/automation/executions', query: {'status': 'FAILED'}),
      api.get('/automation/recipes'),
      api.get('/automation/metrics'),
    ]);
    if (!mounted) return;
    setState(() {
      _manage = data[0]['can_manage'] == true;
      _operate = data[0]['can_operate'] == true;
      _workflows = data[1]['items'] as List;
      _failures = data[2]['items'] as List;
      _recipes = data[3]['items'] as List;
      _metrics = data[4];
    });
  }

  Future<void> _run(Future<void> Function() work) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
      // A notice describes the last action only; "Turned on" must not stay
      // beside a workflow that has since been switched off.
      _notice = null;
    });
    try {
      await work();
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = _bn ? e.messageBn : e.messageEn);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _label(String prefix, String? raw) {
    if (raw == null || raw.isEmpty) return '';
    final key = '$prefix.$raw';
    return automationEn.containsKey(key) ? context.tr(key) : raw;
  }

  String _reason(String? code) {
    if (code == null || code.isEmpty) return '';
    final head = code.split(':').first;
    final key = 'auto.reason.$head';
    return automationEn.containsKey(key)
        ? context.tr(key)
        : context.tr('auto.reason.OTHER', {'code': head});
  }

  @override
  Widget build(BuildContext context) => DefaultTabController(
    length: 3,
    child: Scaffold(
      appBar: AppBar(
        title: Text(context.tr('auto.title')),
        actions: [
          IconButton(
            onPressed: _busy ? null : () => _run(_load),
            icon: const Icon(Icons.refresh),
          ),
        ],
        bottom: TabBar(
          tabs: [
            Tab(text: context.tr('auto.tab.workflows')),
            Tab(text: context.tr('auto.tab.failures')),
            Tab(text: context.tr('auto.tab.recipes')),
          ],
        ),
      ),
      body: Column(
        children: [
          if (_busy) const LinearProgressIndicator(),
          if (_error != null)
            Padding(padding: const EdgeInsets.all(12), child: Text(_error!)),
          if (_notice != null)
            Padding(padding: const EdgeInsets.all(12), child: Text(_notice!)),
          Expanded(
            child: TabBarView(
              children: [_workflowsTab(), _failuresTab(), _recipesTab()],
            ),
          ),
        ],
      ),
    ),
  );

  Widget _workflowsTab() => ListView(
    padding: const EdgeInsets.all(16),
    children: [
      if (_metrics != null)
        Text(
          context.tr('auto.metrics', {
            'runs': _metrics!['executions'],
            'failed': _metrics!['failed'],
            'waiting': _metrics!['waiting'],
          }),
        ),
      const SizedBox(height: 8),
      // The role is known only once the catalog loads; until then an owner
      // would be told they can only watch.
      if (_metrics != null)
        Text(context.tr(_manage ? 'auto.webOnly' : 'auto.readOnly')),
      if (!_busy && _workflows.isEmpty) Text(context.tr('auto.empty')),
      for (final row in _workflows) _workflowTile(row as Map<String, dynamic>),
    ],
  );

  Widget _workflowTile(Map<String, dynamic> row) {
    final runs = (row['runs_7d'] as Map?) ?? const {};
    final published = row['published_version'];
    final subtitle = [
      _label('auto.trigger', row['trigger'] as String?),
      published == null
          ? context.tr('auto.notPublished')
          : context.tr('auto.version', {'n': published}),
      context.tr('auto.runs7d', {
        'ok': runs['SUCCEEDED'] ?? 0,
        'failed': runs['FAILED'] ?? 0,
        'waiting': runs['WAITING'] ?? 0,
      }),
    ].join('\n');
    return SwitchListTile(
      title: Text('${row['name']}'),
      subtitle: Text(subtitle),
      isThreeLine: true,
      value: row['enabled'] == true,
      onChanged: _busy || !_manage || published == null
          ? null
          : (enabled) => _run(() async {
              await ref
                  .read(apiClientProvider)
                  .patch(
                    '/automation/workflows/${row['id']}',
                    body: {'enabled': enabled},
                  );
              await _load();
            }),
    );
  }

  Widget _failuresTab() => ListView(
    padding: const EdgeInsets.all(16),
    children: [
      if (!_busy && _failures.isEmpty) Text(context.tr('auto.noFailures')),
      for (final row in _failures)
        Card(
          child: ListTile(
            title: Text('${row['workflow_name'] ?? ''}'),
            subtitle: Text(
              [
                _label('auto.status', row['status'] as String?),
                _reason(row['last_error'] as String?),
                context.tr(
                  row['retryable'] == true
                      ? 'auto.retryable'
                      : 'auto.notRetryable',
                ),
              ].where((part) => part.isNotEmpty).join(' · '),
            ),
            onTap: () => _openRun('${row['id']}'),
          ),
        ),
    ],
  );

  Future<void> _openRun(String id) async {
    Map<String, dynamic>? detail;
    await _run(() async {
      detail = await ref
          .read(apiClientProvider)
          .get('/automation/executions/$id');
    });
    if (!mounted || detail == null) return;
    final run = detail!;
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (sheet) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: ListView(
            shrinkWrap: true,
            children: [
              Text(
                '${run['workflow_name'] ?? ''}',
                style: Theme.of(sheet).textTheme.titleMedium,
              ),
              Text(
                '${_label('auto.status', run['status'] as String?)} · ${_reason(run['last_error'] as String?)}',
              ),
              const SizedBox(height: 8),
              Text(context.tr('auto.steps')),
              for (final step in (run['steps'] as List? ?? const []))
                Text(
                  '• ${step['step_id']}: ${_label('auto.status', step['status'] as String?)} ${_reason(step['outcome'] as String?)}',
                ),
              const SizedBox(height: 12),
              if (_operate &&
                  run['status'] == 'FAILED' &&
                  run['retryable'] == true)
                FilledButton(
                  onPressed: () async {
                    Navigator.of(sheet).pop();
                    await _run(() async {
                      await ref
                          .read(apiClientProvider)
                          .post('/automation/executions/$id/retry');
                      if (mounted) {
                        setState(() => _notice = context.tr('auto.retried'));
                      }
                      await _load();
                    });
                  },
                  child: Text(context.tr('auto.retry')),
                ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _recipesTab() => ListView(
    padding: const EdgeInsets.all(16),
    children: [
      for (final recipe in _recipes)
        Card(
          child: ListTile(
            title: Text('${(recipe['name'] as Map)[_bn ? 'bn' : 'en']}'),
            subtitle: Text(
              ((recipe['needs'] as List?) ?? const []).isNotEmpty &&
                      recipe['installed'] != true
                  ? context.tr('auto.recipe.webSetup')
                  : _label('auto.trigger', recipe['trigger'] as String?),
            ),
            trailing: recipe['installed'] == true
                ? Chip(label: Text(context.tr('auto.recipe.inUse')))
                : (_manage && ((recipe['needs'] as List?) ?? const []).isEmpty)
                ? FilledButton(
                    onPressed: _busy
                        ? null
                        : () => _run(() async {
                            final result = await ref
                                .read(apiClientProvider)
                                .post(
                                  '/automation/recipes/${recipe['key']}/install',
                                  body: {'locale': _bn ? 'bn' : 'en'},
                                );
                            final blocker = result['blocker'] as String?;
                            if (mounted) {
                              setState(
                                () => _notice = blocker == null
                                    ? context.tr('auto.recipe.done')
                                    : context.tr('auto.recipe.draft', {
                                        'reason': _reason(blocker),
                                      }),
                              );
                            }
                            await _load();
                          }),
                    child: Text(context.tr('auto.recipe.use')),
                  )
                : null,
          ),
        ),
    ],
  );
}
