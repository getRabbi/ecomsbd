import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/features/settings/order_sources_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import '../data/fake_api.dart';

void main() {
  testWidgets('source switches reflect server availability', (tester) async {
    final fake = buildFakeApi();
    fake.adapter.onJson('GET', '/order-sources', {
      'capabilities': [],
      'items': [
        {
          'id': 's1',
          'name': 'Store',
          'provider': 'CUSTOM_PUSH',
          'available': true,
          'enabled': true,
        },
        {
          'id': 's2',
          'name': 'Pending',
          'provider': 'SHOPIFY',
          'available': false,
          'enabled': false,
        },
      ],
    });
    fake.adapter.onJson('PATCH', '/order-sources/s1', {});
    await tester.pumpWidget(
      ProviderScope(
        overrides: [apiClientProvider.overrideWithValue(fake.client)],
        child: const MaterialApp(home: OrderSourcesScreen()),
      ),
    );
    await tester.pumpAndSettle();
    final switches = tester
        .widgetList<SwitchListTile>(find.byType(SwitchListTile))
        .toList();
    expect(switches[0].onChanged, isNotNull);
    expect(switches[1].onChanged, isNull);
    switches[0].onChanged!(false);
    await tester.pumpAndSettle();
    expect(
      fake.adapter.to('PATCH', '/order-sources/s1').single.jsonBody['enabled'],
      false,
    );
  });
}
