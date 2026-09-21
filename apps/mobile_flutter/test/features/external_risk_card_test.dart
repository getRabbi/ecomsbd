import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/features/risk/external_risk_card.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import '../data/fake_api.dart';

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
}
