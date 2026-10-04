import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/data/channels/integration_hub.dart';
import 'package:ecomsbd/design/components/badges.dart';
import 'package:ecomsbd/design/glass.dart';
import 'package:ecomsbd/design/theme.dart';
import 'package:ecomsbd/features/menu/more_screen.dart';
import 'package:ecomsbd/features/settings/connections_screen.dart';
import 'package:ecomsbd/features/settings/courier_accounts_screen.dart';
import 'package:ecomsbd/features/settings/settings_screen.dart';
import 'package:ecomsbd/l10n/app_locale.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:ecomsbd/l10n/app_strings_data.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/auth_harness.dart';
import 'commerce_harness.dart';

/// Connections & Integrations: the one hub for stores, channels and couriers.
///
/// The claims under test:
///
/// * the five launch integrations show Coming soon with no setup or detail
///   actions, regardless of saved connections or server availability;
/// * a courier the server switched on with nothing saved reads "Not
///   connected" with a Connect button — "Disabled" only when the server says
///   the courier is off for this shop;
/// * the counts at the top are counted, and the attention section appears
///   only when something needs the seller;
/// * More and Settings lead to this one screen.

const Size _tall = Size(360, 2400);

const _launchProviders = <String>[
  'SHOPIFY',
  'WOOCOMMERCE',
  'CUSTOM_WEBSITE',
  'MESSENGER',
  'WHATSAPP',
];

Map<String, dynamic> _field(String name, String label, {bool secret = true}) =>
    <String, dynamic>{
      'name': name,
      'label_en': label,
      'label_bn': label,
      'secret': secret,
      'required': true,
      'input_type': secret ? 'password' : 'text',
    };

Map<String, dynamic> _courier(
  String provider,
  String name,
  List<Map<String, dynamic>> fields, {
  bool enabled = true,
  bool requiresStore = false,
}) => <String, dynamic>{
  'provider': provider,
  'display_name': name,
  'verified_at': '2026-09-23',
  'capabilities': <String, dynamic>{'create_single': 'true'},
  'manual_fallback': null,
  'fully_unverified': false,
  'enabled': enabled,
  'documentation_version': 'v1',
  'unknowns': <String, dynamic>{},
  'connect_form': <String, dynamic>{
    'provider': provider,
    'display_name': name,
    'fields': fields,
    'supports_sandbox': provider != 'steadfast',
    'requires_store': requiresStore,
    'supports_store': requiresStore || provider == 'redx',
    'uses_webhook': provider != 'steadfast',
  },
};

List<Map<String, dynamic>> _couriers({bool steadfastEnabled = true}) =>
    <Map<String, dynamic>>[
      _courier('pathao', 'Pathao', <Map<String, dynamic>>[
        _field('client_id', 'Client ID', secret: false),
        _field('client_secret', 'Client Secret'),
      ], requiresStore: true),
      _courier('redx', 'RedX', <Map<String, dynamic>>[
        _field('api_token', 'API Token'),
      ]),
      _courier('steadfast', 'Steadfast', <Map<String, dynamic>>[
        _field('api_key', 'API Key', secret: false),
        _field('secret_key', 'Secret Key'),
      ], enabled: steadfastEnabled),
      <String, dynamic>{
        'provider': 'manual',
        'display_name': 'Manual courier',
        'capabilities': <String, dynamic>{},
        'enabled': true,
        'fully_unverified': false,
        'unknowns': <String, dynamic>{},
      },
    ];

Map<String, dynamic> _account(String provider, String status) =>
    <String, dynamic>{
      'id': 'acct-$provider',
      'provider': provider,
      'label': null,
      'status': status,
      'connected': status == 'CONNECTED',
      'needs_reconnect': status == 'NEEDS_RECONNECT',
      'masked_identifier': '****AB12',
      'last_verified_at': null,
      'last_validation_result': 'VALID',
      'last_validation_message': null,
      'capabilities': <String, dynamic>{},
      'reported_balance_paisa': null,
      'reported_balance_at': null,
      'config': <String, dynamic>{},
      'webhook_configured': false,
    };

Map<String, dynamic> _connection(String id, String provider, String health) =>
    <String, dynamic>{
      'id': id,
      'provider': provider,
      'name': 'My $provider',
      'state': health == 'AUTH_EXPIRED' ? 'AUTH_EXPIRED' : 'ACTIVE',
      'health': health,
      'account_name': 'store.example',
      'orders_today': 1,
      'open_issues': 0,
      'open_conflicts': 0,
      'last_sync_at': null,
      'last_success_at': null,
      'last_webhook_at': null,
      'last_error_code': null,
    };

/// The production availability today: Shopify and Meta are waiting on app
/// approval; WooCommerce and a custom website can be connected on the web.
List<Map<String, dynamic>> _availability() => <Map<String, dynamic>>[
  <String, dynamic>{
    'provider': 'SHOPIFY',
    'available': false,
    'blocker': 'SHOPIFY_APP_SETUP_REQUIRED',
  },
  <String, dynamic>{
    'provider': 'WOOCOMMERCE',
    'available': true,
    'blocker': null,
    'one_click': true,
  },
  <String, dynamic>{
    'provider': 'CUSTOM_WEBSITE',
    'available': true,
    'blocker': null,
  },
  <String, dynamic>{
    'provider': 'MESSENGER',
    'available': false,
    'blocker': 'META_APP_SETUP_REQUIRED',
  },
  <String, dynamic>{
    'provider': 'WHATSAPP',
    'available': false,
    'blocker': 'META_APP_SETUP_REQUIRED',
  },
];

CommerceHarness _server({
  List<Map<String, dynamic>> items = const <Map<String, dynamic>>[],
  List<Map<String, dynamic>> issues = const <Map<String, dynamic>>[],
  List<Map<String, dynamic>>? couriers,
  List<Map<String, dynamic>> accounts = const <Map<String, dynamic>>[],
  bool owner = true,
  bool canRetry = true,
  List<Override> extraOverrides = const <Override>[],
}) {
  final harness = CommerceHarness(extraOverrides: extraOverrides);
  harness.adapter
    ..onJson('GET', '/integrations', <String, dynamic>{
      'providers': _availability(),
      'items': items,
      'can_manage': owner,
      'can_retry': canRetry,
    })
    ..onJson('GET', '/integrations/issues', <String, dynamic>{'items': issues})
    ..onJson('GET', '/couriers/providers', couriers ?? _couriers())
    ..on(
      'GET',
      '/couriers/accounts',
      (_) => owner
          ? FakeReply(accounts)
          : const FakeReply(<String, dynamic>{
              'code': 'FORBIDDEN',
              'message_en':
                  'Your role does not allow courier.credential_manage',
              'message_bn': 'আপনার ভূমিকায় এটি করা যায় না।',
              'retryable': false,
            }, statusCode: 403),
    )
    ..onJson('GET', '/couriers/bookable', <dynamic>[
      for (final provider in <String>['steadfast', 'pathao', 'redx'])
        <String, dynamic>{
          'provider': provider,
          'display_name': provider,
          'bookable': false,
          'reason': 'NOT_CONNECTED',
          'requires_store': provider == 'pathao',
        },
    ]);
  return harness;
}

String _en(String key) => englishStrings[key]!;

void main() {
  group('integration states from the server', () {
    test('each provider maps to what the server said, never inferred', () {
      final rows = integrationRowsFromHub(<String, dynamic>{
        'providers': _availability(),
        'items': <dynamic>[
          _connection('w1', 'WOOCOMMERCE', 'AUTH_EXPIRED'),
          _connection('c1', 'CUSTOM_WEBSITE', 'SYNC_FAILING'),
        ],
      });
      final byProvider = <String, IntegrationSetupState>{
        for (final row in rows) row.provider: row.state,
      };
      expect(byProvider, <String, IntegrationSetupState>{
        'SHOPIFY': IntegrationSetupState.providerApprovalRequired,
        'WOOCOMMERCE': IntegrationSetupState.needsReconnect,
        'CUSTOM_WEBSITE': IntegrationSetupState.needsAttention,
        'MESSENGER': IntegrationSetupState.temporarilyUnavailable,
        'WHATSAPP': IntegrationSetupState.temporarilyUnavailable,
        'INSTAGRAM': IntegrationSetupState.notImplemented,
      });
    });

    test('an unknown blocker is unavailable, not approval or setup', () {
      expect(
        providerSetupState(<String, dynamic>{
          'provider': 'X',
          'available': false,
          'blocker': 'SOMETHING_NEW',
        }),
        IntegrationSetupState.temporarilyUnavailable,
      );
      expect(
        providerSetupState(<String, dynamic>{
          'provider': 'X',
          'available': true,
        }),
        IntegrationSetupState.canConnect,
      );
      expect(providerSetupState(null), IntegrationSetupState.notImplemented);
    });
  });

  group('Connections & Integrations hub', () {
    testWidgets('gates five integrations and keeps courier actions active', (
      tester,
    ) async {
      final harness = _server();
      await pumpCommerceScreen(
        tester,
        const ConnectionsScreen(),
        harness: harness,
        size: _tall,
      );

      expect(find.text(_en('conn.title')), findsOneWidget);

      expect(find.text('Coming soon'), findsNWidgets(5));
      expect(find.text(_en('conn.approvalPending')), findsNothing);
      expect(find.text(_en('conn.unavailableNow')), findsNothing);
      expect(find.text(_en('conn.unavailableNote')), findsNothing);
      expect(find.textContaining('waiting for Shopify'), findsNothing);
      expect(find.textContaining('waiting for Meta'), findsNothing);
      expect(find.text('Official setup required'), findsNothing);

      expect(find.text(_en('conn.setUpOnWeb')), findsNothing);
      final requestsBeforeTaps = harness.adapter.requests.length;
      for (final provider in _launchProviders) {
        final row = find.byKey(ValueKey('hub-provider-$provider'));
        expect(find.byKey(ValueKey('hub-setup-$provider')), findsNothing);
        expect(
          find.descendant(
            of: row,
            matching: find.byWidgetPredicate(
              (widget) => widget is ButtonStyleButton,
            ),
          ),
          findsNothing,
        );
        final badge = tester.widget<StatusChip>(
          find.descendant(of: row, matching: find.byType(StatusChip)),
        );
        expect(badge.tone, Tone.neutral);
        await tester.ensureVisible(row);
        await tester.tap(row);
        await settle(tester);
        expect(find.byType(ConnectionsScreen), findsOneWidget);
        expect(Navigator.of(tester.element(row)).canPop(), isFalse);
      }
      expect(harness.adapter.requests, hasLength(requestsBeforeTaps));

      // Instagram is honest about not existing yet.
      expect(find.byKey(const ValueKey('hub-provider-INSTAGRAM')), findsOne);
      expect(find.text(_en('chan.unavailable')), findsOneWidget);

      // Every courier is switched on with nothing saved: Not connected, with
      // Connect — never Disabled.
      for (final courier in <String>['steadfast', 'pathao', 'redx']) {
        expect(find.byKey(ValueKey('hub-connect-$courier')), findsOneWidget);
      }
      for (final courier in <String>['steadfast', 'pathao', 'redx']) {
        expect(
          find.descendant(
            of: find.byKey(ValueKey('hub-courier-$courier')),
            matching: find.text(_en('provider.notConnected')),
          ),
          findsOneWidget,
          reason: courier,
        );
      }
      expect(find.text(_en('ca.stateDisabled')), findsNothing);
      expect(find.text('Manual courier'), findsNothing);

      // Counted, not invented; nothing needs attention, so no section.
      expect(find.text('Connected: 0'), findsOneWidget);
      expect(find.text('Needs attention: 0'), findsOneWidget);
      expect(find.byKey(const Key('hub-attention')), findsNothing);
      expectNoOverflow(tester);
    });

    testWidgets('a courier the server switched off is Disabled, no Connect', (
      tester,
    ) async {
      await pumpCommerceScreen(
        tester,
        const ConnectionsScreen(),
        harness: _server(couriers: _couriers(steadfastEnabled: false)),
        size: _tall,
      );
      expect(find.text(_en('ca.stateDisabled')), findsOneWidget);
      expect(find.byKey(const ValueKey('hub-connect-steadfast')), findsNothing);
      expect(find.byKey(const ValueKey('hub-connect-pathao')), findsOneWidget);
    });

    testWidgets('Connect opens the courier\'s own server-declared form', (
      tester,
    ) async {
      await pumpCommerceScreen(
        tester,
        const ConnectionsScreen(),
        harness: _server(),
        size: _tall,
      );
      final connect = find.byKey(const ValueKey('hub-connect-pathao'));
      await tester.ensureVisible(connect);
      await tester.tap(connect);
      await settle(tester, frames: 12);

      expect(find.text('Connect Pathao'), findsOneWidget);
      expect(find.text('CLIENT ID'), findsOneWidget);
      expect(find.text('CLIENT SECRET'), findsOneWidget);
      expect(find.text('API KEY'), findsNothing);
      expect(find.text(_en('ca.saveAndCheck')), findsOneWidget);
    });

    testWidgets('counts what is connected and lists what needs attention', (
      tester,
    ) async {
      await pumpCommerceScreen(
        tester,
        const ConnectionsScreen(),
        harness: _server(
          items: <Map<String, dynamic>>[
            _connection('s1', 'CUSTOM_WEBSITE', 'CONNECTED'),
            _connection('w1', 'WOOCOMMERCE', 'AUTH_EXPIRED'),
          ],
          accounts: <Map<String, dynamic>>[
            _account('steadfast', 'CONNECTED'),
            _account('redx', 'NEEDS_RECONNECT'),
          ],
        ),
        size: _tall,
      );

      expect(find.text('Connected: 1'), findsOneWidget);
      expect(find.text('Needs attention: 1'), findsOneWidget);
      expect(find.byKey(const Key('hub-attention')), findsOneWidget);
      expect(find.text('My WOOCOMMERCE'), findsNothing);
      expect(find.text('Coming soon'), findsNWidgets(5));
      expect(find.byKey(const ValueKey('hub-reconnect-redx')), findsOneWidget);
      expect(find.byKey(const ValueKey('hub-manage-steadfast')), findsOne);
      expect(find.text('••••••••AB12'), findsOneWidget);
      // A provider with a connection gets no "set up" row of its own.
      expect(find.byKey(const ValueKey('hub-setup-WOOCOMMERCE')), findsNothing);
    });

    testWidgets('saved connections stay gated without changing their data', (
      tester,
    ) async {
      final connections = <Map<String, dynamic>>[
        _connection('s1', 'SHOPIFY', 'CONNECTED'),
        _connection('w1', 'WOOCOMMERCE', 'AUTH_EXPIRED'),
        _connection('c1', 'CUSTOM_WEBSITE', 'SYNC_FAILING'),
        _connection('m1', 'MESSENGER', 'SETUP_INCOMPLETE'),
        _connection('wa1', 'WHATSAPP', 'DISCONNECTED'),
      ];
      final originals = [
        for (final connection in connections) {...connection},
      ];
      final harness = _server(
        items: connections,
        issues: <Map<String, dynamic>>[
          <String, dynamic>{
            'id': 'e1',
            'connection_id': 'c1',
            'provider': 'CUSTOM_WEBSITE',
            'status': 'FAILED',
            'code': 'MISSING_PHONE',
            'external_ref': '501',
            'retryable': true,
            'updated_at': '2026-09-24T08:00:00Z',
          },
          <String, dynamic>{
            'id': 'e2',
            'connection_id': 'w1',
            'status': 'FAILED',
            'code': 'AUTH_EXPIRED',
            'retryable': true,
          },
        ],
      );
      await pumpCommerceScreen(
        tester,
        const ConnectionsScreen(),
        harness: harness,
        size: _tall,
      );
      expect(find.text('Coming soon'), findsNWidgets(5));
      expect(find.text('Connected: 0'), findsOneWidget);
      expect(find.text('Needs attention: 0'), findsOneWidget);
      expect(find.byKey(const Key('hub-attention')), findsNothing);
      expect(find.text(_en('int.code.MISSING_PHONE')), findsNothing);
      expect(find.text(_en('int.health.AUTH_EXPIRED')), findsNothing);
      expect(find.text(_en('int.retry')), findsNothing);
      final requestsBeforeTaps = harness.adapter.requests.length;
      for (final connection in connections) {
        final row = find.byKey(ValueKey('hub-connection-${connection['id']}'));
        expect(
          find.descendant(
            of: row,
            matching: find.byWidgetPredicate(
              (widget) => widget is ButtonStyleButton,
            ),
          ),
          findsNothing,
        );
        await tester.ensureVisible(row);
        await tester.tap(row);
        await settle(tester);
        expect(find.byType(ConnectionsScreen), findsOneWidget);
        expect(Navigator.of(tester.element(row)).canPop(), isFalse);
      }
      expect(harness.adapter.requests, hasLength(requestsBeforeTaps));
      expect(harness.adapter.requests.every((r) => r.method == 'GET'), isTrue);
      expect(connections, originals);
    });

    testWidgets('someone who is not the owner sees state but no buttons', (
      tester,
    ) async {
      await pumpCommerceScreen(
        tester,
        const ConnectionsScreen(),
        harness: _server(owner: false, canRetry: false),
        size: _tall,
      );
      expect(find.text('Coming soon'), findsNWidgets(5));
      expect(find.text(_en('conn.ownerConnects')), findsNothing);
      expect(find.byKey(const ValueKey('hub-setup-WOOCOMMERCE')), findsNothing);
      for (final courier in <String>['steadfast', 'pathao', 'redx']) {
        expect(find.byKey(ValueKey('hub-connect-$courier')), findsNothing);
      }
      // States still come from the server, via the booking list.
      for (final courier in <String>['steadfast', 'pathao', 'redx']) {
        expect(
          find.descendant(
            of: find.byKey(ValueKey('hub-courier-$courier')),
            matching: find.text(_en('provider.notConnected')),
          ),
          findsOneWidget,
          reason: courier,
        );
      }
    });

    testWidgets('reads in Bangla at 360dp without overflowing', (tester) async {
      final harness = _server(
        items: <Map<String, dynamic>>[
          _connection('w1', 'WOOCOMMERCE', 'AUTH_EXPIRED'),
        ],
      );
      addTearDown(() async {
        await settle(tester, frames: 3);
        await tester.pumpWidget(const SizedBox.shrink());
        await tester.pump();
        await harness.dispose();
        activeAppLocale = AppLocale.en;
      });
      tester.view.physicalSize = const Size(360, 2400) * 3;
      tester.view.devicePixelRatio = 3;
      addTearDown(tester.view.reset);
      activeAppLocale = AppLocale.bn;
      await tester.pumpWidget(
        ProviderScope(
          overrides: <Override>[
            effectsModeProvider.overrideWith((ref) => EffectsMode.reduced),
            ...harness.overrides,
          ],
          child: MaterialApp(
            theme: buildEcomsbdTheme(),
            locale: const Locale('bn'),
            supportedLocales: const <Locale>[Locale('bn'), Locale('en')],
            localizationsDelegates: const <LocalizationsDelegate<Object>>[
              AppStrings.delegate,
              GlobalMaterialLocalizations.delegate,
              GlobalWidgetsLocalizations.delegate,
              GlobalCupertinoLocalizations.delegate,
            ],
            home: const ConnectionsScreen(),
          ),
        ),
      );
      await settle(tester, frames: 10);

      expect(find.text(banglaStrings['conn.title']!), findsOneWidget);
      expect(find.text('শীঘ্রই আসছে'), findsNWidgets(5));
      expect(find.text(banglaStrings['conn.approvalPending']!), findsNothing);
      expect(find.text(banglaStrings['conn.unavailableNow']!), findsNothing);
      expect(find.text(banglaStrings['chan.unavailable']!), findsOneWidget);
      // Only the three couriers retain Connect.
      expect(find.text(banglaStrings['common.connect']!), findsNWidgets(3));
      expectNoOverflow(tester);
    });

    test('hub copy exists in both languages with the same placeholders', () {
      final keys = englishStrings.keys
          .where(
            (k) => k.startsWith('conn.') || k.startsWith('more.connections'),
          )
          .toSet();
      expect(
        banglaStrings.keys
            .where(
              (k) => k.startsWith('conn.') || k.startsWith('more.connections'),
            )
            .toSet(),
        keys,
      );
      final vars = RegExp(r'\{\w+\}');
      for (final key in keys) {
        expect(
          vars.allMatches(banglaStrings[key]!).map((m) => m[0]).toSet(),
          vars.allMatches(englishStrings[key]!).map((m) => m[0]).toSet(),
          reason: key,
        );
      }
      for (final provider in <String>[
        'SHOPIFY',
        'WOOCOMMERCE',
        'CUSTOM_WEBSITE',
        'MESSENGER',
        'WHATSAPP',
        'INSTAGRAM',
      ]) {
        expect(englishStrings['conn.about.$provider'], isNotNull);
      }
    });
  });

  group('one way in', () {
    testWidgets('More has one Connections & Integrations entry', (
      tester,
    ) async {
      await pumpCommerceScreen(
        tester,
        const MoreScreen(),
        harness: _server(),
        size: _tall,
      );
      expect(find.text(_en('more.connections')), findsOneWidget);
      expect(find.text('Integrations'), findsNothing);
      expect(find.text('Connection health'), findsNothing);
      expect(find.text('Website & sales channels'), findsNothing);
      // Courier accounts stays as the Delivery shortcut.
      expect(find.text(_en('menu.courierAccounts')), findsOneWidget);

      // Found by what a seller would type.
      for (final query in <String>['website', 'integration', 'courier']) {
        await tester.enterText(find.byType(TextField), query);
        await settle(tester);
        expect(
          find.text(_en('more.connections')),
          findsOneWidget,
          reason: query,
        );
      }
      await tester.enterText(find.byType(TextField), '');
      await settle(tester);

      final entry = find.text(_en('more.connections'));
      await tester.ensureVisible(entry);
      await tester.tap(entry);
      await settle(tester, frames: 15);
      expect(find.byType(ConnectionsScreen), findsOneWidget);
    });

    testWidgets('Settings opens the same hub', (tester) async {
      final auth = AuthHarness();
      addTearDown(auth.dispose);
      await pumpCommerceScreen(
        tester,
        const SettingsScreen(),
        harness: _server(
          extraOverrides: <Override>[
            authControllerProvider.overrideWith((ref) => auth.controller),
          ],
        ),
        size: _tall,
      );
      final entry = find.text(_en('conn.title'));
      await tester.ensureVisible(entry);
      await tester.tap(entry);
      await settle(tester, frames: 15);
      expect(find.byType(ConnectionsScreen), findsOneWidget);
      expect(find.text(_en('int.title')), findsNothing);
    });

    testWidgets('the hub\'s courier Manage leads to the courier accounts', (
      tester,
    ) async {
      await pumpCommerceScreen(
        tester,
        const ConnectionsScreen(),
        harness: _server(),
        size: _tall,
      );
      final manage = find.text(_en('conn.manage'));
      await tester.ensureVisible(manage);
      await tester.tap(manage);
      await settle(tester, frames: 15);
      expect(find.byType(CourierAccountsScreen), findsOneWidget);
    });
  });
}
