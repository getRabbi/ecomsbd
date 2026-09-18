'use client';

import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

import { useSession } from '@/lib/session';

/**
 * The entry point decides where a visitor belongs, and nothing else.
 *
 * It waits for Supabase to restore the session first: redirecting before that
 * settles would bounce a signed-in seller to the sign-in page on every cold
 * load, which looks exactly like being signed out.
 */
export default function IndexPage() {
  const router = useRouter();
  const { session, loading, t } = useSession();

  useEffect(() => {
    if (loading) {
      return;
    }
    router.replace(session ? '/dashboard' : '/sign-in');
  }, [loading, session, router]);

  return (
    <div className="authpage">
      <p className="card__hint">{t('common.loading')}</p>
    </div>
  );
}
