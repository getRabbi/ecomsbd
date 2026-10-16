'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useRef, type ReactNode } from 'react';

import { RealtimeBridge } from '@/components/RealtimeBridge';
import { Shell } from '@/components/shell';
import { ShopGate } from '@/components/ShopSetup';
import { AccessProvider } from '@/lib/access-context';
import { useSession } from '@/lib/session';
import { ShopProvider, useShop } from '@/lib/shop';

/**
 * Every signed-in screen sits under this layout.
 *
 * The redirect here is a **convenience, not a control**. It keeps a signed-out
 * visitor from staring at an empty frame; it is not what protects the data.
 * That is the API, which verifies the Supabase JWT and checks the caller's
 * permission on every request — so a screen reached by editing the URL renders
 * its own error rather than somebody else's orders.
 *
 * When a session ends on its own (expiry, revocation, another tab), the sign-in
 * page is told so and given the address to come back to. A deliberate sign-out
 * is not an interruption and gets neither.
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { session, loading, t, endedByUser } = useSession();
  const hadSession = useRef(false);

  useEffect(() => {
    if (session) {
      hadSession.current = true;
      return;
    }
    if (loading) {
      return;
    }
    const params = new URLSearchParams();
    if (!endedByUser) {
      const here = `${window.location.pathname}${window.location.search}`;
      if (here !== '/dashboard') {
        params.set('next', here);
      }
      if (hadSession.current) {
        params.set('expired', '1');
      }
    }
    const query = params.toString();
    router.replace(query ? `/sign-in?${query}` : '/sign-in');
  }, [loading, session, endedByUser, router]);

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

  return (
    <ShopProvider key={session.user.id} userId={session.user.id}>
      <RealtimeBridge />
      <ShopGate>
        <Workspace>{children}</Workspace>
      </ShopGate>
    </ShopProvider>
  );
}

/**
 * Keyed by shop, so switching shops remounts every screen: no table, drawer or
 * cached response from one shop can survive into another.
 */
function Workspace({ children }: { children: ReactNode }) {
  const { workspaceKey } = useShop();
  return (
    <AccessProvider key={workspaceKey ?? 'none'}>
      <Shell>{children}</Shell>
    </AccessProvider>
  );
}
