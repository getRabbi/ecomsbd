'use client';

import { useRouter } from 'next/navigation';
import { useEffect, type ReactNode } from 'react';

import { Shell } from '@/components/shell';
import { useSession } from '@/lib/session';

/**
 * Every signed-in screen sits under this layout.
 *
 * The redirect here is a **convenience, not a control**. It keeps a signed-out
 * visitor from staring at an empty frame; it is not what protects the data.
 * That is the API, which verifies the Supabase JWT and checks the caller's
 * permission on every request — so a screen reached by editing the URL renders
 * its own error rather than somebody else's orders.
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { session, loading, t } = useSession();

  useEffect(() => {
    if (!loading && !session) {
      router.replace('/sign-in');
    }
  }, [loading, session, router]);

  if (loading) {
    return (
      <div className="authpage">
        <p className="card__hint">{t('common.loading')}</p>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="authpage">
        <p className="card__hint">{t('auth.needed')}</p>
      </div>
    );
  }

  return <Shell>{children}</Shell>;
}
