/**
 * COD follows the order total (spec WP0.4, launch run 13 D1).
 *
 * The total is items + delivery charge − discount, recomputed on every change.
 * COD is that total unless the seller set it by hand; a COD set by hand is kept
 * and the composer warns while it differs from the total. A COD that arrives
 * equal to the items total (the chat's amount was the item price, or the
 * saved order followed its total) carries nothing of its own, so it follows.
 *
 * Plain functions with no imports, so `node --test` can check them directly.
 */

export interface TotalLine {
  /** Integer paisa; `null` when the line has no price the client knows. */
  unitPricePaisa: number | null;
  quantity: number | null;
}

/** Items + delivery − discount, never below zero; `null` when any part is unknown. */
export function orderTotalPaisa(
  lines: readonly TotalLine[],
  deliveryPaisa: number | null,
  discountPaisa: number | null,
): number | null {
  if (lines.length === 0 || deliveryPaisa === null || discountPaisa === null) return null;
  let items = 0;
  for (const line of lines) {
    const { unitPricePaisa: price, quantity } = line;
    if (price === null || !Number.isInteger(price) || price < 0) return null;
    if (quantity === null || !Number.isInteger(quantity) || quantity < 1) return null;
    items += price * quantity;
  }
  return Math.max(0, items + deliveryPaisa - discountPaisa);
}

/**
 * Whether a COD that came with the form (from a chat or a saved order) was set
 * on purpose. It was unless it equals the total it would otherwise follow;
 * with no computable total there is nothing to follow, so it is kept.
 */
export function codSetByHand(cod: number | null | undefined, total: number | null): boolean {
  if (cod === null || cod === undefined) return false;
  if (total === null) return true;
  return cod !== total;
}

/** The COD the courier will collect: the typed amount, or else the total. */
export function effectiveCod(byHand: boolean, typedPaisa: number | null, total: number | null): number | null {
  return byHand ? typedPaisa : total;
}

/** Warn when a COD set by hand is not the order total. */
export function codDiffers(byHand: boolean, typedPaisa: number | null, total: number | null): boolean {
  return byHand && typedPaisa !== null && total !== null && typedPaisa !== total;
}
