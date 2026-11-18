import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/features/settings/delivery_rules_screen.dart';
import 'package:ecomsbd/l10n/app_locale.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:ecomsbd/l10n/delivery_rules_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/fake_api.dart';
import '../helpers.dart';

typedef _Fake = ({ApiClient client, FakeApiAdapter adapter});

/// The form's own list, not the scrollables inside each text field.
final _list = find.byType(Scrollable).first;

Map<String, dynamic> _rules({bool configured = true}) => {
  'configured': configured,
  'inside_dhaka_charge_paisa': 6000,
  'dhaka_suburb_charge_paisa': 9000,
  'outside_dhaka_charge_paisa': 12050,
  'default_courier': 'steadfast',
  'max_auto_cod_paisa': 500000,
  'risk_low_bps': 5000,
  'trusted_network_bps': 8500,
  'trusted_repeat_min': 2,
  'quiet_start': '21:00',
  'quiet_end': '09:00',
  'autopilot_daily_cap': 30,
  'call_everyone': false,
  'call_nobody': false,
};

Widget _app(_Fake fake, String language) {
  activeAppLocale = language == 'bn' ? AppLocale.bn : AppLocale.en;
  return ProviderScope(
    overrides: [apiClientProvider.overrideWithValue(fake.client)],
    child: MaterialApp(
      locale: Locale(language),
      supportedLocales: const [Locale('en'), Locale('bn')],
      localizationsDelegates: const [
        AppStrings.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: const DeliveryRulesScreen(),
    ),
  );
}

void main() {
  tearDown(() => activeAppLocale = AppLocale.en);

  test('every English delivery-rules string has Bangla', () {
    expect(deliveryRulesBn.keys.toSet(), deliveryRulesEn.keys.toSet());
    expect(deliveryRulesBn.values.where((v) => v.trim().isEmpty), isEmpty);
  });

  test('taka text becomes paisa without floating point', () {
    expect(takaToPaisa('60'), 6000);
    expect(takaToPaisa('১২০.৫'), 12050);
    expect(takaToPaisa('12.35'), 1235);
    expect(takaToPaisa('1,000'), 100000);
    expect(takaToPaisa('-5'), isNull);
    expect(takaToPaisa('5.555'), isNull);
    expect(paisaToTaka(12050), '120.50');
    expect(paisaToTaka(6000), '60');
  });

  for (final language in ['en', 'bn']) {
    testWidgets('loads, edits and saves the rules in $language', (
      tester,
    ) async {
      final fake = buildFakeApi();
      fake.adapter.onJson('GET', '/shop/delivery-rules', _rules());
      fake.adapter.on(
        'PATCH',
        '/shop/delivery-rules',
        (request) => FakeReply({..._rules(), ...request.jsonBody}),
      );
      await tester.pumpWidget(_app(fake, language));
      await tester.pumpAndSettle();

      expect(find.text('120.50'), findsOneWidget);
      await tester.enterText(
        find.byKey(const ValueKey('dr.inside_dhaka_charge_paisa')),
        '70',
      );
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('dr.call_nobody')),
        200,
        scrollable: _list,
      );
      await tester.tap(find.byKey(const ValueKey('dr.call_everyone')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('dr.call_nobody')));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('dr.save')),
        200,
        scrollable: _list,
      );
      await tester.tap(find.byKey(const ValueKey('dr.save')));
      await tester.pumpAndSettle();

      final sent = fake.adapter.to('PATCH', '/shop/delivery-rules');
      expect(sent, hasLength(1));
      final body = sent.single.jsonBody;
      expect(body['inside_dhaka_charge_paisa'], 7000);
      expect(body['outside_dhaka_charge_paisa'], 12050);
      expect(body['risk_low_bps'], 5000);
      expect(body['quiet_start'], '21:00');
      expect(body['default_courier'], 'steadfast');
      // The two call switches are never both on.
      expect(body['call_nobody'], true);
      expect(body['call_everyone'], false);
      final strings = language == 'bn' ? deliveryRulesBn : deliveryRulesEn;
      expect(find.text(strings['dr.saved']!), findsOneWidget);
    });

    testWidgets('a shop without the flag is told so in $language', (
      tester,
    ) async {
      final fake = buildFakeApi();
      fake.adapter.on(
        'GET',
        '/shop/delivery-rules',
        (_) => const FakeReply({
          'code': 'FEATURE_DISABLED',
          'message_en': 'Delivery rules are not enabled',
          'message_bn': 'এই ফিচারটি এখন বন্ধ আছে।',
          'retryable': false,
        }, statusCode: 403),
      );
      await tester.pumpWidget(_app(fake, language));
      await tester.pumpAndSettle();
      final strings = language == 'bn' ? deliveryRulesBn : deliveryRulesEn;
      expect(find.text(strings['dr.notEnabled']!), findsOneWidget);
    });

    testWidgets('fits a 360x800 phone in $language', (tester) async {
      final fake = buildFakeApi();
      fake.adapter.onJson(
        'GET',
        '/shop/delivery-rules',
        _rules(configured: false),
      );
      tester.view.physicalSize = referencePhone * 3;
      tester.view.devicePixelRatio = 3;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(_app(fake, language));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      final strings = language == 'bn' ? deliveryRulesBn : deliveryRulesEn;
      expect(find.text(strings['dr.defaultsNote']!), findsOneWidget);
    });
  }

  testWidgets('an invalid time is refused before anything is sent', (
    tester,
  ) async {
    final fake = buildFakeApi();
    fake.adapter.onJson('GET', '/shop/delivery-rules', _rules());
    await tester.pumpWidget(_app(fake, 'en'));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('dr.quiet_start')),
      200,
      scrollable: _list,
    );
    await tester.enterText(
      find.byKey(const ValueKey('dr.quiet_start')),
      '25:00',
    );
    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('dr.save')),
      200,
      scrollable: _list,
    );
    await tester.tap(find.byKey(const ValueKey('dr.save')));
    await tester.pumpAndSettle();
    expect(fake.adapter.to('PATCH', '/shop/delivery-rules'), isEmpty);
  });
}
