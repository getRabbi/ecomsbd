/**
 * Client-safe configuration.
 *
 * Everything here is `NEXT_PUBLIC_*` and therefore visible to anyone who opens
 * the page. That is the point: this file is the complete list of what the web
 * client is allowed to know, so reviewing it is how you check nothing else
 * leaked in.
 *
 * What must never appear here, or anywhere under `apps/web`:
 *
 * - the Supabase **service role** key. It bypasses row-level security; in a
 *   browser bundle it is a full database handover.
 * - any courier API key or secret. Those live encrypted in the backend vault
 *   and are never returned by any endpoint, to any client.
 * - any database URL or connection string. The web app talks to FastAPI and
 *   has no driver to use one with.
 *
 * The anon key is safe by design — it is a public identifier that grants
 * nothing on its own — but it is still only useful together with a Supabase
 * session, and every business call goes to FastAPI with that session's JWT.
 */

function required(name: string, value: string | undefined): string {
  if (!value) {
    // Failing at startup with the variable's name beats a blank dashboard and
    // a console full of 401s.
    throw new Error(
      `${name} is not set. The web dashboard needs it to reach Supabase and the API.`,
    );
  }
  return value;
}

export const config = {
  supabaseUrl: required(
    'NEXT_PUBLIC_SUPABASE_URL',
    process.env.NEXT_PUBLIC_SUPABASE_URL,
  ),
  supabaseAnonKey: required(
    'NEXT_PUBLIC_SUPABASE_ANON_KEY',
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  ),
  /** The FastAPI base URL, e.g. `https://api.example.com/v1`. */
  apiBaseUrl: required(
    'NEXT_PUBLIC_API_BASE_URL',
    process.env.NEXT_PUBLIC_API_BASE_URL,
  ),
} as const;

export type AppConfig = typeof config;
