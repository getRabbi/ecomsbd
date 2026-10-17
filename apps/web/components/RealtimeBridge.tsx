'use client';

import { useEffect, useRef } from 'react';

import { config } from '@/lib/config';
import { realtimeUrl, setActiveRealtime, startRealtime } from '@/lib/realtime';
import { revalidateEverything, revalidateTopics } from '@/lib/revalidate';
import { useShop } from '@/lib/shop';
import { getAccessToken, onAccessTokenRefreshed } from '@/lib/supabase';

/**
 * Connect the signed-in workspace to the API's realtime socket.
 *
 * Renders nothing. Mounted inside the shop provider, so it exists exactly while
 * someone is signed in with a shop: signing out unmounts it, which closes the
 * socket. Invalidations become ordinary `useApi` refetches; a reconnect or a
 * server `resync` refetches everything on screen; a socket bound to a shop
 * other than the one this tab shows reloads `/me`, the same as a switch made in
 * another tab.
 */
export function RealtimeBridge() {
  const { me, reload } = useShop();
  const shopId = me?.tenant_id ?? null;
  const latest = useRef({ shopId, reload });

  useEffect(() => {
    latest.current = { shopId, reload };
  }, [shopId, reload]);

  const live = shopId !== null;

  useEffect(() => {
    if (!live) {
      return;
    }
    const handle = startRealtime({
      url: realtimeUrl(config.apiBaseUrl),
      getToken: getAccessToken,
      onInvalidate: revalidateTopics,
      onResync: revalidateEverything,
      onShop: (boundTo) => {
        if (boundTo !== latest.current.shopId) {
          latest.current.reload();
        }
      },
    });
    setActiveRealtime(handle);

    const stopTokenWatch = onAccessTokenRefreshed(() => handle.reauth());
    const wake = () => handle.wake();
    const wakeIfVisible = () => {
      if (document.visibilityState === 'visible') {
        handle.wake();
      }
    };
    window.addEventListener('online', wake);
    document.addEventListener('visibilitychange', wakeIfVisible);

    return () => {
      stopTokenWatch();
      window.removeEventListener('online', wake);
      document.removeEventListener('visibilitychange', wakeIfVisible);
      setActiveRealtime(null);
      handle.stop();
    };
  }, [live]);

  return null;
}
