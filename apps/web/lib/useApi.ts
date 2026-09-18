'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { api } from './api';

/**
 * Fetch from the API, with the two behaviours a dense dashboard needs.
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
          setData(result);
          setError(null);
          setSettledKey(key);
        }
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted || ticket !== sequence.current) {
          return;
        }
        setError(caught);
        setSettledKey(key);
      });

    return () => controller.abort();
  }, [path, queryKey, nonce]);

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
