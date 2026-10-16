'use client';

import { useSearchParams } from 'next/navigation';
import { useCallback } from 'react';

/**
 * A single value kept in the address bar — which order's drawer is open, say.
 *
 * In the URL rather than component state so a search result, a notification or
 * a pasted link can open the exact record, and a refresh keeps it open. Written
 * with `history.replaceState`, which Next keeps in step with `useSearchParams`:
 * no navigation, no refetch, no history entry per row click, and the page's
 * filters and paging are left exactly as they were.
 */
export function useSearchParamState(name: string): [string | null, (value: string | null) => void] {
  const value = useSearchParams().get(name);

  const set = useCallback(
    (next: string | null) => {
      const params = new URLSearchParams(window.location.search);
      if (next === null) {
        params.delete(name);
      } else {
        params.set(name, next);
      }
      const query = params.toString();
      window.history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}`);
    },
    [name],
  );

  return [value, set];
}
