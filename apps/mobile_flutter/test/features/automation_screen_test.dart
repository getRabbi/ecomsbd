import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/features/settings/automation_screen.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:ecomsbd/l10n/automation_strings.dart';
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
    home: const AutomationScreen(),
  ),
);

_Fake _fake({
  required bool manage,
  required bool Function() enabled,
  List<Map<String, dynamic>> failures = const [],
  List<Map<String, dynamic>> recipes = const [],
}) {
  final fake = buildFakeApi();
  fake.adapter.onJson('GET', '/automation/catalog', {
    'can_manage': manage,
    'can_operate': manage,
  });
  fake.adapter.on(
    'GET',
    '/automation/workflows',
    (_) => FakeReply({
      'items': [
        {
          'id': 'wf1',
          'name': 'Confirm website orders',
          'trigger': 'order.external_received',
          'enabled': enabled(),
          'published_version': 2,
          'runs_7d': {'SUCCEEDED': 4, 'FAILED': 1},
        },
      ],
    }),
  );
  fake.adapter.onJson('GET', '/automation/executions', {'items': failures});
  fake.adapter.onJson('GET', '/automation/recipes', {'items': recipes});
  fake.adapter.onJson('GET', '/automation/metrics', {
    'executions': 5,
    'failed': 1,
    'waiting': 0,
  });
  return fake;
}

void main() {
  test('every English automation string has Bangla', () {
    expect(automationBn.keys.toSet(), automationEn.keys.toSet());
    expect(automationBn.values.where((v) => v.trim().isEmpty), isEmpty);
  });

  for (final language in ['en', 'bn']) {
    testWidgets('workflow list shows status and toggles in $language', (
      tester,
    ) async {
      var enabled = true;
      final fake = _fake(manage: true, enabled: () => enabled);
      fake.adapter.on('PATCH', '/automation/workflows/wf1', (request) {
        enabled = request.jsonBody['enabled'] as bool;
        return FakeReply({'id': 'wf1', 'enabled': enabled});
      });
      await tester.pumpWidget(_app(fake, language));
      await tester.pumpAndSettle();
      expect(find.text('Confirm website orders'), findsOneWidget);
      expect(
        find.textContaining(language == 'en' ? 'Version 2' : 'সংস্করণ 2'),
        findsOneWidget,
      );
      expect(
        find.textContaining(
          language == 'en'
              ? 'When a website order arrives'
              : 'ওয়েবসাইটের অর্ডার এলে',
        ),
        findsOneWidget,
      );
      tester.widget<SwitchListTile>(find.byType(SwitchListTile)).onChanged!(
        false,
      );
      await tester.pumpAndSettle();
      expect(
        tester.widget<SwitchListTile>(find.byType(SwitchListTile)).value,
        false,
      );
      expect(fake.adapter.to('PATCH', '/automation/workflows/wf1').length, 1);
    });
  }

  testWidgets('a viewer cannot switch workflows', (tester) async {
    final fake = _fake(manage: false, enabled: () => true);
    await tester.pumpWidget(_app(fake, 'en'));
    await tester.pumpAndSettle();
    expect(
      tester.widget<SwitchListTile>(find.byType(SwitchListTile)).onChanged,
      isNull,
    );
    expect(find.textContaining('You can watch workflows'), findsOneWidget);
  });

  testWidgets('a failed run shows its reason and can be retried', (
    tester,
  ) async {
    final fake = _fake(
      manage: true,
      enabled: () => true,
      failures: [
        {
          'id': 'run1',
          'workflow_name': 'Book and track',
          'status': 'FAILED',
          'last_error': 'TRANSIENT:TimeoutError',
          'retryable': true,
        },
      ],
    );
    fake.adapter.onJson('GET', '/automation/executions/run1', {
      'id': 'run1',
      'workflow_name': 'Book and track',
      'status': 'FAILED',
      'last_error': 'TRANSIENT:TimeoutError',
      'retryable': true,
      'steps': [
        {'step_id': 'book', 'status': 'FAILED', 'outcome': null},
      ],
    });
    fake.adapter.onJson('POST', '/automation/executions/run1/retry', {
      'id': 'run1',
      'status': 'QUEUED',
    });
    await tester.pumpWidget(_app(fake, 'bn'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('ব্যর্থ'));
    await tester.pumpAndSettle();
    expect(find.textContaining('সাময়িক সমস্যা'), findsOneWidget);
    await tester.tap(find.text('Book and track'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('ব্যর্থ ধাপ থেকে আবার চেষ্টা'));
    await tester.pumpAndSettle();
    expect(
      fake.adapter.to('POST', '/automation/executions/run1/retry').length,
      1,
    );
  });

  testWidgets('a simple recipe turns on; one needing setup points to web', (
    tester,
  ) async {
    final fake = _fake(
      manage: true,
      enabled: () => true,
      recipes: [
        {
          'key': 'low_stock_notify',
          'name': {
            'en': 'Low stock → notify owner',
            'bn': 'স্টক কম → মালিককে জানান',
          },
          'trigger': 'inventory.low',
          'needs': <String>[],
          'installed': false,
        },
        {
          'key': 'confirmed_book_track',
          'name': {
            'en': 'Confirmed → book courier',
            'bn': 'নিশ্চিত → কুরিয়ার বুক',
          },
          'trigger': 'order.confirmed',
          'needs': ['provider', 'channel'],
          'installed': false,
        },
      ],
    );
    fake.adapter.onJson(
      'POST',
      '/automation/recipes/low_stock_notify/install',
      {'id': 'wf2', 'blocker': null},
    );
    await tester.pumpWidget(_app(fake, 'en'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Recipes'));
    await tester.pumpAndSettle();
    expect(find.textContaining('set it up on the web'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Turn on'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Turn on'));
    await tester.pumpAndSettle();
    final installs = fake.adapter.to(
      'POST',
      '/automation/recipes/low_stock_notify/install',
    );
    expect(installs.length, 1);
    expect(installs.single.jsonBody['locale'], 'en');
  });
}
