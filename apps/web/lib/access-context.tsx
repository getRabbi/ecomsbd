'use client';

import { createContext, useContext, useEffect, type ReactNode } from 'react';
import type { Access } from './access';
import { useApi, type AsyncState } from './useApi';

const AccessContext = createContext<AsyncState<Access> | null>(null);

export function AccessProvider({ children }: { children: ReactNode }) {
  const state = useApi<Access>('/billing/entitlements');
  const { reload } = state;
  useEffect(() => {
    const timer = window.setInterval(reload, 60_000);
    window.addEventListener('focus', reload);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', reload);
    };
  }, [reload]);
  return <AccessContext.Provider value={state}>{children}</AccessContext.Provider>;
}

export function useAccess() {
  return useContext(AccessContext);
}
