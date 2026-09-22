import 'package:ecomsbd/features/money/cases_screen.dart';
import 'package:ecomsbd/design/components/badges.dart';
import 'package:ecomsbd/features/money/cashflow_screen.dart';
import 'package:ecomsbd/features/money/money_screen.dart';
import 'package:ecomsbd/features/money/payouts_screen.dart';
import 'package:ecomsbd/features/money/receivables_screen.dart';
import 'package:ecomsbd/features/money/reconcile_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'commerce_harness.dart';

/// The Phase D money screens, against a fake server.
///
/// The assertions concentrate on the things that would cost a seller money if
/// they were wrong: delivered money shown as arrived, a deduction filed under
/// the wrong name, a match applied without a reason, or a preview that quietly
/// settles something.
void main() {
  group('Money screen', () {
    testWidgets('shows what is owed separately from what arrived', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/money/summary', moneySummaryJson());
      await pumpCommerceScreen(tester, const MoneyScreen(), harness: harness);

      // Master spec section 1.4: delivered is not paid, and the screen never
      // lets those two figures be read as one.
      expect(find.text('৳4,805'), findsWidgets);
      expect(find.textContaining('not paid yet'), findsOneWidget);
      expectNoOverflow(tester);
    });

    testWidgets('an unexplained deduction is named as unexplained', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/money/summary',
          moneySummaryJson(unknownDeduction: 12_000),
        );
      await pumpCommerceScreen(tester, const MoneyScreen(), harness: harness);
      await tester.drag(find.byType(ListView), const Offset(0, -500));
      await settle(tester);

      // Section 84: never folded into "delivery charge".
      expect(find.text('Not explained'), findsOneWidget);
      expect(
        find.textContaining('could not tell what the courier took'),
        findsOneWidget,
      );
    });

    testWidgets('money waiting over a week is called out', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/money/summary',
          moneySummaryJson(
            aging: <Map<String, dynamic>>[
              agingBandJson('0-3 days', 0, 3, 0, 0),
              agingBandJson('4-7 days', 4, 7, 0, 0),
              agingBandJson('8-14 days', 8, 14, 2, 480_500),
              agingBandJson('15+ days', 15, null, 0, 0),
            ],
          ),
        );
      await pumpCommerceScreen(tester, const MoneyScreen(), harness: harness);

      expect(find.textContaining('over a week'), findsOneWidget);
      expect(find.textContaining('8-14 days'), findsOneWidget);
    });

    testWidgets('a clean shop says so rather than showing an empty chart', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/money/summary',
          moneySummaryJson(outstanding: 0, unpaidCount: 0, aging: const []),
        );
      await pumpCommerceScreen(tester, const MoneyScreen(), harness: harness);

      expect(
        find.textContaining('Every delivered parcel has been paid'),
        findsOneWidget,
      );
    });

    testWidgets('open cases are surfaced with a way in', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/money/summary',
          moneySummaryJson(openCases: 3),
        );
      await pumpCommerceScreen(tester, const MoneyScreen(), harness: harness);

      expect(find.text('3 things need you'), findsOneWidget);
      expect(find.text('Open'), findsOneWidget);
    });
  });

  group('Receivables', () {
    testWidgets('a parcel shows what is still owed, not what it was worth', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/money/receivables',
          page(<Map<String, dynamic>>[
            receivableJson(collectible: 140_500, settled: 100_000),
          ]),
        );
      await pumpCommerceScreen(
        tester,
        const ReceivablesScreen(),
        harness: harness,
      );

      expect(find.text('STILL OWED'), findsOneWidget);
      expect(find.text('৳405'), findsOneWidget);
      expect(find.text('of ৳1,405'), findsOneWidget);
      expectNoOverflow(tester);
    });

    testWidgets('a parcel waiting too long is marked', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/money/receivables',
          page(<Map<String, dynamic>>[receivableJson(ageDays: 12)]),
        );
      await pumpCommerceScreen(
        tester,
        const ReceivablesScreen(),
        harness: harness,
      );

      expect(find.text('Waiting 12 days'), findsOneWidget);
    });

    testWidgets('nothing outstanding is good news, not an empty list', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/money/receivables',
          page(<Map<String, dynamic>>[]),
        );
      await pumpCommerceScreen(
        tester,
        const ReceivablesScreen(),
        harness: harness,
      );

      expect(find.text('Nothing outstanding'), findsOneWidget);
    });
  });

  group('Payouts', () {
    testWidgets('a payout that does not explain itself says so', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/payouts',
          page(<Map<String, dynamic>>[
            payoutJson(total: 480_500, applied: 140_500),
          ]),
        );
      await pumpCommerceScreen(tester, const PayoutsScreen(), harness: harness);

      expect(find.text('৳4,805'), findsOneWidget);
      expect(find.text('৳3,400 not yet tied to a parcel'), findsOneWidget);
      expectNoOverflow(tester);
    });

    testWidgets('a statement is previewed before anything is created', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/payouts', page(<Map<String, dynamic>>[]))
        ..adapter.onJson('POST', '/payouts/preview', <String, dynamic>{
          'detected_headers': <String>['Invoice', 'Amount'],
          'column_mapping': <String, String>{
            'merchant_reference': 'Invoice',
            'amount': 'Amount',
          },
          'row_count': 2,
          'invalid_row_count': 1,
          'total_paisa': 140_500,
          'rows': <dynamic>[
            <String, dynamic>{
              'row_number': 1,
              'amount_paisa': 140_500,
              'merchant_reference': 'CP-20260910-0001',
              'errors': <String>[],
            },
            <String, dynamic>{
              'row_number': 2,
              'amount_paisa': null,
              'merchant_reference': 'CP-20260910-0002',
              'errors': <String>["'pending' is not a valid amount"],
            },
          ],
        });

      await pumpCommerceScreen(
        tester,
        PayoutsScreen(
          pickFile: () async =>
              const PickedStatement(name: 'sept.csv', bytes: <int>[1, 2, 3]),
        ),
        harness: harness,
      );

      await tester.tap(find.text('Upload statement'));
      await settle(tester);

      expect(find.text('Before we save this'), findsOneWidget);
      // The bad row is shown with the text the file contained, and with no
      // fabricated zero next to it.
      expect(find.textContaining('is not a valid amount'), findsOneWidget);
      expect(find.text('—'), findsOneWidget);
      // Nothing has been imported while the sheet is open.
      expect(harness.adapter.to('POST', '/payouts/import'), isEmpty);
    });
  });

  group('Reconcile', () {
    testWidgets('preview reports without settling', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/payouts/p1', payoutDetailJson())
        ..adapter.onJson(
          'POST',
          '/reconciliation/payouts/p1/reconcile?shadow=true',
          <String, dynamic>{
            'payout_id': 'p1',
            'shadow': true,
            'exact_matches': 1,
            'suggested': 1,
            'unresolved': 0,
            'applied_paisa': 0,
            'cases_opened': 0,
          },
        );
      await pumpCommerceScreen(
        tester,
        const ReconcileScreen(payoutId: 'p1'),
        harness: harness,
      );

      await tester.tap(find.text('Preview'));
      await settle(tester);

      expect(find.text('Preview only'), findsOneWidget);
      expect(find.text('Nothing was changed.'), findsOneWidget);
      expect(find.text('1 exact · 1 to check · 0 unresolved'), findsOneWidget);
      // The non-shadow endpoint was never called.
      expect(
        harness.adapter.to('POST', '/reconciliation/payouts/p1/reconcile'),
        isEmpty,
      );
    });

    testWidgets('a suggested line shows why, and offers a choice', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/payouts/p1', payoutDetailJson());
      await pumpCommerceScreen(
        tester,
        const ReconcileScreen(payoutId: 'p1'),
        harness: harness,
      );

      expect(find.text('Check this'), findsOneWidget);
      expect(find.text('One parcel could be this'), findsOneWidget);
      // The engine's reasoning, in the seller's words.
      expect(find.textContaining('Same amount'), findsOneWidget);
      expect(find.text('This one'), findsOneWidget);
    });

    testWidgets('a manual match cannot be made without a reason', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/payouts/p1', payoutDetailJson());
      await pumpCommerceScreen(
        tester,
        const ReconcileScreen(payoutId: 'p1'),
        harness: harness,
      );

      await tester.tap(find.text('This one'));
      await settle(tester);

      expect(find.text('Match this payment'), findsOneWidget);
      final button = tester.widget<FilledButton>(
        find.descendant(
          of: find.byType(AlertDialog),
          matching: find.byType(FilledButton),
        ),
      );
      // Section 81.7: a manual match records who and why. No reason, no match.
      expect(button.onPressed, isNull);
      expect(
        harness.adapter.to('POST', '/reconciliation/lines/l2/match'),
        isEmpty,
      );
    });

    testWidgets('an unexplained deduction keeps the courier\'s own words', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/payouts/p1', payoutDetailJson());
      await pumpCommerceScreen(
        tester,
        const ReconcileScreen(payoutId: 'p1'),
        harness: harness,
      );

      expect(find.text('Unexplained deduction'), findsOneWidget);
      expect(find.text('Courier wrote: Adj ref 9931'), findsOneWidget);
    });
  });

  group('Cashflow', () {
    testWidgets('facts and the estimate are shown apart, each labelled', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson('GET', '/money/cashflow', cashflowJson())
        ..adapter.onJson('GET', '/money/couriers', <Map<String, dynamic>>[
          courierBalanceJson(),
        ]);
      await pumpCommerceScreen(
        tester,
        const CashflowScreen(),
        harness: harness,
      );

      expect(find.text('Where your money is'), findsOneWidget);
      expect(find.text('When it may arrive'), findsOneWidget);
      // Facts carry the "actual" marker and the forecast the "estimate" one.
      expect(find.byType(DataQualityBadge), findsNWidgets(2));
      expect(find.text('Later than usual — date unknown'), findsOneWidget);
      expect(find.textContaining('not a promise'), findsOneWidget);
      expectNoOverflow(tester);

      await tester.drag(find.byType(ListView), const Offset(0, -700));
      await settle(tester);
      expect(find.text('Steadfast'), findsOneWidget);
      expect(find.textContaining('overdue across 2 parcels'), findsOneWidget);
      expect(find.textContaining('Over 30 days'), findsOneWidget);
    });

    testWidgets('without payment history the date is not guessed', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/money/cashflow',
          cashflowJson(noHistory: true),
        )
        ..adapter.onJson('GET', '/money/couriers', <Map<String, dynamic>>[]);
      await pumpCommerceScreen(
        tester,
        const CashflowScreen(),
        harness: harness,
      );

      expect(find.text('Not enough history — date unknown'), findsOneWidget);
      expect(find.text('In the next 7 days'), findsNothing);
      expect(find.textContaining('Not enough payment history'), findsOneWidget);
    });
  });

  group('Cases', () {
    testWidgets('a case shows the server\'s own summary', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/reconciliation/cases',
          page(<Map<String, dynamic>>[caseJson()]),
        );
      await pumpCommerceScreen(tester, const CasesScreen(), harness: harness);

      expect(find.text('Delivered, not paid'), findsOneWidget);
      expect(
        find.text(
          'CP-20260910-0001 was delivered 12 days ago and 1405.00 taka has not '
          'arrived',
        ),
        findsOneWidget,
      );
      expectNoOverflow(tester);
    });

    testWidgets('closing a case needs a note', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/reconciliation/cases',
          page(<Map<String, dynamic>>[caseJson()]),
        );
      await pumpCommerceScreen(tester, const CasesScreen(), harness: harness);

      // Scoped to the card: "Sorted" is also a filter chip at the top.
      await tester.tap(
        find.descendant(
          of: find.byType(CaseCard),
          matching: find.text('Sorted'),
        ),
      );
      await settle(tester);

      expect(find.text('What happened?'), findsOneWidget);
      final button = tester.widget<FilledButton>(
        find.descendant(
          of: find.byType(AlertDialog),
          matching: find.byType(FilledButton),
        ),
      );
      expect(button.onPressed, isNull);
      expect(
        harness.adapter.to('PATCH', '/reconciliation/cases/case-1'),
        isEmpty,
      );
    });

    testWidgets('the summary shows expected against actual', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/reconciliation/cases',
          page(<Map<String, dynamic>>[caseJson()]),
        )
        ..adapter.onJson(
          'GET',
          '/reconciliation/summary',
          reconciliationSummaryJson(),
        );
      await pumpCommerceScreen(tester, const CasesScreen(), harness: harness);

      expect(find.text('Courier payments, checked'), findsOneWidget);
      expect(find.text('Should have paid'), findsOneWidget);
      expect(find.text('৳2,810'), findsOneWidget);
      expect(find.text('Discrepancies 1'), findsOneWidget);
      expectNoOverflow(tester);
    });

    testWidgets('a parcel shows expected, actual and accepts its charges', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/reconciliation/cases',
          page(<Map<String, dynamic>>[]),
        )
        ..adapter.onJson(
          'GET',
          '/reconciliation/items',
          page(<Map<String, dynamic>>[reconciliationItemJson()]),
        )
        ..adapter.onJson(
          'POST',
          '/reconciliation/items/item-1/accept-charges',
          <String, dynamic>{
            'accepted_paisa': 8_000,
            'adjustments': 1,
            'items': 1,
          },
        );
      await pumpCommerceScreen(tester, const CasesScreen(), harness: harness);

      await tester.tap(find.text('Parcels'));
      await settle(tester);

      expect(find.text('CP-20260910-0001'), findsOneWidget);
      expect(find.text('Charge differs'), findsOneWidget);
      expect(find.text('Expected'), findsOneWidget);
      expect(find.text('-৳40'), findsOneWidget);
      expectNoOverflow(tester);

      await tester.tap(find.text('Accept charges'));
      await settle(tester);
      // Writing to the ledger is confirmed first, with the amount.
      expect(find.text('Accept courier charges?'), findsOneWidget);
      expect(
        harness.adapter.to(
          'POST',
          '/reconciliation/items/item-1/accept-charges',
        ),
        isEmpty,
      );
      await tester.tap(
        find.descendant(
          of: find.byType(AlertDialog),
          matching: find.text('Accept charges'),
        ),
      );
      await settle(tester);
      expect(
        harness.adapter.to(
          'POST',
          '/reconciliation/items/item-1/accept-charges',
        ),
        hasLength(1),
      );
    });

    testWidgets('a case opens with its history and takes a note', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/reconciliation/cases',
          page(<Map<String, dynamic>>[caseJson()]),
        )
        ..adapter.onJson(
          'GET',
          '/reconciliation/cases/case-1',
          <String, dynamic>{
            ...caseJson(),
            'events': <Map<String, dynamic>>[
              <String, dynamic>{
                'id': 'ev-1',
                'action': 'OPENED',
                'from_status': null,
                'to_status': 'OPEN',
                'note': null,
                'actor_user_id': null,
                'created_at': '2026-09-10T04:00:00Z',
              },
            ],
            'item': null,
          },
        )
        ..adapter.onJson(
          'POST',
          '/reconciliation/cases/case-1/notes',
          <String, dynamic>{
            'id': 'ev-2',
            'action': 'NOTE',
            'from_status': null,
            'to_status': null,
            'note': 'Hub will call back',
            'actor_user_id': 'u1',
            'created_at': '2026-09-11T04:00:00Z',
          },
        );
      await pumpCommerceScreen(tester, const CasesScreen(), harness: harness);

      await tester.tap(find.textContaining('has not arrived'));
      await settle(tester);

      expect(find.text('History'), findsOneWidget);
      expect(find.text('Opened · Automatic'), findsOneWidget);

      await tester.enterText(
        find.descendant(
          of: find.byType(BottomSheet),
          matching: find.byType(TextField),
        ),
        'Hub will call back',
      );
      await tester.pump();
      await tester.tap(find.text('Add note'));
      await settle(tester);

      final notes = harness.adapter.to(
        'POST',
        '/reconciliation/cases/case-1/notes',
      );
      expect(notes, hasLength(1));
      expect(notes.single.body, <String, dynamic>{
        'note': 'Hub will call back',
      });
    });

    testWidgets('an empty case list is good news', (tester) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/reconciliation/cases',
          page(<Map<String, dynamic>>[]),
        );
      await pumpCommerceScreen(tester, const CasesScreen(), harness: harness);

      expect(find.text('Nothing needs you'), findsOneWidget);
    });
  });
}

// --------------------------------------------------------------------------- //
// Fixtures, shaped like the API responses in `backend/app/api/v1`.
// --------------------------------------------------------------------------- //

Map<String, dynamic> receivableJson({
  String id = 'r1',
  int collectible = 140_500,
  int settled = 0,
  int deduction = 0,
  String status = 'ELIGIBLE',
  int? ageDays = 1,
}) => <String, dynamic>{
  'id': id,
  'consignment_id': 'c1',
  'order_id': 'o1',
  'provider': 'manual',
  'status': status,
  'collectible_paisa': collectible,
  'settled_paisa': settled,
  'deduction_paisa': deduction,
  'adjustment_paisa': 0,
  'outstanding_paisa': collectible - settled - deduction,
  'eligible_at': '2026-09-01T10:00:00Z',
  'settled_at': null,
  'status_reason': null,
  'version': 1,
  'created_at': '2026-09-01T10:00:00Z',
  'age_days': ageDays,
  'order_number': 'CP-20260910-0001',
};

Map<String, dynamic> payoutJson({
  String id = 'p1',
  int total = 480_500,
  int applied = 0,
}) => <String, dynamic>{
  'id': id,
  'provider': 'manual',
  'provider_reference': null,
  'source': 'STATEMENT',
  'status': applied == 0 ? 'RECEIVED' : 'PARTIALLY_RECONCILED',
  'total_paisa': total,
  'applied_paisa': applied,
  'unexplained_paisa': total - applied,
  'paid_on': null,
  'received_at': '2026-09-10T04:00:00Z',
  'note': null,
  'source_file_id': 'f1',
  'created_at': '2026-09-10T04:00:00Z',
};

Map<String, dynamic> payoutDetailJson() => <String, dynamic>{
  ...payoutJson(total: 280_500, applied: 140_500),
  'lines': <dynamic>[
    <String, dynamic>{
      'id': 'l1',
      'row_number': 1,
      'amount_paisa': 140_500,
      'applied_paisa': 140_500,
      'status': 'MATCHED',
      'confidence': 'EXACT',
      'provider_consignment_id': null,
      'tracking_code': null,
      'merchant_reference': 'CP-20260910-0001',
      'delivered_on': null,
      'receivable_id': 'r1',
      'match_reason': null,
      'raw': <String, dynamic>{},
      'candidates': <dynamic>[],
    },
    <String, dynamic>{
      'id': 'l2',
      'row_number': 2,
      'amount_paisa': 140_000,
      'applied_paisa': 0,
      'status': 'SUGGESTED',
      'confidence': 'MEDIUM',
      'provider_consignment_id': null,
      'tracking_code': null,
      'merchant_reference': null,
      'delivered_on': null,
      'receivable_id': null,
      'match_reason': null,
      'raw': <String, dynamic>{},
      'candidates': <dynamic>[
        <String, dynamic>{
          'receivable_id': 'r2',
          'consignment_id': 'c2',
          'merchant_reference': 'CP-20260910-0002',
          'outstanding_paisa': 140_000,
          'score': 35,
          'signals': <String>['exact_amount'],
          'rejected_because': null,
        },
      ],
    },
  ],
  'adjustments': <dynamic>[
    <String, dynamic>{
      'id': 'a1',
      'payout_line_id': 'l1',
      'type': 'UNKNOWN_DEDUCTION',
      'amount_paisa': 12_000,
      'provider_label': 'Adj ref 9931',
      'raw_text': 'Adj ref 9931',
      'recognized_rule': null,
    },
  ],
};

Map<String, dynamic> caseJson({
  String id = 'case-1',
  String kind = 'DELIVERED_BUT_UNPAID',
  String status = 'OPEN',
}) => <String, dynamic>{
  'id': id,
  'kind': kind,
  'status': status,
  'priority': 'HIGH',
  'subject_type': 'cod_receivable',
  'subject_id': 'r1',
  'amount_paisa': 140_500,
  'summary':
      'CP-20260910-0001 was delivered 12 days ago and 1405.00 taka has not '
      'arrived',
  'detail': <String, dynamic>{'days_outstanding': 12},
  'receivable_id': 'r1',
  'payout_id': null,
  'payout_line_id': null,
  'consignment_id': 'c1',
  'opened_at': '2026-09-10T04:00:00Z',
  'resolved_at': null,
  'resolution': null,
  'created_at': '2026-09-10T04:00:00Z',
};

Map<String, dynamic> reconciliationSummaryJson() => <String, dynamic>{
  'counts': <String, int>{'MATCHED': 1, 'CHARGE_MISMATCH': 1},
  'matched': 1,
  'discrepancies': 1,
  'unmatched': 0,
  'expected_paisa': 281_000,
  'actual_paisa': 277_000,
  'difference_paisa': -4_000,
  'unmatched_paisa': 0,
  'duplicate_paisa': 0,
  'charges_pending_paisa': 8_000,
  'open_cases': 1,
  'open_case_paisa': 4_000,
};

Map<String, dynamic> reconciliationItemJson() => <String, dynamic>{
  'id': 'item-1',
  'status': 'CHARGE_MISMATCH',
  'provider': 'steadfast',
  'payout_id': 'p1',
  'payout_line_id': 'l1',
  'receivable_id': 'r1',
  'consignment_id': 'c1',
  'case_id': 'case-1',
  'case_status': 'OPEN',
  'merchant_reference': 'CP-20260910-0001',
  'tracking_code': 'TRK1',
  'settlement_date': '2026-09-12',
  'expected_cod_paisa': 140_500,
  'actual_cod_paisa': 140_500,
  'expected_charge_paisa': 6_000,
  'expected_charge_source': 'BOOKED',
  'actual_charge_paisa': 10_000,
  'expected_net_paisa': 134_500,
  'actual_net_paisa': 130_500,
  'difference_paisa': -4_000,
  'charges_pending_paisa': 10_000,
  'line_count': 1,
  'detail': <String, dynamic>{'unknown_deduction': false},
  'evaluated_at': '2026-09-12T04:00:00Z',
  'created_at': '2026-09-12T04:00:00Z',
};

Map<String, dynamic> cashflowJson({bool noHistory = false}) =>
    <String, dynamic>{
      'since': '2026-08-20',
      'until': '2026-09-18',
      'received_paisa': 1_250_000,
      'received_by_courier': <String, int>{'steadfast': 1_250_000},
      'receivable_paisa': 480_500,
      'overdue_paisa': 140_500,
      'overdue_after_days': 7,
      'delivered_unpaid_paisa': 480_500,
      'in_transit_paisa': 90_000,
      'in_transit_count': 3,
      'forecast': <String, dynamic>{
        'quality': 'ESTIMATE',
        'windows': noHistory
            ? <Map<String, dynamic>>[
                <String, dynamic>{
                  'key': 'next_7_days',
                  'parcel_count': 0,
                  'amount_paisa': 0,
                },
                <String, dynamic>{
                  'key': 'no_history',
                  'parcel_count': 3,
                  'amount_paisa': 480_500,
                },
              ]
            : <Map<String, dynamic>>[
                <String, dynamic>{
                  'key': 'next_7_days',
                  'parcel_count': 2,
                  'amount_paisa': 340_000,
                },
                <String, dynamic>{
                  'key': 'past_expected',
                  'parcel_count': 1,
                  'amount_paisa': 140_500,
                },
              ],
      },
      'payout_delays': noHistory
          ? <Map<String, dynamic>>[]
          : <Map<String, dynamic>>[delayJson()],
      'overall_delay': noHistory ? null : delayJson(),
    };

Map<String, dynamic> delayJson() => <String, dynamic>{
  'provider': 'steadfast',
  'samples': 24,
  'average_days': 3.4,
  'median_days': 3,
  'reliable': true,
};

Map<String, dynamic> courierBalanceJson() => <String, dynamic>{
  'provider': 'steadfast',
  'outstanding_paisa': 480_500,
  'parcel_count': 3,
  'delivered_unpaid_count': 3,
  'delivered_unpaid_paisa': 480_500,
  'overdue_count': 2,
  'overdue_paisa': 140_500,
  'oldest_age_days': 34,
  'in_transit_count': 3,
  'in_transit_paisa': 90_000,
  'last_payment_on': '2026-09-15',
  'payout_delay': delayJson(),
  'aging': <Map<String, dynamic>>[
    agingBandJson('0-3 days', 0, 3, 1, 340_000),
    agingBandJson('30+ days', 31, null, 2, 140_500),
  ],
};
