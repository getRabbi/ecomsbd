import 'package:flutter_test/flutter_test.dart';
import 'package:futuregrow_telemetry/futuregrow_telemetry.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/core/api/api_error.dart';
import 'package:ecomsbd/core/storage/token_store.dart';
import 'package:ecomsbd/data/telemetry/futuregrow.dart';

class NoSession implements SessionProvider {
  @override
  Future<StoredSession?> currentSession() async => null;
  @override
  Future<StoredSession?> refreshSession() async => null;
  @override
  Future<void> onSessionInvalidated(ApiError error) async {}
}

class FakeApi extends ApiClient {
  FakeApi() : super(sessionProvider: NoSession());
  bool failWithdrawal = false;
  @override
  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, dynamic>? query,
    bool authenticated = true,
  }) async =>
      path.endsWith('consent') ? {'analytics': true} : {'transactions': []};
  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Object? body,
    Map<String, dynamic>? query,
    bool authenticated = true,
    String? idempotencyKey,
  }) async {
    if (failWithdrawal) throw StateError('offline');
    return body as Map<String, dynamic>;
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test('unavailable AdServices never blocks later session delivery', () async {
    SharedPreferences.setMockInitialValues({});
    final prefs = await SharedPreferences.getInstance();
    var sends = 0;
    final controller = AppTelemetry(
      FakeApi(),
      FutureGrowTelemetry(
        relayUrl: Uri.parse('https://backend.example/telemetry'),
        appId: 'com.ecomsbd.app',
        platform: 'ios',
        appVersion: '1',
        store: PreferenceTelemetryStore(prefs, 'owner'),
        relayHeaders: () async => {},
        readAppleAttributionToken: () async => throw StateError('unavailable'),
        client: MockClient((_) async {
          sends++;
          return http.Response('{"recorded":true}', 202);
        }),
      ),
      prefs,
      'owner',
    );
    await controller.initialize();
    await controller.foreground();
    expect(sends, 5);
    controller.dispose();
  });
  test('durable telemetry namespaces isolate signed-in sellers', () async {
    SharedPreferences.setMockInitialValues({});
    final prefs = await SharedPreferences.getInstance();
    await PreferenceTelemetryStore(prefs, 'first').write('queue', 'saved');
    expect(
      await PreferenceTelemetryStore(prefs, 'first').read('queue'),
      'saved',
    );
    expect(
      await PreferenceTelemetryStore(prefs, 'second').read('queue'),
      isNull,
    );
  });
  test(
    'offline withdrawal stays denied on restart even when server still grants consent',
    () async {
      SharedPreferences.setMockInitialValues({});
      final prefs = await SharedPreferences.getInstance();
      final api = FakeApi();
      var sends = 0;
      AppTelemetry make() => AppTelemetry(
        api,
        FutureGrowTelemetry(
          relayUrl: Uri.parse('https://backend.example/telemetry'),
          appId: 'com.ecomsbd.app',
          platform: 'android',
          appVersion: '1',
          store: PreferenceTelemetryStore(prefs, 'owner'),
          relayHeaders: () async => {},
          client: MockClient((_) async {
            sends++;
            return http.Response('{"recorded":true}', 202);
          }),
        ),
        prefs,
        'owner',
      );
      final first = make();
      await first.initialize();
      expect(sends, 4);
      api.failWithdrawal = true;
      await first.setConsent(false);
      expect(first.consent, false);
      first.dispose();
      final restarted = make();
      await restarted.initialize();
      await restarted.foreground();
      expect(restarted.consent, false);
      expect(sends, 4);
      restarted.dispose();
    },
  );
}
