import 'package:ecomsbd/features/customers/customer_detail_screen.dart';
import 'package:ecomsbd/features/customers/customers_screen.dart';
import 'package:ecomsbd/features/customers/crm_records_screen.dart';
import 'package:ecomsbd/l10n/app_strings_data.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'commerce_harness.dart';

Map<String, dynamic> profile({bool canWrite = true, bool money = true}) => {
  ...customerJson(),
  'can_write': canWrite,
  'can_reveal': false,
  'active_orders': 1,
  'segments': ['REPEAT'],
  'tags': [
    {'id': 'tag1', 'name': 'Wholesale'},
  ],
  'definitions': {
    'new_days': 30,
    'inactive_days': 90,
    'repeat_min_orders': 2,
    'repeat_min_outcomes': 2,
    'rto_min_completed': 10,
    'high_value_min_customers': 5,
  },
  'risk': {
    'state': 'MEDIUM',
    'reasons': ['MIXED_DELIVERY_RATE'],
  },
  'notes': null,
  'next_follow_up_at': '2026-09-20T10:00:00Z',
  'addresses': [
    {'id': 'a1', 'raw_address': 'Mirpur, Dhaka'},
  ],
  'value': money
      ? {
          'total_order_value_paisa': 800000,
          'delivered_revenue_paisa': 500000,
          'measured_profit_paisa': 85000,
          'average_delivered_order_paisa': 250000,
          'measured_parcels': 2,
          'completed_parcels': 3,
        }
      : null,
};

Future<void> scrollTo(WidgetTester tester, Finder target) async {
  for (var n = 0; n < 16 && target.evaluate().isEmpty; n++) {
    await tester.drag(find.byType(ListView).first, const Offset(0, -350));
    await settle(tester, frames: 2);
  }
  await tester.ensureVisible(target.first);
  await settle(tester, frames: 2);
}

void main() {
  testWidgets(
    'CRM profile shows masked facts and links to small detail screens',
    (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/customers/c1/crm', profile())
        ..adapter.onJson('GET', '/customers/c1/timeline', page([]));
      await pumpCommerceScreen(
        tester,
        const CustomerDetailScreen(customerId: 'c1'),
        harness: harness,
      );
      expect(find.text('Nusrat Jahan'), findsOneWidget);
      expect(find.textContaining('01712345678'), findsNothing);
      expect(find.text('Mirpur, Dhaka'), findsOneWidget);
      await scrollTo(tester, find.text('Notes'));
      await tester.tap(find.text('Notes'));
      await settle(tester);
      expect(find.byType(CrmRecordsScreen), findsOneWidget);
      expect(find.text('Add note'), findsOneWidget);
      expectNoOverflow(tester);
    },
  );

  testWidgets('viewer has read-only notes and paginated factual timeline', (
    tester,
  ) async {
    final harness = CommerceHarness()
      ..adapter.on(
        'GET',
        '/customers/c1/timeline',
        (request) => FakeReply({
          'items': [
            {
              'id': request.query['cursor'] == null ? 'e1' : 'e2',
              'kind': 'NOTE',
              'text': request.query['cursor'] == null
                  ? 'First note'
                  : 'Earlier note',
              'created_at': '2026-09-20T10:00:00Z',
              'actor_id': 'member-1',
              'actor_name': 'Rafi',
              'order_id': null,
            },
          ],
          'has_more': request.query['cursor'] == null,
          'next_cursor': request.query['cursor'] == null ? 'opaque.NOTE' : null,
        }),
      );
    await pumpCommerceScreen(
      tester,
      const CrmRecordsScreen(customerId: 'c1', kind: 'notes', canWrite: false),
      harness: harness,
    );
    expect(find.text('Add note'), findsNothing);
    expect(find.textContaining('First note'), findsOneWidget);
    await tester.tap(find.text('Next'));
    await settle(tester);
    expect(
      harness.adapter.to('GET', '/customers/c1/timeline').last.query['cursor'],
      'opaque.NOTE',
    );
    expect(find.textContaining('Earlier note'), findsOneWidget);
    expect(find.textContaining('First note'), findsNothing);
    expectNoOverflow(tester);
  });

  testWidgets('seller notes append through the CRM endpoint', (tester) async {
    final harness = CommerceHarness()
      ..adapter.onJson('GET', '/customers/c1/timeline', page([]))
      ..adapter.onJson('POST', '/customers/c1/notes', {'id': 'note1'});
    await pumpCommerceScreen(
      tester,
      const CrmRecordsScreen(customerId: 'c1', kind: 'notes', canWrite: true),
      harness: harness,
    );
    await tester.tap(find.text('Add note'));
    await settle(tester);
    await tester.enterText(find.byType(TextField), 'Call before dispatch');
    await settle(tester, frames: 2);
    await tester.tap(find.text('Save'));
    await settle(tester);
    expect(harness.adapter.to('POST', '/customers/c1/notes').single.jsonBody, {
      'text': 'Call before dispatch',
    });
    expectNoOverflow(tester);
  });

  testWidgets('follow-up complete and reopen use server state', (tester) async {
    var completed = false;
    final harness = CommerceHarness()
      ..adapter.on(
        'GET',
        '/customers/c1/follow-ups',
        (_) => FakeReply(
          page([
            {
              'id': 'f1',
              'text': 'Confirm bulk order',
              'due_at': '2026-09-20T10:00:00Z',
              'created_by': 'member-1',
              'author_name': 'Rafi',
              'assignee_id': null,
              'assignee_name': null,
              'state': completed ? 'COMPLETED' : 'OVERDUE',
              'completed_at': completed ? '2026-09-21T10:00:00Z' : null,
            },
          ]),
        ),
      )
      ..adapter.on('PATCH', '/customers/c1/follow-ups/f1', (request) {
        completed = request.jsonBody['completed'] as bool;
        return const FakeReply({});
      });
    await pumpCommerceScreen(
      tester,
      const CrmRecordsScreen(
        customerId: 'c1',
        kind: 'follow-ups',
        canWrite: true,
      ),
      harness: harness,
    );
    await tester.tap(find.text('Complete'));
    await settle(tester);
    expect(completed, isTrue);
    await tester.tap(find.text('Reopen'));
    await settle(tester);
    expect(completed, isFalse);
    expectNoOverflow(tester);
  });

  testWidgets('segment filter reaches the server without local filtering', (
    tester,
  ) async {
    final harness = CommerceHarness()
      ..adapter.onJson('GET', '/customers/crm', {
        ...page([customerJson()]),
        'money_locked': 'PERMISSION',
      });
    await pumpCommerceScreen(tester, const CustomersScreen(), harness: harness);
    await tester.tap(find.byType(DropdownButton<String>));
    await settle(tester);
    await tester.tap(find.text('Inactive customers').last);
    await settle(tester);
    expect(
      harness.adapter.to('GET', '/customers/crm').last.query['segment'],
      'INACTIVE',
    );
    expectNoOverflow(tester);
  });

  testWidgets('Bangla CRM profile and financial role lock', (tester) async {
    final harness = CommerceHarness()
      ..adapter.onJson(
        'GET',
        '/customers/c1/crm',
        profile(canWrite: false, money: false),
      );
    await pumpCommerceScreen(
      tester,
      Builder(
        builder: (context) => Localizations.override(
          context: context,
          locale: const Locale('bn'),
          child: const CustomerDetailScreen(customerId: 'c1'),
        ),
      ),
      harness: harness,
    );
    expect(find.text(banglaStrings['crm.customer']!), findsOneWidget);
    await scrollTo(tester, find.text(banglaStrings['crm.value']!));
    await tester.tap(find.text(banglaStrings['crm.value']!));
    await settle(tester);
    await scrollTo(tester, find.text(banglaStrings['crm.locked']!));
    expect(find.text(banglaStrings['crm.locked']!), findsOneWidget);
    expect(find.text(banglaStrings['crm.addTag']!), findsNothing);
    expectNoOverflow(tester);
  });

  test('CRM translations have both languages and identical placeholders', () {
    final keys = englishStrings.keys
        .where((key) => key.startsWith('crm.'))
        .toSet();
    expect(
      banglaStrings.keys.where((key) => key.startsWith('crm.')).toSet(),
      keys,
    );
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
