import 'package:ecomsbd/design/components/navigation.dart';
import 'package:flutter_test/flutter_test.dart';

/// Screen-level tests that belong to no single feature.
///
/// Every screen in the app now reads real data, so its tests live beside the
/// fake server that supplies them:
///
/// * Orders, Products, Customers and Imports — `commerce_screens_test.dart`
///   (Phase B);
/// * Money, receivables, payouts, reconciliation and cases —
///   `money_screens_test.dart` (Phase D);
/// * Home, Insights, expenses and the notification centre —
///   `analytics_screens_test.dart` (Phase E).
///
/// What is left here is the one assertion that is about the app rather than a
/// screen: the navigation is locked.
void main() {
  group('navigation labels', () {
    test('the five destinations are locked and in order', () {
      expect(MainDestination.values.map((d) => d.label).toList(), <String>[
        'Home',
        'Orders',
        'Money',
        'Insights',
        'Menu',
      ]);
    });
  });
}
