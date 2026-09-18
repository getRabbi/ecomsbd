'use client';

import { createBrowserClient } from '@supabase/ssr';
import type { SupabaseClient } from '@supabase/supabase-js';

import { config } from './config';

/**
 * The Supabase browser client.
 *
 * Supabase is used for **authentication only**. It issues the session whose JWT
 * every FastAPI call carries, and nothing in this app ever queries a Supabase
 * table: the business data lives in PostgreSQL behind FastAPI, which is the
 * only thing allowed to read it. A `.from('orders')` anywhere under `apps/web`
 * would be a second, unauthorised path to the same data, with none of the
 * tenant scoping, permission checks or audit the API applies.
 *
 * One instance per browser tab, memoised: creating several would mean several
 * copies of the session and several refresh timers racing to rotate the same
 * token.
 */
let client: SupabaseClient | null = null;

export function getSupabase(): SupabaseClient {
  if (client === null) {
    client = createBrowserClient(config.supabaseUrl, config.supabaseAnonKey);
  }
  return client;
}

/**
 * The current access token, or `null` when nobody is signed in.
 *
 * Read fresh on every call rather than cached: `getSession` returns the token
 * the client has already refreshed in the background, so caching it here would
 * only create a second copy that can go stale and start failing calls a working
 * session would have served.
 */
export async function getAccessToken(): Promise<string | null> {
  const { data } = await getSupabase().auth.getSession();
  return data.session?.access_token ?? null;
}
