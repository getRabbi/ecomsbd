import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';
import '../shared/network_status.dart';

class MessagingScreen extends ConsumerStatefulWidget {
  const MessagingScreen({super.key});
  @override
  ConsumerState<MessagingScreen> createState() => _MessagingScreenState();
}

class _MessagingScreenState extends ConsumerState<MessagingScreen>
    with ReloadOnReconnect {
  List<dynamic> _channels = [],
      _contacts = [],
      _customers = [],
      _orders = [],
      _messages = [],
      _templates = [];
  Map<String, dynamic>? _contact;
  String? _customer, _order, _sendKey;
  String _template = 'order_update';

  /// V4 WP7.1: EMAIL, or SMS once the server says the SMS gateway is on.
  String _channel = 'EMAIL';
  bool _useSavedPhone = true;
  final _recipient = TextEditingController();
  final _evidence = TextEditingController();
  bool _consent = false, _busy = false;
  String? _error;
  bool get _bn => context.strings.locale == AppLocale.bn;
  bool get _smsAvailable =>
      _channels.any((c) => c['kind'] == 'SMS' && c['available'] == true);
  bool get _sms => _smsAvailable && _channel == 'SMS';
  String tx(String en, String bn) => _bn ? bn : en;

  @override
  void initState() {
    super.initState();
    Future.microtask(_load);
  }

  @override
  void onReconnect() {
    if (_error != null) _load();
  }

  @override
  void dispose() {
    _recipient.dispose();
    _evidence.dispose();
    super.dispose();
  }

  Future<void> _run(Future<void> Function() work) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await work();
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = _bn ? e.messageBn : e.messageEn);
    } catch (_) {
      if (mounted) {
        setState(
          () => _error = tx(
            'Could not load. Try again.',
            'লোড করা যায়নি। আবার চেষ্টা করুন।',
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _load() => _run(() async {
    final api = ref.read(apiClientProvider);
    final data = await Future.wait([
      api.get('/messaging/channels'),
      api.get('/messaging/conversations'),
      api.get('/customers'),
      api.get('/messaging/templates'),
    ]);
    if (!mounted) return;
    setState(() {
      _channels = data[0]['items'] as List;
      _contacts = data[1]['items'] as List;
      _customers = data[2]['items'] as List;
      _templates = data[3]['items'] as List;
    });
    await _history();
  });

  /// Order updates only: offers go through campaigns, never one by one.
  bool _orderTemplate(dynamic t) =>
      (t['purpose'] ?? 'TRANSACTIONAL') == 'TRANSACTIONAL' &&
      (t['channel'] ?? 'EMAIL') == (_contact?['channel'] ?? 'EMAIL');

  Future<void> _history() async {
    if (_contact == null) return;
    final api = ref.read(apiClientProvider);
    final data = await Future.wait([
      api.get('/orders', query: {'customer_id': _contact!['customer_id']}),
      api.get(
        '/messaging/messages',
        query: {'conversation_id': _contact!['id']},
      ),
    ]);
    if (mounted) {
      setState(() {
        _orders = data[0]['items'] as List;
        _messages = data[1]['items'] as List;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final api = ref.read(apiClientProvider);
    return Scaffold(
      appBar: AppBar(
        title: Text(tx('Customer messaging', 'গ্রাহককে মেসেজ')),
        actions: [
          IconButton(
            onPressed: _busy ? null : _load,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_busy) const LinearProgressIndicator(),
          if (_error != null)
            Text(
              _error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          for (final c in _channels)
            SwitchListTile(
              title: Text('${c['kind']}'),
              subtitle: Text(
                c['available'] != true
                    ? tx(
                        'Official provider required',
                        'অফিশিয়াল প্রোভাইডার প্রয়োজন',
                      )
                    : c['shop_blocker'] != null
                    ? tx(
                        'Finish setup in Integrations',
                        'ইন্টিগ্রেশন থেকে সেটআপ শেষ করুন',
                      )
                    : c['kind'] == 'SMS'
                    ? tx('Order updates only', 'শুধু অর্ডারের আপডেট')
                    : tx('Order updates and offers', 'অর্ডারের আপডেট ও অফার'),
              ),
              value: c['enabled'] == true,
              onChanged:
                  _busy ||
                      ((c['available'] != true || c['shop_blocker'] != null) &&
                          c['enabled'] != true)
                  ? null
                  : (value) => _run(() async {
                      await api.post(
                        '/messaging/channels/${c['kind']}',
                        body: {'enabled': value},
                      );
                      if (mounted) setState(() => c['enabled'] = value);
                    }),
            ),
          ExpansionTile(
            title: Text(
              tx('Record contact and consent', 'যোগাযোগ ও সম্মতি সংরক্ষণ'),
            ),
            children: [
              DropdownButtonFormField<String>(
                initialValue: _customer,
                isExpanded: true,
                decoration: InputDecoration(
                  labelText: tx('Customer', 'গ্রাহক'),
                ),
                items: [
                  for (final c in _customers)
                    DropdownMenuItem(
                      value: c['id'] as String,
                      child: Text('${c['name'] ?? c['phone_masked']}'),
                    ),
                ],
                onChanged: (v) => setState(() => _customer = v),
              ),
              if (_smsAvailable)
                DropdownButtonFormField<String>(
                  key: const ValueKey('contact-channel'),
                  initialValue: _channel,
                  isExpanded: true,
                  decoration: InputDecoration(
                    labelText: tx('Channel', 'মাধ্যম'),
                  ),
                  items: [
                    DropdownMenuItem(
                      value: 'EMAIL',
                      child: Text(tx('Email', 'ইমেইল')),
                    ),
                    const DropdownMenuItem(value: 'SMS', child: Text('SMS')),
                  ],
                  onChanged: (v) => setState(() => _channel = v ?? 'EMAIL'),
                ),
              if (_sms)
                CheckboxListTile(
                  value: _useSavedPhone,
                  onChanged: (v) => setState(() => _useSavedPhone = v == true),
                  title: Text(
                    tx(
                      'Use the customer’s saved phone number',
                      'গ্রাহকের সংরক্ষিত ফোন নম্বর ব্যবহার করুন',
                    ),
                  ),
                ),
              if (!_sms || !_useSavedPhone)
                TextField(
                  controller: _recipient,
                  keyboardType: _sms
                      ? TextInputType.phone
                      : TextInputType.emailAddress,
                  decoration: InputDecoration(
                    labelText: _sms
                        ? tx('Mobile number', 'মোবাইল নম্বর')
                        : tx('Email', 'ইমেইল'),
                  ),
                ),
              TextField(
                controller: _evidence,
                maxLength: 500,
                decoration: InputDecoration(
                  labelText: tx(
                    'Consent evidence / reason',
                    'সম্মতির প্রমাণ / কারণ',
                  ),
                ),
              ),
              CheckboxListTile(
                value: _consent,
                onChanged: (v) => setState(() => _consent = v == true),
                title: Text(
                  tx(
                    'Customer agreed to order messages',
                    'গ্রাহক অর্ডারের মেসেজ পেতে সম্মত',
                  ),
                ),
              ),
              FilledButton(
                onPressed: _busy || _customer == null
                    ? null
                    : () => _run(() async {
                        final saved = await api.post(
                          '/messaging/conversations',
                          body: {
                            'customer_id': _customer,
                            'channel': _sms ? 'SMS' : 'EMAIL',
                            if (_sms && _useSavedPhone)
                              'use_customer_phone': true
                            else
                              'recipient': _recipient.text,
                            'consent': _consent,
                            'evidence': _evidence.text,
                          },
                        );
                        final contacts = await api.get(
                          '/messaging/conversations',
                        );
                        if (mounted) {
                          setState(() {
                            _contact = saved;
                            _order = null;
                            _sendKey = null;
                            _contacts = contacts['items'] as List;
                          });
                        }
                        await _history();
                      }),
                child: Text(tx('Save', 'সংরক্ষণ')),
              ),
            ],
          ),
          for (final c in _contacts)
            ListTile(
              title: Text('${c['recipient_masked']}'),
              subtitle: Text(
                '${c['channel'] ?? 'EMAIL'} · '
                '${c['consent'] == true ? tx('Consented', 'সম্মতি আছে') : tx('Opted out', 'সম্মতি নেই')}'
                ' · '
                '${c['marketing_consent'] == true ? tx('Offers allowed', 'অফার চালু') : tx('No offers', 'অফার নয়')}',
              ),
              selected: _contact?['id'] == c['id'],
              onTap: _busy
                  ? null
                  : () => _run(() async {
                      setState(() {
                        _contact = Map<String, dynamic>.from(c as Map);
                        _order = null;
                        _sendKey = null;
                      });
                      await _history();
                    }),
            ),
          if (_contact != null) ...[
            Text(
              '${_contact!['recipient_masked']}',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            TextField(
              controller: _evidence,
              decoration: InputDecoration(
                labelText: tx(
                  'Consent change reason',
                  'সম্মতি পরিবর্তনের কারণ',
                ),
              ),
            ),
            OutlinedButton(
              onPressed: _busy
                  ? null
                  : () => _run(() async {
                      final saved = await api.patch(
                        '/messaging/conversations/${_contact!['id']}/consent',
                        body: {
                          'consent': _contact!['consent'] != true,
                          'evidence': _evidence.text,
                        },
                      );
                      final contacts = await api.get(
                        '/messaging/conversations',
                      );
                      if (mounted) {
                        setState(() {
                          _contact = saved;
                          _contacts = contacts['items'] as List;
                        });
                      }
                    }),
              child: Text(
                _contact!['consent'] == true
                    ? tx('Record opt-out', 'মেসেজ বন্ধ করুন')
                    : tx('Record consent', 'সম্মতি সংরক্ষণ'),
              ),
            ),
            OutlinedButton(
              onPressed: _busy
                  ? null
                  : () => _run(() async {
                      final saved = await api.patch(
                        '/messaging/conversations/${_contact!['id']}/marketing-consent',
                        body: {
                          'consent': _contact!['marketing_consent'] != true,
                          'evidence': _evidence.text,
                        },
                      );
                      final contacts = await api.get(
                        '/messaging/conversations',
                      );
                      if (mounted) {
                        setState(() {
                          _contact = saved;
                          _contacts = contacts['items'] as List;
                        });
                      }
                    }),
              child: Text(
                _contact!['marketing_consent'] == true
                    ? tx('Stop offers', 'অফার বন্ধ করুন')
                    : tx('Record consent to offers', 'অফারের সম্মতি সংরক্ষণ'),
              ),
            ),
            DropdownButtonFormField<String>(
              key: ValueKey('order-$_order-${_contact!['id']}'),
              initialValue: _order,
              decoration: InputDecoration(labelText: tx('Order', 'অর্ডার')),
              items: [
                for (final o in _orders)
                  DropdownMenuItem(
                    value: o['id'] as String,
                    child: Text('${o['order_number']}'),
                  ),
              ],
              onChanged: (v) => setState(() {
                _order = v;
                _sendKey = null;
              }),
            ),
            DropdownButtonFormField<String>(
              initialValue: _template,
              decoration: InputDecoration(
                labelText: tx('Template', 'টেমপ্লেট'),
              ),
              items: [
                for (final t in _templates.where(_orderTemplate))
                  DropdownMenuItem(
                    value: t['key'] as String,
                    child: Text('${t['key']}'),
                  ),
              ],
              onChanged: (v) => setState(() {
                _template = v ?? 'order_update';
                _sendKey = null;
              }),
            ),
            for (final t in _templates.where((t) => t['key'] == _template))
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 12),
                child: Text('${t[_bn ? 'body_bn' : 'body_en']}'),
              ),
            FilledButton(
              onPressed:
                  _busy ||
                      _order == null ||
                      _contact!['consent'] != true ||
                      !_channels.any(
                        (c) =>
                            c['kind'] == _contact!['channel'] &&
                            c['enabled'] == true &&
                            c['available'] == true,
                      )
                  ? null
                  : () => _run(() async {
                      _sendKey ??= const Uuid().v4();
                      await api.post(
                        '/messaging/messages',
                        body: {
                          'conversation_id': _contact!['id'],
                          'order_id': _order,
                          'template_key': _template,
                          'locale': _bn ? 'bn' : 'en',
                          'idempotency_key': _sendKey,
                        },
                      );
                      _sendKey = null;
                      await _history();
                    }),
              child: Text(
                tx('Send transactional message', 'অর্ডারের মেসেজ পাঠান'),
              ),
            ),
            for (final m in _messages)
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('${m['subject']}'),
                      Text('${m['body']}'),
                      Text('${m['status']} · ${m['attempts']}'),
                      if (m['status'] == 'FAILED')
                        TextButton(
                          onPressed: _busy
                              ? null
                              : () => _run(() async {
                                  await api.post(
                                    '/messaging/messages/${m['id']}/retry',
                                  );
                                  await _history();
                                }),
                          child: Text(tx('Retry', 'আবার চেষ্টা')),
                        ),
                    ],
                  ),
                ),
              ),
          ],
        ],
      ),
    );
  }
}
