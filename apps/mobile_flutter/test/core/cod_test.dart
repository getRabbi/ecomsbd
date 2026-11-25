import 'package:ecomsbd/core/cod.dart';
import 'package:flutter_test/flutter_test.dart';

/// Spec WP0.4 / launch run 13 D1: COD = items + delivery charge unless the
/// seller set it by hand.
void main() {
  test('the total is items plus delivery minus discount, never below zero', () {
    expect(orderTotal(items: 120000, delivery: 6000), 126000);
    expect(orderTotal(items: 120000, delivery: 6000, discount: 1000), 125000);
    expect(orderTotal(items: 1000, discount: 5000), 0);
  });

  test('a COD equal to the total it follows is not set by hand', () {
    expect(codSetByHand(120000, 120000), isFalse);
    expect(codSetByHand(null, 120000), isFalse);
    expect(codSetByHand(150000, 120000), isTrue);
    expect(codSetByHand(65000, null), isTrue, reason: 'nothing to follow');
  });

  test('only a hand-set COD that is not the total is flagged', () {
    expect(codDiffers(byHand: true, typed: 120000, total: 126000), isTrue);
    expect(codDiffers(byHand: true, typed: 126000, total: 126000), isFalse);
    expect(codDiffers(byHand: false, typed: 120000, total: 126000), isFalse);
    expect(codDiffers(byHand: true, typed: 120000, total: null), isFalse);
  });
}
