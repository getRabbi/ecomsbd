'use client';

/**
 * Keep what is on screen current while it stays open.
 *
 * The phone writes to the same API, so a table left open here falls behind a
 * sale booked there. Every mounted query is fetched again when the tab comes
 * back into view, and every {@link REVALIDATE_EVERY_MS} while it stays in view.
 * The refresh is silent: no spinner, no `refreshing`, and an answer identical
 * to the one on screen keeps its object, so nothing re-renders.
 *
 * The realtime socket (`./realtime`) adds a faster path on top: when the API
 * says a topic changed, the queries that show it refetch at once. The timer
 * and the focus refresh stay exactly as they are — they are what brings a tab
 * back to the truth after a sleep, a dropped socket or a missed message.
 */
const REVALIDATE_EVENT = 'ecomsbd:revalidate';
export const REVALIDATE_EVERY_MS = 30_000;
//: A focus straight after a load, or a load straight after a focus, is the
//: same moment; one request answers both.
export const MIN_REVALIDATE_GAP_MS = 5_000;

export interface RevalidateRequest {
  /** Only queries showing one of these topics; `null` means every query. */
  topics: ReadonlySet<string> | null;
  /**
   * A change is known to have been committed after the last answer, so the
   * {@link MIN_REVALIDATE_GAP_MS} shortcut does not apply, and a request
   * already in flight is followed by one more.
   */
  fresh: boolean;
}

const ROUTINE: RevalidateRequest = { topics: null, fresh: false };

let revalidationSubscribers = 0;
let revalidationTimer: number | null = null;

function dispatch(request: RevalidateRequest): void {
  window.dispatchEvent(new CustomEvent<RevalidateRequest>(REVALIDATE_EVENT, { detail: request }));
}

function revalidateVisible(): void {
  if (document.visibilityState === 'visible') {
    dispatch(ROUTINE);
  }
}

export function subscribeToRevalidation(
  onRevalidate: (request: RevalidateRequest) => void,
): () => void {
  const listener = (event: Event) => {
    onRevalidate((event as CustomEvent<RevalidateRequest>).detail ?? ROUTINE);
  };
  window.addEventListener(REVALIDATE_EVENT, listener);
  if (revalidationSubscribers++ === 0) {
    window.addEventListener('focus', revalidateVisible);
    document.addEventListener('visibilitychange', revalidateVisible);
    revalidationTimer = window.setInterval(revalidateVisible, REVALIDATE_EVERY_MS);
  }
  return () => {
    window.removeEventListener(REVALIDATE_EVENT, listener);
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

/**
 * The API committed changes to these topics: refetch what shows them.
 *
 * A hidden tab does nothing now; coming back into view refreshes everything.
 */
export function revalidateTopics(topics: ReadonlySet<string>): void {
  if (document.visibilityState === 'visible' && topics.size > 0) {
    dispatch({ topics, fresh: true });
  }
}

/** Changes may have been missed (a reconnect): refetch every query on screen. */
export function revalidateEverything(): void {
  if (document.visibilityState === 'visible') {
    dispatch({ topics: null, fresh: true });
  }
}

/**
 * Queries whose answer depends on only a few topics. Anything not listed
 * refetches on every topic: a dashboard, an order table or a customer page
 * mixes orders, couriers, money and stock, and refetching one panel too many
 * is cheaper than leaving one stale.
 */
const NARROW_QUERIES: ReadonlyArray<readonly [path: string, topics: readonly string[]]> = [
  ['/me', ['team', 'shop']],
  ['/tenant', ['shop']],
  ['/team', ['team']],
  ['/billing', ['shop']],
];

/** Whether a query for `path` shows any of `topics`. */
export function queryShowsTopics(path: string, topics: ReadonlySet<string> | null): boolean {
  if (topics === null) {
    return true;
  }
  const narrow = NARROW_QUERIES.find(
    ([prefix]) => path === prefix || path.startsWith(`${prefix}/`) || path.startsWith(`${prefix}?`),
  );
  return narrow === undefined || narrow[1].some((topic) => topics.has(topic));
}

/** The previous answer when the new one says the same thing, so React keeps it. */
export function keepIfUnchanged<T>(previous: T | null, next: T): T {
  return previous !== null && JSON.stringify(previous) === JSON.stringify(next)
    ? previous
    : next;
}
