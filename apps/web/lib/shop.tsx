'use client';

import { useRouter } from 'next/navigation';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { api } from './api';
import { workspaceReady, type Me, type ShopSummary } from './me';
import { readPref, writePref } from './prefs';
import { useApi } from './useApi';

export { workspaceReady, type Me, type ShopSummary } from './me';

/**
 * Which shop this browser session is working in, and what the member may do
 * there — straight from `GET /v1/me`.
 *
 * The shop is bound to the *session* on the server (`POST /v1/auth/select-
 * tenant`), not chosen per request by the client, so the browser cannot ask
 * for another shop's data by sending a different id. Switching rebinds this
 * session only; the same account signed in on a phone keeps its own shop. The
 * browser's other tabs share the session, so they follow the switch.
 *
 * Every API call is slow from Bangladesh (the API and database are an ocean
 * apart), so the workspace does not wait on `/me` when this browser has already
 * opened this person's shop: it renders straight away under the remembered
 * shop, and `/me` confirms — or redirects to setup — when it lands. Only the
 * shop *id* is remembered, never anything it contains.
 */

interface ShopState {
  me: Me | null;
  loading: boolean;
  error: unknown;
  reload: () => void;
  current: ShopSummary | null;
  /** `null` until `/me` answers. */
  permissions: ReadonlySet<string> | null;
  /** `false` while permissions are unknown: never offer an edit on a guess. */
  can: (permission: string) => boolean;
  /** The shop this workspace renders under: confirmed, or remembered. */
  workspaceKey: string | null;
  /** The shop a switch is waiting on, until `/me` reports it. */
  switchingTo: string | null;
  /** Rebind this session to another of the member's shops, then go to `landOn`. */
  switchShop: (tenantId: string, landOn?: string) => Promise<void>;
}

const ShopContext = createContext<ShopState | null>(null);

const LAST_SHOP_KEY = 'ecomsbd.lastShop';
const readyKey = (userId: string) => `ecomsbd.readyShop.${userId}`;

export function rememberedShop(): string | null {
  return readPref(LAST_SHOP_KEY);
}

export function ShopProvider({ userId, children }: { userId: string; children: ReactNode }) {
  const router = useRouter();
  const { data, loading, error, reload } = useApi<Me>('/me');
  const [switchingTo, setSwitchingTo] = useState<string | null>(null);
  const [remembered] = useState(() => readPref(readyKey(userId)));

  useEffect(() => {
    if (data) {
      writePref(readyKey(userId), workspaceReady(data) ? data.tenant_id : null);
    }
  }, [data, userId]);

  // Every tab in this browser shares one Supabase session, and the shop is
  // bound to that session — so a switch made in another tab has already moved
  // this tab's requests too. Re-read `/me` so the screen follows, instead of
  // showing one shop's name over another shop's data and saving into it.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === LAST_SHOP_KEY) {
        reload();
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [reload]);

  const switchShop = useCallback(
    async (tenantId: string, landOn?: string) => {
      await api.post('/auth/select-tenant', { tenant_id: tenantId });
      writePref(LAST_SHOP_KEY, tenantId);
      setSwitchingTo(tenantId);
      if (landOn) {
        router.push(landOn);
      }
      reload();
    },
    [reload, router],
  );

  const value = useMemo<ShopState>(() => {
    const permissions = data ? new Set(data.permissions) : null;
    const current = data?.tenants.find((shop) => shop.id === data.tenant_id) ?? null;
    return {
      me: data,
      loading,
      error,
      reload,
      current,
      permissions,
      can: (permission) => permissions?.has(permission) ?? false,
      workspaceKey: data ? data.tenant_id : remembered,
      switchingTo: switchingTo !== null && data?.tenant_id !== switchingTo ? switchingTo : null,
      switchShop,
    };
  }, [data, loading, error, reload, remembered, switchingTo, switchShop]);

  return <ShopContext.Provider value={value}>{children}</ShopContext.Provider>;
}

export function useShop(): ShopState {
  const value = useContext(ShopContext);
  if (value === null) {
    throw new Error('useShop must be used inside ShopProvider');
  }
  return value;
}
