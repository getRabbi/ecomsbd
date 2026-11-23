import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/features/risk/external_risk_card.dart';
import 'package:ecomsbd/l10n/app_locale.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:ecomsbd/l10n/risk_provider_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/fake_api.dart';
import '../helpers.dart';

const _history = <String, dynamic>{
  'provider_counts': [
    {'courier': 'steadfast', 'delivered': 15, 'cancelled': 2, 'total': 17},
    {'courier': 'pathao', 'delivered': 8, 'cancelled': 1, 'total': 9},
  ],
  'aggregate': {'delivered': 23, 'cancelled': 3, 'total': 26},
  'as_of': null,
  'state': 'OK',
  'success_bps': 8846,
};

Map<String, dynamic> _profile(Map<String, dynamic>? history) => {
  'customer_id': 'c1',
  'own_shop': {'state': 'LOW'},
  'external': {
    'status': 'AVAILABLE',
    'providers': [
      {
        'provider_id': 'licensed',
        'name': 'Licensed Co',
        'enabled': true,
        'health': 'HEALTHY',
        'result': {
          'status': 'FOUND',
          'freshness': 'FRESH',
          'facts': const <Object>[],
          'checked_at': '2026-11-10T10:00:00Z',
          'courier_history': history,
        },
        'last_error': null,
      },
    ],
  },
  'network': {'period': null, 'cells': const <Object>[]},
  'can_lookup': false,
};

Widget _app(ApiClient client, Widget child, String language) {
  activeAppLocale = language == 'bn' ? AppLocale.bn : AppLocale.en;
  return ProviderScope(
    overrides: [apiClientProvider.overrideWithValue(client)],
    child: MaterialApp(
      locale: Locale(language),
      supportedLocales: const [Locale('bn'), Locale('en')],
      localizationsDelegates: const [
        AppStrings.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: Scaffold(body: SingleChildScrollView(child: child)),
    ),
  );
}

void main() {
  tearDown(() => activeAppLocale = AppLocale.en);

  test('the chip reads courier by courier, and kom data under the floor', () {
    expect(
      courierHistoryText(_history, komData: 'kom', allCouriers: 'All'),
      'Steadfast 15/17 · Pathao 8/9',
    );
    expect(
      courierHistoryText(
        {..._history, 'state': 'INSUFFICIENT_DATA', 'success_bps': null},
        komData: 'kom',
        allCouriers: 'All',
      ),
      'kom',
    );
    expect(
      courierHistoryText(
        {..._history, 'provider_counts': const <Object>[]},
        komData: 'kom',
        allCouriers: 'All',
      ),
      'All 23/26',
    );
    expect(
      courierHistoryText(null, komData: 'kom', allCouriers: 'All'),
      isNull,
    );
    expect(courierLabel('redx'), 'RedX');
  });

  test('the new strings have Bangla', () {
    for (final key in ['xr.history', 'xr.komData', 'xr.allCouriers']) {
      expect(riskProviderBn[key], isNotEmpty, reason: key);
      expect(riskProviderEn[key], isNotEmpty, reason: key);
    }
  });

  for (final language in ['en', 'bn']) {
    testWidgets('the order chip fits a 360x800 phone in $language', (
      tester,
    ) async {
      tester.view.physicalSize = referencePhone * 3;
      tester.view.devicePixelRatio = 3;
      addTearDown(tester.view.reset);
      final fake = buildFakeApi();
      fake.adapter.onJson(
        'GET',
        '/customers/c1/risk-profile',
        _profile(_history),
      );
      await tester.pumpWidget(
        _app(fake.client, const CourierHistoryChip(customerId: 'c1'), language),
      );
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      final strings = language == 'bn' ? riskProviderBn : riskProviderEn;
      expect(
        find.text('${strings['xr.history']}: Steadfast 15/17 · Pathao 8/9'),
        findsOneWidget,
      );
    });
  }

  testWidgets('thin history shows kom data on the customer card', (
    tester,
  ) async {
    final fake = buildFakeApi();
    fake.adapter.onJson(
      'GET',
      '/customers/c1/risk-profile',
      _profile({
        ..._history,
        'provider_counts': [
          {'courier': 'redx', 'delivered': 1, 'cancelled': 1, 'total': 2},
        ],
        'aggregate': {'delivered': 1, 'cancelled': 1, 'total': 2},
        'state': 'INSUFFICIENT_DATA',
        'success_bps': null,
      }),
    );
    await tester.pumpWidget(
      _app(fake.client, const ExternalRiskCard(customerId: 'c1'), 'en'),
    );
    await tester.pumpAndSettle();
    expect(
      find.text(
        '${riskProviderEn['xr.history']}: ${riskProviderEn['xr.komData']}',
      ),
      findsOneWidget,
    );
  });

  testWidgets('no stored history renders nothing on the order card', (
    tester,
  ) async {
    final fake = buildFakeApi();
    fake.adapter.onJson('GET', '/customers/c1/risk-profile', _profile(null));
    await tester.pumpWidget(
      _app(fake.client, const CourierHistoryChip(customerId: 'c1'), 'en'),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('xr.historyChip')), findsNothing);
  });
}
