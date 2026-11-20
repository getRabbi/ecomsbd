import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../data/team/models.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/autopilot_strings.dart';
import '../settings/automation_screen.dart';

/// Autopilot (V4 Phase 4): the Settings checklist and switch.
///
/// The server decides everything that matters: turning on needs every
/// checklist line (`409 AUTOPILOT_NOT_READY`), each run re-checks them, and a
/// held chat order stays with the seller with its reason code. This screen
/// labels those codes and picks which buttons to show (same rules as the web,
/// `apps/web/lib/autopilot.ts`). `FEATURE_DISABLED` → "not available".
final autopilotStatusProvider =
    FutureProvider.autoDispose<Map<String, dynamic>>(
      (ref) => ref.watch(apiClientProvider).get('/autopilot'),
    );

/// `GET /autopilot/today`: today's counts and the last five decisions.
final autopilotTodayProvider = FutureProvider.autoDispose<Map<String, dynamic>>(
  (ref) => ref.watch(apiClientProvider).get('/autopilot/today'),
);

/// The copy key for a reason code; an unknown code reads as "held".
String autopilotReasonKey(Object? code) =>
    autopilotReasonCodes.contains(code) ? 'ap.r.$code' : 'ap.r.unknown';

/// The copy key for the courier line's problem.
String autopilotCourierProblemKey(Object? code) =>
    autopilotCourierProblems.contains(code)
    ? 'ap.c.$code'
    : 'ap.r.COURIER_NOT_READY';

/// Whole percent, rounded down so a rate never reads better than it is.
int deliveredPercent(int delivered, int parcels) =>
    parcels > 0 ? (delivered * 100) ~/ parcels : 0;

/// A courier key as a name (`steadfast` → `Steadfast`, `redx` → `RedX`).
String courierDisplayName(String key) => switch (key) {
  'steadfast' => 'Steadfast',
  'pathao' => 'Pathao',
  'redx' => 'RedX',
  '' => key,
  _ => key[0].toUpperCase() + key.substring(1),
};

/// What the viewer's role may do here. The server checks again: Pause needs
/// `order.write` (owner, manager, order operator); turning on/off and Resume
/// need `settings.manage` (owner).
bool autopilotCan(String? role, String permission) {
  final parsed = TeamRole.parse(role);
  return switch (permission) {
    'order.write' => const {
      TeamRole.owner,
      TeamRole.manager,
      TeamRole.orderOperator,
    }.contains(parsed),
    'settings.manage' => parsed == TeamRole.owner,
    _ => false,
  };
}

/// The viewer's role; a signed-out preview reads as the owner (as the menu).
final autopilotRoleProvider = Provider<String>(
  (ref) => ref.watch(authControllerProvider).profile?.role ?? 'OWNER',
);

String autopilotRole(WidgetRef ref) => ref.watch(autopilotRoleProvider);

/// Whether "Turn on" may be pressed: every line but consent passes and
/// consent is ticked now (or was recorded before).
bool canTurnOn(Map<String, dynamic> status, {required bool consentTicked}) {
  if (status['on'] == true) return false;
  final lines = (status['checklist'] as List? ?? const <Object?>[])
      .whereType<Map<dynamic, dynamic>>();
  return lines.every(
    (line) => line['ok'] == true || (line['key'] == 'CONSENT' && consentTicked),
  );
}

/// An error as the seller should read it.
String autopilotErrorText(BuildContext context, Object error, String fallback) {
  if (error is ApiError) {
    if (error.code == ApiErrorCode.featureDisabled) {
      return context.tr('ap.notEnabled');
    }
    final bn = context.strings.locale == AppLocale.bn;
    final text = bn ? error.messageBn : error.messageEn;
    if (text.trim().isNotEmpty) return text;
  }
  return context.tr(fallback);
}

class AutopilotScreen extends ConsumerWidget {
  const AutopilotScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final status = ref.watch(autopilotStatusProvider);
    return Scaffold(
      appBar: AppBar(title: Text(context.tr('ap.title'))),
      body: status.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Text(
              autopilotErrorText(context, error, 'ap.loadFailed'),
              key: const ValueKey('ap.error'),
              textAlign: TextAlign.center,
            ),
          ),
        ),
        data: (values) => _AutopilotBody(status: values),
      ),
    );
  }
}

class _AutopilotBody extends ConsumerStatefulWidget {
  const _AutopilotBody({required this.status});

  final Map<String, dynamic> status;

  @override
  ConsumerState<_AutopilotBody> createState() => _AutopilotBodyState();
}

class _AutopilotBodyState extends ConsumerState<_AutopilotBody> {
  late bool _manualQueue = widget.status['manual_queue'] == true;
  bool _consent = false;
  bool _busy = false;

  Future<void> _save({required bool on}) async {
    setState(() => _busy = true);
    try {
      await ref
          .read(apiClientProvider)
          .put(
            '/autopilot',
            body: <String, dynamic>{
              'on': on,
              if (on) 'consent': _consent,
              if (on) 'manual_queue': _manualQueue,
            },
          );
      ref.invalidate(autopilotTodayProvider);
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(autopilotErrorText(context, error, 'ap.saveFailed')),
          ),
        );
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
        ref.invalidate(autopilotStatusProvider);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final status = widget.status;
    final role = autopilotRole(ref);
    final canManage = autopilotCan(role, 'settings.manage');
    final on = status['on'] == true;
    final pausedUntil = status['paused_until'] as String?;
    final cap = status['daily_cap'];
    final consented = status['consent_at'] != null;
    final lines = (status['checklist'] as List? ?? const <Object?>[])
        .whereType<Map<dynamic, dynamic>>()
        .toList();
    final workflow = status['workflow'] as Map? ?? const <String, Object?>{};
    final theme = Theme.of(context);

    return ListView(
      padding: const EdgeInsets.all(16),
      children: <Widget>[
        Text(context.tr('ap.subtitle')),
        const SizedBox(height: 12),
        Text(
          context.tr(on ? 'ap.on' : 'ap.off'),
          key: const ValueKey('ap.state'),
          style: theme.textTheme.titleMedium,
        ),
        if (pausedUntil != null)
          Text(context.tr('ap.pausedUntil', {'time': _clock(pausedUntil)})),
        if (cap is int) Text(context.tr('ap.dailyCap', {'cap': cap})),
        if (workflow['enabled'] != true) ...<Widget>[
          const SizedBox(height: 8),
          Text(context.tr('ap.workflowOff')),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: TextButton(
              key: const ValueKey('ap.workflowLink'),
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => const AutomationScreen(),
                ),
              ),
              child: Text(context.tr('ap.workflowLink')),
            ),
          ),
        ],
        const SizedBox(height: 16),
        Text(context.tr('ap.what'), style: theme.textTheme.titleSmall),
        for (final key in const ['ap.what1', 'ap.what2', 'ap.what3'])
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text(context.tr(key)),
          ),
        const SizedBox(height: 16),
        Text(context.tr('ap.checklist'), style: theme.textTheme.titleSmall),
        Text(context.tr('ap.checklistHint'), style: theme.textTheme.bodySmall),
        for (final line in lines) ...<Widget>[
          _CheckLine(line: line),
          if (line['key'] == 'COURIER')
            _CourierComparison(
              comparison:
                  status['courier_comparison'] as Map? ??
                  const <String, Object?>{},
            ),
        ],
        const SizedBox(height: 16),
        if (!canManage)
          Text(context.tr('ap.readOnly'), key: const ValueKey('ap.readOnly'))
        else if (on)
          OutlinedButton(
            key: const ValueKey('ap.turnOff'),
            onPressed: _busy ? null : () => _save(on: false),
            child: Text(context.tr('ap.turnOff')),
          )
        else ...<Widget>[
          SwitchListTile(
            key: const ValueKey('ap.manualQueue'),
            contentPadding: EdgeInsets.zero,
            value: _manualQueue,
            onChanged: _busy ? null : (v) => setState(() => _manualQueue = v),
            title: Text(context.tr('ap.manualQueue')),
          ),
          if (!consented)
            CheckboxListTile(
              key: const ValueKey('ap.consent'),
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              value: _consent,
              onChanged: _busy
                  ? null
                  : (v) => setState(() => _consent = v ?? false),
              title: Text(context.tr('ap.consent')),
            ),
          FilledButton(
            key: const ValueKey('ap.turnOn'),
            onPressed:
                _busy ||
                    !canTurnOn(status, consentTicked: _consent || consented)
                ? null
                : () => _save(on: true),
            child: Text(context.tr('ap.turnOn')),
          ),
        ],
      ],
    );
  }
}

/// `HH:mm` in the phone's own time zone.
String _clock(String iso) {
  final at = DateTime.tryParse(iso)?.toLocal();
  if (at == null) return iso;
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(at.hour)}:${two(at.minute)}';
}

class _CheckLine extends StatelessWidget {
  const _CheckLine({required this.line});

  final Map<dynamic, dynamic> line;

  @override
  Widget build(BuildContext context) {
    final key = line['key'] as String? ?? '';
    final ok = line['ok'] == true;
    final detail = line['detail'] as Map? ?? const <String, Object?>{};
    final label = key == 'HISTORY'
        ? context.tr('ap.l.HISTORY', {
            'needed': detail['needed'] ?? 20,
            'confirmed': detail['confirmed'] ?? 0,
          })
        : context.tr('ap.l.$key');
    return Padding(
      key: ValueKey('ap.line.$key'),
      padding: const EdgeInsets.only(top: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(
            ok ? Icons.check_circle : Icons.cancel_outlined,
            size: 20,
            color: ok ? Colors.green.shade700 : Colors.red.shade700,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(label),
                if (!ok && key == 'COURIER')
                  Text(
                    context.tr(autopilotCourierProblemKey(detail['problem'])),
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Below the courier line: the default Autopilot always uses, and the shop's
/// three best couriers by its own delivered rate (a suggestion only).
class _CourierComparison extends StatelessWidget {
  const _CourierComparison({required this.comparison});

  final Map<dynamic, dynamic> comparison;

  @override
  Widget build(BuildContext context) {
    final small = Theme.of(context).textTheme.bodySmall;
    final fallback = comparison['default'] as String?;
    final options = (comparison['options'] as List? ?? const <Object?>[])
        .whereType<Map<dynamic, dynamic>>()
        .toList();
    return Padding(
      key: const ValueKey('ap.comparison'),
      padding: const EdgeInsetsDirectional.only(start: 28, top: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          if (fallback != null)
            Text(
              context.tr('ap.usesDefault', {
                'courier': courierDisplayName(fallback),
              }),
              style: small,
            ),
          if (options.isEmpty)
            Text(context.tr('ap.compareNone'), style: small)
          else ...<Widget>[
            Text(
              context.tr('ap.compare', {'days': comparison['days'] ?? 180}),
              style: small,
            ),
            for (final option in options)
              Text(
                [
                  context.tr('ap.compareRow', {
                    'courier': courierDisplayName(
                      option['courier'] as String? ?? '',
                    ),
                    'percent': deliveredPercent(
                      option['delivered'] as int? ?? 0,
                      option['parcels'] as int? ?? 0,
                    ),
                    'delivered': option['delivered'] ?? 0,
                    'parcels': option['parcels'] ?? 0,
                  }),
                  if (option['is_default'] == true)
                    '(${context.tr('ap.compareDefault')})',
                ].join(' '),
                style: small,
              ),
          ],
        ],
      ),
    );
  }
}
