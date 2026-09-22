'use client';

import { config } from './config';
import { getAccessToken, getSupabase } from './supabase';

/**
 * The only way this app reaches business data.
 *
 * Every screen goes through here, to the same FastAPI the mobile app uses, with
 * the same permissions, the same tenant scoping and the same audit trail. There
 * is no second backend, no route handler that queries a database, and no
 * Supabase table read anywhere in this client — so a permission the API
 * enforces cannot be bypassed by opening the web app instead of the phone.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;
  readonly details: Record<string, unknown> | null;

  constructor(
    status: number,
    code: string,
    message: string,
    options: { retryable?: boolean; details?: Record<string, unknown> | null } = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.retryable = options.retryable ?? false;
    this.details = options.details ?? null;
  }

  /** Whether the caller should send the person back to sign in. */
  get isAuthFailure(): boolean {
    return this.status === 401;
  }

  get isPermissionFailure(): boolean {
    return this.status === 403;
  }
}

export interface Page<T> {
  items: T[];
  /** Opaque. Passed back verbatim; never parsed or constructed by the client. */
  next_cursor: string | null;
  has_more: boolean;
}

type Query = Record<string, string | number | boolean | null | undefined>;

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  query?: Query;
  body?: unknown;
  signal?: AbortSignal;
  /** Language for the API's own error copy. */
  locale?: string;
}

function buildUrl(path: string, query?: Query): string {
  const base = config.apiBaseUrl.replace(/\/$/, '');
  const url = new URL(`${base}${path.startsWith('/') ? path : `/${path}`}`);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== '') {
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

async function parseError(response: Response): Promise<ApiError> {
  let code = 'UNKNOWN';
  let message = `Request failed (${response.status})`;
  let retryable = false;
  let details: Record<string, unknown> | null = null;

  try {
    const body = (await response.json()) as Record<string, unknown>;
    code = typeof body.code === 'string' ? body.code : code;
    retryable = body.retryable === true;
    details = (body.details as Record<string, unknown> | null) ?? null;
    // The API carries both languages; the caller picked one with its header.
    const localized = body.message_en ?? body.message;
    if (typeof localized === 'string' && localized) {
      message = localized;
    }
  } catch {
    // A non-JSON error body is a proxy or a gateway, not the API. The status
    // line is all there is, and inventing a friendlier message would hide it.
  }

  return new ApiError(response.status, code, message, { retryable, details });
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const token = await getAccessToken();
  if (!token) {
    throw new ApiError(401, 'NOT_AUTHENTICATED', 'Please sign in again.');
  }

  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    Accept: 'application/json',
    'Accept-Language': options.locale ?? 'en',
  };
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }

  const response = await fetch(buildUrl(path, options.query), {
    method: options.method ?? 'GET',
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
    // The API authenticates with the bearer token, not a cookie. Sending
    // credentials would invite CSRF on a cross-origin call for no benefit.
    credentials: 'omit',
    cache: 'no-store',
  });

  if (response.status === 401) {
    // The session is gone or rejected. Clearing it locally stops every other
    // panel on the page from retrying with the same dead token.
    await getSupabase().auth.signOut();
    throw new ApiError(401, 'NOT_AUTHENTICATED', 'Your session has ended. Please sign in again.');
  }

  if (!response.ok) {
    throw await parseError(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string, query?: Query, signal?: AbortSignal) =>
    request<T>(path, { query, signal }),
  post: <T>(path: string, body?: unknown, query?: Query) =>
    request<T>(path, { method: 'POST', body, query }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PUT', body }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};

/**
 * Send a file to the API as multipart form data, such as an import upload.
 *
 * The browser only carries the bytes: the API sniffs, parses and validates
 * them, so a large spreadsheet is never read into memory on this side. The
 * boundary header is left to the browser, which is the only thing that knows it.
 */
export async function upload<T>(path: string, form: FormData, signal?: AbortSignal): Promise<T> {
  const token = await getAccessToken();
  if (!token) {
    throw new ApiError(401, 'NOT_AUTHENTICATED', 'Please sign in again.');
  }

  const response = await fetch(buildUrl(path), {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' },
    body: form,
    signal,
    credentials: 'omit',
    cache: 'no-store',
  });

  if (response.status === 401) {
    await getSupabase().auth.signOut();
    throw new ApiError(401, 'NOT_AUTHENTICATED', 'Your session has ended. Please sign in again.');
  }
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as T;
}

/**
 * Download a file the API generates, such as an import's error rows.
 *
 * Kept separate from {@link request} because the response is a file rather than
 * JSON, and because the browser must be handed a blob it can save rather than a
 * parsed body.
 */
export async function download(path: string, fallbackName: string): Promise<void> {
  const token = await getAccessToken();
  if (!token) {
    throw new ApiError(401, 'NOT_AUTHENTICATED', 'Please sign in again.');
  }

  const response = await fetch(buildUrl(path), {
    headers: { Authorization: `Bearer ${token}` },
    credentials: 'omit',
    cache: 'no-store',
  });
  if (!response.ok) {
    throw await parseError(response);
  }

  const disposition = response.headers.get('Content-Disposition') ?? '';
  const match = /filename="?([^"]+)"?/.exec(disposition);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = match?.[1] ?? fallbackName;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}
