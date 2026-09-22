import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';

class AutomationScreen extends ConsumerStatefulWidget {
  const AutomationScreen({super.key});
  @override
  ConsumerState<AutomationScreen> createState() => _AutomationState();
}

class _AutomationState extends ConsumerState<AutomationScreen> {
  List<dynamic> _rules = [];
  bool _busy = false;
  String? _error;
  String tx(String en, String bn) =>
      context.strings.locale == AppLocale.bn ? bn : en;
  @override
  void initState() {
    super.initState();
    Future.microtask(() => _run(_load));
  }

  Future<void> _load() async {
    final result = await ref.read(apiClientProvider).get('/automation/rules');
    if (mounted) {
      setState(() => _rules = result['items'] as List);
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
    appBar: AppBar(
      title: Text(tx('Automation rules', 'অটোমেশন নিয়ম')),
      actions: [
        IconButton(
          onPressed: _busy ? null : () => _run(_load),
          icon: const Icon(Icons.refresh),
        ),
      ],
    ),
    body: ListView(
      padding: const EdgeInsets.all(16),
      children: [
        if (_busy) const LinearProgressIndicator(),
        if (_error != null) Text(_error!),
        Text(
          tx(
            'Build and edit rules on the web. Changes apply to future events; customer messages require consent.',
            'ওয়েবে নিয়ম তৈরি ও সম্পাদনা করুন। নতুন ইভেন্টে নিয়ম কাজ করবে; গ্রাহকের মেসেজে সম্মতি লাগবে।',
          ),
        ),
        if (!_busy && _rules.isEmpty)
          Text(tx('No rules yet', 'এখনো কোনো নিয়ম নেই')),
        for (final rule in _rules)
          SwitchListTile(
            title: Text('${rule['name']}'),
            subtitle: Text(
              rule['trigger'] == 'order.created'
                  ? tx('When an order is created', 'অর্ডার তৈরি হলে')
                  : tx('When order status changes', 'অর্ডারের অবস্থা বদলালে'),
            ),
            value: rule['enabled'] == true,
            onChanged: _busy
                ? null
                : (enabled) => _run(() async {
                    await ref
                        .read(apiClientProvider)
                        .patch(
                          '/automation/rules/${rule['id']}',
                          body: {'enabled': enabled},
                        );
                    await _load();
                  }),
          ),
      ],
    ),
  );
}
