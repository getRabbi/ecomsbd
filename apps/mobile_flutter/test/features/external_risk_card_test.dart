import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/features/risk/external_risk_card.dart';
import 'package:ecomsbd/l10n/app_locale.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import '../data/fake_api.dart';

Widget _app(
  ApiClient client,
  Widget child, {
  Locale locale = const Locale('en'),
}) {
  activeAppLocale = locale.languageCode == 'bn' ? AppLocale.bn : AppLocale.en;
  return ProviderScope(
    overrides: [apiClientProvider.overrideWithValue(client)],
    child: MaterialApp(
      locale: locale,
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

Map<String, dynamic> _profile({
  required String status,
  List<Map<String, dynamic>> providers = const [],
  bool canLookup = true,
  String? period,
  List<Map<String, dynamic>> cells = const [],
}) => {
  'customer_id': 'c1',
  'own_shop': {'state': 'LOW'},
  'external': {'status': status, 'providers': providers},
  'network': {'period': period, 'cells': cells},
  'can_lookup': canLookup,
};

Map<String, dynamic> _section({
  Map<String, dynamic>? result,
  Map<String, dynamic>? lastError,
}) => {
  'provider_id': 'licensed',
  'name': 'Licensed Co',
  'enabled': true,
  'health': 'HEALTHY',
  'result': result,
  'last_error': lastError,
};

void main() {
  testWidgets('external facts remain a separate gated card', (tester) async {
    final fake = buildFakeApi();
    fake.adapter.onJson('GET', '/external-risk/capability', {
      'status': 'GATED',
      'message_en': 'Licensed provider required',
      'message_bn': 'লাইসেন্সপ্রাপ্ত প্রোভাইডার প্রয়োজন',
    });
    await tester.pumpWidget(
      ProviderScope(
        overrides: [apiClientProvider.overrideWithValue(fake.client)],
        child: const MaterialApp(home: Scaffold(body: ExternalRiskCard())),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('বাইরের প্রোভাইডারের তথ্য'), findsOneWidget);
    expect(find.text('লাইসেন্সপ্রাপ্ত প্রোভাইডার প্রয়োজন'), findsOneWidget);
    expect(fake.adapter.requests.length, 1);
    expect(fake.adapter.requests.single.path, '/external-risk/capability');
  });

  testWidgets('provider facts show freshness, network context, never a score', (
    tester,
  ) async {
    final fake = buildFakeApi();
    fake.adapter.onJson(
      'GET',
      '/customers/c1/risk-profile',
      _profile(
        status: 'AVAILABLE',
        providers: [
          _section(
            result: {
              'status': 'FOUND',
              'freshness': 'FRESH',
              'facts': [
                {'code': 'DELIVERED_PARCELS', 'value': 7, 'period_days': 365},
              ],
              'checked_at': '2026-09-20T10:00:00Z',
              'provider_observed_at': '2026-09-19T10:00:00Z',
            },
          ),
        ],
        period: '2026-08',
        cells: [
          {
            'metric': 'RTO_RATE',
            'dimension': 'ALL',
            'status': 'DATA_NOT_SUFFICIENT',
            'value': null,
          },
        ],
      ),
    );
    await tester.pumpWidget(
      _app(fake.client, const ExternalRiskCard(customerId: 'c1')),
    );
    await tester.pumpAndSettle();
    expect(
      find.text('Licensed Co has records for this number'),
      findsOneWidget,
    );
    expect(find.text('Up to date'), findsOneWidget);
    expect(find.text('Delivered parcels: 7'), findsOneWidget);
    expect(
      find.text('Network return (RTO) rate: Data not sufficient'),
      findsOneWidget,
    );
    expect(find.text('Refresh'), findsOneWidget);
    expect(find.textContaining('score'), findsNothing);
  });

  testWidgets(
    'stale and unavailable in Bangla; no refresh without permission',
    (tester) async {
      final fake = buildFakeApi();
      fake.adapter.onJson(
        'GET',
        '/customers/c1/risk-profile',
        _profile(
          status: 'AVAILABLE',
          canLookup: false,
          providers: [
            _section(
              result: {
                'status': 'NOT_FOUND',
                'freshness': 'STALE',
                'facts': <Map<String, dynamic>>[],
                'checked_at': '2026-09-01T10:00:00Z',
              },
              lastError: {
                'code': 'PROVIDER_UNAVAILABLE',
                'at': '2026-09-20T10:00:00Z',
              },
            ),
          ],
        ),
      );
      await tester.pumpWidget(
        _app(
          fake.client,
          const ExternalRiskCard(customerId: 'c1'),
          locale: const Locale('bn'),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('পুরোনো'), findsOneWidget);
      expect(
        find.text('শেষ চেষ্টা ব্যর্থ: প্রোভাইডার এখন পাওয়া যাচ্ছে না'),
        findsOneWidget,
      );
      expect(find.text('এখনো কোনো বেনামি হিসাব প্রকাশ হয়নি।'), findsOneWidget);
      expect(find.text('আবার দেখুন'), findsNothing);
    },
  );

  testWidgets('a gated provider offers no lookup', (tester) async {
    final fake = buildFakeApi();
    fake.adapter.onJson(
      'GET',
      '/customers/c1/risk-profile',
      _profile(status: 'GATED'),
    );
    await tester.pumpWidget(
      _app(fake.client, const ExternalRiskCard(customerId: 'c1')),
    );
    await tester.pumpAndSettle();
    expect(find.textContaining('No licensed risk provider'), findsOneWidget);
    expect(find.text('Check with provider'), findsNothing);
  });
}
