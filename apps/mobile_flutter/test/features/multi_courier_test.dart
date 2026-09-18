import 'package:ecomsbd/data/commerce/models.dart';
import 'package:ecomsbd/data/couriers/models.dart';
import 'package:ecomsbd/features/orders/courier_booking_sheet.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'commerce_harness.dart';

/// Booking with more than one courier.
///
/// The claims under test are the ones that decide whether a seller can ship at
/// all, and whether they ship with the courier they meant:
///
/// * the picker is built from `/couriers/bookable`, so nothing here names a
///   courier and adding one needs no app change;
/// * a courier that cannot take a booking is shown with the reason rather than
///   hidden — hiding it leaves a seller wondering where Pathao went;
/// * the chosen courier is what gets sent;
/// * a provider-specific field appears only for a courier that declares it.
///
/// Copy assertions are in Bangla: the harness pumps a bare `Scaffold` with no
/// `Localizations`, so `AppStrings.of` falls back to Bangla, which is also the
/// app's default. Courier names stay English in both languages.

Map<String, dynamic> _courier(
  String provider,
  String displayName, {
  bool bookable = true,
  String? reason,
  bool requiresStore = false,
  String? storeName,
  bool supportsDeliveryType = false,
}) {
  return <String, dynamic>{
    'provider': provider,
    'display_name': displayName,
    'bookable': bookable,
    'reason': reason,
    'requires_store': requiresStore,
    'store_name': storeName,
    'supports_delivery_type': supportsDeliveryType,
  };
}

SellerOrder _order() {
  return SellerOrder.fromJson(const <String, dynamic>{
    'id': 'order-1',
    'order_number': 'CP-20260918-0042',
    'client_id': 'client-1',
    'customer_name': 'Rahim Uddin',
    'customer_phone_masked': '01712****78',
    'delivery_address_raw': 'House 12, Road 5, Dhanmondi, Dhaka',
    'status': 'PACKED',
    'channel': 'MANUAL',
    'business_date': '2026-09-18',
    'subtotal_paisa': 105000,
    'discount_paisa': 0,
    'delivery_fee_paisa': 0,
    'cod_amount_paisa': 105000,
    'version': 1,
    'created_at': '2026-09-18T05:00:00Z',
    'items': <dynamic>[],
    'fulfillment_state': 'NOT_BOOKED',
    'risk_state': 'NOT_CHECKED',
    'profit_state': 'PENDING',
  });
}

CommerceHarness _harness(List<dynamic> couriers) {
  final harness = CommerceHarness();
  harness.adapter.onJson('GET', '/couriers/bookable', couriers);
  return harness;
}

/// Renders the sheet directly rather than through `showModalBottomSheet`,
/// matching how the V1 booking-sheet tests drive it. The picker is the same
/// widget either way.
Future<void> _openSheet(WidgetTester tester, CommerceHarness harness) async {
  await pumpCommerceScreen(
    tester,
    SingleChildScrollView(child: CourierBookingSheet(order: _order())),
    harness: harness,
  );
  await settle(tester, frames: 8, step: const Duration(milliseconds: 60));
}

void main() {
  group('Courier picker', () {
    testWidgets('lists every courier the server reports', (tester) async {
      final harness = _harness(<dynamic>[
        _courier('steadfast', 'Steadfast', supportsDeliveryType: true),
        _courier('pathao', 'Pathao', storeName: 'Mirpur Warehouse'),
      ]);

      await _openSheet(tester, harness);

      expect(find.text('Steadfast'), findsOneWidget);
      expect(find.text('Pathao'), findsOneWidget);
      expect(find.text('Mirpur Warehouse'), findsOneWidget);
    });

    testWidgets('shows a blocked courier with its reason, not hidden', (
      tester,
    ) async {
      final harness = _harness(<dynamic>[
        _courier('steadfast', 'Steadfast'),
        _courier(
          'pathao',
          'Pathao',
          bookable: false,
          reason: 'NEEDS_PICKUP_STORE',
          requiresStore: true,
        ),
      ]);

      await _openSheet(tester, harness);

      // Hiding it would leave a seller wondering where Pathao went; this tells
      // them what to do about it.
      expect(find.text('Pathao'), findsOneWidget);
      expect(find.text('Choose a pickup store in Settings'), findsOneWidget);
    });

    testWidgets('sends the courier the seller chose', (tester) async {
      final harness = _harness(<dynamic>[
        _courier('steadfast', 'Steadfast', supportsDeliveryType: true),
        _courier('pathao', 'Pathao'),
      ]);
      harness.adapter.onJson(
        'POST',
        '/couriers/orders/order-1/book',
        <String, dynamic>{
          'provider': 'pathao',
          'batch_id': null,
          'booked': 1,
          'ambiguous': 0,
          'failed': 0,
          'items': <dynamic>[
            <String, dynamic>{
              'order_id': 'order-1',
              'consignment_id': '22222222-2222-2222-2222-222222222222',
              'merchant_reference': 'CP-20260918-0042',
              'outcome': 'BOOKED',
              'tracking_code': 'DA200101',
              'error_code': null,
              'message': null,
            },
          ],
        },
      );

      await _openSheet(tester, harness);

      await tester.tap(find.text('Pathao'));
      await tester.pump();
      await tester.tap(find.text('Confirm booking'));
      await settle(tester, frames: 8, step: const Duration(milliseconds: 60));

      final booked = harness.adapter.to(
        'POST',
        '/couriers/orders/order-1/book',
      );
      expect(booked, hasLength(1));
      expect(booked.single.query['provider'], 'pathao');
    });

    testWidgets('offers a delivery type only for a courier that declares it', (
      tester,
    ) async {
      final harness = _harness(<dynamic>[
        _courier('pathao', 'Pathao'),
        _courier('steadfast', 'Steadfast', supportsDeliveryType: true),
      ]);

      await _openSheet(tester, harness);

      // Pathao sorts first and is selected by default; it declares no delivery
      // type, so the toggle is absent rather than sending Steadfast's codes.
      expect(find.text('Home delivery'), findsNothing);

      await tester.tap(find.text('Steadfast'));
      await tester.pump();

      expect(find.text('Home delivery'), findsOneWidget);
    });

    testWidgets('refuses to book when no courier can take it', (tester) async {
      final harness = _harness(<dynamic>[
        _courier(
          'steadfast',
          'Steadfast',
          bookable: false,
          reason: 'NEEDS_RECONNECT',
        ),
      ]);

      await _openSheet(tester, harness);

      final confirm = tester.widget<FilledButton>(
        find.ancestor(
          of: find.text('Confirm booking'),
          matching: find.byType(FilledButton),
        ),
      );
      expect(confirm.onPressed, isNull);
    });

    testWidgets('says so when no courier is connected at all', (tester) async {
      await _openSheet(tester, _harness(<dynamic>[]));

      expect(find.text('No courier connected'), findsOneWidget);
    });
  });

  group('Model safety', () {
    test('an unknown block reason never reads as bookable', () {
      final courier = BookableCourier.fromJson(const <String, dynamic>{
        'provider': 'newcourier',
        'display_name': 'New Courier',
        'bookable': false,
        'reason': 'SOMETHING_ADDED_NEXT_YEAR',
      });

      expect(courier.bookable, isFalse);
      expect(courier.block, BookableBlock.unknown);
      // Not offered as a settings fix, because we do not know that it is one.
      expect(courier.block!.isFixableInSettings, isFalse);
    });

    test('the reasons a seller can fix are marked as such', () {
      for (final reason in <String>[
        'NOT_CONNECTED',
        'NEEDS_RECONNECT',
        'NEEDS_PICKUP_STORE',
      ]) {
        expect(BookableBlock.parse(reason).isFixableInSettings, isTrue, reason: reason);
      }
      for (final reason in <String>['NOT_ENABLED', 'PROVIDER_UNAVAILABLE']) {
        expect(
          BookableBlock.parse(reason).isFixableInSettings,
          isFalse,
          reason: reason,
        );
      }
    });

    test('a missing delivery-type declaration defaults to not offering one', () {
      final courier = BookableCourier.fromJson(const <String, dynamic>{
        'provider': 'x',
        'display_name': 'X',
        'bookable': true,
      });

      // Safer default: sending one courier's service codes to another books
      // the wrong class of delivery.
      expect(courier.supportsDeliveryType, isFalse);
    });
  });
}
