'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState, type FormEvent } from 'react';

import { BrandMark, Card, LocaleToggle } from '@/components/ui';
import { useSession } from '@/lib/session';
import { getSupabase } from '@/lib/supabase';

/**
 * Where a password-reset email lands.
 *
 * The Supabase client exchanges the link's code as the page loads and reports
 * `PASSWORD_RECOVERY`; only then is a new password offered. An ordinary signed-
 * in session that opens this address has proved nothing about the inbox, and a
 * link opened in a different browser has no code verifier to exchange — both
 * get the honest "request a new link" instead of a form that would fail.
 */
export default function ResetPasswordPage() {
  const router = useRouter();
  const { session, loading, recovery, endRecovery, t } = useSession();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError(t('auth.mismatch'));
      return;
    }
    setBusy(true);
    setError(null);
    const { error: updateError } = await getSupabase().auth.updateUser({ password });
    if (updateError) {
      setError(updateError.code === 'weak_password' ? updateError.message : t('auth.updateFailed'));
      setBusy(false);
      return;
    }
    setDone(true);
    endRecovery();
    router.replace('/dashboard');
  }

  const ready = !loading && session !== null && recovery;

  return (
    <main className="authpage">
      <div className="authcard">
        <div className="setuppage__top">
          <span className="brandpill">
            <BrandMark size={28} />
            {t('app.name')}
          </span>
          <LocaleToggle />
        </div>
        <Card title={t('auth.resetTitle')}>
          {loading ? (
            <p className="card__hint">{t('common.loading')}</p>
          ) : done ? (
            <div className="notice notice--good" role="status">{t('auth.passwordUpdated')}</div>
          ) : !ready ? (
            <>
              <p className="card__hint" role="alert">{t('auth.resetExpired')}</p>
              <p style={{ marginTop: 14 }}>
                <Link href="/sign-in" className="btn">
                  {t('auth.backToSignIn')}
                </Link>
              </p>
            </>
          ) : (
            <form onSubmit={submit}>
              <label className="field">
                <span className="field__label">{t('auth.newPassword')}</span>
                <input
                  className="input"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={8}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
                <span className="field__hint">{t('auth.passwordRule')}</span>
              </label>
              <label className="field">
                <span className="field__label">{t('auth.confirmPassword')}</span>
                <input
                  className="input"
                  type="password"
                  autoComplete="new-password"
                  required
                  value={confirm}
                  onChange={(event) => setConfirm(event.target.value)}
                />
              </label>
              {error ? <p className="formerror" role="alert">{error}</p> : null}
              <button
                type="submit"
                className="btn btn--primary btn--block"
                disabled={busy || password.length < 8 || !confirm}
              >
                {t('auth.setPassword')}
              </button>
            </form>
          )}
        </Card>
      </div>
    </main>
  );
}
