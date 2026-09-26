import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';
import '../shared/network_status.dart';

/// Campaign monitoring on the phone: status, results and an emergency stop.
///
/// Campaigns are built on the web. Here a seller watches them and can pause,
/// resume or cancel one; the server decides who may do which.
class CampaignsScreen extends ConsumerStatefulWidget {
  const CampaignsScreen({super.key});
  @override
  ConsumerState<CampaignsScreen> createState() => _CampaignsScreenState();
}

class _CampaignsScreenState extends ConsumerState<CampaignsScreen>
    with ReloadOnReconnect {
  List<dynamic> _campaigns = [];
  Map<String, dynamic>? _overview;
  Map<String, dynamic>? _catalog;
  bool _busy = false;
  String? _error;
  bool get _bn => context.strings.locale == AppLocale.bn;
  String tx(String en, String bn) => _bn ? bn : en;

  @override
  void initState() {
    super.initState();
    Future.microtask(() => _run(_load));
  }

  @override
  void onReconnect() {
    if (_error != null) _run(_load);
  }

  Future<void> _load() async {
    final api = ref.read(apiClientProvider);
    final data = await Future.wait([
      api.get('/campaigns'),
      api.get('/messaging/overview'),
      api.get('/campaigns/catalog'),
    ]);
    if (!mounted) return;
    setState(() {
      _campaigns = data[0]['items'] as List;
      _overview = data[1];
      _catalog = data[2];
    });
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

  int _sent(String purpose) {
    var total = 0;
    for (final row in (_overview?['items'] as List?) ?? const []) {
      if (row['purpose'] == purpose &&
          const {'SENT', 'DELIVERED', 'READ'}.contains(row['status'])) {
        total += row['count'] as int;
      }
    }
    return total;
  }

  String _status(String value) =>
      const <String, List<String>>{
        'DRAFT': ['Draft', 'খসড়া'],
        'SCHEDULED': ['Scheduled', 'নির্ধারিত'],
        'SENDING': ['Sending', 'পাঠানো চলছে'],
        'PAUSED': ['Paused', 'থামানো'],
        'COMPLETED': ['Completed', 'সম্পন্ন'],
        'CANCELLED': ['Cancelled', 'বাতিল'],
        'ACTIVE': ['Active', 'চালু'],
      }[value]?[_bn ? 1 : 0] ??
      value;

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: Text(tx('Campaigns', 'ক্যাম্পেইন')),
      actions: [
        IconButton(
          onPressed: _busy ? null : () => _run(_load),
          icon: const Icon(Icons.refresh),
        ),
      ],
    ),
    body: RefreshIndicator(
      onRefresh: () => _run(_load),
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_busy) const LinearProgressIndicator(),
          if (_error != null)
            Text(
              _error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          Text(
            tx(
              'Build campaigns on the web. Here you can watch them and pause one at any time.',
              'ওয়েবে ক্যাম্পেইন তৈরি করুন। এখানে দেখুন, যেকোনো সময় থামাতে পারবেন।',
            ),
          ),
          if (_overview != null)
            Card(
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      tx('Last 30 days', 'গত ৩০ দিন'),
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    _Fact(
                      tx(
                        'Customers who accept offers',
                        'অফার পেতে রাজি গ্রাহক',
                      ),
                      '${_overview!['marketing_contacts']}',
                    ),
                    _Fact(
                      tx('Offers sent', 'পাঠানো অফার'),
                      '${_sent('MARKETING')}',
                    ),
                    _Fact(
                      tx('Order updates sent', 'পাঠানো অর্ডার আপডেট'),
                      '${_sent('TRANSACTIONAL')}',
                    ),
                    _Fact(
                      tx('Opt-outs', 'অফার বন্ধ করেছেন'),
                      '${_overview!['marketing_opt_outs']}',
                    ),
                  ],
                ),
              ),
            ),
          if (!_busy && _campaigns.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 24),
              child: Text(tx('No campaigns yet', 'এখনো কোনো ক্যাম্পেইন নেই')),
            ),
          for (final c in _campaigns)
            ListTile(
              title: Text('${c['name']}'),
              subtitle: Text(
                '${_status('${c['status']}')} · ${c['channel']} · '
                '${tx('Recipients', 'প্রাপক')}: ${c['total_recipients']}',
              ),
              trailing: const Icon(Icons.chevron_right),
              onTap: () async {
                await Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => CampaignDetailScreen(
                      campaignId: '${c['id']}',
                      canPause: _catalog?['can_pause'] == true,
                      canManage: _catalog?['can_manage'] == true,
                    ),
                  ),
                );
                await _run(_load);
              },
            ),
        ],
      ),
    ),
  );
}

class CampaignDetailScreen extends ConsumerStatefulWidget {
  const CampaignDetailScreen({
    super.key,
    required this.campaignId,
    required this.canPause,
    required this.canManage,
  });

  final String campaignId;
  final bool canPause;
  final bool canManage;

  @override
  ConsumerState<CampaignDetailScreen> createState() =>
      _CampaignDetailScreenState();
}

class _CampaignDetailScreenState extends ConsumerState<CampaignDetailScreen> {
  Map<String, dynamic>? _detail;
  bool _busy = false;
  String? _error;
  bool get _bn => context.strings.locale == AppLocale.bn;
  String tx(String en, String bn) => _bn ? bn : en;

  @override
  void initState() {
    super.initState();
    Future.microtask(() => _run(_load));
  }

  Future<void> _load() async {
    final data = await ref
        .read(apiClientProvider)
        .get('/campaigns/${widget.campaignId}');
    if (mounted) {
      setState(() => _detail = data);
    }
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
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _act(String action) => _run(() async {
    await ref
        .read(apiClientProvider)
        .post('/campaigns/${widget.campaignId}/$action');
    await _load();
  });

  String _count(Object? value) =>
      value == null ? tx('Not reported', 'জানা যায় না') : '$value';

  @override
  Widget build(BuildContext context) {
    final campaign = _detail?['campaign'] as Map<String, dynamic>?;
    final analytics = _detail?['analytics'] as Map<String, dynamic>?;
    final status = '${campaign?['status'] ?? ''}';
    final running = const {'SCHEDULED', 'SENDING', 'ACTIVE'}.contains(status);
    final after = analytics?['orders_after'] as Map<String, dynamic>?;
    return Scaffold(
      appBar: AppBar(
        title: Text('${campaign?['name'] ?? tx('Campaign', 'ক্যাম্পেইন')}'),
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
          if (campaign != null && analytics != null) ...[
            Text(
              '$status · ${campaign['channel']}',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            _Fact(
              tx('Recipients', 'প্রাপক'),
              '${(analytics['recipients'] as Map)['total']}',
            ),
            _Fact(tx('Sent', 'পাঠানো'), '${analytics['sent']}'),
            _Fact(tx('Delivered', 'পৌঁছেছে'), _count(analytics['delivered'])),
            _Fact(
              tx('Read / opened', 'পড়া / খোলা'),
              _count(analytics['read']),
            ),
            _Fact(tx('Failed', 'ব্যর্থ'), '${analytics['failed']}'),
            _Fact(
              tx('Unsubscribed', 'আনসাবস্ক্রাইব'),
              '${analytics['opt_outs']}',
            ),
            if (after != null)
              _Fact(
                tx(
                  'Orders within ${after['window_days']} days after',
                  'পরের ${after['window_days']} দিনে অর্ডার',
                ),
                '${after['orders']}',
              ),
            const SizedBox(height: 16),
            Wrap(
              spacing: 8,
              children: [
                if (widget.canPause && running)
                  FilledButton(
                    onPressed: _busy ? null : () => _act('pause'),
                    child: Text(tx('Pause', 'থামান')),
                  ),
                if (widget.canManage && status == 'PAUSED')
                  OutlinedButton(
                    onPressed: _busy ? null : () => _act('resume'),
                    child: Text(tx('Resume', 'আবার চালু')),
                  ),
                if (widget.canManage &&
                    !const {'COMPLETED', 'CANCELLED'}.contains(status))
                  TextButton(
                    onPressed: _busy
                        ? null
                        : () async {
                            final confirmed = await showDialog<bool>(
                              context: context,
                              builder: (dialog) => AlertDialog(
                                content: Text(
                                  tx(
                                    'Cancel this campaign? Messages not yet sent will not be sent.',
                                    'ক্যাম্পেইন বাতিল করবেন? যেগুলো এখনো যায়নি সেগুলো আর যাবে না।',
                                  ),
                                ),
                                actions: [
                                  TextButton(
                                    onPressed: () =>
                                        Navigator.of(dialog).pop(false),
                                    child: Text(tx('Keep', 'রাখুন')),
                                  ),
                                  TextButton(
                                    onPressed: () =>
                                        Navigator.of(dialog).pop(true),
                                    child: Text(tx('Cancel it', 'বাতিল করুন')),
                                  ),
                                ],
                              ),
                            );
                            if (confirmed == true) await _act('cancel');
                          },
                    child: Text(tx('Cancel campaign', 'ক্যাম্পেইন বাতিল')),
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _Fact extends StatelessWidget {
  const _Fact(this.label, this.value);

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 4),
    child: Row(
      children: [
        Expanded(child: Text(label)),
        Text(value, style: const TextStyle(fontWeight: FontWeight.w600)),
      ],
    ),
  );
}
