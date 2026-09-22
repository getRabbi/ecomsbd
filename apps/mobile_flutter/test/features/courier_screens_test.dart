import 'package:ecomsbd/data/commerce/models.dart';
import 'package:ecomsbd/data/couriers/models.dart';
import 'package:ecomsbd/features/money/payouts_screen.dart';
import 'package:ecomsbd/features/orders/bulk_booking_sheet.dart';
import 'package:ecomsbd/features/orders/courier_booking_sheet.dart';
import 'package:ecomsbd/features/orders/courier_status_card.dart';
import 'package:ecomsbd/features/settings/courier_accounts_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'commerce_harness.dart';

/// Courier screens.
///
/// The claims under test are the ones that decide whether a seller ships a
/// second parcel:
///
/// * an unconfirmed booking shows the Bangla "do not book again" copy and
///   offers **no** retry;
/// * a refused booking — where nothing was created — does offer one;
/// * a bulk result shows its three outcomes separately rather than rolled up;
/// * a secret never appears on screen after it is saved;
/// * "we could not check" never renders as "your key is wrong".

const Map<String, dynamic> _connectedAccount = <String, dynamic>{
  'id': 'acct-1',
  'provider': 'steadfast',
  'label': null,
  'status': 'CONNECTED',
  'connected': true,
  'needs_reconnect': false,
  'masked_identifier': '****abcd',
  'last_verified_at': '2026-09-11T06:00:00Z',
  'last_validation_result': 'VALID',
  'last_validation_message': 'Connected to Steadfast.',
  'capabilities': <String, dynamic>{
    'create_single': 'true',
    'create_bulk': 'true',
    'status_lookup': 'true',
    'returns': 'true',
    'payments': 'true',
    'balance': 'true',
  },
  'reported_balance_paisa': 123456,
  'reported_balance_at': '2026-09-11T06:00:00Z',
};

const Map<String, dynamic> _evidence = <String, dynamic>{
  'provider': 'steadfast',
  'documentation_version': 'V1',
  'documentation_source': 'Steadfast API Documentation V1',
  'verified_at': '2026-09-11',
  'capabilities': <String, dynamic>{
    'create_single': 'true',
    'create_bulk': 'true',
    'status_lookup': 'true',
    'returns': 'true',
    'payments': 'true',
    'balance': 'true',
    'webhook': 'unknown',
    'price_quote': 'false',
  },
  'unknowns': <String, dynamic>{
    'WEBHOOK_CONTRACT': 'unknown',
    'PROVIDER_CREATE_IDEMPOTENCY': 'unknown',
  },
  'blockers': <String>['STEADFAST_WEBHOOK_CONTRACT_REQUIRED'],
  'manual_fallback': 'Manual courier mode.',
};

SellerOrder _order({String status = 'PACKED'}) {
  return SellerOrder.fromJson(<String, dynamic>{
    'id': 'order-1',
    'order_number': 'CP-20260911-0042',
    'client_id': 'client-1',
    'customer_name': 'Test Recipient',
    'customer_phone_masked': '01712****78',
    'delivery_address_raw': 'House 12, Road 3, Mirpur 10, Dhaka',
    'status': status,
    'channel': 'MANUAL',
    'business_date': '2026-09-11',
    'subtotal_paisa': 125000,
    'discount_paisa': 0,
    'delivery_fee_paisa': 0,
    'cod_amount_paisa': 125000,
    'version': 1,
    'created_at': '2026-09-11T05:00:00Z',
    'items': const <dynamic>[],
    'fulfillment_state': 'NOT_BOOKED',
    'risk_state': 'NOT_CHECKED',
    'profit_state': 'PENDING',
  });
}

/// The courier list the accounts screen renders from.
///
/// V2.1 made that screen server-driven: it no longer has a hard-coded
/// Steadfast card, so a fixture that does not answer this renders no couriers
/// at all.
const Map<String, dynamic> _steadfastProvider = <String, dynamic>{
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
    'supports_delivery_type': true,
  },
};

/// The couriers the booking sheet may offer.
const List<dynamic> _bookableSteadfast = <dynamic>[
  <String, dynamic>{
    'provider': 'steadfast',
    'display_name': 'Steadfast',
    'bookable': true,
    'reason': null,
    'requires_store': false,
    'store_name': null,
    'supports_delivery_type': true,
  },
];

void _stubCourierLists(CommerceHarness harness) {
  harness.adapter.onJson('GET', '/couriers/providers', <dynamic>[
    _steadfastProvider,
  ]);
  harness.adapter.onJson('GET', '/couriers/bookable', _bookableSteadfast);
}

void main() {
  group('Courier accounts screen', () {
    testWidgets('shows the masked key and never a secret', (tester) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      harness.adapter.onJson('GET', '/couriers/accounts', <dynamic>[
        _connectedAccount,
      ]);
      harness.adapter.onJson(
        'GET',
        '/couriers/providers/steadfast/evidence',
        _evidence,
      );

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: harness,
      );

      expect(find.text('Connected'), findsWidgets);
      expect(find.text('****abcd'), findsOneWidget);
      // There is no widget on this screen that could hold a key, and no
      // response that carries one.
      expect(find.text('sfk-live-0000'), findsNothing);
    });

    testWidgets('says status arrives by polling when there is no webhook', (
      tester,
    ) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      harness.adapter.onJson('GET', '/couriers/accounts', <dynamic>[
        _connectedAccount,
      ]);
      harness.adapter.onJson(
        'GET',
        '/couriers/providers/steadfast/evidence',
        _evidence,
      );

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: harness,
      );

      expect(
        find.textContaining('does not publish a callback'),
        findsOneWidget,
      );
    });

    testWidgets('offers Connect when no account exists', (tester) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      harness.adapter.onJson('GET', '/couriers/accounts', <dynamic>[]);
      harness.adapter.onJson(
        'GET',
        '/couriers/providers/steadfast/evidence',
        _evidence,
      );

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: harness,
      );

      expect(find.text('Connect'), findsOneWidget);
      expect(find.text('Test connection'), findsNothing);
      // Manual mode is presented as a real path, not an apology.
      expect(find.text('Manual courier mode'), findsOneWidget);
    });

    testWidgets('renders needs-reconnect distinctly from disconnected', (
      tester,
    ) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      harness.adapter.onJson('GET', '/couriers/accounts', <dynamic>[
        <String, dynamic>{
          ..._connectedAccount,
          'status': 'NEEDS_RECONNECT',
          'connected': false,
          'needs_reconnect': true,
        },
      ]);
      harness.adapter.onJson(
        'GET',
        '/couriers/providers/steadfast/evidence',
        _evidence,
      );

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: harness,
      );

      expect(find.text('Needs reconnect'), findsOneWidget);
      expect(find.text('Reconnect'), findsOneWidget);
      expect(
        find.textContaining('stopped accepting these details'),
        findsOneWidget,
      );
    });

    testWidgets('shows the courier balance labelled as the courier\'s', (
      tester,
    ) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      harness.adapter.onJson('GET', '/couriers/accounts', <dynamic>[
        _connectedAccount,
      ]);
      harness.adapter.onJson(
        'GET',
        '/couriers/providers/steadfast/evidence',
        _evidence,
      );

      await pumpCommerceScreen(
        tester,
        const CourierAccountsScreen(),
        harness: harness,
      );

      // Named as Steadfast's figure so it can never be read as a COD total.
      expect(find.text('Steadfast reported balance'), findsOneWidget);
      expect(find.text('৳1,234.56'), findsOneWidget);
    });
  });

  group('Booking sheet', () {
    testWidgets('reviews the details the courier will receive', (tester) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      await pumpCommerceScreen(
        tester,
        _SheetHost(child: CourierBookingSheet(order: _order())),
        harness: harness,
      );

      expect(find.text('Book with Steadfast'), findsOneWidget);
      expect(find.text('CP-20260911-0042'), findsOneWidget);
      expect(find.text('Test Recipient'), findsOneWidget);
      // Masked here too: the courier gets the number, the screen does not.
      expect(find.text('01712****78'), findsOneWidget);
      expect(find.text('৳1,250'), findsOneWidget);
    });

    testWidgets('an unconfirmed booking shows the warning and no retry', (
      tester,
    ) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      harness.adapter.on(
        'POST',
        '/couriers/orders/order-1/book',
        (_) => const FakeReply(<String, dynamic>{
          'provider': 'steadfast',
          'batch_id': null,
          'booked': 0,
          'ambiguous': 1,
          'failed': 0,
          'items': <dynamic>[
            <String, dynamic>{
              'order_id': 'order-1',
              'consignment_id': 'c-1',
              'merchant_reference': 'CP-20260911-0042',
              'outcome': 'BOOKING_UNKNOWN',
              'tracking_code': null,
              'error_code': 'BOOKING_AMBIGUOUS',
              'message':
                  'Booking result নিশ্চিত হয়নি — আবার বুক করবেন না। '
                  'আগের চেষ্টা যাচাই করা হচ্ছে।',
            },
          ],
        }),
      );

      await pumpCommerceScreen(
        tester,
        _SheetHost(child: CourierBookingSheet(order: _order())),
        harness: harness,
      );

      await tester.tap(find.text('Confirm booking'));
      await settle(tester, frames: 6, step: const Duration(milliseconds: 50));

      expect(find.text('Booking result uncertain'), findsOneWidget);
      expect(find.textContaining('did not answer in time'), findsOneWidget);
      // The whole safety model in one assertion.
      expect(find.text('Change and try again'), findsNothing);
      expect(find.text('Confirm booking'), findsNothing);
      expect(find.text('Got it'), findsOneWidget);
    });

    testWidgets('a refused booking does offer a retry', (tester) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      harness.adapter.on(
        'POST',
        '/couriers/orders/order-1/book',
        (_) => const FakeReply(<String, dynamic>{
          'provider': 'steadfast',
          'batch_id': null,
          'booked': 0,
          'ambiguous': 0,
          'failed': 1,
          'items': <dynamic>[
            <String, dynamic>{
              'order_id': 'order-1',
              'consignment_id': 'c-1',
              'merchant_reference': 'CP-20260911-0042',
              'outcome': 'NOT_BOOKED',
              'error_code': 'VALIDATION_ERROR',
              'message': 'The courier rejected the address',
            },
          ],
        }),
      );

      await pumpCommerceScreen(
        tester,
        _SheetHost(child: CourierBookingSheet(order: _order())),
        harness: harness,
      );

      await tester.tap(find.text('Confirm booking'));
      await settle(tester, frames: 6, step: const Duration(milliseconds: 50));

      expect(find.text('Not booked'), findsOneWidget);
      // Safe, because the provider answered and refused: nothing was created.
      expect(find.text('Change and try again'), findsOneWidget);
      expect(find.textContaining('safe to fix the details'), findsOneWidget);
    });

    testWidgets('a successful booking shows the tracking code', (tester) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      harness.adapter.on(
        'POST',
        '/couriers/orders/order-1/book',
        (_) => const FakeReply(<String, dynamic>{
          'provider': 'steadfast',
          'batch_id': null,
          'booked': 1,
          'ambiguous': 0,
          'failed': 0,
          'items': <dynamic>[
            <String, dynamic>{
              'order_id': 'order-1',
              'consignment_id': 'c-1',
              'merchant_reference': 'CP-20260911-0042',
              'outcome': 'BOOKED',
              'tracking_code': 'TESTAA01',
            },
          ],
        }),
      );

      await pumpCommerceScreen(
        tester,
        _SheetHost(child: CourierBookingSheet(order: _order())),
        harness: harness,
      );

      await tester.tap(find.text('Confirm booking'));
      await settle(tester, frames: 6, step: const Duration(milliseconds: 50));

      expect(find.text('Booked'), findsOneWidget);
      expect(find.text('TESTAA01'), findsOneWidget);
    });

    testWidgets('one confirm tap sends one booking request', (tester) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      harness.adapter.on(
        'POST',
        '/couriers/orders/order-1/book',
        (_) => const FakeReply(<String, dynamic>{
          'provider': 'steadfast',
          'batch_id': null,
          'booked': 1,
          'ambiguous': 0,
          'failed': 0,
          'items': <dynamic>[
            <String, dynamic>{
              'order_id': 'order-1',
              'consignment_id': 'c-1',
              'merchant_reference': 'CP-20260911-0042',
              'outcome': 'BOOKED',
              'tracking_code': 'TESTAA01',
            },
          ],
        }),
      );

      await pumpCommerceScreen(
        tester,
        _SheetHost(child: CourierBookingSheet(order: _order())),
        harness: harness,
      );

      // Two taps in the same frame — the cheapest way to ship two parcels if
      // the guard is missing.
      await tester.tap(find.text('Confirm booking'), warnIfMissed: false);
      await tester.tap(find.text('Confirm booking'), warnIfMissed: false);
      await settle(tester, frames: 6, step: const Duration(milliseconds: 50));

      expect(
        harness.adapter.to('POST', '/couriers/orders/order-1/book').length,
        1,
      );
    });
  });

  group('Bulk booking sheet', () {
    testWidgets('shows every outcome separately rather than rolled up', (
      tester,
    ) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      harness.adapter.on(
        'POST',
        '/couriers/orders/book-bulk',
        (_) => const FakeReply(<String, dynamic>{
          'provider': 'steadfast',
          'batch_id': 'batch-1',
          'booked': 1,
          'ambiguous': 1,
          'failed': 1,
          'items': <dynamic>[
            <String, dynamic>{
              'order_id': 'order-1',
              'merchant_reference': 'CP-1',
              'outcome': 'BOOKED',
              'tracking_code': 'T1',
            },
            <String, dynamic>{
              'order_id': 'order-2',
              'merchant_reference': 'CP-2',
              'outcome': 'BOOKING_UNKNOWN',
              'message': 'Checking',
            },
            <String, dynamic>{
              'order_id': 'order-3',
              'merchant_reference': 'CP-3',
              'outcome': 'NOT_BOOKED',
              'message': 'Rejected',
            },
          ],
        }),
      );

      await pumpCommerceScreen(
        tester,
        _SheetHost(child: BulkBookingSheet(orders: <SellerOrder>[_order()])),
        harness: harness,
      );

      await tester.tap(find.textContaining('Book 1 orders'));
      await settle(tester, frames: 6, step: const Duration(milliseconds: 50));

      expect(find.text('1 booked'), findsOneWidget);
      expect(find.text('1 checking result'), findsOneWidget);
      expect(find.text('1 failed safely'), findsOneWidget);
      // Partial success is never hidden behind a single summary line.
      expect(find.text('CP-1'), findsOneWidget);
      expect(find.text('CP-2'), findsOneWidget);
      expect(find.text('CP-3'), findsOneWidget);
      expect(find.textContaining('do not book it again'), findsOneWidget);
    });

    testWidgets('holds back orders it can tell will be refused', (
      tester,
    ) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      await pumpCommerceScreen(
        tester,
        _SheetHost(
          child: BulkBookingSheet(
            orders: <SellerOrder>[
              _order(),
              _order(status: 'CANCELLED'),
            ],
          ),
        ),
        harness: harness,
      );

      expect(find.text('1 held back'), findsOneWidget);
      expect(find.textContaining('1 of 2 selected orders'), findsOneWidget);
    });
  });

  group('Courier status card', () {
    testWidgets('shows the courier word next to the plain one', (tester) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      harness.adapter.onJson(
        'GET',
        '/couriers/consignments/c-1/tracking',
        <String, dynamic>{
          'attempts': <dynamic>[],
          'events': <dynamic>[
            <String, dynamic>{
              'kind': 'STATUS',
              'source': 'POLL',
              'raw_status': 'in_review',
              'normalized_status': 'BOOKED',
              'status_undocumented': false,
              'observed_at': '2026-09-11T06:00:00Z',
              'last_seen_at': '2026-09-11T06:00:00Z',
              'observation_count': 3,
            },
          ],
        },
      );

      await pumpCommerceScreen(
        tester,
        const CourierStatusCard(
          consignmentId: 'c-1',
          provider: 'steadfast',
          trackingCode: 'TESTAA01',
          providerRawStatus: 'in_review',
          normalizedStatus: 'BOOKED',
        ),
        harness: harness,
      );

      // Twice, legitimately: once as the parcel's current state on the chip,
      // once as the newest entry in the timeline with when it was checked.
      expect(find.text('With the courier'), findsNWidgets(2));
      // The courier's own word, so a support call can quote it.
      expect(find.text('in review'), findsOneWidget);
      expect(find.text('TESTAA01'), findsOneWidget);
      expect(find.textContaining('checked '), findsOneWidget);
    });

    testWidgets('asks for quantities on a partial delivery', (tester) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      harness.adapter.onJson(
        'GET',
        '/couriers/consignments/c-1/tracking',
        <String, dynamic>{'attempts': <dynamic>[], 'events': <dynamic>[]},
      );

      await pumpCommerceScreen(
        tester,
        const CourierStatusCard(
          consignmentId: 'c-1',
          provider: 'steadfast',
          providerRawStatus: 'partial_delivered',
          normalizedStatus: 'OUT_FOR_DELIVERY',
          needsQuantityResolution: true,
        ),
        harness: harness,
      );

      expect(find.text('Partly delivered — how many arrived?'), findsOneWidget);
      expect(find.textContaining('does not say how much'), findsOneWidget);
    });

    testWidgets('warns, without a retry, on an unresolved booking', (
      tester,
    ) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      harness.adapter.onJson(
        'GET',
        '/couriers/consignments/c-1/tracking',
        <String, dynamic>{
          'attempts': <dynamic>[
            <String, dynamic>{
              'id': 'a-1',
              'attempt_number': 1,
              'state': 'UNKNOWN',
              'merchant_reference': 'CP-20260911-0042',
              'recovery_attempts': 2,
              'started_at': '2026-09-11T06:00:00Z',
            },
          ],
          'events': <dynamic>[],
        },
      );

      await pumpCommerceScreen(
        tester,
        const CourierStatusCard(
          consignmentId: 'c-1',
          provider: 'steadfast',
          normalizedStatus: 'BOOKING_UNKNOWN',
        ),
        harness: harness,
      );

      expect(find.text('Checking this booking with Steadfast'), findsOneWidget);
      expect(
        find.textContaining('Do not book this order again'),
        findsOneWidget,
      );
    });

    testWidgets('shows an undocumented status as the courier\'s own words', (
      tester,
    ) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      harness.adapter.onJson(
        'GET',
        '/couriers/consignments/c-1/tracking',
        <String, dynamic>{
          'attempts': <dynamic>[],
          'events': <dynamic>[
            <String, dynamic>{
              'kind': 'STATUS',
              'source': 'POLL',
              'raw_status': 'beamed_up',
              'normalized_status': null,
              'status_undocumented': true,
              'observed_at': '2026-09-11T06:00:00Z',
              'last_seen_at': '2026-09-11T06:00:00Z',
              'observation_count': 1,
            },
          ],
        },
      );

      await pumpCommerceScreen(
        tester,
        const CourierStatusCard(consignmentId: 'c-1', provider: 'steadfast'),
        harness: harness,
      );

      // No interpretation is offered, because none is available.
      expect(find.text('beamed_up (new to us)'), findsOneWidget);
    });
  });

  group('Return sheet', () {
    testWidgets('needs an explicit confirmation before it can be sent', (
      tester,
    ) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      await pumpCommerceScreen(
        tester,
        const _SheetHost(child: CourierReturnSheet(consignmentId: 'c-1')),
        harness: harness,
      );

      final button = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Request return'),
      );
      expect(button.onPressed, isNull, reason: 'a return costs money');

      await tester.tap(find.byType(Checkbox));
      await tester.pump();

      final enabled = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Request return'),
      );
      expect(enabled.onPressed, isNotNull);
    });

    testWidgets('an unconfirmed return warns against sending another', (
      tester,
    ) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      harness.adapter.on(
        'POST',
        '/couriers/consignments/c-1/return',
        (_) => const FakeReply(<String, dynamic>{
          'id': 'r-1',
          'consignment_id': 'c-1',
          'state': 'UNKNOWN',
          'provider_return_id': null,
          'provider_status': null,
          'message':
              'রিটার্ন রিকোয়েস্টের ফলাফল নিশ্চিত হয়নি — আবার পাঠাবেন না।',
        }),
      );

      await pumpCommerceScreen(
        tester,
        const _SheetHost(child: CourierReturnSheet(consignmentId: 'c-1')),
        harness: harness,
      );

      await tester.tap(find.byType(Checkbox));
      await tester.pump();
      await tester.tap(find.text('Request return'));
      await settle(tester, frames: 6, step: const Duration(milliseconds: 50));

      expect(find.textContaining('আবার পাঠাবেন না'), findsOneWidget);
      expect(find.textContaining('collected twice'), findsOneWidget);
      expect(find.text('Request return'), findsNothing);
    });
  });

  group('Payouts screen', () {
    testWidgets('labels a synced payment as coming from the Steadfast API', (
      tester,
    ) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      harness.adapter.onJson('GET', '/couriers/accounts', <dynamic>[
        _connectedAccount,
      ]);
      harness.adapter.onJson('GET', '/couriers/payments', <dynamic>[
        <String, dynamic>{
          'id': 'pp-1',
          'provider_payment_id': '55001',
          'provider_reference': 'PAY-55001',
          'sync_state': 'IMPORTED',
          'total_paisa': 1845000,
          'paid_at': '2026-09-10T12:00:00Z',
          'consignment_count': 3,
          'payout_id': 'po-1',
          'first_seen_at': '2026-09-11T06:00:00Z',
          'last_seen_at': '2026-09-11T06:00:00Z',
          'error_message': null,
          'observed_fields': <String>['amount', 'id', 'paid_at'],
          'schema_unverified': true,
        },
      ]);
      harness.adapter.onJson('GET', '/money/payouts', <String, dynamic>{
        'items': <dynamic>[],
        'next_cursor': null,
      });

      await pumpCommerceScreen(
        tester,
        PayoutsScreen(pickFile: () async => null),
        harness: harness,
      );

      expect(find.text('From Steadfast'), findsOneWidget);
      expect(find.text('Steadfast API'), findsOneWidget);
      expect(find.text('PAY-55001'), findsOneWidget);
      expect(find.text('3 parcels'), findsOneWidget);
      // Honest about what is inferred.
      expect(
        find.textContaining('does not publish the format'),
        findsOneWidget,
      );
    });

    testWidgets('hides the section entirely when no courier is connected', (
      tester,
    ) async {
      final harness = CommerceHarness();
      _stubCourierLists(harness);
      harness.adapter.onJson('GET', '/couriers/accounts', <dynamic>[]);
      harness.adapter.onJson('GET', '/money/payouts', <String, dynamic>{
        'items': <dynamic>[],
        'next_cursor': null,
      });

      await pumpCommerceScreen(
        tester,
        PayoutsScreen(pickFile: () async => null),
        harness: harness,
      );

      // A disabled button explaining an absent integration is worse than
      // nothing at all.
      expect(find.text('From Steadfast'), findsNothing);
      expect(find.text('Upload statement'), findsOneWidget);
    });
  });

  group('Model safety', () {
    test('an ambiguous outcome never parses as a failure', () {
      expect(BookingOutcome.parse('BOOKING_UNKNOWN'), BookingOutcome.ambiguous);
      expect(BookingOutcome.parse('BOOKING'), BookingOutcome.ambiguous);
      expect(BookingOutcome.parse('BOOKED'), BookingOutcome.booked);
      expect(BookingOutcome.parse('NOT_BOOKED'), BookingOutcome.failed);
    });

    test('an inconclusive credential check is not an invalid one', () {
      expect(
        CredentialCheck.parse('PROVIDER_UNAVAILABLE').isConclusive,
        isFalse,
      );
      expect(CredentialCheck.parse('UNKNOWN').isConclusive, isFalse);
      expect(CredentialCheck.parse('INVALID').isConclusive, isTrue);
      expect(CredentialCheck.parse('VALID').isConclusive, isTrue);
    });

    test('only a connected account may book', () {
      expect(CourierAccountStatus.connected.canBook, isTrue);
      expect(CourierAccountStatus.needsReconnect.canBook, isFalse);
      expect(CourierAccountStatus.disconnected.canBook, isFalse);
      expect(CourierAccountStatus.unknown.canBook, isFalse);
    });

    test('an unconfirmed return blocks another one', () {
      const request = CourierReturnRequest(
        id: 'r-1',
        consignmentId: 'c-1',
        state: 'UNKNOWN',
        message: '',
      );
      expect(request.isAmbiguous, isTrue);
      expect(request.isFinished, isFalse);
    });
  });
}

/// Hosts a sheet body directly, so the sheet's own content can be pumped
/// without driving `showModalBottomSheet` and its route animation.
class _SheetHost extends StatelessWidget {
  const _SheetHost({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(child: child);
  }
}
