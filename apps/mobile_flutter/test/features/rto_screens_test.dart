import 'package:ecomsbd/data/commerce/risk_repository.dart';
import 'package:ecomsbd/features/insights/rto_screen.dart';
import 'package:ecomsbd/l10n/app_strings_data.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../helpers.dart';
import 'commerce_harness.dart';

Map<String, dynamic> _counts({
  int delivered = 0,
  int returned = 0,
  int courierCancelled = 0,
  bool? sufficient,
}) {
  final rto = returned + courierCancelled;
  final completed = delivered + rto;
  return <String, dynamic>{
    'completed': completed,
    'delivered': delivered,
    'partial': 0,
    'rto': rto,
    'returned': returned,
    'courier_cancelled': courierCancelled,
    'lost': 0,
    'rto_rate_basis_points': completed == 0
        ? null
        : (rto * 10000 / completed).round(),
    'success_rate_basis_points': completed == 0
        ? null
        : (delivered * 10000 / completed).round(),
    'sufficient': sufficient ?? completed >= 10,
  };
}

CommerceHarness _harness({bool productsHaveMore = false}) {
  return CommerceHarness()
    ..adapter.onJson('GET', '/analytics/rto/summary', <String, dynamic>{
      'days': 30,
      'counts': _counts(delivered: 9, returned: 2, courierCancelled: 1),
      'open_now': 4,
      'cancelled_before_dispatch': 3,
      'windows': <Map<String, dynamic>>[
        <String, dynamic>{
          'days': 7,
          'counts': _counts(delivered: 2, returned: 1),
        },
        <String, dynamic>{
          'days': 30,
          'counts': _counts(delivered: 9, returned: 2, courierCancelled: 1),
        },
        <String, dynamic>{
          'days': 90,
          'counts': _counts(delivered: 30, returned: 6),
        },
      ],
      'definition': <String, dynamic>{'min_sample': 10},
    })
    ..adapter.onJson('GET', '/analytics/rto/trend', <String, dynamic>{
      'points': <Map<String, dynamic>>[
        for (var i = 0; i < 12; i++)
          <String, dynamic>{
            'week_start': '2026-07-0${1 + i % 9}',
            'counts': i == 11 ? _counts(delivered: 3, returned: 1) : _counts(),
          },
      ],
    })
    ..adapter.onJson('GET', '/analytics/rto/couriers', <String, dynamic>{
      'items': <Map<String, dynamic>>[
        <String, dynamic>{
          'provider': 'steadfast',
          'counts': _counts(delivered: 3, courierCancelled: 1),
          'recent': _counts(delivered: 3, courierCancelled: 1),
          'previous': _counts(),
          'trend': 'INSUFFICIENT_DATA',
          'in_transit_now': 2,
        },
      ],
      'excluded_providers': <String>['redx'],
    })
    ..adapter.onJson('GET', '/analytics/rto/products', <String, dynamic>{
      'items': <Map<String, dynamic>>[
        <String, dynamic>{
          'product_id': 'p1',
          'product_name': 'Cotton Abaya',
          'counts': _counts(delivered: 1, returned: 1),
          'rto_value_paisa': 125000,
        },
      ],
      'total': productsHaveMore ? 21 : 1,
      'offset': 0,
      'has_more': productsHaveMore,
    })
    ..adapter.onJson('GET', '/analytics/rto/areas', <String, dynamic>{
      'status': 'DATA_NOT_RELIABLE',
      'coverage_basis_points': 2500,
      'items': <Map<String, dynamic>>[],
    })
    ..adapter.onJson('GET', '/analytics/rto/patterns', <String, dynamic>{
      'items': <Map<String, dynamic>>[
        <String, dynamic>{
          'customer_id': 'c1',
          'name': 'Nusrat',
          'phone_masked': '01712****33',
          'counts': _counts(delivered: 1, returned: 2),
          'observations': <Map<String, dynamic>>[
            <String, dynamic>{
              'code': 'REPEAT_RTO',
              'parcel_count': 3,
              'rto_count': 2,
              'delivered_count': 1,
              'earlier_parcel_count': 0,
              'earlier_rto_count': 0,
              'product_name': null,
            },
          ],
        },
      ],
    })
    ..adapter.onJson('GET', '/analytics/rto/customers/c1', <String, dynamic>{
      'customer_id': 'c1',
      'name': 'Nusrat',
      'phone_masked': '01712****33',
      'order_count': 4,
      'counts': _counts(delivered: 1, returned: 2),
      'in_transit_count': 0,
      'cancelled_before_dispatch': 1,
      'recent': <Map<String, dynamic>>[
        <String, dynamic>{
          'order_number': 'CP-20260901-0007',
          'outcome': 'RTO',
          'status': 'RETURNED',
          'provider': 'steadfast',
          'at': '2026-09-01T10:00:00Z',
        },
      ],
      'observations': <Map<String, dynamic>>[
        <String, dynamic>{
          'code': 'PRODUCT_REPEAT_RTO',
          'parcel_count': 2,
          'rto_count': 2,
          'delivered_count': 0,
          'earlier_parcel_count': 0,
          'earlier_rto_count': 0,
          'product_name': 'Cotton Abaya',
        },
      ],
      'risk_state': 'MEDIUM',
      'risk_reasons': <String>['MIXED_DELIVERY_RATE'],
    });
}

Future<void> _scrollTo(WidgetTester tester, Finder finder) async {
  await tester.scrollUntilVisible(
    finder,
    300,
    scrollable: find.byType(Scrollable).first,
  );
  await settle(tester);
}

void main() {
  group('Returns & RTO', () {
    testWidgets('the rate is stated with the counts it came from', (
      tester,
    ) async {
      await pumpCommerceScreen(tester, const RtoScreen(), harness: _harness());

      // The headline and the matching 30-day window tile.
      expect(find.text('25%'), findsNWidgets(2));
      expect(find.text('3 of 12'), findsOneWidget);
      expect(find.text('3 of 12 completed parcels came back'), findsOneWidget);
      // Cancelled before dispatch is reported, separately from RTO.
      expect(find.text('Cancelled before dispatch'), findsOneWidget);
      expect(find.textContaining('RTO = parcels returned'), findsOneWidget);
      expectNoOverflow(tester);
    });

    testWidgets('thin samples are labelled, not ranked; areas stay off', (
      tester,
    ) async {
      await pumpCommerceScreen(tester, const RtoScreen(), harness: _harness());

      await _scrollTo(tester, find.text('Cotton Abaya'));
      // Two parcels: a "limited data" chip rather than a 50% rate.
      expect(find.text('Limited data'), findsWidgets);
      expect(find.text('50%'), findsNothing);

      await _scrollTo(
        tester,
        find.textContaining('District comparison is off'),
      );
      expect(find.textContaining('only 25%'), findsOneWidget);
      expect(find.textContaining('RedX: no live integration'), findsOneWidget);
    });

    testWidgets('a repeat pattern is an observation with its counts', (
      tester,
    ) async {
      await pumpCommerceScreen(tester, const RtoScreen(), harness: _harness());

      await _scrollTo(tester, find.text('Nusrat'));
      expect(
        find.textContaining('2 of 3 completed parcels came back'),
        findsOneWidget,
      );

      await tester.tap(find.text('Nusrat'));
      await settle(tester);
      expect(find.text('Parcel history'), findsOneWidget);
      expect(
        find.textContaining('Cotton Abaya: 2 parcels came back'),
        findsOneWidget,
      );
      expect(find.text('Came back'), findsOneWidget);
    });

    testWidgets('more products are asked of the server, not held locally', (
      tester,
    ) async {
      final harness = _harness(productsHaveMore: true);
      await pumpCommerceScreen(tester, const RtoScreen(), harness: harness);

      await _scrollTo(tester, find.text('Load more'));
      await tester.tap(find.text('Load more'));
      await settle(tester);

      final pages = harness.adapter.to('GET', '/analytics/rto/products');
      expect(pages.last.query['offset'], 1);
      expect(pages.last.query['limit'], 20);
    });
  });

  group('Risk check parcels', () {
    test('parses the parcel view and observations', () {
      final result = RiskCheck.fromJson(<String, dynamic>{
        'found': true,
        'state': 'MEDIUM',
        'order_count': 6,
        'delivered_count': 4,
        'returned_count': 1,
        'cancelled_count': 1,
        'terminal_count': 6,
        'reasons': <String>['MIXED_DELIVERY_RATE'],
        'parcels': _counts(delivered: 4, returned: 1),
        'in_transit_count': 2,
        'recent': <Map<String, dynamic>>[],
        'observations': <Map<String, dynamic>>[
          <String, dynamic>{
            'code': 'RTO_AFTER_DELIVERIES',
            'parcel_count': 1,
            'rto_count': 1,
            'earlier_parcel_count': 4,
            'earlier_rto_count': 0,
          },
        ],
      });

      expect(result.parcels!.rateLabel, '20%');
      expect(result.inTransitCount, 2);
      expect(result.observations.single.vars['earlierDelivered'], 4);
    });

    test('an unknown number has no parcel view', () {
      final result = RiskCheck.fromJson(<String, dynamic>{
        'found': false,
        'state': 'INSUFFICIENT_DATA',
        'parcels': null,
      });
      expect(result.parcels, isNull);
      expect(result.observations, isEmpty);
    });
  });

  test('every RTO string exists in both languages with the same slots', () {
    final placeholder = RegExp(r'\{(\w+)\}');
    final keys = englishStrings.keys.where(
      (key) => key.startsWith('rto.') || key.startsWith('rc.parcels'),
    );
    expect(keys.length, greaterThan(50));
    for (final key in keys) {
      final bn = banglaStrings[key];
      expect(bn, isNotNull, reason: 'no Bangla for $key');
      Set<String> slots(String text) =>
          placeholder.allMatches(text).map((m) => m.group(1)!).toSet();
      expect(slots(bn!), slots(englishStrings[key]!), reason: key);
    }
  });
}
