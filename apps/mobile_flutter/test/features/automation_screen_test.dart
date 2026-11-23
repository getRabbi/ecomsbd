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
  String trigger = 'order.external_received',
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
          'trigger': trigger,
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

  testWidgets('an owner is not told they can only watch while it loads', (
    tester,
  ) async {
    final fake = _fake(manage: true, enabled: () => true);
    await tester.pumpWidget(_app(fake, 'en'));
    expect(find.text(automationEn['auto.readOnly']!), findsNothing);
    await tester.pumpAndSettle();
    expect(find.text(automationEn['auto.readOnly']!), findsNothing);
    expect(find.text(automationEn['auto.webOnly']!), findsOneWidget);
  });

  testWidgets('a notice does not outlive the next action', (tester) async {
    var enabled = true;
    final fake = _fake(
      manage: true,
      enabled: () => enabled,
      recipes: [
        {
          'key': 'low_stock_notify',
          'name': {'en': 'Low stock → notify owner', 'bn': 'স্টক কম'},
          'trigger': 'inventory.low',
          'needs': <String>[],
          'installed': false,
        },
      ],
    );
    fake.adapter.onJson(
      'POST',
      '/automation/recipes/low_stock_notify/install',
      {'id': 'wf2', 'blocker': null},
    );
    fake.adapter.on('PATCH', '/automation/workflows/wf1', (request) {
      enabled = request.jsonBody['enabled'] as bool;
      return FakeReply({'id': 'wf1', 'enabled': enabled});
    });
    await tester.pumpWidget(_app(fake, 'en'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Recipes'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Turn on'));
    await tester.pumpAndSettle();
    expect(find.text(automationEn['auto.recipe.done']!), findsOneWidget);
    await tester.tap(find.text('Workflows'));
    await tester.pumpAndSettle();
    tester.widget<SwitchListTile>(find.byType(SwitchListTile)).onChanged!(
      false,
    );
    await tester.pumpAndSettle();
    expect(find.text(automationEn['auto.recipe.done']!), findsNothing);
  });

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

  test('V4 confirmation triggers and outcomes have English and Bangla', () {
    const keys = [
      'auto.trigger.confirmation.confirmed',
      'auto.trigger.confirmation.cancelled',
      'auto.trigger.confirmation.no_answer',
      'auto.trigger.confirmation.callback_requested',
      'auto.trigger.intake.draft_ready',
      'auto.reason.CONFIRMATION_ENGINE_DISABLED',
      'auto.reason.ORDER_NOT_DRAFT',
      'auto.reason.ALREADY_REQUESTED',
      'auto.reason.AUTOPILOT_DISABLED',
      'auto.reason.CALL',
      'auto.reason.AUTO_CONFIRM',
      'auto.reason.SELLER_REVIEW',
    ];
    for (final key in keys) {
      expect(automationEn[key], isNotNull, reason: key);
      expect(automationBn[key], matches(RegExp('[ঀ-৿]')), reason: key);
    }
  });

  for (final language in ['en', 'bn']) {
    testWidgets('a confirmation workflow and recipe read as text ($language)', (
      tester,
    ) async {
      final strings = language == 'bn' ? automationBn : automationEn;
      final fake = _fake(
        manage: true,
        enabled: () => true,
        trigger: 'confirmation.confirmed',
        recipes: [
          {
            'key': 'order_confirm_customer',
            'name': {
              'en': 'Order → confirm with the customer',
              'bn': 'অর্ডার → গ্রাহকের কাছে নিশ্চিত করুন',
            },
            'trigger': 'order.created',
            'needs': <String>[],
            'installed': false,
          },
        ],
      );
      fake.adapter.onJson(
        'POST',
        '/automation/recipes/order_confirm_customer/install',
        {'id': 'wf2', 'blocker': 'CONFIRMATION_ENGINE_DISABLED'},
      );
      await tester.pumpWidget(_app(fake, language));
      await tester.pumpAndSettle();
      expect(
        find.textContaining(strings['auto.trigger.confirmation.confirmed']!),
        findsOneWidget,
      );
      await tester.tap(find.text(strings['auto.tab.recipes']!));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(FilledButton));
      await tester.pumpAndSettle();
      // Switched off for the shop: kept as a draft, and the seller is told why.
      expect(
        find.textContaining(
          strings['auto.reason.CONFIRMATION_ENGINE_DISABLED']!,
        ),
        findsOneWidget,
      );
    });
  }
}
