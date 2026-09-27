import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';
import '../shared/network_status.dart';

/// Purchasing on the phone (V3.5): follow purchase orders, receive goods,
/// look up suppliers and see where stock is. Building purchase orders and
/// managing suppliers stays on ecomsbd web.
class ProcurementScreen extends ConsumerStatefulWidget {
  const ProcurementScreen({super.key});
  @override
  ConsumerState<ProcurementScreen> createState() => _ProcurementScreenState();
}

class _ProcurementScreenState extends ConsumerState<ProcurementScreen>
    with ReloadOnReconnect {
  List<dynamic> _orders = [], _suppliers = [], _stock = [];
  bool _busy = false;
  String? _error;
  bool get _bn => context.strings.locale == AppLocale.bn;

  @override
  void initState() {
    super.initState();
    Future.microtask(_load);
  }

  @override
  void onReconnect() {
    if (_error != null) _load();
  }

  Future<void> _load() async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final api = ref.read(apiClientProvider);
      final data = await Future.wait([
        api.get('/procurement/purchase-orders'),
        api.get('/procurement/suppliers'),
        api.get('/procurement/stock'),
      ]);
      if (!mounted) return;
      setState(() {
        _orders = data[0]['items'] as List;
        _suppliers = data[1]['items'] as List;
        _stock = data[2]['items'] as List;
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = _bn ? e.messageBn : e.messageEn);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _empty() => Padding(
    padding: const EdgeInsets.all(24),
    child: Text(context.tr('pr.empty')),
  );

  @override
  Widget build(BuildContext context) => DefaultTabController(
    length: 3,
    child: Scaffold(
      appBar: AppBar(
        title: Text(context.tr('pr.title')),
        actions: [
          IconButton(
            onPressed: _busy ? null : _load,
            icon: const Icon(Icons.refresh),
          ),
        ],
        bottom: TabBar(
          tabs: [
            Tab(text: context.tr('pr.tab.orders')),
            Tab(text: context.tr('pr.tab.suppliers')),
            Tab(text: context.tr('pr.tab.stock')),
          ],
        ),
      ),
      body: Column(
        children: [
          if (_busy) const LinearProgressIndicator(),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.all(12),
              child: Text(
                _error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ),
          Expanded(
            child: TabBarView(
              children: [
                RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    children: [
                      Padding(
                        padding: const EdgeInsets.all(12),
                        child: Text(context.tr('pr.webHint')),
                      ),
                      if (!_busy && _orders.isEmpty) _empty(),
                      for (final po in _orders) _orderTile(po as Map),
                    ],
                  ),
                ),
                ListView(
                  children: [
                    if (!_busy && _suppliers.isEmpty) _empty(),
                    for (final s in _suppliers)
                      ListTile(
                        leading: const Icon(Icons.local_shipping_outlined),
                        title: Text('${s['name']}'),
                        subtitle: Text(
                          [
                            if (s['contact_name'] != null)
                              '${s['contact_name']}',
                            if (s['phone'] != null) '${s['phone']}',
                            context.tr('pr.openOrders', {
                              'n': s['open_orders'] ?? 0,
                            }),
                          ].join(' · '),
                        ),
                      ),
                  ],
                ),
                ListView(
                  children: [
                    if (!_busy && _stock.isEmpty) _empty(),
                    for (final row in _stock) _stockTile(row as Map),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    ),
  );

  Widget _orderTile(Map po) {
    final payable = po['payable'] as Map?;
    final status = context.tr('pr.status.${po['status']}');
    String? pay;
    if (payable != null) {
      pay = payable['overdue'] == true
          ? context.tr('pr.pay.overdue')
          : context.tr('pr.pay.${payable['status']}');
    }
    return ListTile(
      title: Text('${po['number']} · ${po['supplier_name'] ?? ''}'),
      subtitle: Text([status, if (pay != null) pay].join(' · ')),
      trailing: Text(Money(po['total_paisa'] as int).format()),
      onTap: () async {
        await Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => PurchaseOrderScreen(orderId: '${po['id']}'),
          ),
        );
        await _load();
      },
    );
  }

  Widget _stockTile(Map row) {
    final locations = (row['locations'] as List)
        .cast<Map>()
        .where((l) => l['is_default'] == true || (l['quantity'] as int) != 0)
        .map(
          (l) =>
              '${l['is_default'] == true ? context.tr('pr.main') : l['name']}: ${l['quantity']}',
        )
        .join(' · ');
    return ListTile(
      title: Text(
        row['variant_name'] == null
            ? '${row['name']}'
            : '${row['name']} (${row['variant_name']})',
      ),
      subtitle: Text(
        [
          context.tr('pr.onHand', {'n': row['on_hand']}),
          if ((row['incoming'] as int) > 0)
            context.tr('pr.incoming', {'n': row['incoming']}),
          locations,
        ].join(' · '),
      ),
      trailing: row['low'] == true
          ? Chip(label: Text(context.tr('pr.low')))
          : null,
    );
  }
}

class PurchaseOrderScreen extends ConsumerStatefulWidget {
  const PurchaseOrderScreen({super.key, required this.orderId});
  final String orderId;
  @override
  ConsumerState<PurchaseOrderScreen> createState() =>
      _PurchaseOrderScreenState();
}

class _PurchaseOrderScreenState extends ConsumerState<PurchaseOrderScreen> {
  Map<String, dynamic>? _detail;
  final Map<String, int> _accept = {}, _reject = {};
  final _challan = TextEditingController();
  // One key per intended receipt: a double tap or a retry records it once.
  String _key = const Uuid().v4();
  bool _busy = false;
  String? _error, _done;
  bool get _bn => context.strings.locale == AppLocale.bn;

  @override
  void initState() {
    super.initState();
    Future.microtask(_load);
  }

  @override
  void dispose() {
    _challan.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final data = await ref
        .read(apiClientProvider)
        .get('/procurement/purchase-orders/${widget.orderId}');
    if (mounted) setState(() => _detail = data);
  }

  Future<void> _run(Future<void> Function() work) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
      _done = null;
    });
    try {
      await work();
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = _bn ? e.messageBn : e.messageEn);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _receive() => _run(() async {
    final lines = <Map<String, Object?>>[
      for (final line in (_detail!['lines'] as List).cast<Map>())
        if ((_accept[line['id']] ?? 0) + (_reject[line['id']] ?? 0) > 0)
          {
            'line_id': line['id'],
            'accepted': _accept[line['id']] ?? 0,
            'rejected': _reject[line['id']] ?? 0,
            if ((_reject[line['id']] ?? 0) > 0) 'reject_reason': 'DAMAGED',
          },
    ];
    final result = await ref
        .read(apiClientProvider)
        .post(
          '/procurement/purchase-orders/${widget.orderId}/receive',
          body: {
            'lines': lines,
            'idempotency_key': _key,
            if (_challan.text.trim().isNotEmpty)
              'supplier_reference': _challan.text.trim(),
          },
        );
    if (!mounted) return;
    setState(() {
      _detail = result;
      _accept.clear();
      _reject.clear();
      _challan.clear();
      _key = const Uuid().v4();
      _done = context.tr('pr.saved');
    });
  });

  Widget _counter(Map<String, int> into, String id, String label) => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      Text(label),
      IconButton(
        onPressed: _busy || (into[id] ?? 0) == 0
            ? null
            : () => setState(() => into[id] = (into[id] ?? 0) - 1),
        icon: const Icon(Icons.remove_circle_outline),
      ),
      Text('${into[id] ?? 0}'),
      IconButton(
        onPressed: _busy
            ? null
            : () => setState(() => into[id] = (into[id] ?? 0) + 1),
        icon: const Icon(Icons.add_circle_outline),
      ),
    ],
  );

  @override
  Widget build(BuildContext context) {
    final detail = _detail;
    final po = detail?['purchase_order'] as Map?;
    final payable = po?['payable'] as Map?;
    final open =
        po != null &&
        (po['status'] == 'ORDERED' || po['status'] == 'PARTIALLY_RECEIVED');
    final canReceive = detail?['can_receive'] == true;
    final anything =
        _accept.values.any((v) => v > 0) || _reject.values.any((v) => v > 0);
    return Scaffold(
      appBar: AppBar(title: Text('${po?['number'] ?? context.tr('pr.title')}')),
      body: po == null
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                if (_busy) const LinearProgressIndicator(),
                if (_error != null)
                  Text(
                    _error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                if (_done != null) Text(_done!),
                Text(
                  '${po['supplier_name'] ?? ''} · ${context.tr('pr.status.${po['status']}')}',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                if (payable != null)
                  Text(
                    [
                      payable['overdue'] == true
                          ? context.tr('pr.pay.overdue')
                          : context.tr('pr.pay.${payable['status']}'),
                      context.tr('pr.owed', {
                        'amount': Money(
                          payable['balance_paisa'] as int,
                        ).format(),
                      }),
                    ].join(' · '),
                  ),
                const SizedBox(height: 12),
                if (open)
                  Text(
                    canReceive
                        ? context.tr('pr.receiveHint')
                        : context.tr('pr.noPermission'),
                  ),
                for (final line in (detail!['lines'] as List).cast<Map>())
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(12),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            '${line['description']}',
                            style: const TextStyle(fontWeight: FontWeight.w600),
                          ),
                          Text(
                            [
                              context.tr('pr.ordered', {
                                'n': line['quantity_ordered'],
                              }),
                              context.tr('pr.received', {
                                'n': line['quantity_received'],
                              }),
                              context.tr('pr.remaining', {
                                'n': line['remaining'],
                              }),
                            ].join(' · '),
                          ),
                          if (open && canReceive) ...[
                            _counter(
                              _accept,
                              '${line['id']}',
                              context.tr('pr.accept'),
                            ),
                            _counter(
                              _reject,
                              '${line['id']}',
                              context.tr('pr.reject'),
                            ),
                          ],
                        ],
                      ),
                    ),
                  ),
                if (open && canReceive) ...[
                  TextField(
                    controller: _challan,
                    maxLength: 120,
                    decoration: InputDecoration(
                      labelText: context.tr('pr.challan'),
                    ),
                  ),
                  FilledButton(
                    onPressed: _busy || !anything ? null : _receive,
                    child: Text(context.tr('pr.save')),
                  ),
                ],
              ],
            ),
    );
  }
}
