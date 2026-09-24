import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';

final externalRiskCapabilityProvider =
    FutureProvider.autoDispose<Map<String, dynamic>>(
      (ref) => ref.watch(apiClientProvider).get('/external-risk/capability'),
    );

/// Provider facts and network context for one customer, read from storage.
final customerRiskProfileProvider = FutureProvider.autoDispose
    .family<Map<String, dynamic>, String>(
      (ref, customerId) => ref
          .watch(apiClientProvider)
          .get('/customers/$customerId/risk-profile'),
    );

/// External provider facts, kept apart from the shop's own Risk Check (V3.7).
///
/// Without a customer it shows whether a provider is available at all. With
/// one it shows what the provider said, how fresh it is, any failed attempt,
/// the anonymous network context, and a refresh for people allowed to ask.
class ExternalRiskCard extends ConsumerWidget {
  const ExternalRiskCard({this.customerId, super.key});
  final String? customerId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final id = customerId;
    if (id == null) return const _CapabilityCard();
    final profile = ref.watch(customerRiskProfileProvider(id));
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: profile.when(
          loading: () => const LinearProgressIndicator(),
          error: (error, _) => Text(_errorText(context, error)),
          data: (data) => _ProfileBody(customerId: id, data: data),
        ),
      ),
    );
  }
}

String _errorText(BuildContext context, Object error) {
  final bn = context.strings.locale == AppLocale.bn;
  if (error is ApiError) return bn ? error.messageBn : error.messageEn;
  return bn ? 'লোড করা যায়নি' : 'Could not load';
}

String _when(BuildContext context, Object? value) {
  final parsed = value is String ? DateTime.tryParse(value) : null;
  if (parsed == null) return '—';
  final local = parsed.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${local.year}-${two(local.month)}-${two(local.day)} '
      '${two(local.hour)}:${two(local.minute)}';
}

class _CapabilityCard extends ConsumerWidget {
  const _CapabilityCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final bn = context.strings.locale == AppLocale.bn;
    final result = ref.watch(externalRiskCapabilityProvider);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              context.tr('xr.title'),
              style: Theme.of(context).textTheme.titleMedium,
            ),
            result.when(
              data: (data) => Text('${data[bn ? 'message_bn' : 'message_en']}'),
              loading: () => const LinearProgressIndicator(),
              error: (error, _) => Text(_errorText(context, error)),
            ),
          ],
        ),
      ),
    );
  }
}

class _ProfileBody extends ConsumerStatefulWidget {
  const _ProfileBody({required this.customerId, required this.data});
  final String customerId;
  final Map<String, dynamic> data;

  @override
  ConsumerState<_ProfileBody> createState() => _ProfileBodyState();
}

class _ProfileBodyState extends ConsumerState<_ProfileBody> {
  bool _busy = false;

  Future<void> _lookup({required bool refresh}) async {
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.maybeOf(context);
    try {
      final body = await ref
          .read(apiClientProvider)
          .post(
            '/external-risk/customers/${widget.customerId}/lookup',
            body: {'refresh': refresh},
          );
      final outcomes = body['outcomes'] as List? ?? const [];
      if (mounted &&
          outcomes.isNotEmpty &&
          (outcomes.first as Map)['outcome'] == 'CACHED') {
        messenger?.showSnackBar(
          SnackBar(content: Text(context.tr('xr.cached'))),
        );
      }
      ref.invalidate(customerRiskProfileProvider(widget.customerId));
    } on ApiError catch (e) {
      if (mounted) {
        messenger?.showSnackBar(
          SnackBar(content: Text(_errorText(context, e))),
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final external = widget.data['external'] as Map<String, dynamic>? ?? {};
    final status = external['status'] as String? ?? 'GATED';
    final sections = [
      for (final s in external['providers'] as List? ?? const [])
        if ((s as Map)['enabled'] == true) s.cast<String, dynamic>(),
    ];
    final network = widget.data['network'] as Map<String, dynamic>? ?? {};
    final cells = [
      for (final c in network['cells'] as List? ?? const [])
        if ((c as Map)['dimension'] == 'ALL') c.cast<String, dynamic>(),
    ];
    final canLookup = widget.data['can_lookup'] == true;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(context.tr('xr.title'), style: theme.textTheme.titleMedium),
        Text(context.tr('xr.hint'), style: theme.textTheme.bodySmall),
        const SizedBox(height: 8),
        if (status == 'GATED') Text(context.tr('xr.gated')),
        if (status == 'NOT_CONFIGURED') Text(context.tr('xr.notConfigured')),
        for (final section in sections) _ProviderFacts(section: section),
        if (status == 'AVAILABLE' && canLookup)
          Wrap(
            spacing: 8,
            children: [
              FilledButton(
                onPressed: _busy ? null : () => _lookup(refresh: false),
                child: Text(context.tr('xr.check')),
              ),
              OutlinedButton(
                onPressed: _busy ? null : () => _lookup(refresh: true),
                child: Text(context.tr('xr.refresh')),
              ),
            ],
          ),
        const Divider(height: 24),
        Text(context.tr('xr.net.title'), style: theme.textTheme.titleSmall),
        Text(context.tr('xr.net.hint'), style: theme.textTheme.bodySmall),
        if (network['period'] == null)
          Text(context.tr('xr.net.none'))
        else ...[
          Text(context.tr('xr.net.period', {'period': network['period']})),
          for (final cell in cells)
            Text(
              '${context.tr('xr.net.${cell['metric']}')}: '
              '${cell['status'] == 'PUBLISHED' ? '${cell['value']}%' : context.tr('xr.net.insufficient')}',
            ),
        ],
      ],
    );
  }
}

class _ProviderFacts extends StatelessWidget {
  const _ProviderFacts({required this.section});
  final Map<String, dynamic> section;

  @override
  Widget build(BuildContext context) {
    final name = '${section['name']}';
    final result = section['result'] as Map<String, dynamic>?;
    final error = section['last_error'] as Map<String, dynamic>?;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (result == null)
            Text(context.tr('xr.none', {'provider': name}))
          else ...[
            Row(
              children: [
                Expanded(
                  child: Text(
                    context.tr(
                      result['status'] == 'FOUND' ? 'xr.found' : 'xr.notFound',
                      {'provider': name},
                    ),
                  ),
                ),
                Chip(
                  label: Text(
                    context.tr(
                      result['freshness'] == 'FRESH' ? 'xr.fresh' : 'xr.stale',
                    ),
                  ),
                ),
              ],
            ),
            for (final fact in result['facts'] as List? ?? const [])
              Text(
                '${context.tr('xr.fact.${(fact as Map)['code']}')}: ${fact['value']}',
              ),
            if (result['provider_observed_at'] != null)
              Text(
                context.tr('xr.observed', {
                  'when': _when(context, result['provider_observed_at']),
                }),
                style: Theme.of(context).textTheme.bodySmall,
              ),
            Text(
              context.tr('xr.checked', {
                'when': _when(context, result['checked_at']),
              }),
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
          if (error != null)
            Text(
              context.tr('xr.unavailable', {
                'reason': context.tr('xr.err.${error['code']}'),
              }),
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
        ],
      ),
    );
  }
}
