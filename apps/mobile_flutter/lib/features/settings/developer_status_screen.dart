import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';

final developerHealthProvider =
    FutureProvider.autoDispose<Map<String, dynamic>>(
      (ref) => ref.watch(apiClientProvider).get('/developers/health'),
    );

final failedDeliveriesProvider = FutureProvider.autoDispose<List<dynamic>>(
  (ref) async =>
      (await ref
              .watch(apiClientProvider)
              .get(
                '/developers/deliveries',
                query: <String, dynamic>{'status': 'FAILED'},
              ))['items']
          as List? ??
      const <dynamic>[],
);

/// API and webhook status (V3.8). Setting up keys and webhooks is web-only;
/// the phone shows whether they work and can retry a failed delivery.
class DeveloperStatusScreen extends ConsumerWidget {
  const DeveloperStatusScreen({super.key});

  String _error(BuildContext context, Object error) {
    final bn = context.strings.locale == AppLocale.bn;
    if (error is ApiError) return bn ? error.messageBn : error.messageEn;
    return bn ? 'লোড করা যায়নি' : 'Could not load';
  }

  String _when(Object? value) {
    final parsed = value is String ? DateTime.tryParse(value) : null;
    if (parsed == null) return '—';
    final l = parsed.toLocal();
    String two(int n) => n.toString().padLeft(2, '0');
    return '${l.year}-${two(l.month)}-${two(l.day)} ${two(l.hour)}:${two(l.minute)}';
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final health = ref.watch(developerHealthProvider);
    final failed = ref.watch(failedDeliveriesProvider);
    return Scaffold(
      appBar: AppBar(title: Text(context.tr('dx.title'))),
      body: health.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(child: Text(_error(context, e))),
        data: (data) {
          final api = data['api'] as Map<String, dynamic>? ?? {};
          final hooks = data['webhooks'] as List? ?? const [];
          final connections = data['connections'] as List? ?? const [];
          return RefreshIndicator(
            onRefresh: () async {
              ref
                ..invalidate(developerHealthProvider)
                ..invalidate(failedDeliveriesProvider);
            },
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text(context.tr('dx.webOnly')),
                const SizedBox(height: 12),
                Card(
                  child: ListTile(
                    leading: Icon(
                      api['state'] == 'ACTIVE'
                          ? Icons.check_circle_outline
                          : Icons.error_outline,
                    ),
                    title: Text(context.tr('dx.api')),
                    subtitle: Text(
                      '${context.tr('dx.api.${api['state']}', {'n': api['active_keys'] ?? 0})}\n'
                      '${context.tr('dx.lastRequest', {'when': _when(api['last_request_at'])})}',
                    ),
                  ),
                ),
                Text(
                  context.tr('dx.webhooks'),
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                if (hooks.isEmpty) Text(context.tr('dx.none')),
                for (final hook in hooks.cast<Map<String, dynamic>>())
                  Card(
                    child: ListTile(
                      title: Text(
                        Uri.tryParse('${hook['url']}')?.host ??
                            '${hook['url']}',
                      ),
                      subtitle: Text(
                        '${context.tr('dx.state.${hook['state']}')}'
                        '${(hook['failed_24h'] ?? 0) > 0 ? ' · ${context.tr('dx.failed24h', {'n': hook['failed_24h']})}' : ''}',
                      ),
                    ),
                  ),
                if (connections.isNotEmpty) ...[
                  Text(
                    context.tr('dx.connections'),
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  for (final c in connections.cast<Map<String, dynamic>>())
                    ListTile(
                      title: Text('${c['name']}'),
                      subtitle: Text('${c['health']}'),
                    ),
                ],
                const SizedBox(height: 12),
                Text(
                  context.tr('dx.failed'),
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                failed.when(
                  loading: () => const LinearProgressIndicator(),
                  error: (e, _) => Text(_error(context, e)),
                  data: (items) => items.isEmpty
                      ? Text(context.tr('dx.noFailed'))
                      : Column(
                          children: [
                            for (final d in items.cast<Map<String, dynamic>>())
                              ListTile(
                                title: Text('${d['topic']}'),
                                subtitle: Text(
                                  'HTTP ${d['last_status'] ?? '—'} · ${_when(d['created_at'])}',
                                ),
                                trailing: TextButton(
                                  onPressed: () async {
                                    final messenger = ScaffoldMessenger.of(
                                      context,
                                    );
                                    final done = context.tr('dx.retried');
                                    try {
                                      await ref
                                          .read(apiClientProvider)
                                          .post(
                                            '/developers/deliveries/${d['id']}/retry',
                                          );
                                      messenger.showSnackBar(
                                        SnackBar(content: Text(done)),
                                      );
                                      ref.invalidate(failedDeliveriesProvider);
                                    } on ApiError catch (e) {
                                      if (context.mounted) {
                                        messenger.showSnackBar(
                                          SnackBar(
                                            content: Text(_error(context, e)),
                                          ),
                                        );
                                      }
                                    }
                                  },
                                  child: Text(context.tr('dx.retry')),
                                ),
                              ),
                          ],
                        ),
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}
