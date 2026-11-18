import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/data/auth/auth_controller.dart';
import 'package:ecomsbd/features/intake/auto_detect_screen.dart';
import 'package:ecomsbd/features/intake/intake_native.dart';
import 'package:ecomsbd/features/intake/quick_order_screen.dart';
import 'package:ecomsbd/l10n/app_locale.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:ecomsbd/l10n/intake_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/fake_api.dart';
import '../helpers.dart';

typedef _Fake = ({ApiClient client, FakeApiAdapter adapter});

const _channel = MethodChannel(MethodChannelIntake.channelName);
final _now = DateTime.utc(2026, 11, 19, 12);

/// The native side of `ecomsbd/intake`, faked at the MethodChannel.
class _FakeNative {
  _FakeNative(this.status, {this.capture});

  Map<String, Object?> status;
  Map<String, Object?>? capture;
  final calls = <MethodCall>[];

  List<MethodCall> named(String method) =>
      calls.where((call) => call.method == method).toList();

  Future<Object?> handle(MethodCall call) async {
    calls.add(call);
    switch (call.method) {
      case 'status':
        return status;
      case 'installId':
        return 'install-0001';
      case 'link':
        final args = call.arguments as Map<Object?, Object?>;
        status = {
          ...status,
          'linked': true,
          'connectionId': args['connectionId'],
        };
        return null;
      case 'takeQuickOrder':
        return capture;
      case 'openNotificationAccess':
      case 'openAppSettings':
      case 'openApp':
        return true;
      default:
        return null;
    }
  }
}

Map<String, Object?> _device({
  bool linked = false,
  bool access = true,
  bool notifications = true,
  int? heartbeat,
  String manufacturer = 'samsung',
}) => {
  'supported': true,
  'linked': linked,
  'revoked': false,
  'connectionId': linked ? 'conn-1' : null,
  'accessGranted': access,
  'notificationsEnabled': notifications,
  'listenerConnected': linked && access,
  'lastHeartbeatAt': heartbeat,
  'rulesVersion': linked ? 1 : null,
  'enabledPackages': ['com.whatsapp.w4b'],
  'autopilotOn': false,
  'pendingUploads': 0,
  'manufacturer': manufacturer,
};

Map<String, dynamic> _settings() => {
  'rules_version': 1,
  'packages': [
    {
      'package': 'com.facebook.pages.app',
      'label': 'Meta Business Suite',
      'default_enabled': true,
      'toggle': null,
      'covered': true,
      'enabled': false,
    },
    {
      'package': 'com.whatsapp.w4b',
      'label': 'WhatsApp Business',
      'default_enabled': true,
      'toggle': null,
      'covered': false,
      'enabled': true,
    },
    {
      'package': 'com.whatsapp',
      'label': 'WhatsApp',
      'default_enabled': false,
      'toggle': null,
      'covered': false,
      'enabled': false,
    },
  ],
};

_FakeNative _install(_FakeNative native) {
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
      .setMockMethodCallHandler(_channel, native.handle);
  addTearDown(
    () => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(_channel, null),
  );
  return native;
}

Widget _app(
  _Fake fake,
  String language,
  Widget home, {
  AuthStage stage = AuthStage.ready,
}) {
  activeAppLocale = language == 'bn' ? AppLocale.bn : AppLocale.en;
  return ProviderScope(
    overrides: [
      apiClientProvider.overrideWithValue(fake.client),
      intakeNativeProvider.overrideWithValue(
        MethodChannelIntake(android: true),
      ),
      quickOrderAuthStageProvider.overrideWithValue(stage),
    ],
    child: MaterialApp(
      locale: Locale(language),
      supportedLocales: const [Locale('en'), Locale('bn')],
      localizationsDelegates: const [
        AppStrings.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: home,
    ),
  );
}

Future<void> _pump(WidgetTester tester, Widget app) async {
  tester.view.physicalSize = referencePhone * 3;
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(app);
  await tester.pumpAndSettle();
}

/// Scroll the screen's list until [key] is built and on screen.
Future<void> _reveal(WidgetTester tester, String key) =>
    tester.scrollUntilVisible(
      find.byKey(ValueKey(key)),
      200,
      scrollable: find.byType(Scrollable).first,
    );

String _t(String language, String key) =>
    (language == 'bn' ? intakeBn : intakeEn)[key]!;

void main() {
  tearDown(() => activeAppLocale = AppLocale.en);

  test('every English intake string has Bangla', () {
    expect(intakeBn.keys.toSet(), intakeEn.keys.toSet());
    expect(intakeBn.values.where((v) => v.trim().isEmpty), isEmpty);
  });

  test('the auto-start guide follows the phone maker', () {
    expect(autoStartGuideKey('Xiaomi'), 'intake.guide.xiaomi');
    expect(autoStartGuideKey('POCO'), 'intake.guide.xiaomi');
    expect(autoStartGuideKey('realme'), 'intake.guide.realme');
    expect(autoStartGuideKey('samsung'), 'intake.guide.samsung');
    expect(autoStartGuideKey('Google'), 'intake.guide.other');
  });

  test('a phone is stale only when linked and silent for a day', () {
    final checkedIn = _now.subtract(const Duration(hours: 25));
    IntakeDeviceStatus status(bool linked, DateTime? at) =>
        IntakeDeviceStatus.fromMap({
          'supported': true,
          'linked': linked,
          'lastHeartbeatAt': at?.millisecondsSinceEpoch,
        });
    expect(status(true, checkedIn).isStale(_now), isTrue);
    expect(status(true, _now).isStale(_now), isFalse);
    expect(status(true, null).isStale(_now), isFalse);
    expect(status(false, checkedIn).isStale(_now), isFalse);
  });

  testWidgets('native calls into Dart arrive as events', (tester) async {
    _install(_FakeNative(_device()));
    final native = MethodChannelIntake(android: true);
    final events = <IntakeEvent>[];
    final subscription = native.events.listen(events.add);
    addTearDown(subscription.cancel);
    for (final method in ['quickOrderChanged', 'openQuickOrder']) {
      await TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .handlePlatformMessage(
            MethodChannelIntake.channelName,
            const StandardMethodCodec().encodeMethodCall(MethodCall(method)),
            (_) {},
          );
    }
    await tester.pump();
    expect(events, [IntakeEvent.quickOrderChanged, IntakeEvent.openQuickOrder]);
  });

  test('off Android the channel is never called', () async {
    final native = _install(_FakeNative(_device()));
    final status = await MethodChannelIntake(android: false).status();
    expect(status.supported, isFalse);
    expect(native.calls, isEmpty);
  });

  for (final language in ['en', 'bn']) {
    testWidgets('links this phone and hands the key to native in $language', (
      tester,
    ) async {
      final native = _install(_FakeNative(_device()));
      final fake = buildFakeApi();
      fake.adapter.onJson('GET', '/intake/settings', _settings());
      fake.adapter.onJson('POST', '/devices/link', {
        'connection_id': 'conn-1',
        'ingest_key': 'edk_${'k' * 40}',
        'key_last4': 'kkkk',
        'scope': 'intake.capture',
      });
      await _pump(
        tester,
        _app(fake, language, AutoDetectScreen(now: () => _now)),
      );

      expect(find.byKey(const ValueKey('intake.disclosure')), findsOneWidget);
      expect(find.text(_t(language, 'intake.disclosureSend')), findsOneWidget);
      await _reveal(tester, 'intake.link');
      await tester.tap(find.byKey(const ValueKey('intake.link')));
      await tester.pumpAndSettle();

      final sent = fake.adapter.to('POST', '/devices/link').single.jsonBody;
      expect(sent['install_id'], 'install-0001');
      expect(sent['platform'], 'android');
      final link = native.named('link').single.arguments as Map;
      expect(link['ingestKey'], 'edk_${'k' * 40}');
      expect(link['connectionId'], 'conn-1');
      expect('${link['apiBase']}', endsWith('/v1'));
      expect(find.text(_t(language, 'intake.linked')), findsOneWidget);
      await _reveal(tester, 'intake.unlink');
      expect(find.byKey(const ValueKey('intake.unlink')), findsOneWidget);
    });

    testWidgets(
      'a stale phone shows its maker guide and toggles sync in $language',
      (tester) async {
        final native = _install(
          _FakeNative(
            _device(
              linked: true,
              access: false,
              notifications: false,
              heartbeat: _now
                  .subtract(const Duration(days: 2))
                  .millisecondsSinceEpoch,
              manufacturer: 'xiaomi',
            ),
          ),
        );
        final fake = buildFakeApi();
        fake.adapter.onJson('GET', '/intake/settings', _settings());
        fake.adapter.on('PATCH', '/intake/settings', (request) {
          final next = _settings();
          (next['packages'] as List)[2]['enabled'] = true;
          (next['packages'] as List)[2]['toggle'] = true;
          return FakeReply(next);
        });
        await _pump(
          tester,
          _app(fake, language, AutoDetectScreen(now: () => _now)),
        );

        await _reveal(tester, 'intake.notificationsOff');
        expect(find.byKey(const ValueKey('intake.openAccess')), findsOneWidget);
        expect(
          find.byKey(const ValueKey('intake.notificationsOff')),
          findsOneWidget,
        );
        await tester.tap(find.byKey(const ValueKey('intake.openAccess')));
        await tester.pumpAndSettle();
        expect(native.named('openNotificationAccess'), hasLength(1));

        final list = find.byType(Scrollable).first;
        await tester.scrollUntilVisible(
          find.byKey(const ValueKey('intake.guide')),
          200,
          scrollable: list,
        );
        expect(find.text(_t(language, 'intake.guide.xiaomi')), findsOneWidget);

        final whatsapp = find.byKey(const ValueKey('intake.app.com.whatsapp'));
        await tester.scrollUntilVisible(whatsapp, 200, scrollable: list);
        final covered = tester.widget<SwitchListTile>(
          find.byKey(const ValueKey('intake.app.com.facebook.pages.app')),
        );
        expect(covered.onChanged, isNull);
        await tester.tap(whatsapp);
        await tester.pumpAndSettle();
        final patch = fake.adapter
            .to('PATCH', '/intake/settings')
            .single
            .jsonBody;
        expect(patch, {
          'package_toggles': {'com.whatsapp': true},
        });
        expect(native.named('syncNow'), hasLength(1));
        expect(tester.widget<SwitchListTile>(whatsapp).value, isTrue);
      },
    );

    testWidgets('a shop without the flag is told so in $language', (
      tester,
    ) async {
      _install(_FakeNative(_device()));
      final fake = buildFakeApi();
      fake.adapter.on(
        'GET',
        '/intake/settings',
        (_) => const FakeReply({
          'code': 'FEATURE_DISABLED',
          'message_en': 'Linked devices are not enabled',
          'message_bn': 'এই ফিচারটি এখন বন্ধ আছে।',
          'retryable': false,
        }, statusCode: 400),
      );
      await _pump(
        tester,
        _app(fake, language, AutoDetectScreen(now: () => _now)),
      );
      expect(find.text(_t(language, 'intake.notEnabled')), findsOneWidget);
      expect(find.byKey(const ValueKey('intake.link')), findsNothing);
    });

    testWidgets(
      'Create sends the buffered chat and shows the draft in $language',
      (tester) async {
        final native = _install(
          _FakeNative(
            _device(linked: true),
            capture: {
              'conversationId': 'c1',
              'expired': false,
              'package': 'com.whatsapp.w4b',
              'conversationKey': 'Rina',
              'senderDisplay': 'Rina',
              'connectionId': 'conn-1',
              'maskedPhone': '01700•••40',
              'district': 'Dhaka',
              'messages': [
                {'text': '2ta nibo', 'sentAt': 1800000000000},
                {'text': '01700000040 Mirpur, Dhaka', 'sentAt': null},
              ],
            },
          ),
        );
        final fake = buildFakeApi();
        fake.adapter.onJson('POST', '/intake/captures/manual', {
          'accepted': 2,
          'duplicates': 0,
          'replayed': false,
          'draft': {
            'id': 'd1',
            'warnings': ['DUPLICATE_ORDER'],
          },
          'attention_id': null,
        });
        await _pump(
          tester,
          _app(fake, language, const QuickOrderScreen(standalone: true)),
        );

        final body = fake.adapter
            .to('POST', '/intake/captures/manual')
            .single
            .jsonBody;
        expect(body['source'], 'DEVICE_NOTIFICATION');
        expect(body['package'], 'com.whatsapp.w4b');
        expect(body['conversation_key'], 'Rina');
        expect(body['connection_id'], 'conn-1');
        final messages = body['messages'] as List;
        expect(messages.first['sent_at'], startsWith('2027-01-15T08:00:00'));
        expect(messages.last['sent_at'], isNull);
        expect(find.text('Rina — order mone hocche'), findsOneWidget);
        expect(find.text('01700•••40 · Dhaka'), findsOneWidget);
        expect(find.text(_t(language, 'qo.created')), findsOneWidget);
        expect(find.text(_t(language, 'qo.duplicate')), findsOneWidget);

        await tester.tap(find.byKey(const ValueKey('qo.done')));
        await tester.pumpAndSettle();
        final finish = native.named('finishQuickOrder').single.arguments as Map;
        expect(finish, {'conversationId': 'c1', 'created': true});
      },
    );

    testWidgets('an expired chat sends nothing in $language', (tester) async {
      final native = _install(
        _FakeNative(
          _device(linked: true),
          capture: {'conversationId': 'c2', 'expired': true},
        ),
      );
      final fake = buildFakeApi();
      await _pump(
        tester,
        _app(fake, language, const QuickOrderScreen(standalone: true)),
      );
      expect(find.text(_t(language, 'qo.expired')), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('qo.close')));
      await tester.pumpAndSettle();
      expect(fake.adapter.to('POST', '/intake/captures/manual'), isEmpty);
      final finish = native.named('finishQuickOrder').single.arguments as Map;
      expect(finish['created'], isFalse);
    });

    testWidgets('signed out, the sheet asks to sign in first in $language', (
      tester,
    ) async {
      _install(
        _FakeNative(
          _device(linked: true),
          capture: {
            'conversationId': 'c3',
            'expired': false,
            'package': 'com.whatsapp.w4b',
            'conversationKey': 'Rina',
            'messages': [
              {'text': '2ta nibo 01700000041 Dhaka', 'sentAt': null},
            ],
          },
        ),
      );
      final fake = buildFakeApi();
      await _pump(
        tester,
        _app(
          fake,
          language,
          const QuickOrderScreen(standalone: true),
          stage: AuthStage.signedOut,
        ),
      );
      expect(find.text(_t(language, 'qo.signIn')), findsOneWidget);
      expect(find.text(_t(language, 'qo.openApp')), findsOneWidget);
      expect(fake.adapter.to('POST', '/intake/captures/manual'), isEmpty);
    });
  }
}
