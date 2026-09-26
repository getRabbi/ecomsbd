import 'package:flutter/material.dart';
import 'package:ecomsbd/data/analytics/models.dart';
import 'package:ecomsbd/features/insights/rto_screen.dart';
import 'package:ecomsbd/features/money/receivables_screen.dart';
import 'package:ecomsbd/features/notifications/notification_centre_screen.dart';
import 'package:ecomsbd/features/orders/order_detail_screen.dart';
import 'package:ecomsbd/features/settings/courier_accounts_screen.dart';
import 'package:ecomsbd/features/settings/notification_settings_screen.dart';
import 'package:flutter_test/flutter_test.dart';

import 'analytics_screens_test.dart' show analyticsHarness, notificationJson;
import 'commerce_harness.dart';

/// V2.2 smart alerts on the phone: categories, resolved state, deep links and
/// the member's own category switches.
void main() {
  group('Smart alerts in the centre', () {
    testWidgets('a resolved alert says so instead of its severity', (
      tester,
    ) async {
      final harness = analyticsHarness()
        ..adapter.onJson(
          'GET',
          '/notifications',
          page(<Map<String, dynamic>>[
            _alert(
              kind: 'HIGH_RTO',
              category: 'RETURNS',
              title: 'RTO 32% in the last 30 days',
              resolvedAt: '2026-09-12T06:00:00Z',
              target: <String, dynamic>{'route': 'returns'},
            ),
          ]),
        );
      await pumpCommerceScreen(
        tester,
        const NotificationCentreScreen(),
        harness: harness,
      );

      expect(find.text('RTO 32% in the last 30 days'), findsOneWidget);
      expect(find.text('Resolved'), findsOneWidget);
      expect(find.text('At risk'), findsNothing);
      expectNoOverflow(tester);
    });

    testWidgets('filtering by category asks the server, in the app language', (
      tester,
    ) async {
      final harness = analyticsHarness()
        ..adapter.onJson(
          'GET',
          '/notifications',
          page(<Map<String, dynamic>>[]),
        );
      await pumpCommerceScreen(
        tester,
        const NotificationCentreScreen(),
        harness: harness,
      );

      // The categories are one horizontally scrolling row.
      await tester.ensureVisible(find.text('Returns & RTO'));
      await tester.tap(find.text('Returns & RTO'));
      await settle(tester);

      final last = harness.adapter.to('GET', '/notifications').last;
      expect(last.query['category'], 'RETURNS');
      expect(last.query['lang'], isNotNull);
    });
  });

  group('Deep links', () {
    test('each target opens the screen it names, unknown ones nothing', () {
      AppNotification alert(String? route, [String? id]) =>
          AppNotification.fromJson(
            _alert(
              target: <String, dynamic>{
                if (route != null) 'route': route,
                if (id != null) 'id': id,
              },
            ),
          );

      expect(
        notificationDestination(alert('receivables')),
        isA<ReceivablesScreen>(),
      );
      expect(notificationDestination(alert('returns')), isA<RtoScreen>());
      expect(
        notificationDestination(alert('courier_account', 'a1')),
        isA<CourierAccountsScreen>(),
      );
      final order = notificationDestination(alert('order', 'o1'));
      expect(order, isA<OrderDetailScreen>());
      expect((order! as OrderDetailScreen).orderId, 'o1');
      expect(notificationDestination(alert('order')), isNull);
      expect(notificationDestination(alert('a_future_screen')), isNull);
      expect(notificationDestination(alert(null)), isNull);
    });
  });

  group('Category switches', () {
    testWidgets('only the categories this role receives, saved as its own', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/account/notification-preferences',
          _preferences(categories: <String>['COURIER', 'RETURNS']),
        )
        ..adapter.onJson(
          'PATCH',
          '/account/notification-preferences',
          _preferences(
            categories: <String>['COURIER', 'RETURNS'],
            muted: <String>['COURIER'],
          ),
        );
      await pumpCommerceScreen(
        tester,
        const NotificationSettingsScreen(),
        harness: harness,
      );

      expect(find.text('Courier'), findsOneWidget);
      expect(find.text('Returns & RTO'), findsOneWidget);
      // Finance-only categories are not offered to this member.
      expect(find.text('Money & payouts'), findsNothing);
      expect(find.textContaining('for you only'), findsOneWidget);

      final courierSwitch = find.descendant(
        of: find.ancestor(of: find.text('Courier'), matching: find.byType(Row)),
        matching: find.byType(Switch),
      );
      await tester.ensureVisible(courierSwitch.first);
      await settle(tester);
      await tester.tap(courierSwitch.first);
      await settle(tester);

      final patch = harness.adapter
          .to('PATCH', '/account/notification-preferences')
          .single;
      expect(patch.jsonBody['muted_categories'], <String>['COURIER']);
    });
  });
}

Map<String, dynamic> _alert({
  String kind = 'PAYOUT_OVERDUE',
  String category = 'MONEY',
  String title = 'Steadfast: ৳8,950 overdue',
  String? resolvedAt,
  Map<String, dynamic>? target,
}) => <String, dynamic>{
  ...notificationJson(kind: kind, severity: 'WARNING', title: title),
  'category': category,
  'state': resolvedAt == null ? 'NEW' : 'RESOLVED',
  'resolved_at': resolvedAt,
  'payload': <String, dynamic>{if (target != null) 'target': target},
};

Map<String, dynamic> _preferences({
  List<String> categories = const <String>[],
  List<String> muted = const <String>[],
}) => <String, dynamic>{
  'push_enabled': true,
  'sms_enabled': false,
  'muted_kinds': <String>[],
  'muted_categories': muted,
  'categories': categories,
  'routine_tracking_push': false,
  'quiet_hours_start': null,
  'quiet_hours_end': null,
  'push_transport_available': true,
  'sms_transport_available': false,
};
