import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';

/// One SMS switch: `available` is the platform side, `enabled` the shop's.
typedef SmsSwitch = ({bool available, bool enabled});

/// Settings → SMS (launch run 14): the SMS confirm link
/// (`GET/PATCH /shop/sms-settings`) and order-update SMS (the messaging
/// channel `SMS`, `POST /messaging/channels/SMS`). Both off until the seller
/// turns them on, because every SMS costs from the SMS balance.
final smsSettingsProvider =
    FutureProvider.autoDispose<({SmsSwitch link, SmsSwitch updates})>((
      ref,
    ) async {
      final api = ref.watch(apiClientProvider);
      final settings = await api.get('/shop/sms-settings');
      final channels = await api.get('/messaging/channels');
      final link = (settings['confirmation_link'] as Map?) ?? const {};
      return (
        link: (
          available: link['available'] == true,
          enabled: link['enabled'] == true,
        ),
        updates: orderUpdateSwitch(channels['items'] as List? ?? const []),
      );
    });

/// The order-update SMS switch, from `GET /messaging/channels`.
SmsSwitch orderUpdateSwitch(List<dynamic> rows) {
  final row = rows
      .whereType<Map>()
      .where((r) => r['kind'] == 'SMS')
      .firstOrNull;
  if (row == null) {
    return (available: false, enabled: false);
  }
  return (
    available: row['available'] == true && row['shop_blocker'] == null,
    enabled: row['enabled'] == true,
  );
}

class SmsSettingsScreen extends ConsumerStatefulWidget {
  const SmsSettingsScreen({super.key});

  @override
  ConsumerState<SmsSettingsScreen> createState() => _SmsSettingsScreenState();
}

class _SmsSettingsScreenState extends ConsumerState<SmsSettingsScreen> {
  bool _busy = false;

  String _errorText(Object error) {
    final bn = context.strings.locale == AppLocale.bn;
    return error is ApiError
        ? (bn ? error.messageBn : error.messageEn)
        : context.tr('ss.loadFailed');
  }

  Future<void> _change(Future<Object?> Function() action) async {
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await action();
      if (mounted) {
        messenger.showSnackBar(SnackBar(content: Text(context.tr('ss.saved'))));
      }
    } catch (error) {
      if (mounted) {
        messenger.showSnackBar(SnackBar(content: Text(_errorText(error))));
      }
    } finally {
      ref.invalidate(smsSettingsProvider);
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(smsSettingsProvider);
    final api = ref.read(apiClientProvider);
    return Scaffold(
      appBar: AppBar(title: Text(context.tr('ss.title'))),
      body: state.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Text(_errorText(error), textAlign: TextAlign.center),
          ),
        ),
        data: (value) => ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text(context.tr('ss.intro')),
            const SizedBox(height: 12),
            _SwitchCard(
              key: const ValueKey('sms.confirmLink'),
              title: 'ss.linkTitle',
              hint: 'ss.linkHint',
              label: 'ss.linkSwitch',
              state: value.link,
              busy: _busy,
              onChanged: (on) => _change(
                () => api.patch(
                  '/shop/sms-settings',
                  body: {'confirmation_link_enabled': on},
                ),
              ),
            ),
            _SwitchCard(
              key: const ValueKey('sms.orderUpdates'),
              title: 'ss.updatesTitle',
              hint: 'ss.updatesHint',
              label: 'ss.updatesSwitch',
              state: value.updates,
              busy: _busy,
              onChanged: (on) => _change(
                () =>
                    api.post('/messaging/channels/SMS', body: {'enabled': on}),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _SwitchCard extends StatelessWidget {
  const _SwitchCard({
    super.key,
    required this.title,
    required this.hint,
    required this.label,
    required this.state,
    required this.busy,
    required this.onChanged,
  });

  final String title;
  final String hint;
  final String label;
  final SmsSwitch state;
  final bool busy;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    // Turning a switch off is always allowed; on only while it is offered.
    final canChange = !busy && (state.available || state.enabled);
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            ListTile(
              title: Text(context.tr(title)),
              subtitle: Text(context.tr(hint)),
            ),
            SwitchListTile(
              title: Text(context.tr(label)),
              subtitle: Text(
                context.tr(
                  state.available || state.enabled
                      ? 'ss.cost'
                      : 'ss.unavailable',
                ),
              ),
              value: state.enabled,
              onChanged: canChange ? onChanged : null,
            ),
          ],
        ),
      ),
    );
  }
}
