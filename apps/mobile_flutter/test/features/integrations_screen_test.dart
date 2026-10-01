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

Map<String, dynamic> syncView({
  String inventory = 'ECOMSBD',
  int conflicts = 1,
}) => {
  'settings': {
    'catalog': true,
    'products': 'MANUAL',
    'inventory': inventory,
    'order_status': 'TWO_WAY',
    'fulfillment': 'ON',
    'location_id': '1',
  },
  'capabilities': {},
  'status_map': {},
  'links': {'MATCHED': 4, 'UNMATCHED': 2},
  'open_conflicts': conflicts,
  'catalog_synced_at': '2026-09-24T08:00:00Z',
  'inventory_synced_at': '2026-09-24T08:05:00Z',
  'reconnect_scopes': [],
};

void syncRoutes(
  ({ApiClient client, FakeApiAdapter adapter}) fake,
  String id, {
  Map<String, dynamic>? view,
  List<dynamic> conflicts = const [],
}) {
  fake.adapter.onJson(
    'GET',
    '/integrations/$id/sync-settings',
    view ?? syncView(conflicts: conflicts.length),
  );
  fake.adapter.onJson('GET', '/integrations/conflicts', {'items': conflicts});
}

void main() {
  test('every provider the API can list has a name in both languages', () {
    // PROVIDERS in backend/app/integrations/service.py.
    for (final provider in [
      'SHOPIFY',
      'WOOCOMMERCE',
      'CUSTOM_WEBSITE',
      'MESSENGER',
      'WHATSAPP',
    ]) {
      final key = 'int.provider.$provider';
      expect(englishStrings[key], isNotNull, reason: key);
      expect(banglaStrings[key], isNotNull, reason: key);
    }
  });

  testWidgets('detail tests the connection and replaces WooCommerce keys', (
    tester,
  ) async {
    final fake = buildFakeApi();
    syncRoutes(fake, 'w1');
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

  testWidgets('detail shows who controls stock, matches and conflicts', (
    tester,
  ) async {
    final fake = buildFakeApi();
    syncRoutes(
      fake,
      's1',
      conflicts: [
        {
          'id': 'k1',
          'kind': 'STOCK_CHANGED_BOTH',
          'entity': 'INVENTORY',
          'updated_at': '2026-09-24T08:00:00Z',
        },
      ],
    );
    fake.adapter.onJson('GET', '/integrations/s1', {
      'connection': connection(id: 's1', health: 'DEGRADED'),
      'events': [
        {
          ...issue(id: 'o1'),
          'kind': 'OUTBOUND',
          'operation': 'PUSH_INVENTORY',
          'code': 'PROVIDER_UNAVAILABLE',
          'external_ref': null,
        },
      ],
      'runs': [],
      'can_manage': true,
      'can_retry': true,
    });
    fake.adapter.onJson('POST', '/integrations/events/o1/retry', {
      'id': 'o1',
      'status': 'QUEUED',
    });
    fake.adapter.onJson('GET', '/integrations/s1/links', {
      'items': [
        {
          'id': 'l1',
          'external_title': 'Panjabi / L',
          'external_sku': 'P-L',
          'internal_name': 'Panjabi',
          'external_qty': 7,
          'state': 'MATCHED',
        },
        {
          'id': 'l2',
          'external_title': 'Scarf',
          'external_sku': null,
          'internal_name': null,
          'external_qty': null,
          'state': 'UNMATCHED',
        },
      ],
    });
    await pumpAtSize(
      tester,
      const IntegrationDetailScreen(id: 's1'),
      overrides: [apiClientProvider.overrideWithValue(fake.client)],
    );
    await tester.pumpAndSettle();
    expect(
      find.text('Stock: ${englishStrings['int.sync.stock.ECOMSBD']}'),
      findsOneWidget,
    );
    expect(find.text('Products matched: 4 · not matched: 2'), findsOneWidget);
    expect(find.text('Conflicts to decide: 1'), findsOneWidget);
    expect(
      find.text(englishStrings['int.sync.kind.STOCK_CHANGED_BOTH']!),
      findsOneWidget,
    );
    // An outbound push that failed reads as what was being sent, and retries.
    await tester.ensureVisible(
      find.text(englishStrings['int.op.PUSH_INVENTORY']!),
    );
    expect(find.text(englishStrings['int.op.PUSH_INVENTORY']!), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text(englishStrings['int.retry']!).hitTestable(),
      120,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text(englishStrings['int.retry']!));
    await tester.pumpAndSettle();
    expect(fake.adapter.to('POST', '/integrations/events/o1/retry').length, 1);
    // Matches are inspected here, changed on the web.
    await tester.scrollUntilVisible(
      find.text(englishStrings['int.sync.mappings']!).hitTestable(),
      -120,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text(englishStrings['int.sync.mappings']!));
    await tester.pumpAndSettle();
    expect(find.text('Panjabi / L'), findsOneWidget);
    expect(find.textContaining('→ Panjabi'), findsOneWidget);
    expect(
      find.text(englishStrings['int.sync.link.UNMATCHED']!),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets('sync section renders in Bangla', (tester) async {
    final fake = buildFakeApi();
    syncRoutes(fake, 's2', view: syncView(inventory: 'EXTERNAL', conflicts: 0));
    fake.adapter.onJson('GET', '/integrations/s2', {
      'connection': connection(id: 's2', provider: 'WOOCOMMERCE'),
      'events': [],
      'runs': [],
      'can_manage': false,
      'can_retry': false,
    });
    await tester.pumpWidget(
      ProviderScope(
        overrides: [apiClientProvider.overrideWithValue(fake.client)],
        child: const MaterialApp(home: IntegrationDetailScreen(id: 's2')),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.text('স্টক: ${banglaStrings['int.sync.stock.EXTERNAL']}'),
      findsOneWidget,
    );
    expect(find.text(banglaStrings['int.sync.title']!), findsOneWidget);
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
