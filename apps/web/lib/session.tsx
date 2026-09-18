'use client';

import type { Session } from '@supabase/supabase-js';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';

import { type Locale, translate, type StringKey } from './i18n';
import { getSupabase } from './supabase';

/**
 * Who is signed in, and which language they read.
 *
 * The session comes from Supabase and nothing else: there is no parallel
 * "current user" fetched from the API and kept in sync, because two sources of
 * truth for *are you signed in* is how a dashboard ends up showing a shop to
 * someone whose session ended.
 */

interface SessionState {
  session: Session | null;
  /** `true` until Supabase has restored the session from storage. */
  loading: boolean;
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: StringKey, vars?: Record<string, string | number>) => string;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionState | null>(null);

const LOCALE_KEY = 'ecomsbd.locale';

//: Used when localStorage is unavailable — a private window, or storage
//: blocked. Without it the choice would not even apply for the current visit,
//: because the stored value is the only state there is.
let fallbackLocale: Locale = 'en';

function readStoredLocale(): Locale {
  try {
    const stored = window.localStorage.getItem(LOCALE_KEY);
    if (stored === 'bn' || stored === 'en') {
      return stored;
    }
  } catch {
    // Fall through to the in-memory value.
  }
  return fallbackLocale;
}

//: Dispatched by `setLocale`. `storage` only fires in *other* tabs, so without
//: this the tab that made the change would be the one tab that did not update.
const LOCALE_EVENT = 'ecomsbd:locale';

function subscribeToLocale(onChange: () => void): () => void {
  window.addEventListener('storage', onChange);
  window.addEventListener(LOCALE_EVENT, onChange);
  return () => {
    window.removeEventListener('storage', onChange);
    window.removeEventListener(LOCALE_EVENT, onChange);
  };
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const supabase = getSupabase();
    let active = true;

    supabase.auth.getSession().then(({ data }) => {
      if (active) {
        setSession(data.session);
        setLoading(false);
      }
    });

    // Covers a token refresh, a sign-out in another tab, and an expiry while
    // the page sits open overnight — all of which must move the UI, not just
    // start failing requests.
    const { data: subscription } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next);
      setLoading(false);
    });

    return () => {
      active = false;
      subscription.subscription.unsubscribe();
    };
  }, []);

  // localStorage *is* the state. Subscribing to it rather than mirroring it
  // into React state means there is one value, not two that can disagree — and
  // a language changed in another tab arrives here, which a read on mount
  // would have missed. The third argument is the server snapshot, since this
  // renders on the server before any storage exists.
  const locale = useSyncExternalStore(
    subscribeToLocale,
    readStoredLocale,
    (): Locale => 'en',
  );

  const setLocale = useCallback((next: Locale) => {
    fallbackLocale = next;
    try {
      window.localStorage.setItem(LOCALE_KEY, next);
    } catch {
      // Private mode, or storage disabled. The in-memory value above still
      // applies, so the choice holds for this visit — it is simply not
      // remembered next time.
    }
    window.dispatchEvent(new Event(LOCALE_EVENT));
  }, []);

  const signOut = useCallback(async () => {
    await getSupabase().auth.signOut();
    setSession(null);
  }, []);

  const value = useMemo<SessionState>(
    () => ({
      session,
      loading,
      locale,
      setLocale,
      t: (key, vars) => translate(locale, key, vars),
      signOut,
    }),
    [session, loading, locale, setLocale, signOut],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const value = useContext(SessionContext);
  if (value === null) {
    throw new Error('useSession must be used inside SessionProvider');
  }
  return value;
}

/** Just the translator, for components that need nothing else. */
export function useT() {
  return useSession().t;
}
