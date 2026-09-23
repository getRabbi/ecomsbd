import 'package:ecomsbd/design/components/navigation.dart';
import 'package:ecomsbd/l10n/app_strings_data.dart';
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
    test('the four destinations are locked and in order', () {
      // The enum name is both the identity and the translation-key suffix
      // (`nav.home`), so locking the names locks the order every seller has
      // learned *and* the keys their labels are read from. The labels
      // themselves are language-dependent and asserted below. Menu is not a
      // destination: it opens from the top bar's hamburger.
      expect(MainDestination.values.map((d) => d.name).toList(), <String>[
        'home',
        'orders',
        'money',
        'insights',
      ]);
    });

    test('every destination has a label in both languages', () {
      for (final destination in MainDestination.values) {
        final key = 'nav.${destination.name}';
        expect(englishStrings[key], isNotNull, reason: 'no English for $key');
        expect(banglaStrings[key], isNotNull, reason: 'no Bangla for $key');
      }
    });
  });
}
