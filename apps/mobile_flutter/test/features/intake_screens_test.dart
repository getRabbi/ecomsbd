import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/data/auth/auth_controller.dart';
import 'package:ecomsbd/data/commerce/commerce_providers.dart';
import 'package:ecomsbd/data/local/database.dart';
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
import 'commerce_harness.dart' show orderJson;
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
  List<Override> extra = const [],
}) {
  activeAppLocale = language == 'bn' ? AppLocale.bn : AppLocale.en;
  return ProviderScope(
    overrides: [
      ...extra,
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
        expect(find.byKey(const ValueKey('qo.create')), findsOneWidget);
        expect(find.text(_t(language, 'qo.duplicate')), findsOneWidget);

        // Save draft: it stays in Chat orders; the phone lets go of the chat.
        await _reveal(tester, 'qo.saveDraft');
        await tester.tap(find.byKey(const ValueKey('qo.saveDraft')));
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

  group('quick-order sheet (WP1.5)', () {
    Map<String, Object?> capture({
      String id = 'c1',
      String source = 'DEVICE_NOTIFICATION',
    }) => {
      'conversationId': id,
      'expired': false,
      'source': source,
      if (source != 'SHARE') ...{
        'package': 'com.whatsapp.w4b',
        'conversationKey': 'Rina',
        'senderDisplay': 'Rina',
        'connectionId': 'conn-1',
      },
      'messages': [
        {'text': '2ta panjabi nibo, Mirpur 10 Dhaka', 'sentAt': null},
      ],
    };

    Map<String, Object?> draft({
      List<String> phones = const ['+8801700000040'],
      String? selected = '+8801700000040',
      List<String> warnings = const [],
      List<String> uncertain = const [],
      String matchStatus = 'MATCHED',
      String? codSource = 'CATALOG',
      int messages = 1,
      Map<String, Object?>? customer,
    }) => {
      'id': 'd1',
      'provider': 'DEVICE',
      'status': 'READY_FOR_REVIEW',
      'capture_source': 'DEVICE_NOTIFICATION',
      'source_app': 'com.whatsapp.w4b',
      'customer_name': 'Rina',
      'phones': phones,
      'selected_phone': selected,
      'address': 'Mirpur 10, Dhaka',
      'items': [
        {
          'name': 'panjabi',
          'quantity': 2,
          'match': {
            'status': matchStatus,
            'product_id': matchStatus == 'MATCHED' ? 'p1' : null,
            'product_name': matchStatus == 'MATCHED' ? 'Black Panjabi' : null,
            'variant_status': 'NONE',
            'unit_price_paisa': matchStatus == 'MATCHED' ? 90000 : null,
            'candidates': matchStatus == 'AMBIGUOUS'
                ? [
                    {'product_id': 'p1', 'name': 'Black Panjabi'},
                    {'product_id': 'p2', 'name': 'White Panjabi'},
                  ]
                : [],
          },
        },
      ],
      'cod_amount_paisa': 186000,
      'cod_source': codSource,
      'warnings': warnings,
      'uncertain_fields': uncertain,
      'missing_fields': <String>[],
      'message_count': messages,
      'customer': customer,
      'last_message_at': '2026-11-19T12:00:00Z',
    };

    Map<String, Object?> reply(
      Map<String, Object?> draft, {
      Map<String, Object?>? courier,
    }) => {
      'accepted': 1,
      'duplicates': 0,
      'replayed': false,
      'draft': draft,
      'courier_suggestion': courier,
      'attention_id': null,
    };

    Future<({_FakeNative native, _Fake fake, EcomsbdDatabase db})> open(
      WidgetTester tester,
      String language,
      Map<String, Object?> body, {
      Map<String, Object?>? chat,
    }) async {
      final native = _install(
        _FakeNative(_device(linked: true), capture: chat ?? capture()),
      );
      final fake = buildFakeApi();
      final db = EcomsbdDatabase.memory();
      addTearDown(db.close);
      fake.adapter.onJson('POST', '/intake/captures/manual', body);
      await _pump(
        tester,
        _app(
          fake,
          language,
          const QuickOrderScreen(standalone: true),
          extra: [
            databaseProvider.overrideWithValue(db),
            tenantIdProvider.overrideWithValue(testTenantId),
          ],
        ),
      );
      return (native: native, fake: fake, db: db);
    }

    for (final language in ['en', 'bn']) {
      testWidgets('shows the parsed order and its warnings in $language', (
        tester,
      ) async {
        await open(
          tester,
          language,
          reply(
            draft(
              warnings: ['COD_MISMATCH', 'ZONE_UNKNOWN'],
              uncertain: ['address', 'amount'],
              codSource: 'CHAT_MISMATCH',
            ),
            courier: {
              'courier': 'steadfast',
              'source': 'HISTORY',
              'zone': 'INSIDE_DHAKA',
              'delivered': 18,
              'parcels': 20,
            },
          ),
        );
        expect(find.text(_t(language, 'qo.checkMarked')), findsOneWidget);
        expect(find.text('Black Panjabi'), findsOneWidget);
        expect(find.text('1860'), findsOneWidget);
        await _reveal(tester, 'qo.codMismatch');
        expect(find.text(_t(language, 'qo.codMismatch')), findsOneWidget);
        expect(find.text(_t(language, 'qo.zoneUnknown')), findsOneWidget);
        await _reveal(tester, 'qo.courier');
        expect(
          find.text(
            _t(language, 'qo.courierHistory')
                .replaceAll('{courier}', 'Steadfast')
                .replaceAll('{delivered}', '18')
                .replaceAll('{parcels}', '20'),
          ),
          findsOneWidget,
        );
        expect(find.text(_t(language, 'qo.courierNote')), findsOneWidget);
        expect(tester.takeException(), isNull);
      });
    }

    testWidgets('Create order confirms the draft with the chosen phone', (
      tester,
    ) async {
      final setup = await open(
        tester,
        'en',
        reply(
          draft(
            phones: ['+8801700000040', '+8801700000041'],
            selected: null,
            uncertain: ['phone'],
          ),
          courier: {'courier': 'pathao', 'source': 'SHOP_DEFAULT'},
        ),
      );
      setup.fake.adapter.onJson('POST', '/chat-orders/d1/confirm', {
        'order': orderJson(id: 'o1', number: 'CP-20261119-0001'),
        'duplicate_check': null,
        'replayed': false,
      });
      expect(find.text(_t('en', 'qo.choosePhone')), findsOneWidget);
      expect(find.text('Courier: Pathao (your default)'), findsOneWidget);

      // No phone chosen yet: the form says so and sends nothing.
      await _reveal(tester, 'qo.create');
      await tester.tap(find.byKey(const ValueKey('qo.create')));
      await tester.pumpAndSettle();
      expect(find.text(_t('en', 'qo.phoneRequired')), findsOneWidget);
      expect(setup.fake.adapter.to('POST', '/chat-orders/d1/confirm'), isEmpty);

      await _reveal(tester, 'qo.phone.1');
      await tester.tap(find.byKey(const ValueKey('qo.phone.1')));
      await tester.pumpAndSettle();
      await _reveal(tester, 'qo.create');
      await tester.tap(find.byKey(const ValueKey('qo.create')));
      await tester.pumpAndSettle();

      final body = setup.fake.adapter
          .to('POST', '/chat-orders/d1/confirm')
          .single
          .jsonBody;
      expect(body['phone'], '+8801700000041');
      expect(body['cod_amount_paisa'], 186000);
      expect(body['items'], [
        {
          'product_id': 'p1',
          'name': 'Black Panjabi',
          'quantity': 2,
          'unit_price_paisa': 90000,
        },
      ]);
      expect(find.text('Order CP-20261119-0001 created.'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('qo.done')));
      await tester.pumpAndSettle();
      final finish =
          setup.native.named('finishQuickOrder').single.arguments as Map;
      expect(finish, {'conversationId': 'c1', 'created': true});
    });

    testWidgets('an ambiguous product must be chosen before creating', (
      tester,
    ) async {
      final setup = await open(
        tester,
        'en',
        reply(draft(matchStatus: 'AMBIGUOUS', codSource: null)),
      );
      expect(find.byKey(const ValueKey('qo.pick.0')), findsOneWidget);
      await _reveal(tester, 'qo.create');
      await tester.tap(find.byKey(const ValueKey('qo.create')));
      await tester.pumpAndSettle();
      expect(find.text(_t('en', 'qo.chooseProduct')), findsOneWidget);
      expect(setup.fake.adapter.to('POST', '/chat-orders/d1/confirm'), isEmpty);
    });

    testWidgets('offline, Create order goes through the outbox', (
      tester,
    ) async {
      final setup = await open(tester, 'en', reply(draft()));
      setup.fake.adapter.offline = true;
      await _reveal(tester, 'qo.create');
      await tester.tap(find.byKey(const ValueKey('qo.create')));
      await tester.pumpAndSettle();

      expect(find.text(_t('en', 'qo.queued')), findsOneWidget);
      final entry =
          (await setup.db.select(setup.db.outboxEntries).get()).single;
      // The draft id is the client id: the server closes the draft when the
      // queued create lands, and a later confirm replays the same order.
      expect(entry.entityId, 'd1');
    });

    testWidgets('Aro message lagbe adds a pasted message to the same chat', (
      tester,
    ) async {
      final setup = await open(tester, 'en', reply(draft()));
      await _reveal(tester, 'qo.more');
      await tester.tap(find.byKey(const ValueKey('qo.more')));
      await tester.pumpAndSettle();

      // Nothing pasted: nothing is sent.
      await tester.tap(find.byKey(const ValueKey('qo.moreAdd')));
      await tester.pumpAndSettle();
      expect(find.text(_t('en', 'qo.moreEmpty')), findsOneWidget);

      setup.fake.adapter.onJson(
        'POST',
        '/intake/captures/manual',
        reply(draft(messages: 2)),
      );
      await tester.enterText(
        find.byKey(const ValueKey('qo.moreText')),
        'ar ekta lal shari',
      );
      await tester.tap(find.byKey(const ValueKey('qo.moreAdd')));
      await tester.pumpAndSettle();

      final sent = setup.fake.adapter.to('POST', '/intake/captures/manual');
      expect(sent, hasLength(2));
      final body = sent.last.jsonBody;
      expect(body['conversation_key'], 'Rina');
      expect(body['package'], 'com.whatsapp.w4b');
      expect((body['messages'] as List).single['text'], 'ar ekta lal shari');
      expect(find.byKey(const ValueKey('qo.create')), findsOneWidget);
    });

    testWidgets('a share opens the same sheet with source SHARE', (
      tester,
    ) async {
      final setup = await open(
        tester,
        'en',
        reply(draft()),
        chat: capture(id: 's1', source: 'SHARE'),
      );
      final body = setup.fake.adapter
          .to('POST', '/intake/captures/manual')
          .single
          .jsonBody;
      expect(body['source'], 'SHARE');
      expect(body['package'], isNull);
      // Its own conversation, so "Aro message lagbe" joins the same draft.
      expect(body['conversation_key'], 'share:s1');
      expect(find.byKey(const ValueKey('qo.create')), findsOneWidget);
    });
  });
}
