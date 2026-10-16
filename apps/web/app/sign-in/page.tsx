'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState, useSyncExternalStore, type FormEvent } from 'react';

import { Icon } from '@/components/icons';
import { BrandMark, LocaleToggle } from '@/components/ui';
import { safeNextPath } from '@/lib/nav';
import { useSession } from '@/lib/session';
import { getSupabase } from '@/lib/supabase';

// The address only changes here by leaving the page, so there is nothing to
// subscribe to; the snapshot is simply read on each render.
function subscribeToNothing(): () => void {
  return () => {};
}

// Supabase sends the browser back with `error` in the query (or the hash, for
// an implicit flow) when an OAuth round trip does not finish: consent refused,
// the account picker closed, a provider error.
function returnedWithOAuthError(): boolean {
  const query = new URLSearchParams(window.location.search);
  const hash = new URLSearchParams(window.location.hash.slice(1));
  return query.has('error') || hash.has('error');
}

function arrivedExpired(): boolean {
  return new URLSearchParams(window.location.search).get('expired') === '1';
}

//: Where to go after an OAuth round trip. The redirect URL handed to Supabase
//: stays exactly `/sign-in` — the allow-listed address — so the destination
//: rides in this tab's session storage instead of the query string.
const NEXT_KEY = 'ecomsbd.signInNext';

function queryNext(): string | null {
  return safeNextPath(new URLSearchParams(window.location.search).get('next'));
}

function readNext(): string | null {
  const fromQuery = queryNext();
  if (fromQuery) {
    return fromQuery;
  }
  try {
    return safeNextPath(window.sessionStorage.getItem(NEXT_KEY));
  } catch {
    return null;
  }
}

function stashNext(): void {
  try {
    const next = queryNext();
    if (next) {
      window.sessionStorage.setItem(NEXT_KEY, next);
    } else {
      window.sessionStorage.removeItem(NEXT_KEY);
    }
  } catch {
    // Without storage the seller lands on the dashboard, which is still right.
  }
}

function clearNext(): void {
  try {
    window.sessionStorage.removeItem(NEXT_KEY);
  } catch {
    // Nothing stored.
  }
}

type Mode = 'signIn' | 'signUp' | 'reset';
type Busy = null | 'password' | 'google' | 'apple' | 'signUp' | 'reset';

function GoogleMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}

/**
 * Sign in, create an account, or reset a password — all with Supabase.
 *
 * The same identity provider the mobile app uses, so one account works on both
 * and the JWT this produces is the one FastAPI already verifies. Google and
 * Apple are offered because an account created with either on the phone has no
 * password. Nothing about a shop is fetched here — a session first, then the
 * workspace asks the API which shop it belongs to, and sets one up if none.
 */
export default function SignInPage() {
  const router = useRouter();
  const { session, loading, t, locale } = useSession();

  const [mode, setMode] = useState<Mode>('signIn');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const oauthFailed = useSyncExternalStore(subscribeToNothing, returnedWithOAuthError, () => false);
  const expired = useSyncExternalStore(subscribeToNothing, arrivedExpired, () => false);
  const shownError = error ?? (oauthFailed && mode === 'signIn' ? t('auth.oauthIncomplete') : null);
  const bn = locale === 'bn';

  useEffect(() => {
    if (!loading && session) {
      const next = readNext();
      clearNext();
      router.replace(next ?? '/dashboard');
    }
  }, [loading, session, router]);

  function switchMode(next: Mode) {
    setMode(next);
    setError(null);
    setInfo(null);
  }

  async function onSignIn(event: FormEvent) {
    event.preventDefault();
    setBusy('password');
    setError(null);

    const { error: signInError } = await getSupabase().auth.signInWithPassword({
      email: email.trim(),
      password,
    });

    if (signInError) {
      // Deliberately one message for a wrong password and an unknown email:
      // telling them apart is how an attacker enumerates who has an account.
      setError(t('auth.failed'));
    }
    // On success the session listener fires and the effect above moves on.
    setBusy(null);
  }

  async function onSignUp(event: FormEvent) {
    event.preventDefault();
    setBusy('signUp');
    setError(null);
    setInfo(null);

    const { data, error: signUpError } = await getSupabase().auth.signUp({
      email: email.trim(),
      password,
      options: { emailRedirectTo: `${window.location.origin}/sign-in` },
    });

    if (signUpError) {
      // A weak password is the seller's to fix, so its own wording helps. Every
      // other refusal reads the same, existing account or not.
      setError(signUpError.code === 'weak_password' ? signUpError.message : t('auth.signUpFailed'));
    } else if (!data.session) {
      setInfo(t('auth.confirmEmail'));
    }
    setBusy(null);
  }

  async function onReset(event: FormEvent) {
    event.preventDefault();
    setBusy('reset');
    setError(null);
    setInfo(null);

    const { error: resetError } = await getSupabase().auth.resetPasswordForEmail(email.trim(), {
      redirectTo: `${window.location.origin}/reset-password`,
    });

    if (resetError && resetError.status === 429) {
      setError(resetError.message);
    } else {
      // The same answer whether or not the address has an account.
      setInfo(t('auth.linkSent'));
    }
    setBusy(null);
  }

  async function onOAuth(provider: 'google' | 'apple') {
    setBusy(provider);
    setError(null);
    stashNext();

    // PKCE: the browser client keeps the code verifier, the provider and
    // Supabase send the browser back here with `?code=`, and the client
    // exchanges it as it loads. The session listener then moves on exactly as
    // for a password.
    const { error: oauthError } = await getSupabase().auth.signInWithOAuth({
      provider,
      options: { redirectTo: `${window.location.origin}/sign-in` },
    });

    if (oauthError) {
      setError(provider === 'google' ? t('auth.googleIncomplete') : t('auth.oauthIncomplete'));
      setBusy(null);
    }
    // Otherwise the browser is already on its way to the provider.
  }

  const emailField = (
    <label className="field">
      <span className="field__label">{t('auth.email')}</span>
      <input
        className="input"
        type="email"
        autoComplete={mode === 'signUp' ? 'email' : 'username'}
        required
        value={email}
        onChange={(event) => setEmail(event.target.value)}
      />
    </label>
  );

  return (
    <main className="auth">
      <section className="auth__brand">
        <div className="auth__brandtop">
          <BrandMark size={40} />
          <span className="auth__brandname">{t('app.name')}</span>
        </div>
        <div>
          <h2 className="auth__headline">{t('brand.headline')}</h2>
          <ul className="auth__points">
            {(['brand.point1', 'brand.point2', 'brand.point3'] as const).map((key) => (
              <li key={key}>
                <span className="auth__tick">
                  <Icon name="check" size={14} />
                </span>
                {t(key)}
              </li>
            ))}
          </ul>
        </div>
        <p className="auth__brandfoot">{t('app.tagline')}</p>
      </section>

      <section className="auth__panel">
        <div className="auth__top">
          <span className="auth__mobilebrand">
            <BrandMark size={28} />
            {t('app.name')}
          </span>
          <LocaleToggle />
        </div>

        <div className="auth__form">
          {mode === 'reset' ? (
            <>
              <h1 className="auth__title">{t('auth.resetTitle')}</h1>
              <p className="auth__subtitle">{t('auth.resetHint')}</p>
              <form onSubmit={onReset}>
                {emailField}
                {shownError ? <p className="formerror" role="alert">{shownError}</p> : null}
                {info ? <div className="notice notice--good" role="status">{info}</div> : null}
                <button
                  type="submit"
                  className="btn btn--primary btn--block"
                  disabled={busy !== null || !email}
                >
                  {busy === 'reset' ? t('auth.sending') : t('auth.sendLink')}
                </button>
              </form>
              <button type="button" className="linkbtn auth__back" onClick={() => switchMode('signIn')}>
                ← {t('auth.backToSignIn')}
              </button>
            </>
          ) : (
            <>
              <div className="authtabs" role="group">
                <button
                  type="button"
                  className="authtab"
                  aria-pressed={mode === 'signIn'}
                  onClick={() => switchMode('signIn')}
                >
                  {t('auth.signIn')}
                </button>
                <button
                  type="button"
                  className="authtab"
                  aria-pressed={mode === 'signUp'}
                  onClick={() => switchMode('signUp')}
                >
                  {t('auth.signUpTab')}
                </button>
              </div>

              <h1 className="auth__title">{mode === 'signIn' ? t('auth.title') : t('auth.signUpTab')}</h1>
              <p className="auth__subtitle">{mode === 'signIn' ? t('auth.subtitle') : t('auth.signUpHint')}</p>

              {expired && mode === 'signIn' ? (
                <div className="notice notice--warn" role="status">
                  {t('auth.expired')}
                </div>
              ) : null}

              <div className="auth__providers">
                <button
                  type="button"
                  className="btn btn--block"
                  onClick={() => void onOAuth('google')}
                  disabled={busy !== null}
                >
                  <GoogleMark />
                  {t('auth.continueWithGoogle')}
                </button>
                <button
                  type="button"
                  className="btn btn--block btn--dark"
                  onClick={() => void onOAuth('apple')}
                  disabled={busy !== null}
                >
                  {t('auth.continueWithApple')}
                </button>
              </div>

              <p className="authdivider">
                <span>{t('auth.or')}</span>
              </p>

              <form onSubmit={mode === 'signIn' ? onSignIn : onSignUp}>
                {emailField}
                <label className="field">
                  <span className="field__labelrow">
                    <span className="field__label">{t('auth.password')}</span>
                    {mode === 'signIn' ? (
                      <button type="button" className="linkbtn" onClick={() => switchMode('reset')}>
                        {t('auth.forgot')}
                      </button>
                    ) : null}
                  </span>
                  <input
                    className="input"
                    type="password"
                    autoComplete={mode === 'signIn' ? 'current-password' : 'new-password'}
                    required
                    minLength={mode === 'signUp' ? 8 : undefined}
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                  {mode === 'signUp' ? <span className="field__hint">{t('auth.passwordRule')}</span> : null}
                </label>

                {shownError ? <p className="formerror" role="alert">{shownError}</p> : null}
                {info ? <div className="notice notice--good" role="status">{info}</div> : null}

                <button
                  type="submit"
                  className="btn btn--primary btn--block"
                  disabled={
                    busy !== null || !email || !password || (mode === 'signUp' && password.length < 8)
                  }
                >
                  {mode === 'signIn'
                    ? busy === 'password'
                      ? t('auth.signingIn')
                      : t('auth.signIn')
                    : busy === 'signUp'
                      ? t('auth.creating')
                      : t('auth.create')}
                </button>
              </form>
            </>
          )}
        </div>

        <footer className="auth__footer">
          <Link href={bn ? '/privacy-policy/bn' : '/privacy-policy'} className="btn btn--ghost btn--sm">
            {t('legal.privacy')}
          </Link>
          <Link href={bn ? '/account-deletion/bn' : '/account-deletion'} className="btn btn--ghost btn--sm">
            {t('legal.deletion')}
          </Link>
        </footer>
      </section>
    </main>
  );
}
