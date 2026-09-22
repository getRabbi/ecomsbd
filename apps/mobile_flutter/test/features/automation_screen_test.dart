import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/features/settings/automation_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import '../data/fake_api.dart';

void main() {
  testWidgets('mobile rule switch uses the authorized API', (tester) async {
    final fake = buildFakeApi();
    bool enabled = true;
    fake.adapter.on(
      'GET',
      '/automation/rules',
      (_) => FakeReply({
        'items': [
          {
            'id': 'rule1',
            'name': 'Follow-up',
            'trigger': 'order.created',
            'enabled': enabled,
          },
        ],
      }),
    );
    fake.adapter.on('PATCH', '/automation/rules/rule1', (request) {
      enabled = request.jsonBody['enabled'] as bool;
      return FakeReply({'enabled': enabled});
    });
    await tester.pumpWidget(
      ProviderScope(
        overrides: [apiClientProvider.overrideWithValue(fake.client)],
        child: const MaterialApp(home: AutomationScreen()),
      ),
    );
    await tester.pumpAndSettle();
    tester.widget<SwitchListTile>(find.byType(SwitchListTile)).onChanged!(
      false,
    );
    await tester.pumpAndSettle();
    expect(
      tester.widget<SwitchListTile>(find.byType(SwitchListTile)).value,
      false,
    );
    expect(fake.adapter.to('PATCH', '/automation/rules/rule1').length, 1);
  });
}
