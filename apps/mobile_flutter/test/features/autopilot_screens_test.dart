import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/features/autopilot/autopilot_home_card.dart';
import 'package:ecomsbd/features/autopilot/autopilot_screen.dart';
import 'package:ecomsbd/l10n/app_locale.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:ecomsbd/l10n/automation_strings.dart';
import 'package:ecomsbd/l10n/autopilot_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/fake_api.dart';
import '../helpers.dart';

typedef _Fake = ({ApiClient client, FakeApiAdapter adapter});

Map<String, dynamic> _line(
  String key, {
  bool ok = true,
  String reason = 'X',
  Map<String, dynamic> detail = const {},
}) => {'key': key, 'ok': ok, 'reason': reason, 'detail': detail};

Map<String, dynamic> _status({
  bool on = false,
  bool courierOk = false,
  bool consent = false,
  String? pausedUntil,
}) => {
  'on': on,
  'consent_at': consent ? '2026-11-20T04:00:00+00:00' : null,
  'paused_until': pausedUntil,
  'manual_queue': false,
  'daily_cap': 30,
  'ready': false,
  'checklist': [
    _line('PLAN'),
    _line('CONFIRMATIONS'),
    _line(
      'COURIER',
      ok: courierOk,
      reason: 'COURIER_NOT_READY',
      detail: {'provider': 'pathao', 'problem': 'NOT_LIVE_VERIFIED'},
    ),
    _line('DELIVERY_RULES'),
    _line('VOICE', detail: {'manual_queue': true}),
    _line('HISTORY', detail: {'confirmed': 23, 'needed': 20}),
    _line('CONSENT', ok: consent, reason: 'CONSENT_MISSING'),
  ],
  'reason': on ? null : 'AUTOPILOT_OFF',
  'workflow': {'installed': true, 'enabled': false},
  'courier_comparison': {
    'default': 'pathao',
    'days': 180,
    'options': [
      {
        'courier': 'steadfast',
        'delivered': 2,
        'parcels': 3,
        'is_default': false,
      },
      {'courier': 'pathao', 'delivered': 3, 'parcels': 4, 'is_default': true},
    ],
  },
};

Map<String, dynamic> _today() => {
  'date': '2026-11-20',
  'converted': 3,
  'confirmed': 1,
  'cancelled': 1,
  'waiting': 1,
  'held': 2,
  'recent': [
    {
      'draft_id': 'd1',
      'outcome': 'CONVERTED',
      'reason': null,
      'order_id': 'o1',
      'order_number': 'ORD-1042',
      'at': '2026-11-20T05:00:00+00:00',
    },
    {
      'draft_id': 'd2',
      'outcome': 'HELD',
      'reason': 'LOCATION_UNRESOLVED',
      'order_id': null,
      'order_number': null,
      'at': '2026-11-20T04:00:00+00:00',
    },
  ],
};

const _disabled = FakeReply({
  'code': 'FEATURE_DISABLED',
  'message_bn': 'চালু নেই',
  'message_en': 'Autopilot is not enabled',
  'retryable': false,
}, statusCode: 403);

Widget _app(_Fake fake, String language, Widget home, {String role = 'OWNER'}) {
  activeAppLocale = language == 'bn' ? AppLocale.bn : AppLocale.en;
  return ProviderScope(
    overrides: [
      apiClientProvider.overrideWithValue(fake.client),
      autopilotRoleProvider.overrideWithValue(role),
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

Future<void> _pumpAt360(WidgetTester tester, Widget app) async {
  tester.view.physicalSize = referencePhone * 3;
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(app);
  await tester.pumpAndSettle();
}

void main() {
  tearDown(() => activeAppLocale = AppLocale.en);

  test('every English Autopilot string has Bangla', () {
    expect(autopilotBn.keys.toSet(), autopilotEn.keys.toSet());
    expect(autopilotBn.values.where((v) => v.trim().isEmpty), isEmpty);
  });

  test('every reason code and courier problem has copy in both languages', () {
    expect(autopilotReasonCodes, hasLength(24));
    expect(autopilotReasonCodes, contains('LOCATION_UNRESOLVED'));
    for (final code in autopilotReasonCodes) {
      expect(autopilotReasonKey(code), 'ap.r.$code');
      expect(autopilotEn.containsKey('ap.r.$code'), isTrue, reason: code);
      expect(autopilotBn.containsKey('ap.r.$code'), isTrue, reason: code);
    }
    expect(autopilotReasonKey('SOMETHING_NEW'), 'ap.r.unknown');
    expect(autopilotReasonKey(null), 'ap.r.unknown');
    expect(autopilotCourierProblems, hasLength(5));
    for (final code in autopilotCourierProblems) {
      expect(autopilotCourierProblemKey(code), 'ap.c.$code');
      expect(autopilotBn.containsKey('ap.c.$code'), isTrue, reason: code);
    }
    expect(autopilotCourierProblemKey('NEW'), 'ap.r.COURIER_NOT_READY');
    for (final key in ['CONVERTED', 'DRAFT_NOT_CONVERTED']) {
      expect(automationEn['auto.reason.$key'], isNotEmpty, reason: key);
      expect(automationBn['auto.reason.$key'], isNotEmpty, reason: key);
    }
  });

  test(
    'rates round down, names read well, roles and turn-on follow the server',
    () {
      expect(deliveredPercent(2, 3), 66);
      expect(deliveredPercent(3, 0), 0);
      expect(courierDisplayName('redx'), 'RedX');
      expect(courierDisplayName('carrybee'), 'Carrybee');
      expect(autopilotCan('OWNER', 'settings.manage'), isTrue);
      expect(autopilotCan('MANAGER', 'settings.manage'), isFalse);
      expect(autopilotCan('ORDER_OPERATOR', 'order.write'), isTrue);
      expect(autopilotCan('VIEWER', 'order.write'), isFalse);
      expect(
        canTurnOn(_status(), consentTicked: true),
        isFalse,
      ); // courier line
      expect(
        canTurnOn(_status(courierOk: true), consentTicked: false),
        isFalse,
      );
      expect(canTurnOn(_status(courierOk: true), consentTicked: true), isTrue);
      expect(
        canTurnOn(_status(on: true, courierOk: true), consentTicked: true),
        isFalse,
      );
    },
  );

  for (final language in ['en', 'bn']) {
    final strings = language == 'bn' ? autopilotBn : autopilotEn;

    testWidgets('Settings checklist at 360x800 in $language', (tester) async {
      final fake = buildFakeApi();
      fake.adapter.onJson('GET', '/autopilot', _status());
      await _pumpAt360(tester, _app(fake, language, const AutopilotScreen()));

      expect(tester.takeException(), isNull);
      expect(find.text(strings['ap.off']!), findsOneWidget);
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('ap.comparison')),
        200,
      );
      expect(find.text(strings['ap.c.NOT_LIVE_VERIFIED']!), findsOneWidget);
      expect(find.byKey(const ValueKey('ap.comparison')), findsOneWidget);
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('ap.turnOn')),
        200,
      );
      final button = tester.widget<FilledButton>(
        find.byKey(const ValueKey('ap.turnOn')),
      );
      expect(button.onPressed, isNull); // the courier line fails
      expect(tester.takeException(), isNull);
    });

    testWidgets('Home card at 360x800 in $language, Pause', (tester) async {
      final fake = buildFakeApi();
      fake.adapter.onJson(
        'GET',
        '/autopilot',
        _status(on: true, courierOk: true, consent: true),
      );
      fake.adapter.onJson('GET', '/autopilot/today', _today());
      fake.adapter.onJson('POST', '/autopilot/pause', {'paused_until': 'x'});
      await _pumpAt360(
        tester,
        _app(
          fake,
          language,
          const Scaffold(
            body: SingleChildScrollView(child: AutopilotHomeCard()),
          ),
        ),
      );

      expect(tester.takeException(), isNull);
      expect(find.text(strings['ap.home']!), findsOneWidget);
      expect(find.text('3'), findsOneWidget);
      expect(
        find.text(strings['ap.madeOrder']!.replaceAll('{number}', 'ORD-1042')),
        findsOneWidget,
      );
      expect(
        find.text(
          strings['ap.heldFor']!.replaceAll(
            '{reason}',
            strings['ap.r.LOCATION_UNRESOLVED']!,
          ),
        ),
        findsOneWidget,
      );
      await tester.tap(find.byKey(const ValueKey('ap.pause')));
      await tester.pumpAndSettle();
      expect(fake.adapter.to('POST', '/autopilot/pause'), hasLength(1));
    });
  }

  testWidgets('the consent tick turns Autopilot on with one PUT', (
    tester,
  ) async {
    final fake = buildFakeApi();
    fake.adapter.onJson('GET', '/autopilot', _status(courierOk: true));
    fake.adapter.onJson(
      'PUT',
      '/autopilot',
      _status(on: true, courierOk: true),
    );
    await _pumpAt360(tester, _app(fake, 'en', const AutopilotScreen()));

    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('ap.consent')),
      200,
    );
    await tester.tap(find.byKey(const ValueKey('ap.consent')));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('ap.turnOn')),
      200,
    );
    await tester.tap(find.byKey(const ValueKey('ap.turnOn')));
    await tester.pumpAndSettle();

    final put = fake.adapter.to('PUT', '/autopilot');
    expect(put, hasLength(1));
    expect(put.single.body, {
      'on': true,
      'consent': true,
      'manual_queue': false,
    });
  });

  testWidgets('a viewer reads the checklist but cannot change it', (
    tester,
  ) async {
    final fake = buildFakeApi();
    fake.adapter.onJson('GET', '/autopilot', _status());
    await _pumpAt360(
      tester,
      _app(fake, 'en', const AutopilotScreen(), role: 'VIEWER'),
    );
    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('ap.readOnly')),
      200,
    );
    expect(find.byKey(const ValueKey('ap.turnOn')), findsNothing);
  });

  testWidgets('feature off: Settings says so and Home shows no card', (
    tester,
  ) async {
    final fake = buildFakeApi();
    fake.adapter.on('GET', '/autopilot', (_) => _disabled);
    await _pumpAt360(tester, _app(fake, 'en', const AutopilotScreen()));
    expect(find.text(autopilotEn['ap.notEnabled']!), findsOneWidget);

    await _pumpAt360(
      tester,
      _app(fake, 'en', const Scaffold(body: AutopilotHomeCard())),
    );
    expect(find.byKey(const ValueKey('ap.homeCard')), findsNothing);
  });

  testWidgets('paused: the owner sees Resume, an operator sees neither', (
    tester,
  ) async {
    final fake = buildFakeApi();
    fake.adapter.onJson(
      'GET',
      '/autopilot',
      _status(
        on: true,
        courierOk: true,
        consent: true,
        pausedUntil: '2026-11-20T06:00:00+00:00',
      ),
    );
    fake.adapter.onJson('GET', '/autopilot/today', _today());
    fake.adapter.onJson('POST', '/autopilot/resume', {'paused_until': null});
    await _pumpAt360(
      tester,
      _app(
        fake,
        'en',
        const Scaffold(body: SingleChildScrollView(child: AutopilotHomeCard())),
      ),
    );
    expect(find.byKey(const ValueKey('ap.pausedNote')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('ap.resume')));
    await tester.pumpAndSettle();
    expect(fake.adapter.to('POST', '/autopilot/resume'), hasLength(1));

    await _pumpAt360(
      tester,
      _app(
        fake,
        'en',
        const Scaffold(body: SingleChildScrollView(child: AutopilotHomeCard())),
        role: 'ORDER_OPERATOR',
      ),
    );
    expect(find.byKey(const ValueKey('ap.resume')), findsNothing);
    expect(find.byKey(const ValueKey('ap.pause')), findsNothing);
  });
}
