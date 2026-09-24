import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/features/settings/integrations_screen.dart';
import 'package:ecomsbd/l10n/app_strings_data.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/fake_api.dart';
import '../helpers.dart';

Map<String, dynamic> connection({
  String id = 'c1',
  String provider = 'SHOPIFY',
  String state = 'CONNECTED',
  String health = 'CONNECTED',
  int issues = 0,
}) => {
  'id': id,
  'provider': provider,
  'name': 'My $provider',
  'state': state,
  'health': health,
  'account_name': 'store.example',
  'orders_today': 3,
  'open_issues': issues,
  'failed_today': 0,
  'last_sync_at': '2026-09-24T08:00:00Z',
  'last_success_at': '2026-09-24T08:00:00Z',
  'last_webhook_at': null,
  'last_error_code': issues > 0 ? 'MISSING_PHONE' : null,
};

Map<String, dynamic> issue({String id = 'e1', bool retryable = true}) => {
  'id': id,
  'connection_id': 'c1',
  'provider': 'SHOPIFY',
  'status': 'FAILED',
  'code': 'MISSING_PHONE',
  'external_ref': '501',
  'retryable': retryable,
  'action': retryable ? 'RETRY' : null,
  'attempts': 1,
  'updated_at': '2026-09-24T08:00:00Z',
};

void hub(
  ({ApiClient client, FakeApiAdapter adapter}) fake, {
  bool canRetry = true,
  List<dynamic>? issues,
}) {
  fake.adapter.onJson('GET', '/integrations', {
    'providers': [
      {'provider': 'SHOPIFY', 'available': true, 'blocker': null},
      {
        'provider': 'MESSENGER',
        'available': false,
        'blocker': 'META_APP_SETUP_REQUIRED',
      },
    ],
    'items': [connection(health: 'DEGRADED', issues: 1)],
    'can_manage': true,
    'can_retry': canRetry,
  });
  fake.adapter.onJson('GET', '/integrations/issues', {
    'items': issues ?? [issue()],
  });
}

void main() {
  testWidgets('lists connections, real availability and health', (
    tester,
  ) async {
    final fake = buildFakeApi();
    hub(fake);
    await pumpAtSize(
      tester,
      const IntegrationsScreen(),
      overrides: [apiClientProvider.overrideWithValue(fake.client)],
    );
    await tester.pumpAndSettle();
    expect(find.text('My SHOPIFY'), findsOneWidget);
    expect(find.text(englishStrings['int.health.DEGRADED']!), findsOneWidget);
    expect(find.textContaining('Orders today: 3'), findsOneWidget);
    // A provider without its official app is shown as such, never as a button.
    expect(
      find.text(englishStrings['int.health.OFFICIAL_SETUP_REQUIRED']!),
      findsOneWidget,
    );
    expect(find.text(englishStrings['int.code.MISSING_PHONE']!), findsWidgets);
    expect(tester.takeException(), isNull);
  });

  testWidgets('retry posts once and shows the queued state', (tester) async {
    final fake = buildFakeApi();
    hub(fake);
    fake.adapter.onJson('POST', '/integrations/events/e1/retry', {
      'id': 'e1',
      'status': 'QUEUED',
    });
    await pumpAtSize(
      tester,
      const IntegrationsScreen(),
      overrides: [apiClientProvider.overrideWithValue(fake.client)],
    );
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text(englishStrings['int.retry']!));
    await tester.tap(find.text(englishStrings['int.retry']!));
    await tester.pumpAndSettle();
    expect(fake.adapter.to('POST', '/integrations/events/e1/retry').length, 1);
    expect(find.textContaining(englishStrings['int.queued']!), findsOneWidget);
    expect(find.text(englishStrings['int.retry']!), findsNothing);
  });

  testWidgets('viewers see problems but no retry controls', (tester) async {
    final fake = buildFakeApi();
    hub(fake, canRetry: false);
    await pumpAtSize(
      tester,
      const IntegrationsScreen(),
      overrides: [apiClientProvider.overrideWithValue(fake.client)],
    );
    await tester.pumpAndSettle();
    expect(find.text(englishStrings['int.code.MISSING_PHONE']!), findsWidgets);
    expect(find.text(englishStrings['int.retry']!), findsNothing);
    expect(find.text(englishStrings['int.resolve']!), findsNothing);
  });

  testWidgets('detail tests the connection and replaces WooCommerce keys', (
    tester,
  ) async {
    final fake = buildFakeApi();
    fake.adapter.onJson('GET', '/integrations/w1', {
      'connection': connection(
        id: 'w1',
        provider: 'WOOCOMMERCE',
        state: 'AUTH_EXPIRED',
        health: 'AUTH_EXPIRED',
      ),
      'events': [],
      'runs': [],
      'can_manage': true,
      'can_retry': true,
    });
    fake.adapter.onJson('POST', '/integrations/w1/test', {
      'ok': false,
      'checks': [
        {'key': 'AUTH', 'ok': false, 'code': 'AUTH_EXPIRED'},
      ],
      'health': 'AUTH_EXPIRED',
    });
    fake.adapter.onJson('POST', '/integrations/w1/woocommerce/keys', {
      'ok': true,
      'checks': [
        {'key': 'AUTH', 'ok': true},
      ],
      'health': 'CONNECTED',
    });
    await pumpAtSize(
      tester,
      const IntegrationDetailScreen(id: 'w1'),
      overrides: [apiClientProvider.overrideWithValue(fake.client)],
    );
    await tester.pumpAndSettle();
    expect(
      find.text(englishStrings['int.health.AUTH_EXPIRED']!),
      findsOneWidget,
    );
    await tester.tap(find.text(englishStrings['int.test']!));
    await tester.pumpAndSettle();
    expect(find.text(englishStrings['int.testFailed']!), findsOneWidget);
    await tester.enterText(find.byType(TextField).at(0), 'ck_abcdef123456');
    await tester.enterText(find.byType(TextField).at(1), 'cs_abcdef123456');
    await tester.ensureVisible(find.text(englishStrings['int.saveKeys']!));
    await tester.tap(find.text(englishStrings['int.saveKeys']!));
    await tester.pumpAndSettle();
    final sent = fake.adapter.to('POST', '/integrations/w1/woocommerce/keys');
    expect(sent.single.jsonBody, {
      'consumer_key': 'ck_abcdef123456',
      'consumer_secret': 'cs_abcdef123456',
    });
    expect(find.text(englishStrings['int.testOk']!), findsOneWidget);
  });

  testWidgets('renders in Bangla', (tester) async {
    final fake = buildFakeApi();
    hub(fake);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [apiClientProvider.overrideWithValue(fake.client)],
        child: const MaterialApp(home: IntegrationsScreen()),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text(banglaStrings['int.title']!), findsOneWidget);
    expect(find.text(banglaStrings['int.health.DEGRADED']!), findsOneWidget);
    expect(find.text(banglaStrings['int.code.MISSING_PHONE']!), findsWidgets);
  });

  test('integration translations exist in both languages', () {
    final keys = englishStrings.keys.where((k) => k.startsWith('int.')).toSet();
    expect(banglaStrings.keys.where((k) => k.startsWith('int.')).toSet(), keys);
    final vars = RegExp(r'\{\w+\}');
    for (final key in keys) {
      expect(banglaStrings[key], isNotEmpty, reason: key);
      expect(
        vars.allMatches(banglaStrings[key]!).map((m) => m[0]).toSet(),
        vars.allMatches(englishStrings[key]!).map((m) => m[0]).toSet(),
        reason: key,
      );
    }
  });
}
