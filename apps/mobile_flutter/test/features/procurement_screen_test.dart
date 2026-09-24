import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/features/procurement/procurement_screen.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/fake_api.dart';

typedef _Fake = ({ApiClient client, FakeApiAdapter adapter});

Widget _app(_Fake fake, String language, Widget home) => ProviderScope(
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
    home: home,
  ),
);

Map<String, dynamic> _po(String status, int received) => {
  'id': 'po1',
  'number': 'PO-00001',
  'supplier_name': 'Rahim Traders',
  'status': status,
  'total_paisa': 100000,
  'received_value_paisa': received * 10000,
  'payable': {
    'status': 'UNPAID',
    'balance_paisa': received * 10000,
    'overdue': false,
  },
};

Map<String, dynamic> _detail(String status, int received, bool canReceive) => {
  'purchase_order': _po(status, received),
  'lines': [
    {
      'id': 'l1',
      'description': 'Cotton panjabi',
      'quantity_ordered': 10,
      'quantity_received': received,
      'quantity_rejected': 0,
      'remaining': 10 - received,
    },
  ],
  'can_receive': canReceive,
};

void main() {
  for (final language in ['en', 'bn']) {
    testWidgets('purchasing lists orders in $language', (tester) async {
      final fake = buildFakeApi();
      fake.adapter.onJson('GET', '/procurement/purchase-orders', {
        'items': [_po('ORDERED', 0)],
      });
      fake.adapter.onJson('GET', '/procurement/suppliers', {'items': []});
      fake.adapter.onJson('GET', '/procurement/stock', {'items': []});
      await tester.pumpWidget(_app(fake, language, const ProcurementScreen()));
      await tester.pumpAndSettle();
      expect(find.textContaining('PO-00001'), findsOneWidget);
      expect(
        find.textContaining(
          language == 'en' ? 'Ordered' : 'অর্ডার দেওয়া হয়েছে',
        ),
        findsOneWidget,
      );
    });
  }

  testWidgets('a receiver records a partial receipt once', (tester) async {
    final fake = buildFakeApi();
    fake.adapter.onJson(
      'GET',
      '/procurement/purchase-orders/po1',
      _detail('ORDERED', 0, true),
    );
    fake.adapter.onJson(
      'POST',
      '/procurement/purchase-orders/po1/receive',
      _detail('PARTIALLY_RECEIVED', 1, true),
    );
    await tester.pumpWidget(
      _app(fake, 'en', const PurchaseOrderScreen(orderId: 'po1')),
    );
    await tester.pumpAndSettle();
    expect(find.text('Record receipt'), findsOneWidget);
    await tester.tap(find.byIcon(Icons.add_circle_outline).first);
    await tester.pump();
    await tester.tap(find.text('Record receipt'));
    await tester.pumpAndSettle();
    final posts = fake.adapter.to(
      'POST',
      '/procurement/purchase-orders/po1/receive',
    );
    expect(posts.length, 1);
    final body = posts.single.jsonBody;
    expect(body['idempotency_key'], isA<String>());
    expect((body['lines'] as List).single, {
      'line_id': 'l1',
      'accepted': 1,
      'rejected': 0,
    });
    expect(find.text('Receipt recorded'), findsOneWidget);
    expect(find.textContaining('9 still to come'), findsOneWidget);
  });

  testWidgets('without stock permission the order is read-only', (
    tester,
  ) async {
    final fake = buildFakeApi();
    fake.adapter.onJson(
      'GET',
      '/procurement/purchase-orders/po1',
      _detail('ORDERED', 0, false),
    );
    await tester.pumpWidget(
      _app(fake, 'bn', const PurchaseOrderScreen(orderId: 'po1')),
    );
    await tester.pumpAndSettle();
    expect(find.byType(FilledButton), findsNothing);
    expect(fake.adapter.requests.where((r) => r.method == 'POST'), isEmpty);
  });
}
