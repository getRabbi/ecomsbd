import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';
import '../shared/network_status.dart';

/// Forecasts on the phone (V3.6): items likely to run out with the suggested
/// reorder, and the cash outlook. The full breakdown and accuracy live on web.
const _knownCodes = {
  'NO_PREFERRED_SUPPLIER',
  'ALREADY_ON_ORDER',
  'NO_SUGGESTION',
};

class ForecastScreen extends ConsumerStatefulWidget {
  const ForecastScreen({super.key});
  @override
  ConsumerState<ForecastScreen> createState() => _ForecastScreenState();
}

class _ForecastScreenState extends ConsumerState<ForecastScreen>
    with ReloadOnReconnect {
  List<dynamic> _items = [];
  Map<dynamic, dynamic> _counts = const {};
  Map<dynamic, dynamic> _method = const {};
  bool _canDraft = false;
  Map<String, dynamic>? _cash;
  bool _cashForbidden = false;
  bool _busy = false;
  String? _error, _done;
  bool get _bn => context.strings.locale == AppLocale.bn;

  @override
  void initState() {
    super.initState();
    Future.microtask(_load);
  }

  @override
  void onReconnect() {
    if (_error != null || (_cash == null && !_cashForbidden)) _load();
  }

  /// Items without enough sales history get no forecast, so they are never
  /// "at risk". An empty at-risk list must not tell a new shop that nothing
  /// will run out when nothing could be forecast at all.
  int get _insufficient => (_counts['insufficient'] as int?) ?? 0;

  bool get _noneForecastable =>
      _insufficient > 0 && _insufficient == _counts['all'];

  Map<String, Object?> get _minimum => {
    'days': _method['min_in_stock_days'],
    'units': _method['min_units'],
  };

  String _message(ApiError e) {
    final code = e.details?['code'];
    if (code is String && _knownCodes.contains(code)) {
      return context.tr('fc.err.$code');
    }
    return _bn ? e.messageBn : e.messageEn;
  }

  Future<void> _load() async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    final api = ref.read(apiClientProvider);
    // Independent reads, asked together: one after the other they took two
    // full round trips before the screen was complete.
    final ((demand, demandError), (cash, cashError)) = await (
      _read(api.get('/forecasting/demand', query: {'filter': 'at_risk'})),
      _read(api.get('/forecasting/cash')),
    ).wait;
    if (!mounted) return;
    setState(() {
      if (demand != null) {
        _items = demand['items'] as List;
        _counts = (demand['counts'] as Map?) ?? const {};
        _method = (demand['method'] as Map?) ?? const {};
        _canDraft = demand['can_draft'] == true;
      }
      if (demandError != null) _error = _message(demandError);
      if (cash != null) _cash = cash;
      if (cashError != null) _cashForbidden = cashError.statusCode == 403;
      _busy = false;
    });
  }

  /// A read's body or its failure, so two can run together and fail apart.
  static Future<(Map<String, dynamic>?, ApiError?)> _read(
    Future<Map<String, dynamic>> call,
  ) async {
    try {
      return (await call, null);
    } on ApiError catch (error) {
      return (null, error);
    }
  }

  Future<void> _draft(Map item) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
      _done = null;
    });
    try {
      final made = await ref
          .read(apiClientProvider)
          .post(
            '/forecasting/draft-purchase-order',
            body: {
              'product_id': item['product_id'],
              'variant_id': item['variant_id'],
            },
          );
      if (mounted) {
        setState(
          () => _done = context.tr('fc.drafted', {'number': made['number']}),
        );
      }
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = _message(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _itemTile(Map item) {
    final name = item['variant_name'] == null
        ? '${item['name']}'
        : '${item['name']} (${item['variant_name']})';
    final days = item['days_of_cover'] as int?;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(name, style: const TextStyle(fontWeight: FontWeight.w600)),
            Text(
              days == null
                  ? ''
                  : days == 0
                  ? context.tr('fc.runsOutNow')
                  : context.tr('fc.runsOut', {'n': days}),
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
            Text(
              [
                context.tr('fc.rate', {'rate': item['rate_per_day']}),
                context.tr('fc.onHand', {'n': item['on_hand']}),
                if ((item['incoming'] as int) > 0)
                  context.tr('fc.incoming', {'n': item['incoming']}),
                context.tr('fc.lead', {'n': item['lead_time_days']}),
              ].join(' · '),
            ),
            Text(context.tr('fc.conf.${item['confidence']}')),
            Row(
              children: [
                Expanded(
                  child: Text(
                    context.tr('fc.suggested', {
                      'n': item['suggested_quantity'],
                    }),
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                if (_canDraft)
                  OutlinedButton(
                    onPressed: _busy ? null : () => _draft(item),
                    child: Text(context.tr('fc.draft')),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _cashView() {
    if (_cashForbidden) {
      return Padding(
        padding: const EdgeInsets.all(24),
        child: Text(context.tr('fc.cash.noAccess')),
      );
    }
    final cash = _cash;
    if (cash == null) return const SizedBox.shrink();
    final inflow = cash['inflow'] as Map;
    final outflow = cash['outflow'] as Map;
    String money(Object? paisa) => Money((paisa as int?) ?? 0).format();
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text(context.tr('fc.cash.hint')),
        const SizedBox(height: 12),
        for (final (key, value) in [
          ('fc.cash.net7', cash['net_7_days_paisa']),
          ('fc.cash.net14', cash['net_14_days_paisa']),
          ('fc.cash.in7', inflow['next_7_days']),
          ('fc.cash.out7', outflow['next_7_days']),
          ('fc.cash.overdue', outflow['overdue']),
          ('fc.cash.committed', cash['committed_on_open_orders_paisa']),
        ])
          ListTile(
            title: Text(context.tr(key)),
            trailing: Text(
              money(value),
              style: Theme.of(context).textTheme.titleMedium,
            ),
          ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) => DefaultTabController(
    length: 2,
    child: Scaffold(
      appBar: AppBar(
        title: Text(context.tr('fc.title')),
        actions: [
          IconButton(
            onPressed: _busy ? null : _load,
            icon: const Icon(Icons.refresh),
          ),
        ],
        bottom: TabBar(
          tabs: [
            Tab(text: context.tr('fc.tab.reorder')),
            Tab(text: context.tr('fc.tab.cash')),
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
          if (_done != null)
            Padding(padding: const EdgeInsets.all(12), child: Text(_done!)),
          Expanded(
            child: TabBarView(
              children: [
                RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    padding: const EdgeInsets.all(12),
                    children: [
                      Text(context.tr('fc.hint')),
                      // Only a list that loaded is known to be empty: a failed
                      // load must not say nothing will run out.
                      if (!_busy && _error == null && _items.isEmpty)
                        Padding(
                          padding: const EdgeInsets.all(24),
                          child: Text(
                            _noneForecastable
                                ? context.tr('fc.emptyInsufficient', _minimum)
                                : context.tr('fc.empty'),
                          ),
                        ),
                      for (final item in _items) _itemTile(item as Map),
                      if (!_busy && _insufficient > 0 && !_noneForecastable)
                        Padding(
                          padding: const EdgeInsets.all(12),
                          child: Text(
                            context.tr('fc.insufficientCount', {
                              'n': _insufficient,
                            }),
                          ),
                        ),
                    ],
                  ),
                ),
                _cashView(),
              ],
            ),
          ),
        ],
      ),
    ),
  );
}
