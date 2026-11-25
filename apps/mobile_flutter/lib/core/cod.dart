/// COD follows the order total (spec WP0.4, launch run 13 D1).
///
/// The total is items + delivery charge − discount, recomputed on every
/// change. COD is that total unless the seller set it by hand; a COD set by
/// hand is kept, and the form warns while it differs from the total. A COD
/// that arrives equal to the total it would follow (the chat's amount was the
/// item price, or a draft's calculated COD) is not the seller's own figure,
/// so it follows. The web has the same rule in `apps/web/lib/cod.ts`.
library;

/// Whether a COD that came with the form was set on purpose.
///
/// It was, unless it equals [total]. With no total to follow (`null`), a
/// stated COD is kept.
bool codSetByHand(int? cod, int? total) {
  if (cod == null) return false;
  if (total == null) return true;
  return cod != total;
}

/// Whether to warn: a COD set by hand that is not the order total.
bool codDiffers({
  required bool byHand,
  required int? typed,
  required int? total,
}) => byHand && typed != null && total != null && typed != total;

/// Items + delivery − discount, never below zero.
int orderTotal({required int items, int delivery = 0, int discount = 0}) {
  final total = items + delivery - discount;
  return total < 0 ? 0 : total;
}
