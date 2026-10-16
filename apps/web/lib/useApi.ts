'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { api } from './api';
import {
  keepIfUnchanged,
  MIN_REVALIDATE_GAP_MS,
  queryShowsTopics,
  subscribeToRevalidation,
} from './revalidate';

/**
 * Fetch from the API, with the behaviours a dense dashboard needs.
 *
 * **Stale data stays visible while a refresh runs.** Blanking a forty-row table
 * to a spinner every time a filter changes makes the screen flicker and loses
 * the seller's place. `data` keeps the previous answer and `refreshing` says a
 * newer one is on the way.
 *
 * **A superseded response is discarded.** Type into a search box and four
 * requests are in flight; without this the slowest one wins and the table shows
 * results for a query the seller has already finished changing. Each fetch
 * takes a sequence number and only the newest may write.
 *
 * Pending is *derived*, not stored: the request key is computed during render
 * and compared with the key of the last settled response. That keeps the effect
 * free of synchronous state writes — which React 19 rightly flags, because they
 * cascade renders — and makes "is this in flight?" impossible to get out of
 * step with which request actually is.
 *
 * **What is on screen stays current.** Whatever is mounted is fetched again,
 * silently, when the tab comes back into view and every thirty seconds while
 * it stays there; `./revalidate` decides when. A realtime invalidation for a
 * topic this query shows does the same at once, and is never skipped: if a
 * request is already in flight, it may have been answered before the change,
 * so one more follows it.
 */
export interface AsyncState<T> {
  data: T | null;
  /** First load, with nothing to show yet. */
  loading: boolean;
  /** A reload while previous data is still on screen. */
  refreshing: boolean;
  error: unknown;
  reload: () => void;
}

export function useApi<T>(
  path: string | null,
  query?: Record<string, string | number | boolean | null | undefined>,
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);
  const [settledKey, setSettledKey] = useState<string | null>(null);

  const sequence = useRef(0);
  //: The ticket of the last request that answered, and when. Equal to
  //: `sequence` exactly when nothing is in flight.
  const settledTicket = useRef(0);
  const settledAt = useRef(0);
  //: Runs once a request in flight lands, when a change was announced while
  //: it was out.
  const followUp = useRef<(() => void) | null>(null);
  // Serialised so the effect compares by value: a fresh object literal on every
  // render would otherwise refetch forever.
  const queryKey = JSON.stringify(query ?? {});
  const requestKey = path === null ? null : `${path}|${queryKey}|${nonce}`;

  useEffect(() => {
    if (path === null) {
      return;
    }

    const key = `${path}|${queryKey}|${nonce}`;
    const ticket = ++sequence.current;
    const controller = new AbortController();

    api
      .get<T>(path, JSON.parse(queryKey), controller.signal)
      .then((result) => {
        if (ticket === sequence.current) {
          settledTicket.current = ticket;
          settledAt.current = Date.now();
          setData(result);
          setError(null);
          setSettledKey(key);
          followUp.current?.();
        }
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted || ticket !== sequence.current) {
          return;
        }
        settledTicket.current = ticket;
        settledAt.current = Date.now();
        setError(caught);
        setSettledKey(key);
        followUp.current?.();
      });

    return () => controller.abort();
  }, [path, queryKey, nonce]);

  useEffect(() => {
    if (path === null) {
      return;
    }

    let controller: AbortController | null = null;
    let changedSince = false;
    let disposed = false;

    const refetch = () => {
      const ticket = sequence.current;
      const current = new AbortController();
      controller = current;
      changedSince = false;
      api
        .get<T>(path, JSON.parse(queryKey), current.signal)
        .then((result) => {
          // A filter changed or a reload started meanwhile: that answer wins.
          if (ticket === sequence.current) {
            settledAt.current = Date.now();
            setData((previous) => keepIfUnchanged(previous, result));
            setError(null);
          }
        })
        .catch(() => {
          // The answer on screen stays. The next refresh, or the seller's own
          // action, tries again and reports a failure that persists.
        })
        .finally(() => {
          if (controller === current) {
            controller = null;
            if (changedSince && !disposed) {
              refetch();
            }
          }
        });
    };

    followUp.current = () => {
      if (changedSince && controller === null) {
        refetch();
      }
    };

    const unsubscribe = subscribeToRevalidation((request) => {
      if (!queryShowsTopics(path, request.topics)) {
        return;
      }
      // Only over a settled answer: a request already in flight is newer than
      // anything this would fetch, and so is one that has just landed —
      // unless a change was committed since, which the request may predate.
      if (controller !== null || settledTicket.current !== sequence.current) {
        if (request.fresh) {
          changedSince = true;
        }
        return;
      }
      if (!request.fresh && Date.now() - settledAt.current < MIN_REVALIDATE_GAP_MS) {
        return;
      }
      refetch();
    });

    return () => {
      disposed = true;
      unsubscribe();
      followUp.current = null;
      controller?.abort();
    };
  }, [path, queryKey]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  const pending = requestKey !== null && requestKey !== settledKey;

  return {
    data,
    loading: pending && data === null,
    refreshing: pending && data !== null,
    error,
    reload,
  };
}

/**
 * Debounce a value, for search boxes.
 *
 * A request per keystroke is a request per keystroke against a metered API.
 */
export function useDebounced<T>(value: T, delay = 300): T {
  const [settled, setSettled] = useState(value);

  useEffect(() => {
    // Inside a timer, so nothing is written synchronously during the effect.
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);

  return settled;
}
