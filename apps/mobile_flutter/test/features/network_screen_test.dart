import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/features/settings/network_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import '../data/fake_api.dart';

void main() {
  testWidgets('gated network never renders invented benchmark figures', (
    tester,
  ) async {
    final fake = buildFakeApi();
    fake.adapter.onJson('GET', '/network-intelligence', {
      'status': 'GATED',
      'opted_in': false,
      'facts': {},
      'message_bn': 'বেনামি সমষ্টি',
      'message_en': 'Anonymous aggregates',
    });
    await tester.pumpWidget(
      ProviderScope(
        overrides: [apiClientProvider.overrideWithValue(fake.client)],
        child: const MaterialApp(home: NetworkScreen()),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.text('যথেষ্ট নিরাপদ নমুনা না পাওয়া পর্যন্ত বন্ধ আছে।'),
      findsOneWidget,
    );
    expect(find.textContaining('RTO:'), findsNothing);
    expect(
      tester.widget<SwitchListTile>(find.byType(SwitchListTile)).value,
      false,
    );
  });
}
