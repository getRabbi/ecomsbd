'use client';

import { useEffect, useState } from 'react';

import { api } from './api';
import { useSession } from './session';

/**
 * The notification centre's shared pieces: the wire shape, where an alert's
 * target lives on the desktop, and the sidebar's unread badge.
 */

/** Fired after anything that changes the unread count; the sidebar badge listens. */
export const NOTIFICATIONS_CHANGED = 'ecomsbd:notifications-changed';

export const NOTIFICATION_CATEGORIES = [
  'MONEY',
  'RECONCILIATION',
  'COURIER',
  'RETURNS',
  'INVENTORY',
  'IMPORTS',
] as const;

export interface AppNotification {
  id: string;
  kind: string;
  severity: 'INFO' | 'ACTION' | 'WARNING' | 'CRITICAL';
  category: string;
  state: 'NEW' | 'READ' | 'RESOLVED';
  /** Already worded by the API in the requested language. */
  title: string;
  body: string;
  amount_paisa: number;
  read_at: string | null;
  resolved_at: string | null;
  payload: { target?: { route?: string; id?: string } };
  created_at: string;
}

export interface NotificationPreferences {
  muted_categories: string[];
  /** The categories this member's role receives, decided by the API. */
  categories: string[];
}

/**
 * Where an alert's target lives on the desktop.
 *
 * The API names a screen, not a URL. Targets are mapped to the list screen that
 * holds them; a target this build does not know opens nothing rather than
 * guessing, because landing on the wrong page is worse than staying put.
 */
const WEB_ROUTES: Record<string, string> = {
  receivables: '/money',
  reconciliation: '/reconciliation',
  reconciliation_case: '/reconciliation',
  returns: '/returns',
  import: '/imports',
  imports: '/imports',
  courier_account: '/couriers',
  courier_accounts: '/couriers',
  order: '/orders',
  orders: '/orders',
  product: '/products',
  products: '/products',
  profit: '/dashboard',
};

export function hrefFor(notification: Pick<AppNotification, 'payload'>): string | null {
  const route = notification.payload?.target?.route;
  return route ? (WEB_ROUTES[route] ?? null) : null;
}

/** The unread count for the sidebar badge, refreshed on navigation and changes. */
export function useUnreadCount(pathname: string): number {
  const [count, setCount] = useState(0);
  const { session } = useSession();

  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    const load = () =>
      api
        .get<{ unread: number }>('/notifications/unread-count')
        .then((body) => {
          if (!cancelled) setCount(body.unread);
        })
        .catch(() => {
          // A badge that cannot load stays as it was; the page itself reports errors.
        });
    load();
    window.addEventListener(NOTIFICATIONS_CHANGED, load);
    return () => {
      cancelled = true;
      window.removeEventListener(NOTIFICATIONS_CHANGED, load);
    };
  }, [session, pathname]);

  return count;
}
