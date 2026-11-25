import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/features/settings/sms_settings_screen.dart';
import 'package:ecomsbd/l10n/app_locale.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:ecomsbd/l10n/sms_settings_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/fake_api.dart';

typedef _Fake = ({ApiClient client, FakeApiAdapter adapter});

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
      home: const SmsSettingsScreen(),
    ),
  );
}

Map<String, dynamic> _channels({
  bool available = true,
  bool enabled = false,
}) => {
  'items': [
    {'kind': 'EMAIL', 'available': true, 'enabled': true, 'shop_blocker': null},
    {
      'kind': 'SMS',
      'available': available,
      'enabled': enabled,
      'shop_blocker': null,
    },
  ],
};

void main() {
  tearDown(() => activeAppLocale = AppLocale.en);

  test('every English SMS settings string has Bangla', () {
    expect(smsSettingsBn.keys.toSet(), smsSettingsEn.keys.toSet());
    expect(smsSettingsBn.values.where((v) => v.trim().isEmpty), isEmpty);
    expect(smsSettingsEn['ss.cost'], 'Costs from your SMS balance.');
  });

  test('the order update switch reads the SMS channel row', () {
    expect(orderUpdateSwitch(const []), (available: false, enabled: false));
    expect(orderUpdateSwitch(_channels(enabled: true)['items'] as List), (
      available: true,
      enabled: true,
    ));
    expect(
      orderUpdateSwitch([
        {
          'kind': 'SMS',
          'available': true,
          'enabled': false,
          'shop_blocker': 'X',
        },
      ]),
      (available: false, enabled: false),
    );
  });

  for (final language in ['en', 'bn']) {
    testWidgets('both switches start off and the confirm link turns on in '
        '$language', (tester) async {
      final fake = buildFakeApi();
      var link = false;
      fake.adapter.on(
        'GET',
        '/shop/sms-settings',
        (_) => FakeReply({
          'confirmation_link': {'available': true, 'enabled': link},
        }),
      );
      fake.adapter.onJson('GET', '/messaging/channels', _channels());
      fake.adapter.on('PATCH', '/shop/sms-settings', (request) {
        link = request.jsonBody['confirmation_link_enabled'] == true;
        return FakeReply({
          'confirmation_link': {'available': true, 'enabled': link},
        });
      });
      await tester.pumpWidget(_app(fake, language));
      await tester.pumpAndSettle();

      final strings = language == 'bn' ? smsSettingsBn : smsSettingsEn;
      expect(find.text(strings['ss.linkSwitch']!), findsOneWidget);
      expect(find.text(strings['ss.cost']!), findsNWidgets(2));
      final switches = find.byType(Switch);
      expect(tester.widgetList<Switch>(switches).map((s) => s.value), [
        false,
        false,
      ]);

      await tester.tap(
        find.descendant(
          of: find.byKey(const ValueKey('sms.confirmLink')),
          matching: find.byType(Switch),
        ),
      );
      await tester.pumpAndSettle();
      expect(
        fake.adapter.requests.where((r) => r.method == 'PATCH').single.jsonBody,
        {'confirmation_link_enabled': true},
      );
      expect(tester.widgetList<Switch>(switches).first.value, isTrue);
    });
  }

  testWidgets('a switch the platform does not offer cannot be turned on', (
    tester,
  ) async {
    final fake = buildFakeApi();
    fake.adapter.onJson('GET', '/shop/sms-settings', {
      'confirmation_link': {'available': false, 'enabled': false},
    });
    fake.adapter.onJson(
      'GET',
      '/messaging/channels',
      _channels(available: false),
    );
    await tester.pumpWidget(_app(fake, 'en'));
    await tester.pumpAndSettle();

    expect(find.text(smsSettingsEn['ss.unavailable']!), findsNWidgets(2));
    for (final widget in tester.widgetList<Switch>(find.byType(Switch))) {
      expect(widget.onChanged, isNull);
    }
  });
}
