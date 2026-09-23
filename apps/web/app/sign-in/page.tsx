'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useState, useSyncExternalStore, type FormEvent } from 'react';

import { Card } from '@/components/ui';
import { useSession } from '@/lib/session';
import { getSupabase } from '@/lib/supabase';

// The address only changes here by leaving the page, so there is nothing to
// subscribe to; the snapshot is simply read on each render.
function subscribeToNothing(): () => void {
  return () => {};
}

// Supabase sends the browser back with `error` in the query (or the hash, for
// an implicit flow) when the Google round trip does not finish: consent
// refused, the account picker closed, a provider error.
function returnedWithOAuthError(): boolean {
  const query = new URLSearchParams(window.location.search);
  const hash = new URLSearchParams(window.location.hash.slice(1));
  return query.has('error') || hash.has('error');
}

/**
 * Sign in with Supabase, with Google or with an email and password.
 *
 * The same identity provider the mobile app uses, so one account works on both
 * and the JWT this produces is the one FastAPI already verifies. An account
 * created with Google on the phone has no password, which is why Google is
 * offered here too. Nothing about a shop is fetched here — a session first,
 * then the dashboard asks the API who it belongs to.
 */
export default function SignInPage() {
  const router = useRouter();
  const { session, loading, t } = useSession();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [googleBusy, setGoogleBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const googleFailed = useSyncExternalStore(
    subscribeToNothing,
    returnedWithOAuthError,
    () => false,
  );
  const shownError = error ?? (googleFailed ? t('auth.googleIncomplete') : null);

  useEffect(() => {
    if (!loading && session) {
      router.replace('/dashboard');
    }
  }, [loading, session, router]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);

    const { error: signInError } = await getSupabase().auth.signInWithPassword({
      email: email.trim(),
      password,
    });

    if (signInError) {
      // Deliberately one message for a wrong password and an unknown email:
      // telling them apart is how an attacker enumerates who has an account.
      setError(t('auth.failed'));
      setBusy(false);
      return;
    }

    // The session listener in SessionProvider will fire; the effect above then
    // moves to the dashboard.
    setBusy(false);
  }

  async function onGoogle() {
    setGoogleBusy(true);
    setError(null);

    // PKCE: the browser client keeps the code verifier, Google and Supabase
    // send the browser back here with `?code=`, and the client exchanges it as
    // it loads. The session listener then moves on exactly as for a password.
    const { error: oauthError } = await getSupabase().auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: `${window.location.origin}/sign-in` },
    });

    if (oauthError) {
      setError(t('auth.googleIncomplete'));
      setGoogleBusy(false);
    }
    // Otherwise the browser is already on its way to Google.
  }

  return (
    <main className="authpage">
      <div className="authcard">
        <Card title={t('auth.title')} hint={t('auth.subtitle')}>
          <button
            type="button"
            className="btn"
            onClick={onGoogle}
            disabled={googleBusy || busy}
            style={{ width: '100%' }}
          >
            {t('auth.continueWithGoogle')}
          </button>

          <p className="authdivider">{t('auth.or')}</p>

          <form onSubmit={onSubmit}>
            <label className="field">
              <span className="field__label">{t('auth.email')}</span>
              <input
                className="input"
                type="email"
                autoComplete="username"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </label>

            <label className="field">
              <span className="field__label">{t('auth.password')}</span>
              <input
                className="input"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </label>

            {shownError ? <p className="formerror">{shownError}</p> : null}

            <button
              type="submit"
              className="btn btn--primary"
              disabled={busy || googleBusy || !email || !password}
              style={{ width: '100%' }}
            >
              {busy ? t('auth.signingIn') : t('auth.signIn')}
            </button>
          </form>
        </Card>
      </div>
    </main>
  );
}
