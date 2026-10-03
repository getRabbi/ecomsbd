/** Only a short-lived signup capability/code lives in memory. No provider token. */
export type Bootstrap = {
  browser_session: string;
  connection_id: string;
  app_id: string;
  config_id: string;
  graph_version: string;
};
export type Assets = { waba_id?: string; phone_number_id?: string };
export type SignupResult = { result: string; connection_id: string; return_url: string };

export function signupState(fragment: string): string | null {
  const state = new URLSearchParams(fragment.replace(/^#/, '')).get('state');
  return state && /^[A-Za-z0-9_-]{40,100}$/.test(state) ? state : null;
}

export function metaMessage(origin: string, raw: unknown): { event: string; assets: Assets } | null {
  if (!['https://www.facebook.com', 'https://web.facebook.com', 'https://business.facebook.com'].includes(origin)) return null;
  try {
    const value = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (!value || value.type !== 'WA_EMBEDDED_SIGNUP') return null;
    if (!['FINISH', 'CANCEL', 'ERROR'].includes(value.event)) return null;
    const assets: Assets = {};
    for (const key of ['waba_id', 'phone_number_id'] as const) {
      const id = value.data?.[key];
      if (typeof id === 'string' && /^[0-9]{1,40}$/.test(id)) assets[key] = id;
    }
    return { event: value.event, assets };
  } catch { return null; }
}

export function loginOptions(configId: string) {
  // Meta v4: products/assets/permissions are selected in the configuration.
  return { config_id: configId, response_type: 'code', override_default_response_type: true, extras: { setup: {} } };
}

export function appLink(connectionId: string, result: string): string | null {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(connectionId) || !/^[A-Z_]{1,40}$/.test(result)) return null;
  return `com.ecomsbd.app://integrations/return?connection=${connectionId}&result=${result}`;
}

export async function signupRequest<T>(api: string, action: 'bootstrap' | 'complete', body: object): Promise<T> {
  const response = await fetch(`${api.replace(/\/$/, '')}/integration-callbacks/whatsapp-signup/${action}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body), credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer',
  });
  if (!response.ok) throw new Error('SIGNUP_REQUEST_FAILED');
  return response.json() as Promise<T>;
}

export function completionBody(session: Bootstrap, code: string | null, assets: Assets) {
  return { browser_session: session.browser_session, connection_id: session.connection_id,
    ...(code ? { authorization_code: code, ...assets } : { cancelled: true }) };
}
