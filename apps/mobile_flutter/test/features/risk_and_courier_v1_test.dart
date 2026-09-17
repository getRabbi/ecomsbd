import 'package:ecomsbd/data/commerce/risk_repository.dart';
import 'package:ecomsbd/design/components/badges.dart';
import 'package:ecomsbd/features/risk/risk_check_screen.dart';
import 'package:ecomsbd/features/settings/courier_accounts_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'commerce_harness.dart';

/// Risk check and the courier credential form.
///
/// The claims under test are the two a seller's money depends on:
///
/// * a thin history is reported as thin, never as "low risk";
/// * the secret key is never rendered in clear text on the device.

Map<String, dynamic> _risk({
  required String state,
  int orders = 0,
  int delivered = 0,
  int returned = 0,
  int cancelled = 0,
  int? rate,
  bool found = true,
  List<String> reasons = const <String>['OWN_SHOP_HISTORY_ONLY'],
}) {
  return <String, dynamic>{
    'found': found,
    'customer_id': found ? 'cust-1' : null,
    'name': found ? 'Nusrat' : null,
    'phone_masked': found ? '01712****78' : null,
    'phone_last4': found ? '5678' : null,
    'state': state,
    'order_count': orders,
    'delivered_count': delivered,
    'returned_count': returned,
    'cancelled_count': cancelled,
    'terminal_count': delivered + returned + cancelled,
    'success_rate_basis_points': rate,
    'first_order_at': null,
    'last_order_at': null,
    'reasons': reasons,
    'checks_remaining': 7,
  };
}

void main() {
  group('Risk model', () {
    test('insufficient data is never banded as low', () {
      final check = RiskCheck.fromJson(
        _risk(state: 'INSUFFICIENT_DATA', orders: 1, delivered: 1),
      );
      expect(check.hasEnoughHistory, isFalse);
      expect(check.level, RiskLevel.unknown);
      expect(check.level, isNot(RiskLevel.low));
    });

    test('each band maps to its own badge level', () {
      expect(RiskCheck.fromJson(_risk(state: 'LOW')).level, RiskLevel.low);
      expect(
        RiskCheck.fromJson(_risk(state: 'MEDIUM')).level,
        RiskLevel.medium,
      );
      expect(RiskCheck.fromJson(_risk(state: 'HIGH')).level, RiskLevel.high);
    });

    test('the success rate is read as a whole percentage', () {
      final check = RiskCheck.fromJson(
        _risk(state: 'LOW', delivered: 4, returned: 1, rate: 8000),
      );
      expect(check.successPercent, 80);
    });

    test('no finished orders means no rate at all, not zero', () {
      final check = RiskCheck.fromJson(_risk(state: 'INSUFFICIENT_DATA'));
      expect(check.successPercent, isNull);
    });

    test('unknown reason codes do not break the screen', () {
      final check = RiskCheck.fromJson(
        _risk(state: 'LOW', reasons: <String>['SOMETHING_NEW']),
      );
      expect(check.reasons.single, RiskReason.unknown);
      expect(check.reasons.single.labelKey, isNotEmpty);
    });
  });

  group('Risk check screen', () {
    testWidgets('shows the history the band was computed from', (tester) async {
      final harness = CommerceHarness();
      harness.adapter.onJson(
        'GET',
        '/customers/risk-check',
        _risk(
          state: 'HIGH',
          orders: 4,
          delivered: 1,
          returned: 3,
          rate: 2500,
          reasons: <String>['WEAK_DELIVERY_RATE', 'OWN_SHOP_HISTORY_ONLY'],
        ),
      );

      await pumpCommerceScreen(
        tester,
        const RiskCheckScreen(),
        harness: harness,
      );

      await tester.enterText(find.byType(TextFormField), '01712345678');
      await tester.pump();
      await tester.tap(find.byType(FilledButton));
      await settle(tester, frames: 6, step: const Duration(milliseconds: 50));

      // The counts a seller checks the verdict against.
      expect(find.text('1'), findsWidgets);
      expect(find.text('3'), findsWidgets);
      expect(find.text('25%'), findsOneWidget);
      expect(find.byType(RiskBadge), findsOneWidget);
      // The number was sent, and the masked form is what came back.
      expect(
        harness.adapter
            .to('GET', '/customers/risk-check')
            .single
            .query['phone'],
        '01712345678',
      );
      expect(find.text('01712****78'), findsOneWidget);
    });

    testWidgets('an unknown number reports thin history, not low risk', (
      tester,
    ) async {
      final harness = CommerceHarness();
      harness.adapter.onJson(
        'GET',
        '/customers/risk-check',
        _risk(
          state: 'INSUFFICIENT_DATA',
          found: false,
          reasons: <String>['NO_ORDERS', 'OWN_SHOP_HISTORY_ONLY'],
        ),
      );

      await pumpCommerceScreen(
        tester,
        const RiskCheckScreen(),
        harness: harness,
      );

      await tester.enterText(find.byType(TextFormField), '01700000001');
      await tester.pump();
      await tester.tap(find.byType(FilledButton));
      await settle(tester, frames: 6, step: const Duration(milliseconds: 50));

      final badge = tester.widget<RiskBadge>(find.byType(RiskBadge));
      expect(badge.level, RiskLevel.unknown);
    });

    testWidgets('does not call the API for an empty number', (tester) async {
      final harness = CommerceHarness();
      await pumpCommerceScreen(
        tester,
        const RiskCheckScreen(),
        harness: harness,
      );

      // A metered endpoint must not be spendable by a mis-tap.
      final button = tester.widget<FilledButton>(find.byType(FilledButton));
      expect(button.onPressed, isNull);
      expect(harness.adapter.to('GET', '/customers/risk-check'), isEmpty);
    });
  });

  group('Courier credential form', () {
    testWidgets('the secret key is never shown in clear text', (tester) async {
      final harness = CommerceHarness();
      harness.adapter.onJson('GET', '/couriers/accounts', <dynamic>[]);
      harness.adapter.onJson(
        'GET',
        '/couriers/providers/steadfast/evidence',
        <String, dynamic>{
          'provider': 'steadfast',
          'documentation_version': 'V1',
          'documentation_source': 'Steadfast API Documentation V1',
          'verified_at': '2026-09-11',
          'capabilities': <String, dynamic>{'create_single': 'true'},
          'unknowns': <String, dynamic>{},
          'blockers': <String>[],
          'manual_fallback': 'Manual courier mode.',
        },
      );

      // The accounts screen renders every courier from this list now, rather
      // than from a hard-coded Steadfast card, so the form under test is
       // reached through the server's own declaration of it.
      harness.adapter.onJson('GET', '/couriers/providers', <dynamic>[
        <String, dynamic>{
          'provider': 'steadfast',
          'display_name': 'Steadfast',
          'capabilities': <String, dynamic>{'create_single': 'true'},
          'enabled': true,
          'fully_unverified': false,
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
          },
        },
      ]);

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: harness,
      );

      // Open the connect sheet from the provider card.
      await tester.tap(find.byType(FilledButton).first);
      await settle(tester, frames: 6, step: const Duration(milliseconds: 50));

      final fields = find.byType(TextFormField);
      expect(fields, findsNWidgets(2), reason: 'API key and secret key');

      await tester.enterText(fields.at(0), 'sfk-live-0000');
      await tester.enterText(fields.at(1), 'secret-value-0000');
      await tester.pump();

      final editables = tester
          .widgetList<EditableText>(find.byType(EditableText))
          .toList();
      final obscured = editables.where((field) => field.obscureText).toList();

      // Exactly one: the key identifies which credential is loaded and stays
      // readable, the secret is the half that must not be shoulder-surfable.
      expect(obscured, hasLength(1), reason: 'only the secret is hidden');
      expect(obscured.single.controller.text, 'secret-value-0000');
      expect(
        editables.firstWhere((field) => !field.obscureText).controller.text,
        'sfk-live-0000',
      );
    });
  });
}
