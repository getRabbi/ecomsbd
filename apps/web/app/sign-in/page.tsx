'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useState, type FormEvent } from 'react';

import { Card } from '@/components/ui';
import { useSession } from '@/lib/session';
import { getSupabase } from '@/lib/supabase';

/**
 * Sign in with Supabase.
 *
 * The same identity provider the mobile app uses, so one account works on both
 * and the JWT this produces is the one FastAPI already verifies. Nothing about
 * a shop is fetched here — a session first, then the dashboard asks the API who
 * it belongs to.
 */
export default function SignInPage() {
  const router = useRouter();
  const { session, loading, t } = useSession();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <main className="authpage">
      <div className="authcard">
        <Card title={t('auth.title')} hint={t('auth.subtitle')}>
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

            {error ? <p className="formerror">{error}</p> : null}

            <button
              type="submit"
              className="btn btn--primary"
              disabled={busy || !email || !password}
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
