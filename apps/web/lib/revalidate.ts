'use client';

/**
 * Keep what is on screen current while it stays open.
 *
 * The phone writes to the same API, so a table left open here falls behind a
 * sale booked there. Every mounted query is fetched again when the tab comes
 * back into view, and every {@link REVALIDATE_EVERY_MS} while it stays in view.
 * The refresh is silent: no spinner, no `refreshing`, and an answer identical
 * to the one on screen keeps its object, so nothing re-renders.
 */
const REVALIDATE_EVENT = 'ecomsbd:revalidate';
export const REVALIDATE_EVERY_MS = 30_000;
//: A focus straight after a load, or a load straight after a focus, is the
//: same moment; one request answers both.
export const MIN_REVALIDATE_GAP_MS = 5_000;

let revalidationSubscribers = 0;
let revalidationTimer: number | null = null;

function revalidateVisible(): void {
  if (document.visibilityState === 'visible') {
    window.dispatchEvent(new Event(REVALIDATE_EVENT));
  }
}

export function subscribeToRevalidation(onRevalidate: () => void): () => void {
  window.addEventListener(REVALIDATE_EVENT, onRevalidate);
  if (revalidationSubscribers++ === 0) {
    window.addEventListener('focus', revalidateVisible);
    document.addEventListener('visibilitychange', revalidateVisible);
    revalidationTimer = window.setInterval(revalidateVisible, REVALIDATE_EVERY_MS);
  }
  return () => {
    window.removeEventListener(REVALIDATE_EVENT, onRevalidate);
    if (--revalidationSubscribers === 0) {
      window.removeEventListener('focus', revalidateVisible);
      document.removeEventListener('visibilitychange', revalidateVisible);
      if (revalidationTimer !== null) {
        window.clearInterval(revalidationTimer);
        revalidationTimer = null;
      }
    }
  };
}

/** The previous answer when the new one says the same thing, so React keeps it. */
export function keepIfUnchanged<T>(previous: T | null, next: T): T {
  return previous !== null && JSON.stringify(previous) === JSON.stringify(next)
    ? previous
    : next;
}
