import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';

class OrderSourcesScreen extends ConsumerStatefulWidget {
  const OrderSourcesScreen({super.key});
  @override
  ConsumerState<OrderSourcesScreen> createState() => _OrderSourcesState();
}

class _OrderSourcesState extends ConsumerState<OrderSourcesScreen> {
  List<dynamic> _items = [], _capabilities = [];
  bool _busy = false;
  String? _error;
  final _name = TextEditingController();
  String tx(String en, String bn) =>
      context.strings.locale == AppLocale.bn ? bn : en;
  @override
  void initState() {
    super.initState();
    Future.microtask(() => _run(_load));
  }

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final result = await ref.read(apiClientProvider).get('/order-sources');
    if (mounted) {
      setState(() {
        _items = result['items'] as List;
        _capabilities = result['capabilities'] as List;
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
      if (mounted) {
        setState(() => _error = tx(e.messageEn, e.messageBn));
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: Text(tx('Order sources', 'অর্ডারের উৎস'))),
    body: ListView(
      padding: const EdgeInsets.all(16),
      children: [
        if (_busy) const LinearProgressIndicator(),
        if (_error != null) Text(_error!),
        for (final c in _capabilities)
          ListTile(
            title: Text('${c['provider']}'),
            subtitle: Text(
              c['available'] == true
                  ? tx('Available', 'উপলভ্য')
                  : tx(
                      'Official contract required',
                      'অফিশিয়াল চুক্তি প্রয়োজন',
                    ),
            ),
          ),
        TextField(
          controller: _name,
          maxLength: 120,
          decoration: InputDecoration(
            labelText: tx('Source name', 'উৎসের নাম'),
          ),
        ),
        FilledButton(
          onPressed: _busy
              ? null
              : () => _run(() async {
                  await ref
                      .read(apiClientProvider)
                      .post(
                        '/order-sources',
                        body: {
                          'name': _name.text,
                          'provider': 'CUSTOM_PUSH',
                          'enabled': true,
                        },
                      );
                  _name.clear();
                  await _load();
                }),
          child: Text(tx('Create custom source', 'নিজস্ব উৎস তৈরি')),
        ),
        Text(
          tx(
            'Configure mappings and view ingestion history on the web.',
            'ওয়েবে ম্যাপিং ও অর্ডার আসার ইতিহাস দেখুন।',
          ),
        ),
        for (final s in _items)
          SwitchListTile(
            title: Text('${s['name']}'),
            subtitle: SelectableText('${s['provider']}\n${s['id']}'),
            value: s['enabled'] == true,
            onChanged: _busy || s['available'] != true
                ? null
                : (enabled) => _run(() async {
                    await ref
                        .read(apiClientProvider)
                        .patch(
                          '/order-sources/${s['id']}',
                          body: {'enabled': enabled},
                        );
                    await _load();
                  }),
          ),
      ],
    ),
  );
}
