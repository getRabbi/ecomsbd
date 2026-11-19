import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';

/// The confirmation call queue (V4 WP2.3).
///
/// `GET /confirmations/queue` lists the orders waiting for a call, highest
/// COD first. "Call" asks the server for the number (`call_now`, which keeps
/// the shop's quiet hours unless the seller overrides them) and opens the
/// phone app with a `tel:` link — the app never reads the call log. When the
/// seller comes back to the app, the outcome sheet asks what the customer
/// said: Confirm, Cancel or Dhoreni (no answer).
final callQueueProvider = FutureProvider.autoDispose<Map<String, dynamic>>(
  (ref) => ref.watch(apiClientProvider).get('/confirmations/queue'),
);

/// Opens the phone app on a number. Replaced in tests.
typedef Dialer = Future<bool> Function(String phone);

final dialerProvider = Provider<Dialer>(
  (ref) =>
      (phone) => launchUrl(Uri(scheme: 'tel', path: phone)),
);

/// What the seller says the customer answered.
enum CallOutcome { confirmed, cancelled, noAnswer }

const _actions = <CallOutcome, String>{
  CallOutcome.confirmed: 'mark_confirmed',
  CallOutcome.cancelled: 'mark_cancelled',
  CallOutcome.noAnswer: 'mark_no_answer',
};

const _saved = <CallOutcome, String>{
  CallOutcome.confirmed: 'cq.confirmed',
  CallOutcome.cancelled: 'cq.cancelled',
  CallOutcome.noAnswer: 'cq.noAnswerSaved',
};

String callReasonKey(String? code) {
  const known = <String>{
    'CUSTOMER_BLOCKED',
    'HIGH_RISK',
    'HIGH_VALUE',
    'TRUSTED_REPEAT',
    'TRUSTED_NETWORK',
    'VERIFY_NEW',
    'SHOP_MANUAL',
    'CALL_EVERYONE',
  };
  return known.contains(code) ? 'cq.reason.$code' : 'cq.reason.other';
}

class CallQueueScreen extends ConsumerStatefulWidget {
  const CallQueueScreen({super.key});

  @override
  ConsumerState<CallQueueScreen> createState() => _CallQueueScreenState();
}

class _CallQueueScreenState extends ConsumerState<CallQueueScreen> {
  late final AppLifecycleListener _lifecycle;

  /// The order the seller is on the phone about; asked about on return.
  Map<String, dynamic>? _calling;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _lifecycle = AppLifecycleListener(onResume: _onResume);
  }

  @override
  void dispose() {
    _lifecycle.dispose();
    super.dispose();
  }

  void _onResume() {
    final order = _calling;
    if (order == null || !mounted) {
      return;
    }
    _calling = null;
    _askOutcome(order);
  }

  String _message(Object error, String fallback) {
    if (error is ApiError) {
      return context.strings.locale == AppLocale.bn
          ? error.messageBn
          : error.messageEn;
    }
    return context.tr(fallback);
  }

  void _say(String text) {
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(text)));
  }

  Future<void> _call(Map<String, dynamic> order) async {
    final api = ref.read(apiClientProvider);
    final path = '/orders/${order['id']}/confirmation/call_now';
    setState(() => _busy = true);
    try {
      Map<String, dynamic> reply;
      try {
        reply = await api.post(path, body: <String, dynamic>{});
      } on ApiError catch (error) {
        if (error.details?['code'] != 'CONFIRMATION_QUIET_HOURS') {
          rethrow;
        }
        if (!await _overrideQuiet(error.details!)) {
          return;
        }
        reply = await api.post(
          path,
          body: <String, dynamic>{'override_quiet': true},
        );
      }
      final phone = reply['phone'] as String?;
      if (!mounted || phone == null) {
        return;
      }
      _calling = order;
      final opened = await ref.read(dialerProvider)(phone);
      if (!opened && mounted) {
        _calling = null;
        _say(context.tr('cq.dialFailed'));
        ref.invalidate(callQueueProvider);
      }
    } catch (error) {
      if (mounted) {
        _say(_message(error, 'cq.actionFailed'));
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<bool> _overrideQuiet(Map<String, dynamic> details) async {
    final vars = <String, Object?>{
      'start': details['quiet_start'],
      'end': details['quiet_end'],
    };
    final answer = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(context.tr('cq.quietTitle')),
        content: Text(context.tr('cq.quietBody', vars)),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: Text(context.tr('cq.later')),
          ),
          FilledButton(
            key: const ValueKey('cq.quietOverride'),
            onPressed: () => Navigator.of(context).pop(true),
            child: Text(context.tr('cq.quietOverride')),
          ),
        ],
      ),
    );
    return answer ?? false;
  }

  Future<void> _askOutcome(Map<String, dynamic> order) async {
    final outcome = await showModalBottomSheet<CallOutcome>(
      context: context,
      builder: (context) => _OutcomeSheet(order: order),
    );
    if (outcome == null || !mounted) {
      ref.invalidate(callQueueProvider);
      return;
    }
    setState(() => _busy = true);
    try {
      await ref
          .read(apiClientProvider)
          .post(
            '/orders/${order['id']}/confirmation/${_actions[outcome]}',
            body: <String, dynamic>{},
          );
      if (mounted) {
        _say(context.tr(_saved[outcome]!));
      }
    } catch (error) {
      if (mounted) {
        _say(_message(error, 'cq.actionFailed'));
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
        ref.invalidate(callQueueProvider);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final queue = ref.watch(callQueueProvider);
    return Scaffold(
      appBar: AppBar(title: Text(context.tr('cq.title'))),
      body: queue.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => _Centered(
          text: error is ApiError && error.code == ApiErrorCode.featureDisabled
              ? context.tr('cq.notEnabled')
              : _message(error, 'cq.loadFailed'),
        ),
        data: (body) {
          final items = (body['items'] as List<dynamic>? ?? const <dynamic>[])
              .cast<Map<String, dynamic>>();
          final quiet = body['quiet_hours'] as Map<String, dynamic>?;
          return RefreshIndicator(
            onRefresh: () => ref.refresh(callQueueProvider.future),
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: <Widget>[
                if (quiet?['active'] == true)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: Text(
                      context.tr('cq.quietNow', <String, Object?>{
                        'start': quiet!['start'],
                        'end': quiet['end'],
                      }),
                    ),
                  ),
                if (items.isEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 48),
                    child: Text(
                      context.tr('cq.empty'),
                      textAlign: TextAlign.center,
                    ),
                  ),
                for (final item in items)
                  _QueueRow(
                    item: item,
                    busy: _busy,
                    onCall: () => _call(item['order'] as Map<String, dynamic>),
                    onOutcome: () =>
                        _askOutcome(item['order'] as Map<String, dynamic>),
                  ),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _Centered extends StatelessWidget {
  const _Centered({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) => Center(
    child: Padding(
      padding: const EdgeInsets.all(24),
      child: Text(text, textAlign: TextAlign.center),
    ),
  );
}

class _QueueRow extends StatelessWidget {
  const _QueueRow({
    required this.item,
    required this.busy,
    required this.onCall,
    required this.onOutcome,
  });

  final Map<String, dynamic> item;
  final bool busy;
  final VoidCallback onCall;
  final VoidCallback onOutcome;

  @override
  Widget build(BuildContext context) {
    final order = item['order'] as Map<String, dynamic>;
    final confirmation = item['confirmation'] as Map<String, dynamic>;
    final last = item['last_call'] as Map<String, dynamic>?;
    final calls = item['manual_calls'] as int? ?? 0;
    final name = order['customer_name'] as String?;
    final phone = order['customer_phone_masked'] as String? ?? '';
    final cod = Money(order['cod_amount_paisa'] as int? ?? 0).format();
    final theme = Theme.of(context);
    final lastText = switch (last?['status']) {
      'NO_ANSWER' => context.tr('cq.lastNoAnswer'),
      'STARTED' => context.tr('cq.lastStarted'),
      _ => null,
    };
    return Card(
      key: ValueKey('cq.row.${order['id']}'),
      margin: const EdgeInsets.only(bottom: 12),
      child: InkWell(
        onTap: busy ? null : onOutcome,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      '${order['order_number']} · $cod',
                      style: theme.textTheme.titleMedium,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    Text(
                      [
                        if (name != null && name.isNotEmpty) name,
                        phone,
                      ].join(' · '),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 4),
                    Text(
                      context.tr(
                        callReasonKey(confirmation['reason_code'] as String?),
                      ),
                      style: theme.textTheme.bodySmall,
                    ),
                    if (calls > 0 || lastText != null)
                      Text(
                        [
                          if (calls > 0)
                            context.tr('cq.calls', <String, Object?>{
                              'count': calls,
                            }),
                          if (lastText != null) lastText,
                        ].join(' · '),
                        style: theme.textTheme.bodySmall,
                      ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              FilledButton.icon(
                key: ValueKey('cq.call.${order['id']}'),
                onPressed: busy ? null : onCall,
                icon: const Icon(Icons.call),
                label: Text(context.tr('cq.call')),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _OutcomeSheet extends StatelessWidget {
  const _OutcomeSheet({required this.order});

  final Map<String, dynamic> order;

  @override
  Widget build(BuildContext context) {
    final cod = Money(order['cod_amount_paisa'] as int? ?? 0).format();
    Widget choice(CallOutcome outcome, String label, {bool primary = false}) {
      final key = ValueKey('cq.outcome.${outcome.name}');
      void pick() => Navigator.of(context).pop(outcome);
      return Padding(
        padding: const EdgeInsets.only(top: 8),
        child: SizedBox(
          width: double.infinity,
          child: primary
              ? FilledButton(key: key, onPressed: pick, child: Text(label))
              : OutlinedButton(key: key, onPressed: pick, child: Text(label)),
        ),
      );
    }

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(
              context.tr('cq.outcomeTitle'),
              style: Theme.of(context).textTheme.titleMedium,
            ),
            Text('${order['order_number']} · $cod'),
            choice(
              CallOutcome.confirmed,
              context.tr('cq.confirm'),
              primary: true,
            ),
            choice(CallOutcome.cancelled, context.tr('cq.cancel')),
            choice(CallOutcome.noAnswer, context.tr('cq.noAnswer')),
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: Text(context.tr('cq.later')),
            ),
          ],
        ),
      ),
    );
  }
}

/// "N orders waiting for a call" on the orders list; nothing while the queue
/// is empty, switched off or unreachable.
class CallQueueBanner extends ConsumerWidget {
  const CallQueueBanner({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final body = ref.watch(callQueueProvider).valueOrNull;
    final count = (body?['items'] as List<dynamic>?)?.length ?? 0;
    if (count == 0) {
      return const SizedBox.shrink();
    }
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Card(
        key: const ValueKey('cq.banner'),
        child: ListTile(
          leading: const Icon(Icons.phone_in_talk_outlined),
          title: Text(
            context.tr('cq.banner', <String, Object?>{'count': count}),
          ),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => Navigator.of(context).push(
            MaterialPageRoute<void>(builder: (_) => const CallQueueScreen()),
          ),
        ),
      ),
    );
  }
}
