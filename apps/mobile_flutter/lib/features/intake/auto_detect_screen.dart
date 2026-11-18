import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../core/env.dart';
import '../../core/money.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';
import '../shared/data_state.dart';
import 'intake_native.dart';

/// Settings → Auto order detect (V4 WP1.4).
///
/// Disclosure first (what is read, what is sent, how to stop), then this
/// phone (link, Notification Access, status), then which apps are read
/// (`GET/PATCH /v1/intake/settings`). The server answers FEATURE_DISABLED
/// while the shop's `intake_device_enabled` flag is off.
final intakeSettingsProvider = FutureProvider.autoDispose<Map<String, dynamic>>(
  (ref) => ref.watch(apiClientProvider).get('/intake/settings'),
);

/// The auto-start guide for this phone's maker.
String autoStartGuideKey(String manufacturer) {
  final maker = manufacturer.toLowerCase();
  if (maker.contains('xiaomi') ||
      maker.contains('redmi') ||
      maker.contains('poco')) {
    return 'intake.guide.xiaomi';
  }
  for (final brand in const ['oppo', 'realme', 'vivo', 'samsung']) {
    if (maker.contains(brand)) return 'intake.guide.$brand';
  }
  return 'intake.guide.other';
}

class AutoDetectScreen extends ConsumerStatefulWidget {
  const AutoDetectScreen({super.key, this.now});

  /// Fixed clock for tests.
  final DateTime Function()? now;

  @override
  ConsumerState<AutoDetectScreen> createState() => _AutoDetectScreenState();
}

class _AutoDetectScreenState extends ConsumerState<AutoDetectScreen>
    with WidgetsBindingObserver {
  bool _busy = false;
  Map<String, dynamic>? _settings;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Back from system settings (Notification Access, battery): re-read.
    if (state == AppLifecycleState.resumed) {
      ref.invalidate(intakeDeviceStatusProvider);
    }
  }

  String _message(ApiError error) => context.strings.locale == AppLocale.bn
      ? error.messageBn
      : error.messageEn;

  void _snack(String text) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  Future<void> _run(Future<void> Function() action) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await action();
    } on ApiError catch (error) {
      if (mounted) _snack(_message(error));
    } on PlatformException {
      if (mounted) _snack(context.tr('intake.linkFailed'));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Map<String, Object?> _deviceInfo(String installId) => <String, Object?>{
    'install_id': installId,
    'platform': 'android',
    'app_version': ref.read(appVersionProvider),
    'covered_packages': const <String>[],
  };

  Future<void> _storeKey(Map<String, dynamic> reply) async {
    await ref
        .read(intakeNativeProvider)
        .link(
          apiBase: '${Env.apiBaseUrl}${Env.apiVersionPrefix}',
          ingestKey: reply['ingest_key'] as String,
          connectionId: reply['connection_id'] as String,
        );
    ref.invalidate(intakeDeviceStatusProvider);
    if (mounted) _snack(context.tr('intake.linked'));
  }

  Future<void> _link() => _run(() async {
    final native = ref.read(intakeNativeProvider);
    final reply = await ref
        .read(apiClientProvider)
        .post('/devices/link', body: _deviceInfo(await native.installId()));
    await _storeKey(reply);
  });

  Future<void> _claim() async {
    final code = await showDialog<String>(
      context: context,
      builder: (_) => const _PairingCodeDialog(),
    );
    if (code == null) return;
    await _run(() async {
      final native = ref.read(intakeNativeProvider);
      final reply = await ref
          .read(apiClientProvider)
          .post(
            '/devices/pair/claim',
            authenticated: false,
            body: <String, Object?>{
              ..._deviceInfo(await native.installId()),
              'code': code,
            },
          );
      await _storeKey(reply);
    });
  }

  Future<void> _unlink() => _run(() async {
    await ref.read(intakeNativeProvider).unlink();
    ref.invalidate(intakeDeviceStatusProvider);
    if (mounted) _snack(context.tr('intake.unlinked'));
  });

  Future<void> _toggle(String package, bool on, IntakeDeviceStatus device) =>
      _run(() async {
        final reply = await ref
            .read(apiClientProvider)
            .patch(
              '/intake/settings',
              body: <String, Object?>{
                'package_toggles': <String, bool>{package: on},
              },
            );
        setState(() => _settings = reply);
        // The phone re-reads its rules now instead of at the next daily check.
        if (device.linked) await ref.read(intakeNativeProvider).syncNow();
        if (mounted) _snack(context.tr('intake.saved'));
      });

  @override
  Widget build(BuildContext context) {
    final settings = ref.watch(intakeSettingsProvider);
    final device = ref.watch(intakeDeviceStatusProvider);
    return Scaffold(
      appBar: AppBar(title: Text(context.tr('intake.title'))),
      body: settings.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) {
          if (error is ApiError && error.code == ApiErrorCode.featureDisabled) {
            return _Message(context.tr('intake.notEnabled'));
          }
          return _body(device, null, error);
        },
        data: (values) => _body(device, _settings ?? values, null),
      ),
    );
  }

  Widget _body(
    AsyncValue<IntakeDeviceStatus> device,
    Map<String, dynamic>? settings,
    Object? settingsError,
  ) {
    return device.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (_, _) => _Message(context.tr('intake.loadFailed')),
      data: (status) {
        if (!status.supported) {
          return _Message(context.tr('intake.unsupported'));
        }
        final now = (widget.now ?? DateTime.now)();
        final theme = Theme.of(context);
        return ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
          children: [
            const _Disclosure(),
            _section('intake.sectionPhone'),
            ..._phone(status, now, theme),
            _section('intake.sectionApps'),
            ..._apps(status, settings, settingsError, theme),
          ],
        );
      },
    );
  }

  List<Widget> _phone(
    IntakeDeviceStatus status,
    DateTime now,
    ThemeData theme,
  ) {
    if (!status.linked || status.revoked) {
      return [
        if (status.revoked)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Text(
              context.tr('intake.revoked'),
              style: TextStyle(color: theme.colorScheme.error),
            ),
          ),
        FilledButton(
          key: const ValueKey('intake.link'),
          onPressed: _busy ? null : _link,
          child: Text(context.tr('intake.link')),
        ),
        TextButton(
          key: const ValueKey('intake.enterCode'),
          onPressed: _busy ? null : _claim,
          child: Text(context.tr('intake.enterCode')),
        ),
      ];
    }
    final native = ref.read(intakeNativeProvider);
    final heartbeat = status.lastHeartbeatAt;
    return [
      ListTile(
        key: const ValueKey('intake.access'),
        contentPadding: EdgeInsets.zero,
        leading: Icon(
          status.accessGranted ? Icons.check_circle : Icons.error_outline,
          color: status.accessGranted
              ? Colors.green.shade700
              : theme.colorScheme.error,
        ),
        title: Text(
          context.tr(
            status.accessGranted ? 'intake.accessOn' : 'intake.accessOff',
          ),
        ),
      ),
      if (!status.accessGranted) ...[
        FilledButton(
          key: const ValueKey('intake.openAccess'),
          onPressed: native.openNotificationAccess,
          child: Text(context.tr('intake.openAccess')),
        ),
        Padding(
          padding: const EdgeInsets.only(top: 6, bottom: 8),
          child: Text(
            context.tr('intake.accessNote'),
            style: theme.textTheme.bodySmall,
          ),
        ),
      ],
      if (!status.notificationsEnabled)
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Text(
            context.tr('intake.notificationsOff'),
            key: const ValueKey('intake.notificationsOff'),
            style: TextStyle(color: theme.colorScheme.error),
          ),
        ),
      ListTile(
        key: const ValueKey('intake.status'),
        contentPadding: EdgeInsets.zero,
        leading: Icon(
          status.working && status.listenerConnected
              ? Icons.hearing
              : Icons.hearing_disabled,
        ),
        title: Text(
          context.tr(
            status.working && status.listenerConnected
                ? 'intake.listening'
                : 'intake.notListening',
          ),
        ),
        subtitle: Text(
          heartbeat == null
              ? context.tr('intake.noHeartbeat')
              : context.tr('intake.lastHeartbeat', {
                  'time': formatRelative(heartbeat),
                }),
        ),
      ),
      if (status.isStale(now))
        Card(
          key: const ValueKey('intake.stale'),
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  context.tr('intake.staleTitle'),
                  style: theme.textTheme.titleSmall,
                ),
                const SizedBox(height: 6),
                Text(context.tr('intake.staleBody')),
                const SizedBox(height: 6),
                Text(
                  context.tr(autoStartGuideKey(status.manufacturer)),
                  key: const ValueKey('intake.guide'),
                ),
                const SizedBox(height: 8),
                OutlinedButton(
                  onPressed: native.openAppSettings,
                  child: Text(context.tr('intake.openAppSettings')),
                ),
              ],
            ),
          ),
        ),
      const SizedBox(height: 8),
      OutlinedButton(
        key: const ValueKey('intake.unlink'),
        onPressed: _busy ? null : _unlink,
        child: Text(context.tr('intake.unlink')),
      ),
    ];
  }

  List<Widget> _apps(
    IntakeDeviceStatus status,
    Map<String, dynamic>? settings,
    Object? error,
    ThemeData theme,
  ) {
    if (settings == null) {
      final forbidden = error is ApiError && error.code == 'FORBIDDEN';
      return [
        Text(
          forbidden
              ? context.tr('intake.appsForbidden')
              : (error is ApiError
                    ? _message(error)
                    : context.tr('intake.loadFailed')),
        ),
      ];
    }
    final packages = (settings['packages'] as List<dynamic>? ?? const [])
        .cast<Map<String, dynamic>>();
    return [
      Text(context.tr('intake.appsNote'), style: theme.textTheme.bodySmall),
      for (final item in packages)
        SwitchListTile(
          key: ValueKey('intake.app.${item['package']}'),
          contentPadding: EdgeInsets.zero,
          title: Text('${item['label']}'),
          subtitle: item['covered'] == true
              ? Text(context.tr('intake.covered'))
              : null,
          value: item['enabled'] == true,
          onChanged: _busy || item['covered'] == true
              ? null
              : (on) => _toggle('${item['package']}', on, status),
        ),
    ];
  }

  Widget _section(String key) => Padding(
    padding: const EdgeInsets.only(top: 20, bottom: 8),
    child: Text(
      context.tr(key),
      style: Theme.of(context).textTheme.titleMedium,
    ),
  );
}

class _Disclosure extends StatelessWidget {
  const _Disclosure();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      key: const ValueKey('intake.disclosure'),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              context.tr('intake.disclosureTitle'),
              style: theme.textTheme.titleSmall,
            ),
            for (final key in const [
              'intake.disclosureRead',
              'intake.disclosureKeep',
              'intake.disclosureSend',
              'intake.disclosureStop',
            ])
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(context.tr(key)),
              ),
          ],
        ),
      ),
    );
  }
}

class _Message extends StatelessWidget {
  const _Message(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Center(
    child: Padding(
      padding: const EdgeInsets.all(24),
      child: Text(text, textAlign: TextAlign.center),
    ),
  );
}

class _PairingCodeDialog extends StatefulWidget {
  const _PairingCodeDialog();

  @override
  State<_PairingCodeDialog> createState() => _PairingCodeDialogState();
}

class _PairingCodeDialogState extends State<_PairingCodeDialog> {
  final _controller = TextEditingController();
  String? _error;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _submit() {
    final code = normalizeDigits(_controller.text).replaceAll(' ', '');
    if (!RegExp(r'^[0-9]{6}$').hasMatch(code)) {
      setState(() => _error = context.tr('intake.codeInvalid'));
      return;
    }
    Navigator.of(context).pop(code);
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(context.tr('intake.codeTitle')),
      content: TextField(
        key: const ValueKey('intake.code'),
        controller: _controller,
        autofocus: true,
        keyboardType: TextInputType.number,
        maxLength: 7,
        decoration: InputDecoration(
          hintText: context.tr('intake.codeHint'),
          errorText: _error,
        ),
        onSubmitted: (_) => _submit(),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: Text(context.tr('intake.cancel')),
        ),
        FilledButton(
          key: const ValueKey('intake.codeSubmit'),
          onPressed: _submit,
          child: Text(context.tr('intake.codeSubmit')),
        ),
      ],
    );
  }
}
