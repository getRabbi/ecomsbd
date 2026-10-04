import 'dart:async';

import 'package:ecomsbd/data/channels/integration_connect.dart';
import 'package:ecomsbd/features/settings/connections_screen.dart';
import 'package:ecomsbd/features/settings/integration_setup_screens.dart';
import 'package:ecomsbd/features/settings/integrations_screen.dart';
import 'package:ecomsbd/l10n/app_strings_data.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'commerce_harness.dart';

/// WooCommerce and Custom Website are connected on the phone, end to end,
/// against the same endpoints the web uses — and nothing sends the seller to a
/// web dashboard.

const Size _tall = Size(360, 2000);

String _en(String key) => englishStrings[key]!;

Map<String, dynamic> _conn(
  String id,
  String provider, {
  String state = 'PENDING',
  String health = 'SETUP_INCOMPLETE',
  Map<String, dynamic> extra = const <String, dynamic>{},
}) => <String, dynamic>{
  'id': id,
  'provider': provider,
  'name': 'My $provider',
  'state': state,
  'health': health,
  'account_name': 'mystore.com',
  'account_id': 'https://mystore.com',
  'webhook_state': null,
  'sync_state': null,
  'last_success_at': null,
  'last_webhook_at': null,
  'last_sync_at': null,
  'last_error_at': null,
  'last_error_code': null,
  'created_at': '2026-10-01T00:00:00Z',
  'open_issues': 0,
  'open_conflicts': 0,
  'orders_today': 0,
  'failed_today': 0,
  ...extra,
};

Map<String, dynamic> _detail(
  Map<String, dynamic> connection, {
  Object? custom,
}) => <String, dynamic>{
  'connection': connection,
  'runs': <dynamic>[],
  'events': <dynamic>[],
  'availability': <String, dynamic>{'available': true},
  'can_manage': true,
  'can_retry': true,
  if (custom != null) 'custom': custom,
};

class _Browser {
  final List<Uri> opened = <Uri>[];
  final StreamController<IntegrationReturn> returns =
      StreamController<IntegrationReturn>.broadcast();

  Future<bool> open(Uri uri) async {
    opened.add(uri);
    return true;
  }
}

CommerceHarness _harness(_Browser browser) => CommerceHarness(
  extraOverrides: [
    externalOpenerProvider.overrideWithValue(browser.open),
    integrationReturnsProvider.overrideWithValue(browser.returns.stream),
  ],
);

void main() {
  testWidgets('the launch hub blocks setup even when providers are available', (
    tester,
  ) async {
    final browser = _Browser();
    final harness = _harness(browser)
      ..adapter.onJson('GET', '/integrations', <String, dynamic>{
        'providers': <Map<String, dynamic>>[
          <String, dynamic>{'provider': 'WOOCOMMERCE', 'available': true},
          <String, dynamic>{'provider': 'CUSTOM_WEBSITE', 'available': true},
        ],
        'items': <dynamic>[],
        'can_manage': true,
        'can_retry': true,
      })
      ..adapter.onJson('GET', '/integrations/issues', <String, dynamic>{
        'items': <dynamic>[],
      })
      ..adapter.onJson('GET', '/couriers/providers', <String, dynamic>{
        'items': <dynamic>[],
      })
      ..adapter.onJson('GET', '/couriers/accounts', <String, dynamic>{
        'items': <dynamic>[],
      });
    await pumpCommerceScreen(
      tester,
      const ConnectionsScreen(),
      harness: harness,
      size: _tall,
    );
    expect(find.text('Set up on web'), findsNothing);
    expect(find.text('Coming soon'), findsNWidgets(2));
    for (final provider in ['WOOCOMMERCE', 'CUSTOM_WEBSITE']) {
      expect(find.byKey(ValueKey('hub-setup-$provider')), findsNothing);
      await tester.tap(find.byKey(ValueKey('hub-provider-$provider')));
      await settle(tester, frames: 10);
    }
    expect(find.text('Connect WooCommerce'), findsNothing);
    expect(find.byType(ConnectionsScreen), findsOneWidget);
    expect(harness.adapter.requests.every((r) => r.method == 'GET'), isTrue);
    expect(browser.opened, isEmpty);
  });

  testWidgets(
    'WooCommerce one-click: approve in the store, return, connected',
    (tester) async {
      final browser = _Browser();
      final harness = _harness(browser);
      harness.adapter
        ..onJson('POST', '/integrations', <String, dynamic>{
          'connection': _conn('w1', 'WOOCOMMERCE'),
        })
        ..onJson('POST', '/integrations/w1/connect', <String, dynamic>{
          'authorize_url':
              'https://mystore.com/wc-auth/v1/authorize?app_name=ecomsbd',
          'manual': false,
        })
        ..onJson(
          'GET',
          '/integrations/w1',
          _detail(_conn('w1', 'WOOCOMMERCE', extra: {'keys_received': true})),
        )
        ..onJson('POST', '/integrations/w1/test', <String, dynamic>{
          'ok': true,
          'checks': <dynamic>[
            <String, dynamic>{'key': 'AUTH', 'ok': true},
          ],
          'health': 'CONNECTED',
          'connection': _conn(
            'w1',
            'WOOCOMMERCE',
            state: 'CONNECTED',
            health: 'CONNECTED',
          ),
        });
      await pumpCommerceScreen(
        tester,
        const ProviderSignInScreen(provider: 'WOOCOMMERCE'),
        harness: harness,
        size: _tall,
      );
      await tester.enterText(
        find.descendant(
          of: find.byKey(const Key('ics-address')),
          matching: find.byType(TextField),
        ),
        'https://mystore.com',
      );
      await tester.tap(find.byKey(const Key('ics-continue')));
      await settle(tester, frames: 10);

      final connect = harness.adapter.to('POST', '/integrations/w1/connect');
      expect(connect.single.jsonBody, <String, dynamic>{
        'return_to': 'app',
        'store_url': 'https://mystore.com',
      });
      expect(browser.opened.single.host, 'mystore.com');
      expect(find.text(_en('ics.waitTitle')), findsOneWidget);

      // The store's approval page sends the seller back into the app.
      browser.returns.add(
        const IntegrationReturn(connectionId: 'w1', result: 'woocommerce'),
      );
      await settle(tester, frames: 10);
      expect(harness.adapter.to('POST', '/integrations/w1/test'), hasLength(1));
      expect(find.byKey(const Key('ics-connected')), findsOneWidget);
      expect(find.text('mystore.com'), findsOneWidget);
    },
  );

  testWidgets('WooCommerce manual keys: invalid keys are reported, not kept', (
    tester,
  ) async {
    final browser = _Browser();
    final harness = _harness(browser);
    var attempts = 0;
    harness.adapter
      ..onJson('POST', '/integrations', <String, dynamic>{
        'connection': _conn('w2', 'WOOCOMMERCE'),
      })
      ..onJson('POST', '/integrations/w2/connect', <String, dynamic>{
        'authorize_url': null,
        'manual': true,
      })
      ..on('POST', '/integrations/w2/woocommerce/keys', (request) {
        attempts++;
        final ok = attempts > 1;
        return FakeReply(<String, dynamic>{
          'ok': ok,
          'checks': <dynamic>[
            <String, dynamic>{
              'key': 'AUTH',
              'ok': ok,
              if (!ok) 'code': 'AUTH_EXPIRED',
            },
          ],
          'health': ok ? 'CONNECTED' : 'AUTH_EXPIRED',
          'connection': _conn(
            'w2',
            'WOOCOMMERCE',
            state: ok ? 'CONNECTED' : 'AUTH_EXPIRED',
            health: ok ? 'CONNECTED' : 'AUTH_EXPIRED',
          ),
        });
      });
    await pumpCommerceScreen(
      tester,
      const ProviderSignInScreen(provider: 'WOOCOMMERCE'),
      harness: harness,
      size: _tall,
    );
    await tester.enterText(
      find.descendant(
        of: find.byKey(const Key('ics-address')),
        matching: find.byType(TextField),
      ),
      'mystore.com',
    );
    await tester.tap(find.byKey(const Key('ics-continue')));
    await settle(tester, frames: 10);
    expect(find.byKey(const Key('ics-key')), findsOneWidget);
    expect(browser.opened, isEmpty);

    Future<void> submit() async {
      await tester.enterText(
        find.descendant(
          of: find.byKey(const Key('ics-key')),
          matching: find.byType(TextField),
        ),
        'ck_aaaabbbbcccc',
      );
      await tester.enterText(
        find.descendant(
          of: find.byKey(const Key('ics-secret')),
          matching: find.byType(TextField),
        ),
        'cs_aaaabbbbcccc',
      );
      await tester.tap(find.byKey(const Key('ics-save-keys')));
      await settle(tester, frames: 10);
    }

    await submit();
    expect(find.text(_en('int.code.AUTH_EXPIRED')), findsOneWidget);
    expect(find.byKey(const Key('ics-connected')), findsNothing);
    await submit();
    expect(find.byKey(const Key('ics-connected')), findsOneWidget);
    final sent = harness.adapter.to(
      'POST',
      '/integrations/w2/woocommerce/keys',
    );
    expect(sent.last.jsonBody['consumer_key'], 'ck_aaaabbbbcccc');
  });

  testWidgets('Custom Website: key shown once, then the setup panel', (
    tester,
  ) async {
    final harness = _harness(_Browser());
    final custom = <String, dynamic>{
      'api_base_url': 'https://api.scalemyprints.com/public/v1',
      'orders_endpoint':
          'https://api.scalemyprints.com/public/v1/sources/s1/orders',
      'source_id': 's1',
      'scopes': <String>['orders:write'],
      'topics': <String>['order.confirmed', 'order.cancelled'],
      'signature_header': 'X-Ecomsbd-Signature',
      'key_active': true,
      'key_created_at': '2026-10-01T00:00:00Z',
      'last_api_call_at': null,
      'last_order_at': null,
      'last_order_id': null,
      'webhook_url': null,
      'webhook_enabled': false,
      'webhook_topics': <String>[],
      'deliveries': <String, dynamic>{
        'failed_24h': 0,
        'last_status': null,
        'last_at': null,
      },
    };
    harness.adapter
      ..onJson('POST', '/integrations', <String, dynamic>{
        'connection': _conn('cw1', 'CUSTOM_WEBSITE'),
        'api_key': 'eck_live_ONCE_ONLY_123',
        'package': <String, dynamic>{},
      })
      ..onJson(
        'GET',
        '/integrations/cw1',
        _detail(_conn('cw1', 'CUSTOM_WEBSITE'), custom: custom),
      )
      ..onJson('GET', '/integrations/conflicts', <String, dynamic>{
        'items': <dynamic>[],
      })
      ..onJson('POST', '/integrations/cw1/test-order', <String, dynamic>{
        'valid': true,
        'creates_data': false,
        'problems': <dynamic>[],
        'normalized': <String, dynamic>{},
      })
      ..onJson('POST', '/integrations/cw1/go-live', <String, dynamic>{
        'connection': _conn(
          'cw1',
          'CUSTOM_WEBSITE',
          state: 'CONNECTED',
          health: 'CONNECTED',
        ),
      });
    await pumpCommerceScreen(
      tester,
      const CustomWebsiteSetupScreen(),
      harness: harness,
      size: _tall,
    );
    await tester.enterText(
      find.descendant(
        of: find.byKey(const Key('ics-site-name')),
        matching: find.byType(TextField),
      ),
      'mystore.com',
    );
    await tester.tap(find.byKey(const Key('ics-site-create')));
    await settle(tester, frames: 12);
    expect(
      harness.adapter.to('POST', '/integrations').single.jsonBody,
      <String, dynamic>{'provider': 'CUSTOM_WEBSITE', 'name': 'mystore.com'},
    );
    expect(find.text('eck_live_ONCE_ONLY_123'), findsOneWidget);
    expect(find.text(_en('ics.onceWarning')), findsOneWidget);

    await tester.tap(find.byKey(const Key('ics-saved')));
    await settle(tester, frames: 14);
    // Gone for good: the setup page never shows the key again.
    expect(find.text('eck_live_ONCE_ONLY_123'), findsNothing);
    expect(find.byType(IntegrationDetailScreen), findsOneWidget);
    expect(find.byKey(const Key('custom-website-panel')), findsOneWidget);
    expect(
      find.text('https://api.scalemyprints.com/public/v1/sources/s1/orders'),
      findsOneWidget,
    );
    expect(find.text('Authorization: Bearer <API key>'), findsOneWidget);

    await tester.ensureVisible(find.byKey(const Key('ics-test-order')));
    await tester.tap(find.byKey(const Key('ics-test-order')));
    await settle(tester, frames: 8);
    expect(find.text(_en('ics.testOrderOk')), findsOneWidget);
    await tester.ensureVisible(find.byKey(const Key('ics-go-live')));
    await tester.tap(find.byKey(const Key('ics-go-live')));
    await settle(tester, frames: 8);
    expect(
      harness.adapter.to('POST', '/integrations/cw1/go-live'),
      hasLength(1),
    );
  });
}
