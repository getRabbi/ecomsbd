import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/money.dart';
import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/crm_repository.dart';
import '../../data/commerce/list_controllers.dart';
import '../../l10n/app_strings.dart';
import '../risk/external_risk_card.dart';
import 'crm_records_screen.dart';
import 'crm_widgets.dart';

class CustomerDetailScreen extends ConsumerStatefulWidget {
  const CustomerDetailScreen({required this.customerId, super.key});
  final String customerId;
  @override
  ConsumerState<CustomerDetailScreen> createState() => _CustomerDetailState();
}

class _CustomerDetailState extends ConsumerState<CustomerDetailScreen> {
  bool _busy = false;
  String? _phone;
  Future<void> _mutate(Future<void> Function() work) async {
    setState(() => _busy = true);
    try {
      await work();
      if (!mounted) return;
      ref.invalidate(crmCustomerProvider(widget.customerId));
      ref.invalidate(customerListProvider);
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(context.tr('crm.error'))));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _tag() async {
    final tag = await pickCrmItem(context);
    if (tag != null && mounted) {
      await _mutate(
        () => ref
            .read(crmRepositoryProvider)
            .tag(widget.customerId, tag['id'] as String),
      );
    }
  }

  Future<void> _createTag() async {
    final name = await crmTextDialog(
      context,
      context.tr('crm.tagName'),
      maxLength: 60,
    );
    if (name != null && mounted) {
      await _mutate(() async {
        final tag = await ref.read(crmRepositoryProvider).add(
          '/customers/crm/tags',
          {'name': name},
        );
        await ref
            .read(crmRepositoryProvider)
            .tag(widget.customerId, tag['id'] as String);
      });
    }
  }

  Future<void> _flag(String flag) async {
    String? reason;
    if (flag == 'BLOCKED') {
      reason = await crmTextDialog(
        context,
        context.tr('crm.blockReason'),
        maxLength: 400,
      );
      if (reason == null) return;
    }
    if (!mounted) return;
    await _mutate(() async {
      await ref
          .read(customersRepositoryProvider)
          .update(widget.customerId, flag: flag, flagReason: reason);
    });
  }

  Future<void> _reveal() async {
    final reason = await crmTextDialog(
      context,
      context.tr('crm.revealReason'),
      maxLength: 200,
    );
    if (reason == null || !mounted) return;
    await _mutate(() async {
      final phone = await ref
          .read(customersRepositoryProvider)
          .revealPhone(widget.customerId, reason: reason);
      if (mounted) setState(() => _phone = phone);
    });
  }

  Widget _fact(String label, Object? value) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 5),
    child: Row(
      children: [
        Expanded(child: Text(context.tr('crm.$label'))),
        Flexible(child: Text('$value', textAlign: TextAlign.end)),
      ],
    ),
  );
  String _money(Object? paisa) =>
      paisa == null ? context.tr('crm.noValue') : Money(paisa as int).format();
  @override
  Widget build(BuildContext context) {
    final detail = ref.watch(crmCustomerProvider(widget.customerId));
    return Scaffold(
      appBar: AppBar(title: Text(context.tr('crm.customer'))),
      body: detail.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, _) => Center(
          child: TextButton(
            onPressed: () =>
                ref.invalidate(crmCustomerProvider(widget.customerId)),
            child: Text(context.tr('common.retry')),
          ),
        ),
        data: (customer) {
          final canWrite = customer['can_write'] == true;
          final value = customer['value'] as Map<String, dynamic>?;
          final risk = customer['risk'] as Map<String, dynamic>?;
          return RefreshIndicator(
            onRefresh: () async {
              ref.invalidate(crmCustomerProvider(widget.customerId));
              await ref.read(crmCustomerProvider(widget.customerId).future);
            },
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  customer['name'] as String? ??
                      customer['phone_masked'] as String,
                  style: Theme.of(context).textTheme.headlineSmall,
                ),
                Text(_phone ?? customer['phone_masked'] as String),
                for (final address in customer['addresses'] as List? ?? [])
                  Text((address as Map)['raw_address'] as String),
                if (customer['can_reveal'] == true && _phone == null)
                  TextButton(
                    onPressed: _busy ? null : _reveal,
                    child: Text(context.tr('crm.reveal')),
                  ),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      children: [
                        _fact(
                          'firstOrder',
                          crmDate(context, customer['first_order_at']),
                        ),
                        _fact(
                          'lastOrder',
                          crmDate(context, customer['last_order_at']),
                        ),
                        _fact('orderCount', customer['order_count']),
                        _fact('delivered', customer['delivered_count']),
                        _fact('returned', customer['returned_count']),
                        _fact('cancelled', customer['cancelled_count']),
                        _fact('active', customer['active_orders']),
                        _fact(
                          'success',
                          customer['success_rate_basis_points'] == null
                              ? '—'
                              : '${(customer['success_rate_basis_points'] as int) / 100}%',
                        ),
                      ],
                    ),
                  ),
                ),
                if (risk != null)
                  Card(
                    child: ExpansionTile(
                      title: Text(context.tr('crm.risk')),
                      subtitle: Text(
                        context.tr(
                          'crm.${{'LOW': 'LOW_RISK', 'MEDIUM': 'MEDIUM_RISK', 'HIGH': 'HIGH_RISK'}[risk['state']] ?? 'INSUFFICIENT_DATA'}',
                        ),
                      ),
                      children: [
                        for (final reason in risk['reasons'] as List? ?? [])
                          Padding(
                            padding: const EdgeInsets.all(8),
                            child: Text(context.tr('crm.$reason')),
                          ),
                      ],
                    ),
                  ),
                const ExternalRiskCard(),
                ExpansionTile(
                  title: Text(context.tr('crm.value')),
                  children: [
                    Padding(
                      padding: const EdgeInsets.all(16),
                      child: value == null
                          ? Text(context.tr('crm.locked'))
                          : Column(
                              children: [
                                _fact(
                                  'totalValue',
                                  _money(value['total_order_value_paisa']),
                                ),
                                _fact(
                                  'revenue',
                                  _money(value['delivered_revenue_paisa']),
                                ),
                                _fact(
                                  'profit',
                                  _money(value['measured_profit_paisa']),
                                ),
                                _fact(
                                  'average',
                                  _money(
                                    value['average_delivered_order_paisa'],
                                  ),
                                ),
                                Text(
                                  context.tr('crm.coverage', {
                                    'measured': value['measured_parcels'],
                                    'completed': value['completed_parcels'],
                                  }),
                                ),
                              ],
                            ),
                    ),
                  ],
                ),
                Text(
                  context.tr('crm.segment'),
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                Wrap(
                  spacing: 6,
                  children: [
                    for (final segment in customer['segments'] as List? ?? [])
                      Chip(label: Text(context.tr('crm.$segment'))),
                  ],
                ),
                CrmDefinitions(customer['definitions'] as Map<String, dynamic>),
                Text(
                  context.tr('crm.tags'),
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                Wrap(
                  spacing: 6,
                  children: [
                    for (final tag in customer['tags'] as List? ?? [])
                      InputChip(
                        label: Text((tag as Map)['name'] as String),
                        onDeleted: !canWrite || _busy
                            ? null
                            : () => _mutate(
                                () => ref
                                    .read(crmRepositoryProvider)
                                    .tag(
                                      widget.customerId,
                                      tag['id'] as String,
                                      remove: true,
                                    ),
                              ),
                        deleteButtonTooltipMessage: context.tr('crm.removeTag'),
                      ),
                  ],
                ),
                if (canWrite)
                  Wrap(
                    children: [
                      TextButton(
                        onPressed: _busy ? null : _tag,
                        child: Text(context.tr('crm.addTag')),
                      ),
                      TextButton(
                        onPressed: _busy ? null : _createTag,
                        child: Text(context.tr('crm.createTag')),
                      ),
                    ],
                  ),
                if (customer['notes'] != null)
                  ListTile(
                    title: Text(context.tr('crm.legacy')),
                    subtitle: Text(customer['notes'] as String),
                  ),
                for (final kind in [
                  'orders',
                  'notes',
                  'follow-ups',
                  'timeline',
                ])
                  Card(
                    child: ListTile(
                      title: Text(
                        context.tr(
                          'crm.${kind == 'follow-ups' ? 'followups' : kind}',
                        ),
                      ),
                      subtitle:
                          kind == 'follow-ups' &&
                              customer['next_follow_up_at'] != null
                          ? Text(
                              crmDate(context, customer['next_follow_up_at']),
                            )
                          : null,
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => Navigator.push(
                        context,
                        MaterialPageRoute<void>(
                          builder: (_) => CrmRecordsScreen(
                            customerId: widget.customerId,
                            kind: kind,
                            canWrite: canWrite,
                          ),
                        ),
                      ),
                    ),
                  ),
                if (canWrite)
                  Wrap(
                    children: [
                      TextButton(
                        onPressed: _busy
                            ? null
                            : () => _flag(
                                customer['flag'] == 'STARRED'
                                    ? 'NONE'
                                    : 'STARRED',
                              ),
                        child: Text(
                          context.tr(
                            customer['flag'] == 'STARRED'
                                ? 'crm.unstar'
                                : 'common.starred',
                          ),
                        ),
                      ),
                      TextButton(
                        onPressed: _busy
                            ? null
                            : () => _flag(
                                customer['flag'] == 'BLOCKED'
                                    ? 'NONE'
                                    : 'BLOCKED',
                              ),
                        child: Text(
                          context.tr(
                            customer['flag'] == 'BLOCKED'
                                ? 'crm.unblock'
                                : 'common.blocked',
                          ),
                        ),
                      ),
                    ],
                  ),
              ],
            ),
          );
        },
      ),
    );
  }
}
