import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:futuregrow_telemetry/futuregrow_telemetry.dart';
import 'package:http/http.dart' as http;
import 'package:package_info_plus/package_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../../app/providers.dart';
import '../../core/api/api_client.dart';
import '../auth/auth_controller.dart';

class PreferenceTelemetryStore implements TelemetryStore {
  PreferenceTelemetryStore(this.preferences, this.account);
  final SharedPreferences preferences;
  final String account;
  @override
  Future<String?> read(String key) async =>
      preferences.getString('telemetry.$account.$key');
  @override
  Future<void> write(String key, String value) async {
    if (!await preferences.setString('telemetry.$account.$key', value)) {
      throw StateError('Telemetry storage unavailable');
    }
  }
}

class _RelayClient extends http.BaseClient {
  _RelayClient(this.api, this.account, this.stillSameAccount);
  final ApiClient api;
  final String account;
  final Future<bool> Function() stillSameAccount;
  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    if (!await stillSameAccount()) throw StateError('Account changed');
    final raw = await request.finalize().bytesToString();
    // Uses the app's existing token refresh/authentication pipeline. No server key.
    final response = await api.post(
      '/telemetry',
      body: {
        ...jsonDecode(raw) as Map<String, dynamic>,
        'accountBinding': account,
      },
    );
    return http.StreamedResponse(
      Stream.value(utf8.encode(jsonEncode(response))),
      200,
    );
  }
}

class AppTelemetry extends ChangeNotifier with WidgetsBindingObserver {
  AppTelemetry(this.api, this.client, this.preferences, this.account);
  final ApiClient api;
  final FutureGrowTelemetry client;
  final SharedPreferences preferences;
  final String account;
  String get _withdrawalKey => 'telemetry.$account.withdrawalPending';
  bool consent = false;
  bool busy = false;
  String? error;
  bool _running = false;
  bool _disposed = false;
  static const channel = MethodChannel('ecomsbd/telemetry');
  Future<void> initialize() async {
    WidgetsBinding.instance.addObserver(this);
    try {
      if (preferences.getBool(_withdrawalKey) == true) {
        await api.post(
          '/telemetry/consent',
          body: {'analytics': false, 'account': account},
        );
        await preferences.remove(_withdrawalKey);
      }
      consent = (await api.get('/telemetry/consent'))['analytics'] == true;
      if (_disposed) return;
      await client.setAnalyticsConsent(consent);
      await foreground();
    } catch (_) {
      error = 'Usage reporting is unavailable. Nothing new will be sent.';
    }
  }

  Future<void> setConsent(bool value) async {
    if (busy) return;
    busy = true;
    error = null;
    // Stop/purge locally before attempting a withdrawal over the network.
    if (!value) {
      consent = false;
      await preferences.setBool(_withdrawalKey, true);
      await client.setAnalyticsConsent(false);
    }
    notifyListeners();
    try {
      await api.post(
        '/telemetry/consent',
        body: {'analytics': value, 'account': account},
      );
      await preferences.remove(_withdrawalKey);
      if (_disposed) return;
      consent = value;
      await client.setAnalyticsConsent(value);
      await foreground();
    } catch (_) {
      error = 'Consent could not be saved. Retry when connected.';
    }
    busy = false;
    if (!_disposed) notifyListeners();
  }

  Future<void> foreground() async {
    if (!consent || _running || _disposed) return;
    _running = true;
    try {
      await client.recordInstall();
      await client.recordEvent('first_open');
      await client.recordEvent('session');
      // This authenticated controller exists only after seller onboarding.
      await client.recordEvent('activation');
      final response = await api.get('/telemetry/conversions');
      for (final value in (response['transactions'] as List? ?? [])) {
        final row = value as Map;
        await client.recordEvent(
          'conversion',
          occurrenceId: row['id'] as String,
          conversionName: 'subscription_paid',
          valueMicros: row['valueMicros'] as int,
          currency: row['currency'] as String,
        );
      }
      await client.flush();
      // Optional AdServices availability must never hold the general telemetry outbox.
      if (client.platform == 'ios' && consent && !_disposed) {
        final storage = PreferenceTelemetryStore(preferences, account);
        if (await storage.read('appleAttributionDone') != '1') {
          final installId = await storage.read(
            'fg.${client.platform}.${client.appId}.installId',
          );
          final token = await client.readAppleAttributionToken?.call();
          if (installId != null && token != null && consent && !_disposed) {
            final ack = await api.post(
              '/telemetry',
              body: {
                'kind': 'apple_attribution',
                'appId': client.appId,
                'installId': installId,
                'token': token,
                'accountBinding': account,
                'consent': {'analytics': true},
              },
            );
            if (ack['recorded'] == true ||
                ack['status'] == 'already_attributed' ||
                ack['status'] == 'already_reconciled') {
              await storage.write('appleAttributionDone', '1');
            }
          }
        }
      }
    } catch (_) {
      /* Durable outbox retries on the next foreground. */
    } finally {
      _running = false;
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) unawaited(foreground());
  }

  @override
  void dispose() {
    _disposed = true;
    WidgetsBinding.instance.removeObserver(this);
    // Stop this instance synchronously; retain its account-specific retry queue.
    client.close();
    super.dispose();
  }
}

final appTelemetryProvider = FutureProvider<AppTelemetry?>((ref) async {
  final state = ref.watch(
    authControllerProvider.select(
      (s) => (s.stage, s.profile?.userId, s.profile?.tenantId),
    ),
  );
  if (state.$1 != AuthStage.ready || !(Platform.isAndroid || Platform.isIOS)) {
    return null;
  }
  final auth = ref.watch(authRepositoryProvider);
  final session = await auth.currentSession();
  if (session == null) return null;
  final account = '${session.userId}:${session.tenantId}';
  final info = await PackageInfo.fromPlatform();
  final api = ref.watch(apiClientProvider);
  final preferences = await SharedPreferences.getInstance();
  final client = FutureGrowTelemetry(
    // Transport is the authenticated ApiClient above, not this nominal URL.
    relayUrl: Uri.parse('https://relay.invalid/telemetry'),
    appId: info.packageName,
    platform: Platform.isIOS ? 'ios' : 'android',
    appVersion: info.version,
    store: PreferenceTelemetryStore(preferences, account),
    relayHeaders: () async => {},
    client: _RelayClient(api, account, () async {
      final current = await auth.currentSession();
      return current != null &&
          '${current.userId}:${current.tenantId}' == account;
    }),
    readInstallReferrer: () =>
        AppTelemetry.channel.invokeMethod<String>('installReferrer'),
    readAppleAttributionToken: () =>
        AppTelemetry.channel.invokeMethod<String>('appleAttributionToken'),
  );
  final controller = AppTelemetry(api, client, preferences, account);
  ref.onDispose(controller.dispose);
  await controller.initialize();
  return controller;
});

class UsageTelemetrySetting extends ConsumerWidget {
  const UsageTelemetrySetting({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) => ref
      .watch(appTelemetryProvider)
      .when(
        loading: () => const SizedBox.shrink(),
        error: (_, _) => const Text('Usage reporting unavailable'),
        data: (controller) => controller == null
            ? const SizedBox.shrink()
            : ListenableBuilder(
                listenable: controller,
                builder: (context, _) => Column(
                  children: [
                    SwitchListTile(
                      title: const Text('Share app usage statistics'),
                      subtitle: const Text(
                        'Optional: install source, first open, shop setup, sessions and verified subscription payments. Customer orders and contact details are excluded.',
                      ),
                      value: controller.consent,
                      onChanged: controller.busy
                          ? null
                          : (value) => unawaited(controller.setConsent(value)),
                    ),
                    if (controller.error != null) Text(controller.error!),
                  ],
                ),
              ),
      );
}

class UsageTelemetryHost extends ConsumerWidget {
  const UsageTelemetryHost({super.key, required this.child});
  final Widget child;
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    ref.watch(appTelemetryProvider);
    return child;
  }
}
