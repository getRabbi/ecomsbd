import 'package:ecomsbd/core/money.dart';
import 'package:ecomsbd/data/channels/channel_models.dart';
import 'package:ecomsbd/data/commerce/list_controllers.dart';
import 'package:ecomsbd/data/couriers/courier_compare.dart';
import 'package:flutter_test/flutter_test.dart';

/// The seller command centre's own logic: courier cost per successful
/// delivery, channel state from the integrations hub, and the order groups.
void main() {
  group('Courier cost per successful delivery', () {
    CourierRateEstimate estimate({int? rtoBps, bool sufficient = true}) =>
        CourierRateEstimate(
          provider: 'steadfast',
          displayName: 'Steadfast',
          origin: RateOrigin.sample,
          deliveryFee: const Money(7000),
          codFee: const Money(1000),
          returnFee: const Money(5000),
          rtoRateBps: rtoBps,
          rtoSampleSufficient: sufficient,
        );

    test('pays for returns out of the successful deliveries', () {
      // 10% RTO: 0.9 × ৳80 + 0.1 × ৳120 = ৳84 per shipment, over 0.9
      // successes = ৳93.33.
      expect(estimate(rtoBps: 1000).costPerSuccess, const Money(9333));
      expect(estimate(rtoBps: 1000).total, const Money(8000));
    });

    test('is not shown without a sufficient return-rate sample', () {
      expect(estimate().costPerSuccess, isNull);
      expect(estimate(rtoBps: 1000, sufficient: false).costPerSuccess, isNull);
    });
  });

  group('Channel state', () {
    test('is connected only when the hub has a healthy connection', () {
      final statuses = channelStatusesFromHub(<String, dynamic>{
        'providers': <Map<String, dynamic>>[
          <String, dynamic>{'provider': 'MESSENGER', 'available': true},
          <String, dynamic>{'provider': 'WHATSAPP', 'available': true},
          <String, dynamic>{'provider': 'CUSTOM_WEBSITE', 'available': false},
        ],
        'items': <Map<String, dynamic>>[
          <String, dynamic>{
            'provider': 'MESSENGER',
            'health': 'DEGRADED',
            'name': 'QA Page',
          },
        ],
      });
      ChannelHealth of(SalesChannel c) =>
          statuses.firstWhere((s) => s.channel == c).health;

      expect(of(SalesChannel.facebook), ChannelHealth.needsAttention);
      expect(of(SalesChannel.whatsapp), ChannelHealth.notConnected);
      expect(of(SalesChannel.website), ChannelHealth.unavailable);
      expect(of(SalesChannel.instagram), ChannelHealth.unavailable);
    });
  });

  test('order groups reuse the server statuses unchanged', () {
    expect(OrderFilterGroup.all.statuses, isEmpty);
    expect(OrderFilterGroup.confirmed.statuses, <String>{
      'CONFIRMED',
      'PACKED',
    });
    expect(OrderFilterGroup.delivered.statuses, <String>{'COMPLETED'});
  });
}
