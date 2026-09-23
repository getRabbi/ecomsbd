import 'package:ecomsbd/design/glass.dart';
import 'package:ecomsbd/design/theme.dart';
import 'package:ecomsbd/features/settings/courier_accounts_screen.dart';
import 'package:ecomsbd/l10n/app_locale.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:ecomsbd/l10n/app_strings_data.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../helpers.dart';
import 'commerce_harness.dart';

/// The courier accounts hub: every courier on one screen, and one screen to
/// manage each.
///
/// The claims under test:
///
/// * every courier the server knows is listed with its real state — connected,
///   not connected, switched off for this shop, or without an integration —
///   and only the ones that can be connected offer to be;
/// * a saved credential is shown masked (`••••••••AB12`) or as "Configured",
///   never in full, and replacing it starts from an empty form;
/// * someone the server will not show the accounts to sees state but no
///   Connect or Manage, rather than buttons that would be refused.

const Size _tall = Size(360, 1800);

Map<String, dynamic> _form(String provider, String name, List<String> fields) {
  return <String, dynamic>{
    'provider': provider,
    'display_name': name,
    'fields': <dynamic>[
      for (final (index, field) in fields.indexed)
        <String, dynamic>{
          'name': field.toLowerCase().replaceAll(' ', '_'),
          'label_en': field,
          'label_bn': field,
          'secret': true,
          'required': true,
          'input_type': index == 0 ? 'text' : 'password',
        },
    ],
    'supports_sandbox': provider == 'pathao',
    'requires_store': provider == 'pathao',
    'uses_webhook': provider == 'pathao',
  };
}

Map<String, dynamic> _steadfast({bool enabled = true}) => <String, dynamic>{
  'provider': 'steadfast',
  'display_name': 'Steadfast',
  'capabilities': <String, dynamic>{
    'create_single': 'true',
    'status_lookup': 'true',
    'webhook': 'unknown',
    'price_quote': 'false',
  },
  'enabled': enabled,
  'fully_unverified': false,
  'unknowns': <String, dynamic>{},
  'connect_form': _form('steadfast', 'Steadfast', <String>[
    'API Key',
    'Secret Key',
  ]),
};

Map<String, dynamic> _pathao() => <String, dynamic>{
  'provider': 'pathao',
  'display_name': 'Pathao',
  'capabilities': <String, dynamic>{'create_single': 'true', 'webhook': 'true'},
  'enabled': true,
  'fully_unverified': false,
  'unknowns': <String, dynamic>{},
  'connect_form': _form('pathao', 'Pathao', <String>[
    'Client ID',
    'Client Secret',
  ]),
};

const Map<String, dynamic> _redx = <String, dynamic>{
  'provider': 'redx',
  'display_name': 'RedX',
  'capabilities': <String, dynamic>{'create_single': 'unknown'},
  'enabled': false,
  'fully_unverified': true,
  'unknowns': <String, dynamic>{'REDX_API_DOCUMENTATION_REQUIRED': 'unknown'},
  'manual_fallback': 'Manual courier mode.',
};

/// Manual mode is in the server's list, but is not a courier to connect.
const Map<String, dynamic> _manual = <String, dynamic>{
  'provider': 'manual',
  'display_name': 'Manual courier',
  'capabilities': <String, dynamic>{},
  'enabled': true,
  'fully_unverified': false,
  'unknowns': <String, dynamic>{},
};

Map<String, dynamic> _account({
  String provider = 'steadfast',
  String status = 'CONNECTED',
  String masked = '****AB12',
}) => <String, dynamic>{
  'id': 'acct-$provider',
  'provider': provider,
  'label': null,
  'status': status,
  'connected': status == 'CONNECTED',
  'needs_reconnect': status == 'NEEDS_RECONNECT',
  'masked_identifier': masked,
  'last_verified_at': null,
  'last_validation_result': 'VALID',
  'last_validation_message': null,
  'capabilities': <String, dynamic>{},
  'reported_balance_paisa': null,
  'reported_balance_at': null,
  'config': <String, dynamic>{},
  'webhook_configured': false,
};

const Map<String, dynamic> _forbidden = <String, dynamic>{
  'code': 'FORBIDDEN',
  'message_en': 'Your role does not allow courier.credential_manage',
  'message_bn': 'আপনার ভূমিকায় এটি করা যায় না।',
  'retryable': false,
};

/// A fake server whose accounts list follows what the app does to it.
class _Server {
  _Server({
    List<Map<String, dynamic>>? providers,
    List<Map<String, dynamic>>? accounts,
    this.accountsForbidden = false,
  }) : providers =
           providers ?? <Map<String, dynamic>>[_steadfast(), _pathao(), _redx],
       accounts = accounts ?? <Map<String, dynamic>>[_account()] {
    harness.adapter.onJson('GET', '/couriers/providers', <dynamic>[
      ...this.providers,
      _manual,
    ]);
    harness.adapter.on(
      'GET',
      '/couriers/accounts',
      (_) => accountsForbidden
          ? const FakeReply(_forbidden, statusCode: 403)
          : FakeReply(this.accounts),
    );
    for (final provider in <String>['steadfast', 'pathao']) {
      harness.adapter.onJson(
        'GET',
        '/couriers/providers/$provider/evidence',
        <String, dynamic>{
          'provider': provider,
          'documentation_version': 'V1',
          'capabilities': <String, dynamic>{'create_single': 'true'},
          'unknowns': <String, dynamic>{},
          'blockers': <String>[],
        },
      );
    }
  }

  final CommerceHarness harness = CommerceHarness();
  final List<Map<String, dynamic>> providers;
  List<Map<String, dynamic>> accounts;
  final bool accountsForbidden;
}

Future<void> _open(WidgetTester tester, String label, {int at = 0}) async {
  final target = find.text(label).at(at);
  await tester.ensureVisible(target);
  await tester.pump();
  await tester.tap(target);
  // Long enough for a page transition to finish, so the screen underneath is
  // offstage and only the new one is found.
  await settle(tester, frames: 20, step: const Duration(milliseconds: 60));
}

void main() {
  group('Courier accounts list', () {
    testWidgets('lists every courier with its real state', (tester) async {
      final server = _Server();
      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: server.harness,
        size: _tall,
      );

      expect(find.text('Steadfast'), findsOneWidget);
      expect(find.text('Connected'), findsOneWidget);
      expect(find.text('••••••••AB12'), findsOneWidget);

      expect(find.text('Pathao'), findsOneWidget);
      expect(find.text('Not connected'), findsOneWidget);

      expect(find.text('RedX'), findsOneWidget);
      expect(find.text('Unavailable'), findsOneWidget);
      expect(find.text('Official integration required'), findsOneWidget);

      // Manual mode has its own card, not a courier row.
      expect(find.text('Manual courier'), findsNothing);
      expect(find.text('Manual courier mode'), findsOneWidget);

      // One Manage (Steadfast), one Connect (Pathao), nothing for RedX.
      expect(find.text('Manage'), findsOneWidget);
      expect(find.text('Connect'), findsOneWidget);

      // Capabilities are the verified ones only: "unknown" is not a feature.
      expect(find.text('Book parcels'), findsNWidgets(2));
      expect(find.text('Track status'), findsOneWidget);
      expect(find.text('Live status updates'), findsOneWidget);
    });

    testWidgets('lays out at 360dp without overflowing', (tester) async {
      final server = _Server(
        accounts: <Map<String, dynamic>>[
          _account(),
          _account(provider: 'pathao', status: 'NEEDS_RECONNECT'),
        ],
      );
      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: server.harness,
      );
      expectNoOverflow(tester);
    });

    testWidgets('a courier switched off for this shop offers no Connect', (
      tester,
    ) async {
      final server = _Server(
        providers: <Map<String, dynamic>>[_steadfast(enabled: false)],
        accounts: <Map<String, dynamic>>[],
      );
      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: server.harness,
        size: _tall,
      );

      expect(find.text('Disabled'), findsOneWidget);
      expect(find.text('Connect'), findsNothing);
      expect(find.text('Manage'), findsNothing);
    });

    testWidgets('credentials saved before a courier was switched off can '
        'still be managed', (tester) async {
      final server = _Server(
        providers: <Map<String, dynamic>>[_steadfast(enabled: false)],
      );
      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: server.harness,
        size: _tall,
      );

      expect(find.text('Disabled'), findsOneWidget);
      expect(find.text('Manage'), findsOneWidget);

      // Testable and removable, but not re-keyed while switched off.
      await _open(tester, 'Manage');
      expect(find.text('Test connection'), findsOneWidget);
      expect(find.text('Disconnect'), findsOneWidget);
      expect(find.text('Update credentials'), findsNothing);
    });
  });

  group('Managing Steadfast', () {
    testWidgets('shows the key masked and the secret only as configured', (
      tester,
    ) async {
      final server = _Server();
      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: server.harness,
        size: _tall,
      );
      await _open(tester, 'Manage');

      expect(find.text('Credentials'), findsOneWidget);
      expect(find.text('API Key'), findsOneWidget);
      expect(find.text('••••••••AB12'), findsOneWidget);
      expect(find.text('Secret Key'), findsOneWidget);
      expect(find.text('Configured'), findsOneWidget);
      expect(find.text('****AB12'), findsNothing);
      // Nothing on the screen can hold a credential.
      expect(find.byType(EditableText), findsNothing);

      expect(find.text('Update credentials'), findsOneWidget);
      expect(find.text('Test connection'), findsOneWidget);
      expect(find.text('Disconnect'), findsOneWidget);
    });

    testWidgets('replaces credentials from an empty form', (tester) async {
      final server = _Server();
      Object? sent;
      server.harness.adapter.on(
        'POST',
        '/couriers/accounts/steadfast/connect',
        (request) {
          sent = request.body;
          server.accounts = <Map<String, dynamic>>[
            _account(masked: '****WXYZ'),
          ];
          return FakeReply(<String, dynamic>{
            'result': 'VALID',
            'message': 'Connected to Steadfast.',
            'account': _account(masked: '****WXYZ'),
          }, statusCode: 201);
        },
      );

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: server.harness,
        size: _tall,
      );
      await _open(tester, 'Manage');
      await _open(tester, 'Update credentials');

      expect(find.text('Update Steadfast credentials'), findsOneWidget);
      final fields = find.byType(EditableText);
      expect(fields, findsNWidgets(2));
      // Nothing saved is put back into the form: there is nothing to read.
      for (final field in tester.widgetList<EditableText>(fields)) {
        expect(field.controller.text, isEmpty);
      }

      await tester.enterText(fields.at(0), 'sfk-new-WXYZ');
      await tester.enterText(fields.at(1), 'secret-new-0000');
      await tester.pump();
      await _open(tester, 'Save and check');

      expect(sent, <String, dynamic>{
        'credentials': <String, dynamic>{
          'api_key': 'sfk-new-WXYZ',
          'secret_key': 'secret-new-0000',
        },
      });
      expect(find.text('Connection successful'), findsOneWidget);
      expect(find.text('••••••••WXYZ'), findsOneWidget);
      expect(find.text('secret-new-0000'), findsNothing);
      expect(find.byType(EditableText), findsNothing);
    });

    testWidgets('a rejected replacement keeps the sheet open with the reason', (
      tester,
    ) async {
      final server = _Server();
      server.harness.adapter.on(
        'POST',
        '/couriers/accounts/steadfast/connect',
        (_) => const FakeReply(<String, dynamic>{
          'code': 'INVALID_COURIER_CREDENTIALS',
          'message_en': 'Steadfast rejected these credentials.',
          'message_bn': 'Steadfast এই তথ্য নেয়নি।',
          'retryable': false,
        }, statusCode: 422),
      );

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: server.harness,
        size: _tall,
      );
      await _open(tester, 'Manage');
      await _open(tester, 'Update credentials');
      final fields = find.byType(EditableText);
      await tester.enterText(fields.at(0), 'wrong-key');
      await tester.enterText(fields.at(1), 'wrong-secret');
      await tester.pump();
      await _open(tester, 'Save and check');

      expect(
        find.text('Steadfast rejected these credentials.'),
        findsOneWidget,
      );
      expect(find.text('Update Steadfast credentials'), findsOneWidget);
    });

    testWidgets('a connection test says whether it worked', (tester) async {
      final server = _Server();
      var result = 'VALID';
      server.harness.adapter.on(
        'POST',
        '/couriers/accounts/steadfast/test',
        (_) => FakeReply(<String, dynamic>{
          'result': result,
          'message': '',
          'account': _account(),
        }),
      );

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: server.harness,
        size: _tall,
      );
      await _open(tester, 'Manage');

      await _open(tester, 'Test connection');
      expect(find.text('Connection successful'), findsOneWidget);

      result = 'INVALID';
      await _open(tester, 'Test connection');
      expect(find.text('Connection failed'), findsOneWidget);
      expect(find.text('Connection successful'), findsNothing);

      // "We could not check" is not "your key is wrong".
      result = 'PROVIDER_UNAVAILABLE';
      await _open(tester, 'Test connection');
      expect(find.text('Steadfast did not answer'), findsOneWidget);
      expect(find.text('Connection failed'), findsNothing);
    });

    testWidgets('disconnecting returns to the list, not connected', (
      tester,
    ) async {
      final server = _Server();
      server.harness.adapter.on('DELETE', '/couriers/accounts/steadfast', (_) {
        server.accounts = <Map<String, dynamic>>[
          _account(status: 'DISCONNECTED'),
        ];
        return FakeReply(_account(status: 'DISCONNECTED'));
      });

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: server.harness,
        size: _tall,
      );
      await _open(tester, 'Manage');
      await _open(tester, 'Disconnect');
      // Confirm in the dialog.
      await _open(tester, 'Disconnect', at: 1);

      expect(
        server.harness.adapter.to('DELETE', '/couriers/accounts/steadfast'),
        hasLength(1),
      );
      expect(find.text('Steadfast disconnected'), findsOneWidget);
      expect(find.text('Credentials'), findsNothing);
      expect(find.text('Not connected'), findsNWidgets(2));
      // The erased key's old hint is not shown for a disconnected account.
      expect(find.text('••••••••AB12'), findsNothing);
      expect(find.text('Manage'), findsNothing);
    });
  });

  group('Connecting Pathao', () {
    testWidgets('asks for Pathao\'s own fields and lands on its account', (
      tester,
    ) async {
      final server = _Server();
      Object? sent;
      server.harness.adapter.on('POST', '/couriers/accounts/pathao/connect', (
        request,
      ) {
        sent = request.body;
        server.accounts = <Map<String, dynamic>>[
          _account(),
          _account(provider: 'pathao', masked: '****9012'),
        ];
        return FakeReply(<String, dynamic>{
          'result': 'VALID',
          'message': 'Connected to Pathao.',
          'account': _account(provider: 'pathao', masked: '****9012'),
        }, statusCode: 201);
      });

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: server.harness,
        size: _tall,
      );
      await _open(tester, 'Connect');

      expect(find.text('Connect Pathao'), findsOneWidget);
      expect(find.text('CLIENT ID'), findsOneWidget);
      expect(find.text('CLIENT SECRET'), findsOneWidget);
      expect(find.text('API KEY'), findsNothing);

      final fields = find.byType(EditableText);
      await tester.enterText(fields.at(0), 'pathao-id-9012');
      await tester.enterText(fields.at(1), 'pathao-secret');
      await tester.pump();
      await _open(tester, 'Save and check');

      expect((sent! as Map<String, dynamic>)['credentials'], <String, dynamic>{
        'client_id': 'pathao-id-9012',
        'client_secret': 'pathao-secret',
      });
      // Straight on to the account, with the outcome and what is left to do.
      expect(find.text('Connection successful'), findsOneWidget);
      expect(find.text('Client ID'), findsOneWidget);
      expect(find.text('••••••••9012'), findsOneWidget);
      expect(find.text('Choose store'), findsOneWidget);
    });
  });

  group('Someone who may not manage credentials', () {
    testWidgets('sees each courier\'s state and no way to change it', (
      tester,
    ) async {
      final server = _Server(accountsForbidden: true);
      server.harness.adapter.onJson('GET', '/couriers/bookable', <dynamic>[
        <String, dynamic>{
          'provider': 'pathao',
          'display_name': 'Pathao',
          'bookable': false,
          'reason': 'NOT_CONNECTED',
          'requires_store': true,
        },
        <String, dynamic>{
          'provider': 'steadfast',
          'display_name': 'Steadfast',
          'bookable': true,
          'reason': null,
          'requires_store': false,
        },
      ]);

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: server.harness,
        size: _tall,
      );

      expect(
        find.text(
          'Only the shop owner can connect couriers or change their '
          'credentials.',
        ),
        findsOneWidget,
      );
      expect(find.text('Connected'), findsOneWidget);
      expect(find.text('Not connected'), findsOneWidget);
      expect(find.text('Unavailable'), findsOneWidget);
      expect(find.text('Connect'), findsNothing);
      expect(find.text('Manage'), findsNothing);
      expect(find.textContaining('••••'), findsNothing);
      // No attempt to read what the server withholds from this role.
      expect(
        server.harness.adapter.to(
          'GET',
          '/couriers/providers/steadfast/evidence',
        ),
        isEmpty,
      );
    });

    testWidgets('says "unknown" when the server shares no state at all', (
      tester,
    ) async {
      final server = _Server(accountsForbidden: true);
      server.harness.adapter.on(
        'GET',
        '/couriers/bookable',
        (_) => const FakeReply(_forbidden, statusCode: 403),
      );

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: server.harness,
        size: _tall,
      );

      expect(find.text('Unknown'), findsNWidgets(2));
      expect(find.text('Connected'), findsNothing);
      expect(find.text('Not connected'), findsNothing);
      expect(find.text('Connect'), findsNothing);
    });
  });

  group('In Bangla', () {
    testWidgets('the list and the account read in Bangla', (tester) async {
      final server = _Server();
      addTearDown(() async {
        await settle(tester, frames: 3);
        await tester.pumpWidget(const SizedBox.shrink());
        await tester.pump();
        await server.harness.dispose();
        activeAppLocale = AppLocale.en;
      });
      tester.view.physicalSize = referencePhone * 3;
      tester.view.devicePixelRatio = 3;
      addTearDown(tester.view.reset);

      activeAppLocale = AppLocale.bn;
      await tester.pumpWidget(
        ProviderScope(
          overrides: <Override>[
            effectsModeProvider.overrideWith((ref) => EffectsMode.reduced),
            ...server.harness.overrides,
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
            home: const Scaffold(body: CourierAccountsScreen()),
          ),
        ),
      );
      await settle(tester, frames: 6, step: const Duration(milliseconds: 50));

      expect(find.text('কুরিয়ার অ্যাকাউন্ট'), findsOneWidget);
      expect(find.text('যুক্ত আছে'), findsOneWidget);
      expect(find.text('পরিচালনা করুন'), findsOneWidget);
      expectNoOverflow(tester);

      await _open(tester, 'পরিচালনা করুন');
      expect(find.text('ক্রেডেনশিয়াল'), findsOneWidget);
      expect(find.text('সেট করা আছে'), findsOneWidget);
      expect(find.text('ক্রেডেনশিয়াল আপডেট করুন'), findsOneWidget);
      expectNoOverflow(tester);
    });

    test('every courier-accounts string exists in both languages', () {
      Set<String> keys(Map<String, String> table) =>
          table.keys.where((key) => key.startsWith('ca.')).toSet();
      expect(keys(banglaStrings), keys(englishStrings));
      for (final key in keys(banglaStrings)) {
        expect(banglaStrings[key], isNotEmpty, reason: key);
      }
    });
  });
}
