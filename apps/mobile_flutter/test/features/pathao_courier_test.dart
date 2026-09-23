import 'package:ecomsbd/data/couriers/models.dart';
import 'package:ecomsbd/features/settings/courier_accounts_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'commerce_harness.dart';

/// Pathao on the courier accounts screen.
///
/// The claims under test are the ones that decide whether a seller can
/// actually get a Pathao parcel moving:
///
/// * the connect form is built from the server's declaration, so it asks for
///   Client ID and Client Secret rather than Steadfast's field names;
/// * "connected" is not presented as bookable while the mandatory pickup store
///   is missing — Pathao refuses every create without one;
/// * the callback URL is offered, because Pathao publishes no status lookup and
///   a parcel's status never advances without it;
/// * the Client Secret is never rendered in clear text.
///
/// Copy assertions are in English: the test harness renders with the English
/// locale. Provider and technical terms stay English in both languages anyway,
/// so those assertions hold either way.

const Map<String, dynamic> _pathaoProvider = <String, dynamic>{
  'provider': 'pathao',
  'display_name': 'Pathao',
  'verified_at': '2026-09-17',
  'capabilities': <String, dynamic>{
    'create_single': 'true',
    'webhook': 'true',
    'status_lookup': 'false',
  },
  'manual_fallback': 'Manual courier mode.',
  'fully_unverified': false,
  'enabled': true,
  'documentation_version': 'aladdin/api/v1',
  'unknowns': <String, dynamic>{'STATUS_LOOKUP_CONTRACT': 'unknown'},
  'connect_form': <String, dynamic>{
    'provider': 'pathao',
    'display_name': 'Pathao',
    'fields': <dynamic>[
      <String, dynamic>{
        'name': 'client_id',
        'label_en': 'Client ID',
        'label_bn': 'Client ID',
        'secret': true,
        'required': true,
        'help_en': 'Pathao merchant panel → Developer API.',
        'input_type': 'text',
      },
      <String, dynamic>{
        'name': 'client_secret',
        'label_en': 'Client Secret',
        'label_bn': 'Client Secret',
        'secret': true,
        'required': true,
        'input_type': 'password',
      },
    ],
    'supports_sandbox': true,
    'requires_store': true,
    'uses_webhook': true,
    'webhook_help_en':
        'Pathao does not offer a status lookup — parcel updates arrive only by '
        'webhook.',
  },
};

const Map<String, dynamic> _steadfastProvider = <String, dynamic>{
  'provider': 'steadfast',
  'display_name': 'Steadfast',
  'capabilities': <String, dynamic>{'create_single': 'true'},
  'fully_unverified': false,
  'enabled': true,
  'unknowns': <String, dynamic>{},
  'connect_form': <String, dynamic>{
    'provider': 'steadfast',
    'display_name': 'Steadfast',
    'fields': <dynamic>[
      <String, dynamic>{
        'name': 'api_key',
        'label_en': 'API Key',
        'label_bn': 'API Key',
        'secret': true,
        'required': true,
        'input_type': 'text',
      },
      <String, dynamic>{
        'name': 'secret_key',
        'label_en': 'Secret Key',
        'label_bn': 'Secret Key',
        'secret': true,
        'required': true,
        'input_type': 'password',
      },
    ],
    'supports_sandbox': false,
    'requires_store': false,
    'uses_webhook': false,
    'supports_delivery_type': true,
  },
};

const Map<String, dynamic> _steadfastEvidence = <String, dynamic>{
  'provider': 'steadfast',
  'documentation_version': 'V1',
  'documentation_source': 'Steadfast API Documentation V1',
  'verified_at': '2026-09-11',
  'capabilities': <String, dynamic>{'create_single': 'true'},
  'unknowns': <String, dynamic>{},
  'blockers': <String>[],
  'manual_fallback': 'Manual courier mode.',
};

Map<String, dynamic> _pathaoAccount({
  String? storeId,
  String? storeName,
  bool webhookConfigured = false,
  bool sandbox = false,
}) {
  return <String, dynamic>{
    'id': 'acct-pathao-1',
    'provider': 'pathao',
    'label': null,
    'status': 'CONNECTED',
    'connected': true,
    'needs_reconnect': false,
    'masked_identifier': '****9012',
    'last_verified_at': null,
    'last_validation_result': 'VALID',
    'last_validation_message': 'Connected to Pathao.',
    'capabilities': <String, dynamic>{'create_single': 'true'},
    'reported_balance_paisa': null,
    'reported_balance_at': null,
    'config': <String, dynamic>{
      if (storeId != null) 'store_id': storeId,
      if (storeName != null) 'store_name': storeName,
      'sandbox': sandbox,
    },
    'webhook_configured': webhookConfigured,
  };
}

CommerceHarness _harness({List<dynamic> accounts = const <dynamic>[]}) {
  final harness = CommerceHarness();
  harness.adapter.onJson('GET', '/couriers/accounts', accounts);
  harness.adapter.onJson(
    'GET',
    '/couriers/providers/steadfast/evidence',
    _steadfastEvidence,
  );
  harness.adapter.onJson('GET', '/couriers/providers', <dynamic>[
    _steadfastProvider,
    _pathaoProvider,
  ]);
  return harness;
}

/// Open Pathao's management screen from the accounts list.
Future<void> _openPathao(WidgetTester tester) async {
  final manage = find.text('Manage');
  await tester.ensureVisible(manage);
  await tester.pump();
  await tester.tap(manage);
  // Long enough for the page transition to finish, so the list underneath
  // goes offstage and only the management screen is found.
  await settle(tester, frames: 20, step: const Duration(milliseconds: 60));
}

void main() {
  group('Pathao on the courier accounts screen', () {
    testWidgets('offers Pathao alongside Steadfast', (tester) async {
      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: _harness(),
      );

      expect(find.text('Pathao'), findsOneWidget);
      expect(find.text('Steadfast'), findsWidgets);
    });

    testWidgets('asks for the field names Pathao uses, not Steadfast\'s', (
      tester,
    ) async {
      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: _harness(),
      );

      // The Pathao card's own Connect button, not Steadfast's. It sits below
      // the fold on a 360dp phone, so it is scrolled into view first.
      final connect = find.byType(FilledButton).last;
      await tester.ensureVisible(connect);
      await tester.pump();
      await tester.tap(connect);
      await settle(tester, frames: 6, step: const Duration(milliseconds: 50));

      // `LabelledField` paints its label uppercased.
      expect(find.text('CLIENT ID'), findsOneWidget);
      expect(find.text('CLIENT SECRET'), findsOneWidget);
      expect(find.text('API KEY'), findsNothing);
    });

    testWidgets('never shows the Client Secret in clear text', (tester) async {
      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: _harness(),
      );

      final connect = find.byType(FilledButton).last;
      await tester.ensureVisible(connect);
      await tester.pump();
      await tester.tap(connect);
      await settle(tester, frames: 6, step: const Duration(milliseconds: 50));

      final fields = find.byType(TextFormField);
      await tester.enterText(fields.at(0), 'pathao-client-id');
      await tester.enterText(fields.at(1), 'pathao-client-secret');
      await tester.pump();

      final editables = tester
          .widgetList<EditableText>(find.byType(EditableText))
          .toList();
      final obscured = editables.where((field) => field.obscureText).toList();

      // The Client ID identifies which credential is loaded and stays
      // readable; the secret is the half that must not be shoulder-surfable.
      expect(
        obscured.map((f) => f.controller.text),
        contains('pathao-client-secret'),
      );
      expect(
        obscured.map((f) => f.controller.text),
        isNot(contains('pathao-client-id')),
      );
    });

    testWidgets('a connected account with no pickup store is not bookable', (
      tester,
    ) async {
      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: _harness(accounts: <dynamic>[_pathaoAccount()]),
      );

      // Pathao refuses every create without store_id, so "connected" alone
      // would be a lie the seller only discovers at booking time.
      expect(
        find.textContaining('Choose a pickup store'),
        findsOneWidget,
        reason: 'the blocking state is stated, not left to fail at booking',
      );

      await _openPathao(tester);
      expect(find.textContaining('Choose a pickup store'), findsOneWidget);
      expect(find.text('Choose store'), findsOneWidget);
      expect(find.text('Not chosen yet'), findsOneWidget);
    });

    testWidgets('shows the chosen pickup store once one is set', (
      tester,
    ) async {
      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: _harness(
          accounts: <dynamic>[
            _pathaoAccount(storeId: '12345', storeName: 'Mirpur Warehouse'),
          ],
        ),
      );
      await _openPathao(tester);

      expect(find.text('Mirpur Warehouse'), findsOneWidget);
      expect(find.text('Change store'), findsOneWidget);
      expect(find.textContaining('Choose a pickup store'), findsNothing);
    });

    testWidgets('lists the pickup stores the courier reports', (tester) async {
      final harness = _harness(accounts: <dynamic>[_pathaoAccount()]);
      harness.adapter.onJson(
        'GET',
        '/couriers/accounts/pathao/stores',
        <dynamic>[
          <String, dynamic>{
            'provider_store_id': '12345',
            'name': 'Mirpur Warehouse',
            'address': 'Mirpur 10, Dhaka',
          },
        ],
      );

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: harness,
      );
      await _openPathao(tester);

      final choose = find.text('Choose store');
      await tester.ensureVisible(choose);
      await tester.pump();
      await tester.tap(choose);
      await settle(tester, frames: 8, step: const Duration(milliseconds: 60));

      expect(find.text('Mirpur Warehouse'), findsOneWidget);
      expect(find.text('Mirpur 10, Dhaka'), findsOneWidget);
    });

    testWidgets('marks a sandbox account so it cannot be mistaken for live', (
      tester,
    ) async {
      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: _harness(
          accounts: <dynamic>[_pathaoAccount(storeId: '12345', sandbox: true)],
        ),
      );

      expect(find.text('Sandbox'), findsOneWidget);
    });

    testWidgets('hands over the callback URL and says why it matters', (
      tester,
    ) async {
      final harness = _harness(
        accounts: <dynamic>[
          _pathaoAccount(storeId: '12345', webhookConfigured: true),
        ],
      );
      harness.adapter.onJson('GET', '/couriers/accounts/pathao/webhook', <
        String,
        dynamic
      >{
        'provider': 'pathao',
        'supported': true,
        'callback_url':
            'https://api.example.com/v1/webhooks/couriers/pathao/tok-123',
        'secret_configured': true,
        'help_en':
            'Pathao does not offer a status lookup — parcel updates arrive '
            'only by webhook.',
        'help_bn':
            'Pathao-তে স্ট্যাটাস লুকআপ নেই — পার্সেলের আপডেট শুধু webhook দিয়েই আসে।',
      });

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: harness,
      );
      await _openPathao(tester);

      expect(find.text('Status updates are set up'), findsOneWidget);
      expect(
        find.text(
          'https://api.example.com/v1/webhooks/couriers/pathao/tok-123',
        ),
        findsOneWidget,
      );
      // The courier's own reason, so a seller reads why this matters for
      // *this* courier rather than a generic note.
      expect(
        find.textContaining('does not offer a status lookup'),
        findsOneWidget,
      );
    });

    testWidgets('warns when no webhook secret is stored', (tester) async {
      final harness = _harness(
        accounts: <dynamic>[_pathaoAccount(storeId: '12345')],
      );
      harness.adapter
          .onJson('GET', '/couriers/accounts/pathao/webhook', <String, dynamic>{
            'provider': 'pathao',
            'supported': true,
            'callback_url':
                'https://api.example.com/v1/webhooks/couriers/pathao/t',
            'secret_configured': false,
          });

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: harness,
      );
      await _openPathao(tester);

      expect(find.text('Status updates are not set up'), findsOneWidget);
    });

    testWidgets('a courier the shop may not use is listed as disabled', (
      tester,
    ) async {
      final harness = CommerceHarness();
      harness.adapter.onJson('GET', '/couriers/accounts', <dynamic>[]);
      harness.adapter.onJson(
        'GET',
        '/couriers/providers/steadfast/evidence',
        _steadfastEvidence,
      );
      harness.adapter.onJson('GET', '/couriers/providers', <dynamic>[
        _steadfastProvider,
        <String, dynamic>{..._pathaoProvider, 'enabled': false},
      ]);

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: harness,
      );

      // Shown as it is — switched off for this shop — with nothing to press.
      expect(find.text('Pathao'), findsOneWidget);
      expect(find.text('Disabled'), findsOneWidget);
      expect(
        find.text('Pathao is not switched on for your shop yet.'),
        findsOneWidget,
      );
      expect(find.text('Connect'), findsOneWidget, reason: "Steadfast's only");
    });

    testWidgets('a courier with no verified contract cannot be connected', (
      tester,
    ) async {
      final harness = CommerceHarness();
      harness.adapter.onJson('GET', '/couriers/accounts', <dynamic>[]);
      harness.adapter.onJson(
        'GET',
        '/couriers/providers/steadfast/evidence',
        _steadfastEvidence,
      );
      harness.adapter.onJson('GET', '/couriers/providers', <dynamic>[
        _steadfastProvider,
        // A courier as the server reports one with no verified contract:
        // enabled, but with every capability unknown and therefore no connect
        // form. It is listed so the seller can see where it stands, with
        // nothing to connect.
        <String, dynamic>{
          'provider': 'othercourier',
          'display_name': 'Other Courier',
          'capabilities': <String, dynamic>{},
          'enabled': true,
          'fully_unverified': true,
          'unknowns': <String, dynamic>{
            'OTHER_COURIER_DOCUMENTATION_REQUIRED': 'unknown',
          },
          'manual_fallback': 'Manual courier mode.',
        },
      ]);

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: harness,
      );

      expect(find.text('Other Courier'), findsOneWidget);
      expect(find.text('Unavailable'), findsOneWidget);
      expect(find.text('Official integration required'), findsOneWidget);
      // No capability is drawn from an all-unknown manifest, and the only
      // Connect button is Steadfast's.
      expect(find.text('Connect'), findsOneWidget);
      expect(find.text('Manage'), findsNothing);
      // Manual mode is still presented as a real path, not an apology.
      expect(find.text('Manual courier mode'), findsOneWidget);
    });
  });

  group('Model safety', () {
    test('a connect form with no declaration is not connectable', () {
      final manual = CourierProviderInfo.fromJson(const <String, dynamic>{
        'provider': 'manual',
        'display_name': 'Manual',
        'capabilities': <String, dynamic>{},
        'enabled': true,
        'fully_unverified': true,
      });

      // Manual mode has nothing to connect, so it never gets a card.
      expect(manual.connectForm, isNull);
      expect(manual.isConnectable, isFalse);
    });

    test('the account exposes config but never a secret', () {
      final account = CourierAccount.fromJson(
        _pathaoAccount(storeId: '12345', storeName: 'Mirpur', sandbox: true),
      );

      expect(account.storeId, '12345');
      expect(account.storeName, 'Mirpur');
      expect(account.sandbox, isTrue);
      // There is no field on this model that could hold one.
      expect(account.config.containsKey('client_secret'), isFalse);
      expect(account.config.containsKey('webhook_secret'), isFalse);
    });

    test('a webhook setup never carries the secret itself', () {
      final setup = WebhookSetup.fromJson(const <String, dynamic>{
        'provider': 'pathao',
        'supported': true,
        'callback_url': 'https://api.example.com/v1/webhooks/couriers/pathao/t',
        'secret_configured': true,
      });

      // Only whether one is stored, which is all this screen needs.
      expect(setup.secretConfigured, isTrue);
      expect(setup.callbackUrl, isNotNull);
    });
  });
}
