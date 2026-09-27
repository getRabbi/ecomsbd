import 'package:ecomsbd/features/billing/plans_screen.dart';
import 'package:ecomsbd/features/settings/account_security_screen.dart';
import 'package:ecomsbd/features/settings/data_privacy_screen.dart';
import 'package:ecomsbd/features/settings/notification_settings_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'commerce_harness.dart';

/// The Phase F screens, against a fake server.
///
/// What these assert is the same thing the backend tests assert, from the other
/// side: **the client never decides what a seller is entitled to.** Every plan,
/// figure and button here comes from a server response, so the tests are mostly
/// about what the screen does with an *awkward* answer — a failing payment, an
/// exhausted quota, a purchase channel that cannot sell anything.
void main() {
  group('Plans screen', () {
    testWidgets('renders the plan the server says the shop is on', (
      tester,
    ) async {
      final harness = _billingHarness();
      await pumpCommerceScreen(tester, const PlansScreen(), harness: harness);

      expect(find.text('Starter'), findsWidgets);
      expect(find.text('Active'), findsOneWidget);
      expectNoOverflow(tester);
    });

    testWidgets('a failing payment says what happens and when', (tester) async {
      final harness = _billingHarness(
        entitlements: _entitlementsJson(plan: 'pro', status: 'GRACE'),
        subscription: _subscriptionJson(
          plan: 'pro',
          status: 'GRACE',
          inGrace: true,
          graceUntil: '2026-09-20T00:00:00Z',
        ),
      );
      await pumpCommerceScreen(tester, const PlansScreen(), harness: harness);

      // Section 16 of the Phase F brief: the seller is told it still works,
      // until when, and what to do — not just that something is wrong.
      expect(find.text('We could not take your payment'), findsOneWidget);
      expect(
        find.textContaining('Everything still works until'),
        findsOneWidget,
      );
    });

    testWidgets('a plan that will not renew keeps its paid-for time', (
      tester,
    ) async {
      final harness = _billingHarness(
        entitlements: _entitlementsJson(
          plan: 'pro',
          status: 'CANCEL_AT_PERIOD_END',
        ),
        subscription: _subscriptionJson(
          plan: 'pro',
          status: 'CANCEL_AT_PERIOD_END',
          cancelAtPeriodEnd: true,
        ),
      );
      await pumpCommerceScreen(tester, const PlansScreen(), harness: harness);

      expect(find.textContaining('will not renew'), findsOneWidget);
      expect(find.textContaining('already paid for it'), findsOneWidget);
    });

    testWidgets('support credit is named as support credit', (tester) async {
      final harness = _billingHarness(
        subscription: _subscriptionJson(provider: 'manual_admin'),
      );
      await pumpCommerceScreen(tester, const PlansScreen(), harness: harness);

      // Otherwise a seller sees a paid plan they do not remember buying and
      // reasonably assumes they are being charged for it.
      expect(
        find.textContaining('given to you by our support team'),
        findsOneWidget,
      );
      expect(
        find.textContaining('will not renew or charge you'),
        findsOneWidget,
      );
    });

    testWidgets('usage comes from the server and shows the cap', (
      tester,
    ) async {
      final harness = _billingHarness();
      await pumpCommerceScreen(tester, const PlansScreen(), harness: harness);

      expect(find.text('Orders this month'), findsOneWidget);
      expect(find.text('12 of 20'), findsOneWidget);
    });

    testWidgets('an exhausted quota explains what upgrading does', (
      tester,
    ) async {
      final harness = _billingHarness(
        usage: <Map<String, dynamic>>[_usageJson(used: 20, limit: 20)],
      );
      await pumpCommerceScreen(tester, const PlansScreen(), harness: harness);

      expect(find.text('20 of 20'), findsOneWidget);
      // And that nothing already recorded is at risk.
      expect(
        find.textContaining('what you have already recorded stays'),
        findsOneWidget,
      );
    });

    testWidgets('unlimited is rendered as unlimited, never as -1', (
      tester,
    ) async {
      final harness = _billingHarness(
        usage: <Map<String, dynamic>>[
          _usageJson(used: 340, limit: -1, unlimited: true),
        ],
      );
      await pumpCommerceScreen(tester, const PlansScreen(), harness: harness);

      expect(find.text('340 · unlimited'), findsOneWidget);
      expect(find.textContaining('-1'), findsNothing);
    });

    testWidgets('a Play build is never offered an external payment link', (
      tester,
    ) async {
      // Master spec section 27.1, from the client side. The server says the
      // channel is PLAY and nothing is purchasable; the screen must not
      // improvise a bKash button.
      final harness = _billingHarness(
        channel: <String, dynamic>{
          'channel': 'PLAY',
          'can_purchase': false,
          'allows_external_payment_link': false,
          'providers': <Map<String, dynamic>>[
            <String, dynamic>{
              'provider': 'play',
              'available': false,
              'blocker': 'PLAY_BILLING_EXTERNAL_CONFIGURATION_REQUIRED',
              'allowed_in_channel': true,
            },
            <String, dynamic>{
              'provider': 'bkash_web',
              'available': true,
              'blocker': 'NONE',
              'allowed_in_channel': false,
            },
          ],
        },
      );
      await pumpCommerceScreen(tester, const PlansScreen(), harness: harness);

      expect(find.textContaining('Upgrade with bKash'), findsNothing);
      expect(
        find.textContaining('In-app purchase is not switched on yet'),
        findsWidgets,
      );
    });

    testWidgets('a web build offers the provider the server allows', (
      tester,
    ) async {
      final harness = _billingHarness(
        channel: <String, dynamic>{
          'channel': 'WEB',
          'can_purchase': true,
          'allows_external_payment_link': true,
          'providers': <Map<String, dynamic>>[
            <String, dynamic>{
              'provider': 'bkash_web',
              'available': true,
              'blocker': 'NONE',
              'allowed_in_channel': true,
            },
          ],
        },
      );
      await pumpCommerceScreen(tester, const PlansScreen(), harness: harness);

      expect(find.textContaining('Upgrade with bKash'), findsWidgets);
    });

    testWidgets('the downgrade promise is on screen before deciding', (
      tester,
    ) async {
      final harness = _billingHarness();
      await pumpCommerceScreen(tester, const PlansScreen(), harness: harness);
      await tester.drag(find.byType(ListView), const Offset(0, -900));
      await settle(tester);

      // Section 51. A seller weighing whether to keep paying should be able to
      // read that their history is not the thing being held over them.
      expect(find.text('Your records stay yours'), findsOneWidget);
      expect(find.textContaining('never your history'), findsOneWidget);
    });

    testWidgets('a failure to read billing says so rather than showing Free', (
      tester,
    ) async {
      final harness = CommerceHarness()..offline = true;
      await pumpCommerceScreen(tester, const PlansScreen(), harness: harness);

      // The one thing this screen must never do: render a plan it could not
      // read. "Free" would be indistinguishable from a real answer.
      expect(find.text('Starter'), findsNothing);
      expect(find.text('No internet connection'), findsOneWidget);
    });
  });

  group('Devices screen', () {
    testWidgets('marks this device and offers to sign out the others', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/account/devices', <Map<String, dynamic>>[
          _deviceJson(isCurrent: true, model: 'Redmi Note 12'),
          _deviceJson(
            id: '22222222-2222-4222-8222-222222222222',
            model: 'Galaxy A14',
          ),
        ]);
      await pumpCommerceScreen(
        tester,
        const AccountSecurityScreen(),
        harness: harness,
      );

      expect(find.text('This device'), findsOneWidget);
      // Exactly one sign-out button: the current device does not get one, so a
      // seller cannot lock themselves out by tapping the wrong row.
      expect(find.widgetWithText(TextButton, 'Sign out'), findsOneWidget);
      expectNoOverflow(tester);
    });

    testWidgets('signing out every other device is confirmed first', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/account/devices', <Map<String, dynamic>>[
          _deviceJson(isCurrent: true),
        ]);
      await pumpCommerceScreen(
        tester,
        const AccountSecurityScreen(),
        harness: harness,
      );

      await tester.tap(find.text('Sign out every other device'));
      await settle(tester);

      expect(find.text('Sign out every other device?'), findsOneWidget);
      expect(find.textContaining('This phone stays signed in'), findsOneWidget);
    });
  });

  group('Notification settings', () {
    testWidgets('a transport that does not exist is shown as unavailable', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/account/notification-preferences',
          _preferencesJson(),
        );
      await pumpCommerceScreen(
        tester,
        const NotificationSettingsScreen(),
        harness: harness,
      );

      // Better than a switch that silently changes nothing.
      expect(find.text('Not switched on yet in this app'), findsOneWidget);
      expect(
        find.text('Not switched on yet — no SMS provider is connected'),
        findsOneWidget,
      );
    });

    testWidgets('says the centre keeps everything regardless', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/account/notification-preferences',
          _preferencesJson(),
        );
      await pumpCommerceScreen(
        tester,
        const NotificationSettingsScreen(),
        harness: harness,
      );

      // Section 94: push is a second copy. A seller turning it off should know
      // they are not turning off the information.
      expect(
        find.textContaining('always in your notification centre'),
        findsOneWidget,
      );
    });

    testWidgets('routine tracking push is off by default', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/account/notification-preferences',
          _preferencesJson(),
        );
      await pumpCommerceScreen(
        tester,
        const NotificationSettingsScreen(),
        harness: harness,
      );

      expect(find.text('Every tracking update'), findsOneWidget);
      expect(find.textContaining('one push per scan is noise'), findsOneWidget);
    });
  });

  group('Data and privacy', () {
    testWidgets('says what is removed and what is kept', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/account/privacy', _privacyJson())
        ..adapter.onJson('GET', '/exports', <Map<String, dynamic>>[]);
      await pumpCommerceScreen(
        tester,
        const DataPrivacyScreen(),
        harness: harness,
      );
      await tester.drag(find.byType(ListView), const Offset(0, -600));
      await settle(tester);

      // Section 100. Both halves, in the server's own words.
      expect(find.text('Permanently removed'), findsOneWidget);
      expect(find.text('Kept, with names and numbers removed'), findsOneWidget);
      expect(find.textContaining('Customer names'), findsOneWidget);
      expect(find.textContaining('financial ledger'), findsOneWidget);
    });

    testWidgets('closing the account needs the word typed', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/account/privacy', _privacyJson())
        ..adapter.onJson('GET', '/exports', <Map<String, dynamic>>[]);
      await pumpCommerceScreen(
        tester,
        const DataPrivacyScreen(),
        harness: harness,
      );
      await tester.drag(find.byType(ListView), const Offset(0, -900));
      await settle(tester);

      await tester.tap(find.text('Close my account'));
      await settle(tester);

      expect(find.text('Close this shop?'), findsOneWidget);
      // The confirm button stays disabled until CLOSE is typed: this is the one
      // action a seller cannot undo by tapping again.
      final confirm = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Close my account'),
      );
      expect(confirm.onPressed, isNull);
    });

    testWidgets('a scheduled deletion offers to keep the account', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/account/privacy',
          _privacyJson(requested: true, scheduledFor: '2026-09-24T00:00:00Z'),
        )
        ..adapter.onJson('GET', '/exports', <Map<String, dynamic>>[]);
      await pumpCommerceScreen(
        tester,
        const DataPrivacyScreen(),
        harness: harness,
      );
      await tester.drag(find.byType(ListView), const Offset(0, -600));
      await settle(tester);

      expect(find.text('Your account will close'), findsOneWidget);
      expect(find.text('Keep my account'), findsOneWidget);
      expect(find.textContaining('you can change your mind'), findsOneWidget);
    });
  });
}

// --------------------------------------------------------------------------- //
// Fixtures
// --------------------------------------------------------------------------- //

CommerceHarness _billingHarness({
  Map<String, dynamic>? entitlements,
  Map<String, dynamic>? subscription,
  Map<String, dynamic>? channel,
  List<Map<String, dynamic>>? usage,
}) {
  return CommerceHarness()
    ..adapter.onJson(
      'GET',
      '/billing/channel',
      channel ??
          <String, dynamic>{
            'channel': 'DIRECT',
            'can_purchase': false,
            'allows_external_payment_link': true,
            'providers': <Map<String, dynamic>>[],
          },
    )
    ..adapter.onJson('GET', '/billing/plans', <Map<String, dynamic>>[
      _planJson(code: 'free', name: 'Free', paisa: 0, orders: 20),
      _planJson(code: 'starter', name: 'Starter', paisa: 19900),
      _planJson(code: 'pro', name: 'Pro', paisa: 39900, team: 5),
    ])
    ..adapter.onJson(
      'GET',
      '/billing/entitlements',
      entitlements ?? _entitlementsJson(),
    )
    ..adapter.onJson('GET', '/billing/usage', <String, dynamic>{
      'plan': 'starter',
      'usage': usage ?? <Map<String, dynamic>>[_usageJson()],
    })
    ..adapter.onJson(
      'GET',
      '/billing/subscription',
      subscription ?? _subscriptionJson(),
    );
}

Map<String, dynamic> _planJson({
  required String code,
  required String name,
  required int paisa,
  int orders = -1,
  int team = 1,
}) {
  return <String, dynamic>{
    'code': code,
    'name': name,
    'price': <String, dynamic>{'amount_paisa': paisa, 'currency': 'BDT'},
    'entitlements': <String, dynamic>{
      'orders_monthly_limit': orders,
      'courier_account_limit': code == 'free' ? 1 : 3,
      'reconciliation': code != 'free',
      'profit_history_days': code == 'free' ? 1 : -1,
      'bulk_booking': code != 'free',
      'advanced_profit': code == 'pro',
      'csv_export': code != 'free',
      'team_member_limit': team,
    },
  };
}

Map<String, dynamic> _entitlementsJson({
  String plan = 'starter',
  String status = 'ACTIVE',
}) {
  return <String, dynamic>{
    'plan': plan,
    'status': status,
    'source': 'manual_admin',
    'valid_until': '2026-10-10T00:00:00Z',
    'entitlements': <String, dynamic>{
      'orders_monthly_limit': -1,
      'csv_export': true,
    },
  };
}

Map<String, dynamic> _subscriptionJson({
  String plan = 'starter',
  String status = 'ACTIVE',
  String provider = 'play',
  bool cancelAtPeriodEnd = false,
  bool inGrace = false,
  String? graceUntil,
}) {
  return <String, dynamic>{
    'id': '11111111-1111-4111-8111-111111111111',
    'plan': plan,
    'status': status,
    'provider': provider,
    'distribution_channel': 'DIRECT',
    'current_period_end': '2026-10-10T00:00:00Z',
    'trial_end': null,
    'grace_until': graceUntil,
    'cancel_at_period_end': cancelAtPeriodEnd,
    'in_grace': inGrace,
    'status_reason': null,
    'verified_at': '2026-09-10T00:00:00Z',
    'last_synced_at': '2026-09-10T00:00:00Z',
    'provider_reference_suffix': 'ab12',
  };
}

Map<String, dynamic> _usageJson({
  String entitlement = 'orders_monthly_limit',
  int used = 12,
  int limit = 20,
  bool unlimited = false,
}) {
  return <String, dynamic>{
    'entitlement': entitlement,
    'period': 'MONTHLY',
    'period_key': '2026-09',
    'used': used,
    'limit': limit,
    'remaining': unlimited ? null : (limit - used).clamp(0, limit),
    'unlimited': unlimited,
    'resets_at': null,
  };
}

Map<String, dynamic> _deviceJson({
  String id = '11111111-1111-4111-8111-111111111111',
  String model = 'Redmi Note 12',
  bool isCurrent = false,
}) {
  return <String, dynamic>{
    'id': id,
    'platform': 'ANDROID',
    'app_version': '0.1.0',
    'os_version': 'Android 13',
    'model': model,
    'last_seen_at': '2026-09-10T09:00:00Z',
    'created_at': '2026-09-01T09:00:00Z',
    'revoked': false,
    'is_current': isCurrent,
    'push_enabled': false,
  };
}

Map<String, dynamic> _preferencesJson() {
  return <String, dynamic>{
    'push_enabled': true,
    'sms_enabled': false,
    'muted_kinds': <String>[],
    'routine_tracking_push': false,
    'quiet_hours_start': null,
    'quiet_hours_end': null,
    // The shipped state: no transport is configured in this deployment.
    'push_transport_available': false,
    'sms_transport_available': false,
  };
}

Map<String, dynamic> _privacyJson({
  bool requested = false,
  String? scheduledFor,
}) {
  return <String, dynamic>{
    'retention_note':
        'Your customers’ names, phone numbers and addresses are permanently '
        'anonymised. Your money records are kept in anonymised form.',
    'grace_days': 14,
    'deletion_requested': requested,
    'scheduled_for': scheduledFor,
    'anonymised_on_deletion': <String>[
      'Customer names, phone numbers and addresses',
      'Your own name and contact number',
    ],
    'retained_after_deletion': <String>[
      'The financial ledger, in anonymised form',
      'COD receivables and payouts, in anonymised form',
    ],
  };
}
