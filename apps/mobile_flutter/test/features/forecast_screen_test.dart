import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/features/forecasting/forecast_screen.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:ecomsbd/l10n/forecasting_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/fake_api.dart';

typedef _Fake = ({ApiClient client, FakeApiAdapter adapter});

Widget _app(_Fake fake, String language) => ProviderScope(
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
    home: const ForecastScreen(),
  ),
);

Map<String, dynamic> _item() => {
  'product_id': 'p1',
  'variant_id': null,
  'name': 'Borka',
  'variant_name': null,
  'confidence': 'HIGH',
  'rate_per_day': 2.0,
  'on_hand': 10,
  'incoming': 0,
  'lead_time_days': 7,
  'days_of_cover': 5,
  'suggested_quantity': 32,
  'at_risk': true,
};

void _demand(_Fake fake, {required bool canDraft}) {
  fake.adapter.onJson('GET', '/forecasting/demand', {
    'items': [_item()],
    'can_draft': canDraft,
  });
}

void main() {
  for (final language in ['en', 'bn']) {
    testWidgets('shows what is likely to run out in $language', (tester) async {
      final fake = buildFakeApi();
      _demand(fake, canDraft: false);
      fake.adapter.on(
        'GET',
        '/forecasting/cash',
        (_) => const FakeReply({
          'code': 'FORBIDDEN',
          'message_en': 'No',
          'message_bn': 'না',
        }, statusCode: 403),
      );
      await tester.pumpWidget(_app(fake, language));
      await tester.pumpAndSettle();
      expect(find.text('Borka'), findsOneWidget);
      expect(
        find.text(
          language == 'en'
              ? 'Runs out in about 5 days'
              : 'প্রায় 5 দিনে ফুরাবে',
        ),
        findsOneWidget,
      );
      // A viewer sees the suggestion but cannot draft.
      expect(find.byType(OutlinedButton), findsNothing);
      await tester.tap(find.byType(Tab).last);
      await tester.pumpAndSettle();
      expect(
        find.text(
          language == 'en'
              ? 'The cash outlook needs money access.'
              : 'টাকার অনুমান দেখতে টাকার হিসাবের অনুমতি লাগবে।',
        ),
        findsOneWidget,
      );
    });
  }

  for (final language in ['en', 'bn']) {
    testWidgets('a shop without enough history is not told nothing runs out '
        'in $language', (tester) async {
      final fake = buildFakeApi();
      fake.adapter.onJson('GET', '/forecasting/demand', {
        'items': <Map<String, dynamic>>[],
        'counts': {'all': 2, 'at_risk': 0, 'insufficient': 2},
        'method': {'min_in_stock_days': 14, 'min_units': 5},
        'can_draft': false,
      });
      fake.adapter.onJson('GET', '/forecasting/cash', {
        'inflow': {'next_7_days': 0},
        'outflow': {'next_7_days': 0, 'overdue': 0},
        'net_7_days_paisa': 0,
        'net_14_days_paisa': 0,
        'committed_on_open_orders_paisa': 0,
      });
      await tester.pumpWidget(_app(fake, language));
      await tester.pumpAndSettle();
      final strings = language == 'en' ? forecastingEn : forecastingBn;
      expect(find.text(strings['fc.empty']!), findsNothing);
      expect(
        find.textContaining(language == 'en' ? '14 days' : '14 দিনের'),
        findsOneWidget,
      );
    });
  }

  testWidgets('items without history are counted beside a real forecast', (
    tester,
  ) async {
    final fake = buildFakeApi();
    fake.adapter.onJson('GET', '/forecasting/demand', {
      'items': [_item()],
      'counts': {'all': 4, 'at_risk': 1, 'insufficient': 3},
      'method': {'min_in_stock_days': 14, 'min_units': 5},
      'can_draft': false,
    });
    fake.adapter.onJson('GET', '/forecasting/cash', {
      'inflow': {'next_7_days': 0},
      'outflow': {'next_7_days': 0, 'overdue': 0},
      'net_7_days_paisa': 0,
      'net_14_days_paisa': 0,
      'committed_on_open_orders_paisa': 0,
    });
    await tester.pumpWidget(_app(fake, 'en'));
    await tester.pumpAndSettle();
    expect(find.text('Borka'), findsOneWidget);
    expect(
      find.text('3 items have too little sales history to forecast yet.'),
      findsOneWidget,
    );
  });

  testWidgets('a manager drafts the suggested reorder', (tester) async {
    final fake = buildFakeApi();
    _demand(fake, canDraft: true);
    fake.adapter.onJson('GET', '/forecasting/cash', {
      'inflow': {'next_7_days': 500000},
      'outflow': {'next_7_days': 40000, 'overdue': 0},
      'net_7_days_paisa': 460000,
      'net_14_days_paisa': 460000,
      'committed_on_open_orders_paisa': 0,
    });
    fake.adapter.onJson('POST', '/forecasting/draft-purchase-order', {
      'purchase_order_id': 'po1',
      'number': 'PO-00007',
      'quantity': 32,
    });
    await tester.pumpWidget(_app(fake, 'en'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Draft PO'));
    await tester.pumpAndSettle();
    final posts = fake.adapter.to('POST', '/forecasting/draft-purchase-order');
    expect(posts.length, 1);
    expect(posts.single.jsonBody['product_id'], 'p1');
    expect(find.textContaining('PO-00007'), findsOneWidget);
  });

  testWidgets('offline, it says so and does not claim nothing will run out, '
      'then fills in once back online', (tester) async {
    final fake = buildFakeApi();
    _demand(fake, canDraft: false);
    fake.adapter.onJson('GET', '/forecasting/cash', {
      'inflow': {'next_7_days': 0},
      'outflow': {'next_7_days': 0, 'overdue': 0},
      'net_7_days_paisa': 0,
      'net_14_days_paisa': 0,
      'committed_on_open_orders_paisa': 0,
    });
    fake.adapter.offline = true;
    await tester.pumpWidget(_app(fake, 'en'));
    await tester.pumpAndSettle();

    expect(find.textContaining('No internet connection'), findsOneWidget);
    expect(find.text('Nothing is likely to run out right now.'), findsNothing);
    expect(find.text('Borka'), findsNothing);

    fake.adapter.offline = false;
    final container = ProviderScope.containerOf(
      tester.element(find.byType(ForecastScreen)),
    );
    final monitor = container.read(networkMonitorProvider.notifier);
    monitor.reportUnreachable();
    await tester.pump();
    monitor.reportReachable();
    await tester.pumpAndSettle();

    expect(find.textContaining('No internet connection'), findsNothing);
    expect(find.text('Borka'), findsOneWidget);
  });
}
