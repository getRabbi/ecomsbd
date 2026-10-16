'use client';

import { useCallback, useSyncExternalStore } from 'react';

/**
 * Per-browser conveniences — a collapsed sidebar, the last shop worked in.
 *
 * Nothing here is business state or sensitive: if storage is blocked or
 * cleared, the workstation simply starts from its defaults. Same pattern as the
 * locale in `session.tsx`: storage is the state, read through
 * `useSyncExternalStore`, with an in-memory copy for when storage is refused.
 */

const PREFS_EVENT = 'ecomsbd:prefs';
const memory = new Map<string, string>();

export function readPref(key: string): string | null {
  try {
    const stored = window.localStorage.getItem(key);
    if (stored !== null) {
      return stored;
    }
  } catch {
    // Private mode or storage disabled; the in-memory value still applies.
  }
  return memory.get(key) ?? null;
}

export function writePref(key: string, value: string | null): void {
  if (value === null) {
    memory.delete(key);
  } else {
    memory.set(key, value);
  }
  try {
    if (value === null) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, value);
    }
  } catch {
    // Remembered for this visit only.
  }
  window.dispatchEvent(new Event(PREFS_EVENT));
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener('storage', onChange);
  window.addEventListener(PREFS_EVENT, onChange);
  return () => {
    window.removeEventListener('storage', onChange);
    window.removeEventListener(PREFS_EVENT, onChange);
  };
}

export function usePersistedFlag(key: string): [boolean, (next: boolean) => void] {
  const value = useSyncExternalStore(
    subscribe,
    () => readPref(key) === '1',
    () => false,
  );
  const set = useCallback((next: boolean) => writePref(key, next ? '1' : '0'), [key]);
  return [value, set];
}

/** Whether a CSS media query matches, kept live as the window resizes. */
export function useMediaQuery(query: string): boolean {
  const subscribeToQuery = useCallback(
    (onChange: () => void) => {
      const list = window.matchMedia(query);
      list.addEventListener('change', onChange);
      return () => list.removeEventListener('change', onChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribeToQuery,
    () => window.matchMedia(query).matches,
    () => false,
  );
}
